"""Tests for src/utils.py (haversine_km, nearest_commune)."""
import math
import pytest


def test_haversine_km_known():
    """Paris to Lyon ≈ 393 km (well-known reference)."""
    from src.utils import haversine_km
    d = haversine_km(48.8566, 2.3522, 45.7578, 4.8320)
    assert 390 < d < 396


def test_haversine_km_zero():
    """Same point → 0 km."""
    from src.utils import haversine_km
    d = haversine_km(48.8566, 2.3522, 48.8566, 2.3522)
    assert d == pytest.approx(0.0, abs=1e-9)


def test_haversine_km_symmetry():
    """haversine(a, b) == haversine(b, a)."""
    from src.utils import haversine_km
    a = haversine_km(48.8566, 2.3522, 45.7578, 4.8320)
    b = haversine_km(45.7578, 4.8320, 48.8566, 2.3522)
    assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)


def test_haversine_km_antipodal():
    """Antipodal points ≈ half the Earth's circumference (≈ 20015 km)."""
    from src.utils import haversine_km
    d = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert 20000 < d < 20030


def test_nearest_commune_closest_wins():
    """Spot near a cluster: the closest commune wins."""
    from src.utils import nearest_commune
    spot = {"lat": 44.0, "lon": 2.0}
    communes = [
        {"name": "Far", "lat": 50.0, "lon": 5.0},
        {"name": "Near", "lat": 44.01, "lon": 2.01},
        {"name": "Medium", "lat": 45.0, "lon": 3.0},
    ]
    assert nearest_commune(spot, communes) == "Near"


def test_nearest_commune_empty_list():
    """Empty communes list → None."""
    from src.utils import nearest_commune
    assert nearest_commune({"lat": 44.0, "lon": 2.0}, []) is None


def test_nearest_commune_handles_missing_coords():
    """Commune with lat=None is skipped (does not raise, does not win)."""
    from src.utils import nearest_commune
    spot = {"lat": 44.0, "lon": 2.0}
    communes = [
        {"name": "NoCoords", "lat": None, "lon": None},
        {"name": "Valid", "lat": 44.0, "lon": 2.0},
    ]
    assert nearest_commune(spot, communes) == "Valid"
