"""Tests for src/tile_id.py (Decision 3: universal tile naming)."""
import pytest

from src.tile_id import TILE_PATTERN, tile_id, tile_bounds


def test_canonical_example():
    """The example from spec-technique.md: (42.7283, 1.6492) → N042E001."""
    assert tile_id(42.7283, 1.6492) == "N042E001"


def test_sw_quadrant():
    """SW quadrant with negative coords."""
    assert tile_id(-3.5, -42.25) == "S004W043"


def test_equator_prime_meridian():
    """(0, 0) → N000E000."""
    assert tile_id(0, 0) == "N000E000"


def test_south_of_equator():
    """Just south of equator + west of Greenwich → S001W001."""
    assert tile_id(-0.0001, -0.0001) == "S001W001"


def test_north_pole_band():
    """N089 band (lat in [89, 90))."""
    assert tile_id(89.9999, 42) == "N089E042"


def test_south_pole_band():
    """S090 band (lat in [-90, -89))."""
    assert tile_id(-89.9999, 42) == "S090E042"


def test_north_pole_exact_raises():
    """Exact +90 is a pole point → ValueError."""
    with pytest.raises(ValueError):
        tile_id(90.0, 42)


def test_south_pole_exact_raises():
    """Exact -90 is a pole point → ValueError."""
    with pytest.raises(ValueError):
        tile_id(-90.0, 42)


def test_antimeridian():
    """lon = -180 → W180 tile (the antimeridian)."""
    assert tile_id(0, -180) == "N000W180"


def test_longitude_180_raises():
    """lon = 180 is invalid (use -180 for W180)."""
    with pytest.raises(ValueError):
        tile_id(0, 180)


def test_tile_bounds_roundtrip():
    """tile_bounds(tile_id(lat, lon)) contains (lat, lon)."""
    lat, lon = 42.7283, 1.6492
    tid = tile_id(lat, lon)
    lat_min, lon_min, lat_max, lon_max = tile_bounds(tid)
    assert lat_min <= lat < lat_max
    assert lon_min <= lon < lon_max


def test_tile_bounds_invalid_raises():
    """Bad format raises ValueError."""
    with pytest.raises(ValueError):
        tile_bounds("invalid")
    with pytest.raises(ValueError):
        tile_bounds("N42E001")  # not zero-padded
    with pytest.raises(ValueError):
        tile_bounds("N042E0001")  # 4-digit lon


def test_all_tiles_unique_and_deterministic():
    """Property test: all 64,800 tiles (180 lat × 360 lon) are unique and well-formed.

    Uses lat = i + 0.5 for i in range(-90, 90) — this covers every 1° band
    (S090 via floor(-89.5)=-90, N089 via floor(89.5)=89) while staying clear
    of the exact poles (±90.0) and the antimeridian (+180.0).
    """
    names = set()
    for lat in range(-90, 90):       # 180 band midpoints (avoid ±90 poles)
        for lon in range(-180, 180):  # 360 band midpoints (avoid +180 antimeridian)
            tid = tile_id(float(lat) + 0.5, float(lon) + 0.5)
            assert TILE_PATTERN.match(tid), f"Format invalid: {tid}"
            names.add(tid)
    assert len(names) == 64800, f"Expected 64800 unique tiles, got {len(names)}"
