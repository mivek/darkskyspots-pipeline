"""Tests for the Natural Earth land mask and national clip."""

import logging

import pytest
from rasterio.transform import from_origin
from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from src.geography import Geography, classify_candidates, load_geography, validate_country_codes
from src.coverage import ensure_coverage
from src.extract import redundancy_filter
from src.tile_id import tile_id


def synthetic_geography() -> Geography:
    west = box(-2, -1, 0, 2)
    east = box(0, -1, 2, 2)
    island = box(10, 10, 11, 11)
    country_a = west
    country_b = east
    countries = {"AA": country_a, "BB": country_b, "CC": island}
    codes = tuple(sorted(countries))
    return Geography(
        land=unary_union([west, east, island]),
        countries=countries,
        country_tree=STRtree([countries[code] for code in codes]),
        country_codes=codes,
    )


def candidate(lon, lat, **extra):
    return {"lon": lon, "lat": lat, "darkness": 0.8, **extra}


def test_interior_is_assigned_and_tagged():
    kept, stats = classify_candidates([candidate(-1, 1)], ["AA"], geography=synthetic_geography())
    assert kept[0]["country"] == "AA"
    assert stats["country_candidates"] == 1


def test_sea_is_rejected():
    kept, stats = classify_candidates([candidate(5, 5)], ["AA"], geography=synthetic_geography())
    assert kept == []
    assert stats["sea_rejected"] == 1


def test_exact_coastline_is_land_because_covers_is_inclusive():
    kept, _ = classify_candidates([candidate(-2, 0)], ["AA"], geography=synthetic_geography())
    assert len(kept) == 1


def test_island_is_preserved():
    kept, _ = classify_candidates([candidate(10.5, 10.5)], ["CC"], geography=synthetic_geography())
    assert kept[0]["country"] == "CC"


def test_hole_is_rejected():
    geo = synthetic_geography()
    holey = box(-2, -1, 0, 2).difference(box(-1.5, 0, -0.5, 1))
    countries = {**geo.countries, "AA": holey}
    codes = tuple(sorted(countries))
    geo = Geography(unary_union([holey, geo.countries["BB"], geo.countries["CC"]]), countries, STRtree([countries[c] for c in codes]), codes)
    kept, stats = classify_candidates([candidate(-1, 0.5)], ["AA"], geography=geo)
    assert kept == []
    assert stats["sea_rejected"] == 1


def test_boundary_resolution_is_global_before_allowed_filter():
    # AA wins the lexical fallback.  BB must not get a different answer just
    # because this run's configured country list contains only BB.
    kept, stats = classify_candidates([candidate(0, 0)], ["BB"], geography=synthetic_geography())
    assert kept == []
    assert stats["other_country_rejected"] == 1
    assert stats["lexical_area_ties"] == 0


def test_equal_pixel_area_uses_lexical_tie_break_and_warns(caplog):
    geo = synthetic_geography()
    with caplog.at_level(logging.WARNING, logger="src.geography"):
        kept, stats = classify_candidates(
            [candidate(0, 0, row=0, col=0)],
            ["AA", "BB"],
            geography=geo,
            transform=from_origin(-1, 1, 2, 2),
            crs="EPSG:4326",
            equal_area_epsg=6933,
        )
    assert kept[0]["country"] == "AA"
    assert stats["ambiguous_country_candidates"] == 1
    assert stats["lexical_area_ties"] == 1
    assert "equal-area country boundary" in caplog.text


def test_unconfigured_country_is_rejected():
    kept, stats = classify_candidates([candidate(1, 1)], ["AA"], geography=synthetic_geography())
    assert kept == []
    assert stats["other_country_rejected"] == 1


def test_invalid_natural_earth_code_is_rejected():
    with pytest.raises(ValueError, match="ZZ"):
        validate_country_codes(["AA", "ZZ"], geography=synthetic_geography())


def test_andorra_and_monaco_are_kept_in_shared_french_tiles():
    geo = load_geography()
    andorra, _ = classify_candidates(
        [candidate(1.561736, 42.536035)], ["AD"], geography=geo
    )
    monaco, _ = classify_candidates(
        [candidate(7.402929, 43.741607)], ["MC"], geography=geo
    )
    assert andorra[0]["country"] == "AD"
    assert monaco[0]["country"] == "MC"
    assert tile_id(42.536035, 1.561736) == "N042E001"
    assert tile_id(43.741607, 7.402929) == "N043E007"
    france_pyrenees, _ = classify_candidates(
        [candidate(1.0, 42.8)], ["FR"], geography=geo
    )
    france_riviera, _ = classify_candidates(
        [candidate(7.1, 43.6)], ["FR"], geography=geo
    )
    assert france_pyrenees[0]["country"] == "FR"
    assert france_riviera[0]["country"] == "FR"
    assert tile_id(france_pyrenees[0]["lat"], france_pyrenees[0]["lon"]) == "N042E001"
    assert tile_id(france_riviera[0]["lat"], france_riviera[0]["lon"]) == "N043E007"


def test_foreign_or_sea_minimum_cannot_suppress_or_reenter_via_coverage():
    geo = synthetic_geography()
    foreign = candidate(0.01, 0, darkness=0.99, bortle=3)
    publishable = candidate(-0.01, 0, darkness=0.80, bortle=3)
    classified, _ = classify_candidates(
        [foreign, publishable], ["AA"], geography=geo
    )
    assert classified == [publishable]
    filtered = redundancy_filter(classified, min_distance_km=100)
    assert filtered == [publishable]
    covered = ensure_coverage(
        filtered,
        classified,
        [{"lat": 0, "lon": -0.01}],
        min_spots=2,
        radius_km=100,
    )
    assert covered == [publishable]
