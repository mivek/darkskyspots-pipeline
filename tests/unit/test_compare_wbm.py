from types import SimpleNamespace

import numpy as np
from rasterio.transform import from_origin
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

from src.geography import Geography
from tools.compare_wbm import historical_check, select_scenario, transitions
from tools.audit_wbm_osm import covered_points


def test_four_scenarios_choose_expected_coordinates(monkeypatch):
    # No comparison may accidentally access an online service.
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('Unexpected network call')))
    tr = from_origin(0, .01, .001, .001)
    g = box(-1, -1, 1, 1)
    geo = Geography(g, {'AA': g}, STRtree([g]), ('AA',))
    region = dict(bbox=[-1, -1, 1, 1], osm_country_code=['AA'], equal_area_epsg=6933)
    sampler = SimpleNamespace(wbm_classes=lambda lat, lon: np.array([2 if x < .001 else 0 for x in lon]))
    d, b = np.array([[.9, .8, .7]]), np.array([[1, 2, 3]])
    results = {s: select_scenario(d, b, tr, 'EPSG:4326', region, sampler, s, geography=geo) for s in 'ABCD'}
    assert [(p['col'], p['bortle']) for p in results['A']] == [(0, 1)]
    assert results['B'] == []
    assert [(p['col'], p['bortle']) for p in results['C']] == [(0, 1)]
    assert [(p['col'], p['bortle']) for p in results['D']] == [(1, 2)]
    delta = transitions(results['A'], results['D'], tr)
    assert delta['cells'][0]['kind'] == 'moved'
    assert delta['cells'][0]['before']['col'] == 0
    assert delta['cells'][0]['after']['col'] == 1
    assert delta['cells'][0]['darkness_delta'] == .8 - .9


def test_reference_comparison_records_last_bits_but_rejects_other_drift():
    old = dict(id='0.5000_0.5000', lat=.5, lon=.5, darkness=.8, bortle=2, country='AA')
    new = {**old, 'darkness': .8 + 1e-14}
    result = historical_check([old], [new])
    assert not result['exact'] and result['compatible']
    assert 'darkness' in result['differences'][0]['fields']
    assert not historical_check([old], [{**new, 'bortle': 3}])['compatible']
    assert not historical_check([old], [{**new, 'darkness': .81}])['compatible']
    assert not historical_check([old], [{**new, 'lat': .6}])['compatible']


def test_osm_water_check_preserves_island_holes_and_shore_boundaries():
    water = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                    holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    points = [Point(1, 1), Point(5, 5), Point(0, 2), Point(12, 12)]
    assert covered_points(water, STRtree(points)).tolist() == [0, 2]


def test_independent_scalar_verifier_distinguishes_land_water_nodata(tmp_path):
    from tests.unit.test_elevation import make_sampler
    from tools.check_wbm_results import direct_classes
    with make_sampler(tmp_path, [[1, 1], [1, 1]], [[0, 1], [255, 3]]):
        pass
    points = [dict(lat=lat, lon=lon, row=row, col=col) for lat, lon, row, col in
              [(.75, .25, 0, 0), (.75, .75, 0, 1), (.25, .25, 1, 0), (.25, .75, 1, 1)]]
    assert direct_classes(points, 'manifest', tmp_path, tmp_path) == {(0, 0): 0, (0, 1): 1, (1, 0): -1, (1, 1): 3}
