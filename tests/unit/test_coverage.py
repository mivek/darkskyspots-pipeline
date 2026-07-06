"""Tests for src/coverage.py (GeoNames places loader, coverage logic)."""
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


def test_load_places_parses_geonames(tmp_path, mock_region):
    """Parse a tab-separated GeoNames snippet, verify correct extraction of
    name, lat, lon, population."""
    from src.coverage import load_places
    # Create a fake .txt file with 3 real-looking GeoNames lines
    # Format: geonameid\tname\tasciiname\talternatenames\tlatitude\tlongitude
    #         \tfeature class\tfeature code\tcountry code\tcc2\tadmin1 code
    #         \tadmin2 code\tadmin3 code\tadmin4 code\tpopulation\televation
    #         \tdem\ttimezone\tmodification date
    lines = [
        # Paris
        "2988507\tParis\tParis\tCity of Light\t48.85341\t2.3488\tP\tPPLC\tFR\t\t11\t75\t\t\t2161000\t33\t33\tEurope/Paris\t2025-01-01\n",
        # Lyon
        "2996944\tLyon\tLyon\t\t45.74846\t4.84671\tP\tPPLA\tFR\t\t84\t69\t\t\t513275\t174\t174\tEurope/Paris\t2025-01-01\n",
        # Small village near Toulouse (no population)
        "6452033\tCugnaux\tCugnaux\t\t43.5377\t1.3441\tP\tPPL\tFR\t\t31\t31\t\t\t\tnull\t185\tEurope/Paris\t2025-01-01\n",
    ]
    txt_path = tmp_path / "cities500.txt"
    txt_path.write_text("".join(lines), encoding="utf-8")

    # Load from the tmp_path directory (not the real data/ dir)
    places = load_places(mock_region, data_dir=str(tmp_path))
    assert len(places) == 3
    assert places[0]["name"] == "Paris"
    assert places[0]["lat"] == 48.85341
    assert places[0]["lon"] == 2.3488
    assert places[0]["population"] == 2161000
    assert places[1]["name"] == "Lyon"
    assert places[1]["lat"] == 45.74846
    assert places[1]["lon"] == 4.84671
    assert places[2]["name"] == "Cugnaux"
    assert places[2]["population"] is None  # empty string -> None


def test_load_places_filters_by_bbox_margin(tmp_path, mock_region):
    """Verify places outside the bbox+100km margin are filtered out."""
    from src.coverage import load_places
    # Use a narrow bbox and a point far outside
    tight_region = dict(mock_region, bbox=[2.0, 48.0, 3.0, 49.0])  # tiny box near Paris
    lines = [
        # Paris (lon=2.35, lat=48.85) — inside tight bbox
        "2988507\tParis\tParis\t\t48.85341\t2.3488\tP\tPPLC\tFR\t\t11\t75\t\t\t2161000\t33\t33\tEurope/Paris\t2025-01-01\n",
        # Moscow (lon=37.62, lat=55.75) — far outside any margin
        "524901\tMoscow\tMoscow\t\t55.75222\t37.61556\tP\tPPLC\tRU\t\t48\t\t\t\t12678079\t156\t156\tEurope/Moscow\t2025-01-01\n",
        # London (lon=-0.12, lat=51.51) — ~350 km from tight bbox, outside 100km margin
        "2643743\tLondon\tLondon\t\t51.50722\t-0.12750\tP\tPPLC\tGB\t\tENG\t\t\t\t8982000\t15\t15\tEurope/London\t2025-01-01\n",
    ]
    txt_path = tmp_path / "cities500.txt"
    txt_path.write_text("".join(lines), encoding="utf-8")

    places = load_places(tight_region, data_dir=str(tmp_path))
    # Only Paris should be retained
    assert len(places) == 1
    assert places[0]["name"] == "Paris"


def test_attach_near_town_sets_nearest_commune():
    """2 spots, 3 communes; each spot's near field is set to its closest commune."""
    from src.coverage import attach_near_town
    spots = [
        {"lat": 48.4, "lon": 2.1, "darkness": 0.9},  # ~12 km from Paris
        {"lat": 45.42, "lon": 4.58, "darkness": 0.7},  # ~12 km from Lyon
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
    """Empty communes list -> near becomes empty string (too far from anything)."""
    from src.coverage import attach_near_town
    spots = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9}]
    out = attach_near_town(spots, [])
    assert "near" in out[0]
    assert out[0]["near"] == ""


def test_attach_near_town_preserves_existing_near():
    """If spot['near'] is already set and non-None, do not overwrite."""
    from src.coverage import attach_near_town
    spots = [{"lat": 48.0, "lon": 2.0, "darkness": 0.9, "near": "CustomName"}]
    communes = [{"name": "Paris", "lat": 48.5, "lon": 2.0}]
    out = attach_near_town(spots, communes)
    assert out[0]["near"] == "CustomName"


def test_attach_near_town_leaves_empty_when_too_far():
    """All communes > 25 km away → near becomes empty string."""
    from src.coverage import attach_near_town
    spots = [{"lat": 44.0, "lon": 2.0, "darkness": 0.9}]  # middle of France
    communes = [
        {"name": "Paris", "lat": 48.5, "lon": 2.0},       # ~500 km
        {"name": "Lyon", "lat": 45.5, "lon": 4.5},         # ~200 km
        {"name": "Marseille", "lat": 43.0, "lon": 5.0},    # ~250 km
    ]
    out = attach_near_town(spots, communes)
    assert out[0]["near"] == ""


def test_attach_near_town_sets_near_when_within_range():
    """Commune within 25 km → near is set."""
    from src.coverage import attach_near_town
    spots = [{"lat": 48.45, "lon": 2.05, "darkness": 0.9}]  # ~7 km from Paris center
    communes = [
        {"name": "Paris", "lat": 48.5, "lon": 2.0},
    ]
    out = attach_near_town(spots, communes)
    assert out[0]["near"] == "Paris"
