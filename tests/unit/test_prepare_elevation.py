"""CDSE preparation tests with a paginated, isolated S3 service double."""
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from botocore.exceptions import ClientError
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from src.elevation import ElevationSampler
from tools.prepare_elevation import prepare


def raster(value):
    with MemoryFile() as mem:
        with mem.open(driver='GTiff', width=2, height=2, count=1, dtype='int16',
                      crs='EPSG:4326', transform=from_origin(0, 1, .5, .5), nodata=-9999) as dst:
            dst.write(np.full((2, 2), value, dtype='int16'), 1)
        return mem.read()


def key(product, cell, layer):
    lat, lon = cell
    coord = f'{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_{"E" if lon >= 0 else "W"}{abs(lon):03d}_00'
    stem = f'Copernicus_DSM_{"10" if product == "GLO-30" else "30"}_{coord}'
    return f'CCM/COP-DEM_{product}-DGED/SAR_DGE/2011/01/25/product_{product}_{cell[0]}_{cell[1]}/{layer}/{stem}_{layer}.tif'


class FakeS3:
    def __init__(self, objects, fail_listing=None, fail_download=None):
        self.objects = objects
        self.fail_listing = fail_listing
        self.fail_download = fail_download
        self.listed = []
        self.downloaded = []

    def get_paginator(self, operation):
        assert operation == 'list_objects_v2'
        return self

    def paginate(self, Bucket, Prefix):
        assert Bucket == 'eodata'
        self.listed.append(Prefix)
        found = [{'Key': name, 'Size': len(data)} for name, data in self.objects.items() if name.startswith(Prefix)]
        yield {'Contents': found[:1]}
        if self.fail_listing and self.fail_listing in Prefix:
            raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'permission denied'}}, 'ListObjectsV2')
        yield {'Contents': found[1:]}

    def download_file(self, bucket, name, target):
        self.downloaded.append(name)
        if self.fail_download == name:
            Path(target).write_bytes(b'partial')
            raise OSError('connection interrupted')
        Path(target).write_bytes(self.objects[name])


class CatalogueResponse:
    def __init__(self, products):
        self.products = products

    def raise_for_status(self):
        pass

    def json(self):
        return {'value': self.products}


class FakeCatalogue:
    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get(self, url, params=None, timeout=None):
        import re
        assert url == 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
        query = params['$filter']
        self.calls.append(query)
        product = 'GLO-90' if 'GLO-90-DGED' in query else 'GLO-30'
        cells = re.findall(r"Value eq '([NS]\d{2}_[EW]\d{3})'", query)
        products = []
        for name in cells:
            lat = int(name[1:3]) * (-1 if name[0] == 'S' else 1)
            lon = int(name[5:8]) * (-1 if name[4] == 'W' else 1)
            candidate = key(product, (lat, lon), 'DEM')
            mask = key(product, (lat, lon), 'WBM')
            if candidate not in self.objects and mask not in self.objects:
                continue
            prefix = candidate.rsplit('/', 2)[0]
            products.append({'S3Path': '/eodata/' + prefix, 'Online': True,
                             'Attributes': [{'Name': 'gridId', 'Value': name},
                                            {'Name': 'dataset', 'Value': f'COP-DEM_{product}-DGED/2024_1'}]})
        return CatalogueResponse(products)


def pair(product, cell):
    return {key(product, cell, 'DEM'): raster(42), key(product, cell, 'WBM'): raster(0)}


def run(client, tmp_path, cells={(0, 0)}, **kwargs):
    return prepare(client, 'test', {'bbox': [0, 0, 2, 1]}, cells,
                   tmp_path / 'cache', tmp_path / 'manifests', '2024_1',
                   catalog_session=kwargs.pop('catalog_session', FakeCatalogue(client.objects)), **kwargs)


def test_paginated_selection_manifest_and_verified_reuse(tmp_path, capsys):
    objects = {**pair('GLO-30', (0, 0)), **pair('GLO-30', (1, 1))}
    client = FakeS3(objects)
    manifest = run(client, tmp_path)
    assert len(client.downloaded) == 2
    tile = manifest['tiles'][0]
    assert tile['dem']['source'] == 's3://eodata/' + key('GLO-30', (0, 0), 'DEM')
    assert tile['dem']['size'] == len(objects[key('GLO-30', (0, 0), 'DEM')])
    path = tmp_path / 'manifests' / 'test.json'
    assert json.loads(path.read_text()) == manifest
    with ElevationSampler(path, tmp_path / 'cache') as sampler:
        assert sampler.sample(.75, .25) == 42
    run(client, tmp_path)
    assert len(client.downloaded) == 2
    cached = tmp_path / 'cache' / tile['dem']['path']
    cached.write_bytes(b'x' * cached.stat().st_size)
    with pytest.raises(ValueError, match='Corrupt elevation'):
        run(client, tmp_path)
    assert len(client.downloaded) == 2
    refreshed = run(client, tmp_path, refresh=True)
    assert len(client.downloaded) == 4
    assert 'Cache validation failed' in capsys.readouterr().err
    assert refreshed['tiles'][0]['dem']['path'] != tile['dem']['path']
    with ElevationSampler(path, tmp_path / 'cache') as sampler:
        assert sampler.sample(.75, .25) == 42


def test_dry_run_does_not_write_and_lists_sizes(tmp_path, capsys):
    client = FakeS3(pair('GLO-30', (0, 0)))
    assert run(client, tmp_path, dry_run=True) is None
    assert not client.downloaded
    assert not list(tmp_path.iterdir())
    assert '2 files' in capsys.readouterr().out


def test_fallback_only_for_absent_complete_cell(tmp_path):
    client = FakeS3({**pair('GLO-30', (0, 0)), **pair('GLO-90', (0, 1))})
    with pytest.raises(ValueError, match='GLO-30 cells missing'):
        run(client, tmp_path, cells={(0, 0), (0, 1)})
    assert not client.downloaded
    manifest = run(client, tmp_path, cells={(0, 0), (0, 1)}, allow_glo90=True)
    assert [tile['product'] for tile in manifest['tiles']] == ['GLO-30', 'GLO-90']


def test_incomplete_pair_does_not_trigger_fallback(tmp_path):
    objects = pair('GLO-30', (0, 0))
    del objects[key('GLO-30', (0, 0), 'WBM')]
    client = FakeS3({**objects, **pair('GLO-90', (0, 0))})
    with pytest.raises(ValueError, match='Missing DEM/WBM pair'):
        run(client, tmp_path, allow_glo90=True)
    assert len(client.listed) == 1
    assert not client.downloaded


def test_pagination_failure_never_triggers_fallback(tmp_path):
    client = FakeS3({**pair('GLO-30', (0, 0)), **pair('GLO-90', (0, 0))}, fail_listing='GLO-30')
    with pytest.raises(ClientError, match='permission denied'):
        run(client, tmp_path, allow_glo90=True)
    assert len(client.listed) == 1
    assert not client.downloaded
    assert not list(tmp_path.iterdir())


def test_interrupted_download_does_not_write_manifest_or_partial_file(tmp_path):
    dem = key('GLO-30', (0, 0), 'DEM')
    client = FakeS3(pair('GLO-30', (0, 0)), fail_download=dem)
    with pytest.raises(OSError, match='connection interrupted'):
        run(client, tmp_path, allow_glo90=True)
    assert len(client.listed) == 1
    assert not (tmp_path / 'manifests' / 'test.json').exists()
    assert not list(tmp_path.rglob('*.part'))


def test_empty_catalogue_never_triggers_fallback(tmp_path):
    client = FakeS3(pair('GLO-90', (0, 0)))
    with pytest.raises(ValueError, match='GLO-30 cells missing'):
        run(client, tmp_path)
    assert not client.listed
    assert not client.downloaded


def test_invalid_raster_never_writes_manifest(tmp_path):
    objects = pair('GLO-30', (0, 0))
    objects[key('GLO-30', (0, 0), 'WBM')] = b'not a tiff'
    with pytest.raises(rasterio.errors.RasterioIOError):
        run(FakeS3(objects), tmp_path)
    assert not (tmp_path / 'manifests' / 'test.json').exists()


def test_missing_credentials_reported_without_network(monkeypatch):
    from tools.prepare_elevation import client_from_environment
    monkeypatch.delenv('CDSE_ACCESS_KEY', raising=False)
    monkeypatch.delenv('CDSE_SECRET_KEY', raising=False)
    with pytest.raises(ValueError, match='CDSE_ACCESS_KEY'):
        client_from_environment()


def test_short_download_is_rejected(tmp_path):
    class ShortDownload(FakeS3):
        def download_file(self, bucket, name, target):
            Path(target).write_bytes(b'partial')
    client = ShortDownload(pair('GLO-30', (0, 0)))
    with pytest.raises(ValueError, match='Incorrect size'):
        run(client, tmp_path)
    assert not (tmp_path / 'manifests' / 'test.json').exists()
    assert not list(tmp_path.rglob('*.part'))


def test_catalogue_failure_does_not_trigger_fallback(tmp_path):
    class BrokenCatalogue:
        def get(self, *args, **kwargs):
            raise ConnectionError('connection closed')
    client = FakeS3(pair('GLO-30', (0, 0)))
    with pytest.raises(ConnectionError, match='connection closed'):
        run(client, tmp_path, allow_glo90=True, catalog_session=BrokenCatalogue())
    assert not client.listed
    assert not client.downloaded


def test_catalogue_uses_version_and_single_grid_filter():
    from tools.prepare_elevation import catalogue_products
    client = FakeCatalogue({**pair('GLO-30', (0, 0)), **pair('GLO-30', (0, 1))})
    products = catalogue_products(client, 'GLO-30', '2024_1', {(0, 0), (0, 1)})
    assert len(products) == 2
    assert len(client.calls) == 2
    assert all('COP-DEM_GLO-30-DGED/2024_1' in call and ' or ' not in call for call in client.calls)
    assert all(path.startswith('CCM/COP-DEM_GLO-30-DGED/') for path in products.values())


def test_empty_s3_listing_for_catalogue_product_is_error(tmp_path):
    objects = pair('GLO-30', (0, 0))
    client = FakeS3({})
    with pytest.raises(ValueError, match='S3 returned no objects'):
        run(client, tmp_path, allow_glo90=True, catalog_session=FakeCatalogue(objects))
    assert len(client.listed) == 1
    assert not client.downloaded


def test_catalogue_page_failure_never_triggers_fallback(tmp_path):
    class PagedCatalogue:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                class FirstPage(CatalogueResponse):
                    def json(self):
                        return {'value': [], '@odata.nextLink': 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products?$skiptoken=next'}
                return FirstPage([])
            raise ConnectionError('catalogue unavailable')

    client = FakeS3(pair('GLO-90', (0, 0)))
    with pytest.raises(ConnectionError, match='catalogue unavailable'):
        run(client, tmp_path, allow_glo90=True, catalog_session=PagedCatalogue())
    assert not client.listed
    assert not client.downloaded


def test_catalogue_page_link_must_stay_on_cdse(tmp_path):
    class BadLinkCatalogue:
        def get(self, url, **kwargs):
            class BadPage(CatalogueResponse):
                def json(self):
                    return {'value': [], '@odata.nextLink': 'https://example.org/unexpected'}
            return BadPage([])

    client = FakeS3({})
    with pytest.raises(ValueError, match='Invalid or repeated catalogue page'):
        run(client, tmp_path, catalog_session=BadLinkCatalogue())
    assert not client.listed


class OfflineNetwork:
    def __getattr__(self, name):
        raise AssertionError(f'Unexpected network access: {name}')


def test_valid_cache_reused_offline_even_during_dry_run(tmp_path, monkeypatch):
    from tools import prepare_elevation
    original = run(FakeS3(pair('GLO-30', (0, 0))), tmp_path)
    monkeypatch.setattr(prepare_elevation, 'client_from_environment', lambda: pytest.fail('S3 client created'))
    monkeypatch.setattr(prepare_elevation.requests, 'Session', lambda: pytest.fail('catalogue session created'))
    args = (None, 'test', {'bbox': [0, 0, 2, 1]}, {(0, 0)}, tmp_path / 'cache', tmp_path / 'manifests', '2024_1')
    assert prepare(*args, catalog_session=OfflineNetwork()) == original
    assert prepare(*args, catalog_session=OfflineNetwork(), dry_run=True) is None


@pytest.mark.parametrize('change,expected', [
    (lambda d: d.update(schema=2), 'schema'),
    (lambda d: d.update(region='other'), 'region'),
    (lambda d: d.update(bbox=[0, 0, 3, 1]), 'bbox'),
    (lambda d: d['tiles'][0].update(version='2023_1'), 'version'),
    (lambda d: d['tiles'].append(d['tiles'][0].copy()), 'duplicate'),
    (lambda d: d['tiles'][0].update(lon=1), 'coverage'),
    (lambda d: d['tiles'][0].update(product='GLO-90'), 'allow-glo90'),
])
def test_incompatible_manifest_fails_offline(tmp_path, change, expected):
    run(FakeS3(pair('GLO-30', (0, 0))), tmp_path)
    path = tmp_path / 'manifests' / 'test.json'
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=expected):
        prepare(OfflineNetwork(), 'test', {'bbox': [0, 0, 2, 1]}, {(0, 0)},
                tmp_path / 'cache', tmp_path / 'manifests', '2024_1', catalog_session=OfflineNetwork())


def test_malformed_manifest_fails_without_network(tmp_path):
    path = tmp_path / 'manifests' / 'test.json'
    path.parent.mkdir()
    path.write_text('{')
    with pytest.raises(ValueError, match='Invalid or incompatible elevation cache'):
        prepare(OfflineNetwork(), 'test', {'bbox': [0, 0, 2, 1]}, {(0, 0)},
                tmp_path / 'cache', path.parent, '2024_1', catalog_session=OfflineNetwork())


@pytest.mark.parametrize('kind', ['missing', 'size', 'sha256'])
def test_invalid_file_fails_without_network(tmp_path, kind):
    data = run(FakeS3(pair('GLO-30', (0, 0))), tmp_path)
    path = tmp_path / 'cache' / data['tiles'][0]['dem']['path']
    if kind == 'missing':
        path.unlink()
    elif kind == 'size':
        path.write_bytes(b'x')
    else:
        path.write_bytes(b'x' * path.stat().st_size)
    with pytest.raises(ValueError, match='(Missing|Corrupt) elevation'):
        prepare(OfflineNetwork(), 'test', {'bbox': [0, 0, 2, 1]}, {(0, 0)},
                tmp_path / 'cache', tmp_path / 'manifests', '2024_1', catalog_session=OfflineNetwork())


def test_refresh_failure_preserves_old_manifest_and_files(tmp_path):
    objects = pair('GLO-30', (0, 0))
    old = run(FakeS3(objects), tmp_path)
    path = tmp_path / 'manifests' / 'test.json'
    before = path.read_bytes()
    failed = FakeS3(objects, fail_download=key('GLO-30', (0, 0), 'WBM'))
    with pytest.raises(OSError, match='connection interrupted'):
        run(failed, tmp_path, refresh=True)
    assert path.read_bytes() == before
    assert (tmp_path / 'cache' / old['tiles'][0]['dem']['path']).exists()
    with ElevationSampler(path, tmp_path / 'cache') as sampler:
        assert sampler.sample(.75, .25) == 42


def test_refresh_dry_run_uses_network_without_writes(tmp_path):
    objects = pair('GLO-30', (0, 0))
    run(FakeS3(objects), tmp_path)
    path = tmp_path / 'manifests' / 'test.json'
    before = path.read_bytes()
    client = FakeS3(objects)
    assert run(client, tmp_path, refresh=True, dry_run=True) is None
    assert client.listed and not client.downloaded
    assert path.read_bytes() == before


def test_both_s3_pages_are_needed_to_find_pair(tmp_path):
    client = FakeS3(pair('GLO-30', (0, 0)))
    data = run(client, tmp_path)
    assert len(client.listed) == 1
    assert len(client.downloaded) == 2
    assert data['tiles'][0]['dem']['source'].endswith('_DEM.tif')
    assert data['tiles'][0]['wbm']['source'].endswith('_WBM.tif')
