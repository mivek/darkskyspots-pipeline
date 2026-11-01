"""Frozen real-cell regressions; no local DEM downloads or network required."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio

from src.extract import mesh_darkest
from src.selection import CandidateQualifier


CASES_PATH = Path(__file__).parents[1] / 'fixtures/wbm_selection_cases.json'


@pytest.mark.parametrize('case', json.loads(CASES_PATH.read_text())['cases'], ids=lambda c: c['id'])
def test_real_cell_selects_frozen_best_eligible_pixel(case, monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('Unexpected network call')))
    tr = rasterio.Affine(*case['transform'])
    d, b = np.array(case['darkness']), np.array(case['bortle'])
    classes = np.array(case['wbm_classes'])

    def sample(lats, lons):
        rows, cols = rasterio.transform.rowcol(tr, lons, lats)
        return classes[rows, cols]

    q = CandidateQualifier(case['region_config'], tr, 'EPSG:4326', SimpleNamespace(wbm_classes=sample))
    actual = mesh_darkest(d, tr, qualify=q)
    expected = case['expected']
    if expected is None:
        assert actual == []
    else:
        assert len(actual) == 1
        p = actual[0]
        assert (p['row'], p['col']) == (expected['row'], expected['col'])
        assert (p['lat'], p['lon'], p['darkness'], p['country']) == (
            expected['lat'], expected['lon'], expected['darkness'], expected['country'])
        assert int(b[p['row'], p['col']]) == expected['bortle']
        assert classes[p['row'], p['col']] == 0
    r, c = case['original_local_pixel']
    if classes[r, c] != 0:
        assert not actual or (actual[0]['row'], actual[0]['col']) != (r, c)
