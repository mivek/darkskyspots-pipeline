"""Tests for src/coverage.py (OSM communes loader, coverage logic)."""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def test_haversine_km_known():
    """Paris to Lyon ≈ 393 km (well-known reference, re-exported via coverage)."""
    from src.utils import haversine_km
    d = haversine_km(48.8566, 2.3522, 45.7578, 4.8320)
    assert 390 < d < 396


def test_haversine_km_zero():
    """Same point -> 0 km."""
    from src.utils import haversine_km
    d = haversine_km(48.8566, 2.3522, 48.8566, 2.3522)
    assert d == pytest.approx(0.0, abs=1e-9)


def test_ensure_coverage_sufficient():
    """5 candidates within 100 km, min=4 -> no change."""
    from src.coverage import ensure_coverage
    candidates = [
        {"lat": 48.0 + i * 0.1, "lon": 2.0, "darkness": 0.9 - i * 0.05}
        for i in range(5)
    ]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    out = ensure_coverage(candidates, candidates, communes, min_spots=4, radius_km=100)
    assert len(out) == 5


def test_ensure_coverage_insufficient():
    """2 candidates within range, adds 2 darkest from mesh pool."""
    from src.coverage import ensure_coverage
    candidates = [
        {"lat": 48.0, "lon": 2.0, "darkness": 0.9},
        {"lat": 48.1, "lon": 2.0, "darkness": 0.8},
    ]
    pool = candidates + [
        {"lat": 48.2, "lon": 2.0, "darkness": 0.7},
        {"lat": 48.3, "lon": 2.0, "darkness": 0.6},
        {"lat": 48.4, "lon": 2.0, "darkness": 0.5},
    ]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    out = ensure_coverage(candidates, pool, communes, min_spots=4, radius_km=100)
    assert len(out) == 4


def test_ensure_coverage_empty_candidates():
    """No candidates, no mesh points -> returns []."""
    from src.coverage import ensure_coverage
    out = ensure_coverage([], [], [{"name": "X", "lat": 48.0, "lon": 2.0}])
    assert out == []


def test_ensure_coverage_adds_darkest_first():
    """When adding multiple, the darkest of the pool are chosen first."""
    from src.coverage import ensure_coverage
    candidates = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9}]
    pool = [
        {"lat": 48.0, "lon": 2.0, "darkness": 0.9},  # new dict, not the candidate object
        {"lat": 48.3, "lon": 2.0, "darkness": 0.5},
        {"lat": 48.1, "lon": 2.0, "darkness": 0.7},
        {"lat": 48.5, "lon": 2.0, "darkness": 0.6},
    ]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    # 1 candidate within 100 km of the commune, min=3 -> need 2 more.
    out = ensure_coverage(candidates, pool, communes, min_spots=3, radius_km=100)
    # The 2 added points should be the 2 darkest in the pool (excluding the
    # original candidate's id).
    pool_sorted = sorted([p for p in pool if id(p) != id(candidates[0])],
                         key=lambda p: p["darkness"], reverse=True)
    expected_added = pool_sorted[:2]
    expected_darknesses = [p["darkness"] for p in expected_added]
    added_darknesses = [c["darkness"] for c in out if id(c) != id(candidates[0])]
    assert sorted(added_darknesses, reverse=True) == expected_darknesses
    assert sorted(added_darknesses, reverse=True) == added_darknesses  # darkest first


def test_ensure_coverage_no_duplicates():
    """Same point not added twice (id-based dedup)."""
    from src.coverage import ensure_coverage
    point = {"lat": 48.5, "lon": 2.0, "darkness": 0.7}
    candidates = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9}]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    out = ensure_coverage(candidates, [point], communes, min_spots=4, radius_km=100)
    matching = [c for c in out if c["lat"] == 48.5 and c["lon"] == 2.0]
    assert len(matching) == 1


def test_load_communes_mocked(mock_region):
    """Mock requests.get to return a fake Overpass response, verify parsing,
    headers, and bbox query."""
    from src.coverage import load_communes
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 48.5,
                "lon": 2.0,
                "tags": {"name": "Paris", "population": "2100000"},
            },
            {
                "type": "way",
                "id": 2,
                "center": {"lat": 45.5, "lon": 4.0},
                "tags": {"name": "Lyon"},
            },
            {
                "type": "node",
                "id": 3,
                "lat": 43.0,
                "lon": 1.0,
                "tags": {},  # no name
            },
        ]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("src.coverage.requests.get") as mock_get:
        mock_get.return_value = mock_response
        communes = load_communes(mock_region)

    mock_get.assert_called_once()
    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"]["User-Agent"] == "darkskyspots-pipeline/1.0"
    # bbox [-5, 41, 10, 51] -> Overpass order (lat_min, lon_min, lat_max, lon_max) = (41, -5, 51, 10)
    assert "(41,-5,51,10)" in kwargs["params"]["data"]

    assert len(communes) == 3
    assert communes[0]["name"] == "Paris"
    assert communes[0]["population"] == 2100000
    assert communes[1]["name"] == "Lyon"
    assert communes[1]["lat"] == 45.5
    assert communes[2]["name"] == ""  # empty name, not skipped


def test_attach_near_town_sets_nearest_commune():
    """2 spots, 3 communes; each spot's near field is set to its closest commune."""
    from src.coverage import attach_near_town
    spots = [
        {"lat": 48.0, "lon": 2.0, "darkness": 0.9},
        {"lat": 45.0, "lon": 4.0, "darkness": 0.7},
    ]
    communes = [
        {"name": "Paris", "lat": 48.5, "lon": 2.0},
        {"name": "Lyon", "lat": 45.5, "lon": 4.5},
        {"name": "Marseille", "lat": 43.0, "lon": 5.0},
    ]
    out = attach_near_town(spots, communes)
    assert out[0]["near"] == "Paris"
    assert out[1]["near"] == "Lyon"


def test_attach_near_town_skips_when_no_commune():
    """Empty communes list -> near stays as before (None or unset)."""
    from src.coverage import attach_near_town
    spots = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9}]
    out = attach_near_town(spots, [])
    assert "near" in out[0]
    assert out[0]["near"] is None


def test_attach_near_town_preserves_existing_near():
    """If spot['near'] is already set and non-None, do not overwrite."""
    from src.coverage import attach_near_town
    spots = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9, "near": "CustomName"}]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    out = attach_near_town(spots, communes)
    assert out[0]["near"] == "CustomName"


def test_load_communes_retries_on_failure(mock_region):
    """2 failures then success; verify 3 calls made and result is parsed."""
    from src.coverage import load_communes
    import requests
    from unittest.mock import MagicMock, patch

    success_response = MagicMock()
    success_response.json.return_value = {"elements": [
        {"type": "node", "id": 1, "lat": 48.5, "lon": 2.0,
         "tags": {"name": "Paris", "population": "2100000"}}
    ]}
    success_response.raise_for_status = MagicMock()

    fail_resp = requests.ConnectionError("fail")
    mock_get = MagicMock(side_effect=[fail_resp, fail_resp, success_response])

    with patch("src.coverage.requests.get", mock_get), patch("src.coverage.time.sleep") as mock_sleep:
        communes = load_communes(mock_region)

    assert len(communes) == 1
    assert communes[0]["name"] == "Paris"
    assert mock_get.call_count == 3
    assert mock_sleep.call_count == 2  # verify backoff was invoked


def test_load_communes_raises_after_all_retries_exhausted(mock_region):
    """3 failures with no success raises RuntimeError."""
    from src.coverage import load_communes
    import requests
    from unittest.mock import MagicMock, patch

    fail_resp = requests.ConnectionError("fail")
    mock_get = MagicMock(side_effect=[fail_resp, fail_resp, fail_resp])

    with patch("src.coverage.requests.get", mock_get), patch("src.coverage.time.sleep"):
        with pytest.raises(RuntimeError, match="3 attempts"):
            load_communes(mock_region)

    assert mock_get.call_count == 3
