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


def test_paginated_selection_manifest_and_verified_reuse(tmp_path):
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
    run(client, tmp_path)
    assert len(client.downloaded) == 3


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
