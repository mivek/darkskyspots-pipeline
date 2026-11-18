"""Shared pytest fixtures for the darkskyspots pipeline test suite."""
import hashlib
import json
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


@pytest.fixture
def synthetic_baseline(tmp_path, monkeypatch):
    import src.accessibility_baseline as auth

    root = tmp_path / "baseline"
    spots, clusters = root / "spots", root / "clusters"
    spots.mkdir(parents=True)
    clusters.mkdir()
    envelopes = {
        "selected.json": {"version": 1, "tile": "selected", "spots": [
            {"id": "a", "country": "GB", "lat": 51.0, "lon": -1.0, "name": "Original"},
            {"id": "c", "country": "IE", "lat": 53.0, "lon": -8.0, "name": "Second"},
        ]},
        "other.json": {"version": 1, "tile": "other", "spots": [
            {"id": "b", "country": "FR", "lat": 45.0, "lon": 2.0},
        ]},
    }
    for name, value in envelopes.items():
        (spots / name).write_text(json.dumps(value), encoding="utf-8")
    for level in range(1, 7):
        (clusters / f"L{level}.json").write_text("[]", encoding="utf-8")
    (clusters / "index.json").write_text("{}", encoding="utf-8")

    entries = {}
    package = {}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        info = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        entries[relative] = dict(info)
        if relative.startswith("spots/"):
            info["expected_spot_records"] = len(envelopes[path.name]["spots"])
        elif relative != "clusters/index.json":
            info["actual_records"] = 0
        package[relative] = info

    source = {"repository": auth.APPROVED_BASELINE_REPOSITORY, "commit": auth.APPROVED_BASELINE_COMMIT}
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({**source, "files": entries}), encoding="utf-8")
    pin = hashlib.sha256(inventory.read_bytes()).hexdigest()
    monkeypatch.setattr(auth, "APPROVED_G2_INVENTORY_SHA256", pin)
    monkeypatch.setattr(auth, "APPROVED_G2_SPOT_COUNT", 2)
    (root / "baseline.json").write_text(json.dumps({
        **source, "inventory_sha256": pin, "g2_spot_records": 2, "files": package,
    }), encoding="utf-8")
    return root, inventory, pin, envelopes
