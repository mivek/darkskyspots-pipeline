"""Tests for src/extract.py (mesh_darkest + redundancy_filter)."""
import numpy as np
import pytest
from rasterio.transform import from_bounds


def test_mesh_darkest_simple():
    """A cell with two known values retains the darkest (highest) value."""
    from src.extract import mesh_darkest
    # 9x9, exactly one ~0.45-degree cell at ~0.05 degrees per pixel.
    darkness = np.full((9, 9), 0.5, dtype=np.float64)
    darkness[5, 5] = 0.9  # global maximum
    transform = from_bounds(-5, 41, -4.55, 41.45, 9, 9)
    points = mesh_darkest(darkness, transform, mesh_km=50)
    # Mesh of 50 km / 0.05 deg per pixel = 9 px per cell, so one cell covers the grid.
    # The maximum is found at (5, 5).
    assert len(points) == 1
    assert (points[0]["row"], points[0]["col"]) == (5, 5)
    assert points[0]["darkness"] == pytest.approx(0.9)


def test_mesh_darkest_skips_nan():
    """All-NaN cell produces no point."""
    from src.extract import mesh_darkest
    darkness = np.full((10, 10), np.nan, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    points = mesh_darkest(darkness, transform, mesh_km=50)
    assert points == []


def test_mesh_darkest_uniform_cell_chooses_first_finite_row_major():
    """Equal maxima choose the first finite pixel in row-major order (D6)."""
    from src.extract import mesh_darkest
    darkness = np.full((3, 3), 0.8, dtype=np.float64)
    darkness[0, 0] = np.nan
    darkness[1, 1] = np.nan
    transform = from_bounds(-5, 41, -4.85, 41.15, 3, 3)
    points = mesh_darkest(darkness, transform, mesh_km=50)
    assert len(points) == 1
    assert (points[0]["row"], points[0]["col"]) == (0, 1)
    assert points[0]["darkness"] == pytest.approx(0.8)


def test_mesh_darkest_deterministic():
    """Same input twice returns same points (D6 tie-breaker)."""
    from src.extract import mesh_darkest
    darkness = np.random.default_rng(42).uniform(0.0, 1.0, (20, 20))
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    a = mesh_darkest(darkness, transform, mesh_km=50)
    b = mesh_darkest(darkness, transform, mesh_km=50)
    assert a == b


def test_mesh_darkest_cell_size():
    """A 100x100 array with mesh_km=50 (~0.45 deg, ~3-4 px) yields 500-1000 points."""
    from src.extract import mesh_darkest
    darkness = np.random.default_rng(0).uniform(0.0, 1.0, (100, 100))
    transform = from_bounds(-5, 41, 10, 51, 100, 100)
    points = mesh_darkest(darkness, transform, mesh_km=50)
    # ~15 deg lon / 0.45 deg/cell = 33 cols; 10 deg lat / 0.45 deg = 22 rows; ~726 cells
    assert 500 < len(points) < 1000


def test_mesh_darkest_transform():
    """Lat/lon output is reasonable for the transform."""
    from src.extract import mesh_darkest
    darkness = np.full((10, 10), 0.5, dtype=np.float64)
    darkness[5, 5] = 0.9
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    points = mesh_darkest(darkness, transform, mesh_km=50)
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
