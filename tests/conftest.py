"""Shared pytest fixtures for the darkskyspots pipeline test suite."""
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds


@pytest.fixture
def tmp_geotiff(tmp_path: Path) -> Path:
    """Create a small synthetic GeoTIFF (10x10, EPSG:4326, gradient values)."""
    path = tmp_path / "test.tif"
    data = np.arange(100, dtype=np.float64).reshape(10, 10)
    transform = from_bounds(-5, 41, 10, 51, 10, 10)
    profile = {
        "driver": "GTiff",
        "height": 10,
        "width": 10,
        "count": 1,
        "dtype": "float64",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "lzw",
        "tiled": False,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def sample_alr_data() -> np.ndarray:
    """Return a 30x30 float64 ALR array with a NaN halo for testing."""
    data = np.random.default_rng(42).uniform(0.05, 15.0, (30, 30)).astype(np.float64)
    data[:5, :] = np.nan
    data[-5:, :] = np.nan
    data[:, :5] = np.nan
    data[:, -5:] = np.nan
    return data


@pytest.fixture
def wide_geotiff(tmp_path: Path) -> Path:
    """Create a 2000x2000 EPSG:3035 GeoTIFF (~464m/px) for overlap slicing tests.

    The image must be > 2 * ALR_R_px (~1332 px) on each side to have valid
    interior pixels after the nightskyquality NaN halo is applied.
    """
    path = tmp_path / "wide_test.tif"
    data = np.arange(2000 * 2000, dtype=np.float64).reshape(2000, 2000)
    transform = rasterio.Affine(
        464.0, 0.0, 3_500_000.0, 0.0, -464.0, 3_000_000.0
    )
    profile = {
        "driver": "GTiff",
        "height": 2000,
        "width": 2000,
        "count": 1,
        "dtype": "float64",
        "crs": "EPSG:3035",
        "transform": transform,
        "compress": "lzw",
        "tiled": False,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def mock_region() -> dict:
    return {
        "bbox": [-5, 41, 10, 51],
        "equal_area_epsg": 3035,
        "admin_level": 8,
        "osm_country_code": "FR",
        "name": "France",
    }
