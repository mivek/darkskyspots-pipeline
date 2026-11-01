import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.elevation import ElevationSampler, sha256
from tools.check_elevation import compare


def make_sampler(tmp_path, elevations, water, **options):
    entries = {}
    for layer, array, nodata in [('dem', elevations, -9999), ('wbm', water, 255)]:
        path = tmp_path / f'{layer}.tif'
        with rasterio.open(path, 'w', driver='GTiff', width=2, height=2, count=1,
                           dtype='int16', crs='EPSG:4326', transform=from_origin(0, 1, .5, .5), nodata=nodata, **options) as out:
            out.write(np.array(array, dtype='int16'), 1)
        entries[layer] = {'path': path.name, 'size': path.stat().st_size, 'sha256': sha256(path)}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'tiles': [{'lat': 0, 'lon': 0, 'product': 'GLO-30', **entries}]}))
    return ElevationSampler(manifest, tmp_path)


def test_land_and_water_and_nodata(tmp_path):
    with make_sampler(tmp_path, [[-12, 0], [100, -9999]], [[0, 0], [1, 0]]) as sampler:
        assert sampler.sample(.75, .25) == -12
        assert sampler.sample(.75, .75) == 0
        assert sampler.sample(.25, .25) is None
        assert sampler.sample(.25, .75) is None
        assert dict(sampler.counts) == {'land': 2, 'ocean': 1, 'lake': 0, 'river': 0, 'nodata': 1}
        with pytest.raises(ValueError, match='No prepared DEM'):
            sampler.sample(.5, 1.5)


def test_water_classes_are_counted_separately(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[1, 2], [3, 0]]) as sampler:
        assert sampler.sample(.75, .25) is None
        assert sampler.sample(.75, .75) is None
        assert sampler.sample(.25, .25) is None
        assert sampler.sample(.25, .75) == 1
        assert sampler.counts == {'ocean': 1, 'lake': 1, 'river': 1, 'land': 1}


def test_grouped_wbm_reads_are_cached_and_do_not_touch_altitude_counts(tmp_path):
    with make_sampler(tmp_path, [[-12, 0], [4, 5]], [[0, 1], [255, 3]]) as sampler:
        expected = [0, 1, -1, 3, 0]
        lats, lons = [.75, .75, .25, .25, .75], [.25, .75, .25, .75, .25]
        assert sampler.wbm_classes(lats, lons).tolist() == expected
        reads = sampler.wbm_io['block_reads']
        assert reads < len(lats)
        assert sampler.wbm_classes(lats, lons).tolist() == expected
        assert sampler.wbm_io['block_reads'] == reads
        assert sampler.wbm_io['transformed_points'] == 0
        assert sampler.wbm_cache_size <= sampler.wbm_cache_limit
        assert sampler.counts == {'ocean': 0, 'lake': 0, 'river': 0}
        assert sampler.sample(.75, .25) == -12


def test_grouped_wbm_rejects_unknown_classes_and_missing_tiles(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[7, 0], [0, 0]]) as sampler:
        with pytest.raises(ValueError, match='Invalid WBM'):
            sampler.wbm_classes([.75], [.25])
        with pytest.raises(ValueError, match='No prepared DEM'):
            sampler.wbm_classes([.75], [1.25])


def test_zero_wbm_cache_budget_does_not_retain_arrays(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 0], [0, 0]]) as sampler:
        sampler.wbm_cache_limit = 0
        assert sampler.wbm_classes([.75], [.25]).tolist() == [0]
        assert not sampler.wbm_cache and sampler.wbm_cache_size == 0
        reads = sampler.wbm_io['block_reads']
        sampler.wbm_classes([.75], [.25])
        assert sampler.wbm_io['block_reads'] > reads


def test_wbm_cache_evicts_old_blocks_and_accounts_for_masks(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 1], [2, 3]], blockysize=1) as sampler:
        sampler.wbm_cache_limit = 6  # two int16 values plus two mask bytes
        assert sampler.wbm_classes([.75], [.25]).tolist() == [0]
        assert sampler.wbm_classes([.25], [.25]).tolist() == [2]
        assert sampler.wbm_classes([.75], [.25]).tolist() == [0]
        assert sampler.wbm_io['block_reads'] == 3
        assert sampler.wbm_cache_size == sampler.wbm_io['peak_cache_bytes'] == 6


def test_projected_wbm_transforms_a_group_not_each_point(tmp_path, monkeypatch):
    from rasterio.transform import from_bounds
    from unittest.mock import Mock
    from src import elevation
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 1], [2, 3]]):
        pass
    m = json.loads((tmp_path / 'manifest.json').read_text())
    for layer in ['dem', 'wbm']:
        p = tmp_path / f'{layer}.tif'
        with rasterio.open(p, 'r+') as ds:
            ds.crs = 'EPSG:3857'
            ds.transform = from_bounds(0, 0, 111319.49079327357, 111325.1428663851, 2, 2)
        m['tiles'][0][layer].update(size=p.stat().st_size, sha256=sha256(p))
    (tmp_path / 'manifest.json').write_text(json.dumps(m))
    spy = Mock(wraps=elevation.transform)
    monkeypatch.setattr(elevation, 'transform', spy)
    with ElevationSampler(tmp_path / 'manifest.json', tmp_path) as sampler:
        assert sampler.wbm_classes([.75, .25], [.25, .75]).tolist() == [0, 3]
        assert spy.call_count == 1
        assert sampler.wbm_io['transformed_points'] == 2


def test_unreadable_and_missing_wbm_are_technical_errors(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 0], [0, 0]]):
        pass
    p = tmp_path / 'wbm.tif'
    p.write_bytes(b'not a raster')
    m = json.loads((tmp_path / 'manifest.json').read_text())
    m['tiles'][0]['wbm'].update(size=p.stat().st_size, sha256=sha256(p))
    (tmp_path / 'manifest.json').write_text(json.dumps(m))
    with pytest.raises(rasterio.errors.RasterioIOError):
        ElevationSampler(tmp_path / 'manifest.json', tmp_path)
    p.unlink()
    with pytest.raises(ValueError, match='Missing elevation wbm'):
        ElevationSampler(tmp_path / 'manifest.json', tmp_path)


def test_unmasked_nan_is_invalid_wbm_not_declared_nodata(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 0], [0, 0]]):
        pass
    p = tmp_path / 'wbm.tif'
    with rasterio.open(p, 'w', driver='GTiff', width=2, height=2, count=1,
                       dtype='float32', crs='EPSG:4326', transform=from_origin(0, 1, .5, .5)) as ds:
        ds.write(np.array([[np.nan, 0], [0, 0]], dtype='float32'), 1)
    m = json.loads((tmp_path / 'manifest.json').read_text())
    m['tiles'][0]['wbm'].update(size=p.stat().st_size, sha256=sha256(p))
    (tmp_path / 'manifest.json').write_text(json.dumps(m))
    with ElevationSampler(tmp_path / 'manifest.json', tmp_path) as sampler:
        with pytest.raises(ValueError, match='Invalid WBM'):
            sampler.wbm_classes([.75], [.25])


def test_invalid_water_mask_and_corrupt_file(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[4, 0], [0, 0]]) as sampler:
        with pytest.raises(ValueError, match='Invalid WBM'):
            sampler.sample(.75, .25)
    (tmp_path / 'dem.tif').write_bytes(b'bad')
    with pytest.raises(ValueError, match='Corrupt'):
        ElevationSampler(tmp_path / 'manifest.json', tmp_path)


def test_witness_is_sampled_at_its_own_coordinates(tmp_path):
    with make_sampler(tmp_path, [[10, 20], [30, 40]], [[0, 0], [0, 0]]) as sampler:
        result = compare(sampler, {'elevations_m': {'0.75_0.25': 11, '0.25_0.75': 37}})
    assert result['paired'] == 2
    assert result['bias_median'] == 1
    assert result['absolute_error_median'] == 2


def test_migration_preserves_other_spot_fields_and_regenerates_clusters(tmp_path, monkeypatch):
    from tools import migrate_elevation
    from types import SimpleNamespace

    repo = tmp_path / 'repo'
    spots = repo / 'spots'
    spots.mkdir(parents=True)
    original = {'id': 'x', 'lat': .75, 'lon': .25, 'country': 'FR', 'altitude': None, 'darkness': .9}
    water_spots = [
        {'id': name, 'lat': lat, 'lon': lon, 'country': 'FR', 'altitude': None}
        for name, lat, lon in [('ocean', .75, .75), ('lake', .25, .25), ('river', .25, .75)]
    ]
    envelope = {'tile': 'N000E000', 'version': '2025.1', 'source': 'test', 'generated': 'old', 'spots': [original, *water_spots]}
    path = spots / 'N000E000.json'
    path.write_text(json.dumps(envelope))
    sampler = make_sampler(tmp_path, [[-12, 1], [1, 1]], [[0, 1], [2, 3]])
    monkeypatch.setattr(migrate_elevation, 'load_regions', lambda: {'test': {}})
    monkeypatch.setattr(migrate_elevation, 'build_region_ownerships', lambda _: {'test': SimpleNamespace(owns=lambda spot: True)})
    monkeypatch.setattr(migrate_elevation, 'ElevationSampler', lambda *args: sampler)
    monkeypatch.setattr(migrate_elevation, '_migration_data_year', lambda *_: 2025)
    cluster_calls = []
    monkeypatch.setattr(migrate_elevation, 'write_cluster_files', lambda *args, **kwargs: cluster_calls.append((args, kwargs)))
    report = migrate_elevation.migrate(repo, tmp_path, tmp_path)
    result = json.loads(path.read_text())
    assert result['spots'][0] == {**original, 'altitude': -12}
    assert result['version'] == envelope['version']
    assert report['changed_tiles'] == 1
    assert report['samples']['test'] == {'land': 1, 'ocean': 1, 'lake': 1, 'river': 1}
    assert cluster_calls[0][0] == (repo / 'spots', repo / 'clusters')
    assert cluster_calls[0][1]['generated']


def test_mismatched_water_grid_is_rejected(tmp_path):
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 0], [0, 0]]):
        pass
    mask = tmp_path / 'wbm.tif'
    with rasterio.open(mask, 'r+') as dataset:
        dataset.transform = from_origin(.1, 1, .5, .5)
    manifest = tmp_path / 'manifest.json'
    content = json.loads(manifest.read_text())
    content['tiles'][0]['wbm']['sha256'] = sha256(mask)
    content['tiles'][0]['wbm']['size'] = mask.stat().st_size
    manifest.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='Mismatched'):
        ElevationSampler(manifest, tmp_path)
