"""Tests for src/extract.py (mesh_minima + redundancy_filter)."""
import numpy as np
import pytest
from rasterio.transform import from_bounds


def test_mesh_minima_simple():
    """Small array with a known minimum in the center: finder picks it."""
    from src.extract import mesh_minima
    # 10x10, one cell (~5km mesh, ~0.05 deg per pixel)
    darkness = np.full((10, 10), 0.5, dtype=np.float64)
    darkness[5, 5] = 0.1  # global minimum
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    points = mesh_minima(darkness, transform, mesh_km=50)
    # Mesh of 50 km / 0.05 deg per pixel = 1000 px per cell. So one cell covers the whole grid.
    # But min is found at (5, 5).
    assert any(abs(p["row"] - 5) < 2 and abs(p["col"] - 5) < 2 for p in points)


def test_mesh_minima_skips_nan():
    """All-NaN cell produces no point."""
    from src.extract import mesh_minima
    darkness = np.full((10, 10), np.nan, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    points = mesh_minima(darkness, transform, mesh_km=50)
    assert points == []


def test_mesh_minima_deterministic():
    """Same input twice returns same points (D6 tie-breaker)."""
    from src.extract import mesh_minima
    darkness = np.random.default_rng(42).uniform(0.0, 1.0, (20, 20))
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    a = mesh_minima(darkness, transform, mesh_km=50)
    b = mesh_minima(darkness, transform, mesh_km=50)
    assert a == b


def test_mesh_minima_cell_size():
    """A 100x100 array with mesh_km=50 (~0.45 deg, ~3-4 px) yields 500-1000 points."""
    from src.extract import mesh_minima
    darkness = np.random.default_rng(0).uniform(0.0, 1.0, (100, 100))
    transform = from_bounds(-5, 41, 10, 51, 100, 100)
    points = mesh_minima(darkness, transform, mesh_km=50)
    # ~15 deg lon / 0.45 deg/cell = 33 cols; 10 deg lat / 0.45 deg = 22 rows; ~726 cells
    assert 500 < len(points) < 1000


def test_mesh_minima_transform():
    """Lat/lon output is reasonable for the transform."""
    from src.extract import mesh_minima
    darkness = np.full((10, 10), 0.5, dtype=np.float64)
    darkness[5, 5] = 0.1
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    points = mesh_minima(darkness, transform, mesh_km=50)
    assert len(points) >= 1
    p = points[0]
    # The point should be within the bbox of the transform
    assert -5 <= p["lon"] <= 10
    assert 41 <= p["lat"] <= 51


# --- redundancy_filter tests ---

def test_filter_same_bortle_close():
    """2 spots within 15 km, same bortle -> only 1 kept (the darker)."""
    from src.extract import redundancy_filter
    cands = [
        {"lat": 44.0, "lon": 2.0, "darkness": 0.9, "bortle": 2},
        {"lat": 44.05, "lon": 2.05, "darkness": 0.8, "bortle": 2},  # ~5.6 km
    ]
    out = redundancy_filter(cands)
    assert len(out) == 1
    assert out[0]["darkness"] == 0.9


def test_filter_same_bortle_far():
    """2 spots > 15 km apart, same bortle -> both kept."""
    from src.extract import redundancy_filter
    cands = [
        {"lat": 44.0, "lon": 2.0, "darkness": 0.9, "bortle": 2},
        {"lat": 45.0, "lon": 3.0, "darkness": 0.8, "bortle": 2},  # ~150 km
    ]
    out = redundancy_filter(cands)
    assert len(out) == 2


def test_filter_different_bortle_close():
    """2 spots close but different bortle -> both kept (different display class)."""
    from src.extract import redundancy_filter
    cands = [
        {"lat": 44.0, "lon": 2.0, "darkness": 0.9, "bortle": 2},
        {"lat": 44.05, "lon": 2.05, "darkness": 0.8, "bortle": 4},  # ~5.6 km
    ]
    out = redundancy_filter(cands)
    assert len(out) == 2


def test_filter_sorting():
    """Output is sorted by darkness (darkest first)."""
    from src.extract import redundancy_filter
    cands = [
        {"lat": 44.0, "lon": 2.0, "darkness": 0.3, "bortle": 2},
        {"lat": 45.0, "lon": 3.0, "darkness": 0.9, "bortle": 2},
        {"lat": 46.0, "lon": 4.0, "darkness": 0.5, "bortle": 2},
    ]
    out = redundancy_filter(cands)
    darknesses = [c["darkness"] for c in out]
    assert darknesses == sorted(darknesses, reverse=True)


def test_filter_empty():
    """Empty input -> empty output."""
    from src.extract import redundancy_filter
    assert redundancy_filter([]) == []


def test_filter_reorders():
    """Output is sorted by darkness (input order does not match)."""
    from src.extract import redundancy_filter
    cands = [
        {"lat": 44.0, "lon": 2.0, "darkness": 0.1, "bortle": 2},
        {"lat": 50.0, "lon": 5.0, "darkness": 0.9, "bortle": 2},  # far away
    ]
    out = redundancy_filter(cands)
    # The far spot is darker and first in output
    assert out[0]["darkness"] == 0.9
