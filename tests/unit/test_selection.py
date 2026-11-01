"""Selection invariants, including exact legacy coordinates and country rules."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box
from shapely.strtree import STRtree

from src.extract import mesh_darkest, redundancy_filter
from src.coverage import ensure_coverage
from src.geography import Geography, classify_candidates
from src.selection import CandidateQualifier


def geography(countries=None):
    countries = countries or {'AA': box(-1, -1, 2, 2)}
    from shapely.ops import unary_union
    return Geography(unary_union(list(countries.values())), countries,
                     STRtree(list(countries.values())), tuple(countries))


def qualifier(transform, classes, *, geo=None, bbox=None):
    sampler = SimpleNamespace(wbm_classes=lambda lat, lon: np.asarray([classes(x, y) for x, y in zip(lat, lon)]))
    region = dict(bbox=bbox or [-1, -1, 2, 2], osm_country_code=['AA'], equal_area_epsg=6933)
    return CandidateQualifier(region, transform, 'EPSG:4326', sampler, geography=geo or geography())


def test_retries_water_and_nodata_then_selects_land_once():
    tr = from_origin(0, .01, .001, .001)
    d = np.array([[.9, .8, .7, .6]])
    q = qualifier(tr, lambda lat, lon: {0: 2, 1: -1, 2: 0, 3: 0}[int(lon / .001)])
    stats = {}
    result = mesh_darkest(d, tr, qualify=q, stats=stats)
    assert [(x['row'], x['col'], x['darkness']) for x in result] == [(0, 2, .7)]
    assert q.counts['wbm_lake'] == q.counts['wbm_nodata'] == 1
    assert stats['ranked_cells'] == 1
    assert stats['candidates_examined'] == 3
    assert np.array_equal(d, [[.9, .8, .7, .6]])


def test_all_water_or_nodata_leaves_cell_empty():
    tr = from_origin(0, .01, .001, .001)
    q = qualifier(tr, lambda lat, lon: -1)
    assert mesh_darkest(np.array([[.8, .7]]), tr, qualify=q) == []
    assert q.counts['wbm_nodata'] == 2


def test_legacy_admissible_maximum_is_exactly_preserved():
    tr = from_origin(-.017123456789, .0123456789, .001, .001)
    d = np.random.default_rng(32).random((51, 73))
    q = qualifier(tr, lambda lat, lon: 0)
    legacy = mesh_darkest(d, tr)
    actual = mesh_darkest(d, tr, qualify=q, batch_size=3)
    assert [{k: v for k, v in p.items() if k != 'country'} for p in actual] == legacy


def test_equal_maxima_use_row_major_after_rejection_and_batch_order():
    tr = from_origin(0, .1, .001, .001)
    d = np.full((2, 50), .8)
    q = qualifier(tr, lambda lat, lon: 0 if int(round(lon / .001 - .5)) % 45 else 1)
    result = mesh_darkest(d, tr, qualify=q, batch_size=1)
    assert [(p['row'], p['col']) for p in result] == [(0, 1), (0, 46)]


def test_geographic_rejection_tries_another_pixel_before_wbm():
    tr = from_origin(0, .01, .001, .001)
    geo = geography({'BB': box(0, 0, .001, .02), 'AA': box(.001, 0, 1, 1)})
    q = qualifier(tr, lambda lat, lon: 0, geo=geo)
    result = mesh_darkest(np.array([[.9, .8]]), tr, qualify=q)
    assert result[0]['col'] == 1 and result[0]['country'] == 'AA'
    assert q.counts['other_country_rejected'] == 1
    assert q.counts['wbm_land'] == 1


def test_same_country_area_rule_is_called_with_original_pixel():
    # Centre is on both countries; AA has a larger part of the source pixel.
    tr = from_origin(-1, 1, 2, 2)
    geo = geography({'AA': box(-1, -1, .5, 1), 'BB': box(0, -1, 1, 1)})
    spy = Mock(wraps=classify_candidates)
    q = CandidateQualifier(dict(bbox=[-2, -2, 2, 2], osm_country_code=['AA'], equal_area_epsg=6933),
                           tr, 'EPSG:4326', SimpleNamespace(wbm_classes=lambda *_: [0]),
                           geography=geo, classify=spy)
    result = mesh_darkest(np.array([[.8]]), tr, mesh_km=500, qualify=q)
    assert result[0]['country'] == 'AA'
    assert q.counts['ambiguous_country_candidates'] == 1
    assert spy.call_args.kwargs['transform'] == tr
    assert spy.call_args.kwargs['crs'] == 'EPSG:4326'
    assert spy.call_args.args[0][0]['row'] == spy.call_args.args[0][0]['col'] == 0


def test_missing_wbm_and_unsupported_viirs_crs_fail_explicitly():
    with pytest.raises(ValueError, match='verified terrain'):
        CandidateQualifier({}, None, None, None)
    with pytest.raises(ValueError, match='EPSG:4326'):
        mesh_darkest(np.array([[.8]]), from_origin(0, 1, .1, .1), qualify=lambda p: p, crs='EPSG:3035')


def test_water_never_enters_redundancy_or_coverage():
    tr = from_origin(0, .01, .001, .001)
    q = qualifier(tr, lambda lat, lon: 1 if lon < .001 else 0)
    selected = mesh_darkest(np.array([[.9, .8]]), tr, qualify=q)
    for p in selected:
        p['bortle'] = 2
    filtered = redundancy_filter(selected)
    covered = ensure_coverage(filtered, selected, [dict(lat=.01, lon=0)], min_spots=4)
    assert covered == selected
    assert [(p['row'], p['col']) for p in covered] == [(0, 1)]


def test_outside_bbox_is_not_queried_in_wbm():
    tr = from_origin(0, .01, .001, .001)
    sampler = Mock()
    q = CandidateQualifier(dict(bbox=[1, 1, 2, 2], osm_country_code=['AA'], equal_area_epsg=6933),
                           tr, 'EPSG:4326', sampler, geography=geography())
    assert mesh_darkest(np.array([[.8]]), tr, qualify=q) == []
    sampler.wbm_classes.assert_not_called()
