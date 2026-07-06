"""Full pipeline smoke test with synthetic data (Phase 12, Task 12.1)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.alr import compute_alr
from src.config import MESH_KM
from src.convert import alr_to_bortle, alr_to_darkness
from src.coverage import ensure_coverage
from src.enrich import enrich_all, spot_id
from src.extract import mesh_minima, redundancy_filter
from src.tile_export import (
    classify_spots_into_tiles,
    enumerate_tiles_in_bbox,
    tile_envelope,
    write_empty_tiles,
    write_tile_file,
)
from src.publish import bump_version


@pytest.fixture
def large_enough_geotiff(tmp_path: Path) -> Path:
    """
    Create a 700x700 GeoTIFF with a non-trivial gradient.

    700 > 2x R_px(666), so the fork will produce a central region of non-NaN
    values. (All-NaN at the edges is fine.)
    """
    import rasterio
    from rasterio.transform import from_bounds

    path = tmp_path / "large_test.tif"
    x = np.linspace(0, 1, 700)
    y = np.linspace(0, 1, 700)
    xx, yy = np.meshgrid(x, y)
    data = (0.5 + 30.0 * (xx + yy) / 2).astype(np.float64)
    transform = from_bounds(-5, 41, 10, 51, 700, 700)
    profile = {
        "driver": "GTiff",
        "height": 700,
        "width": 700,
        "count": 1,
        "dtype": "float64",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "lzw",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture
def test_communes() -> list[dict]:
    """Return a minimal set of test communes for coverage/enrichment."""
    return [
        {"name": "TestVille", "lat": 48.5, "lon": 2.0, "population": 1000},
    ]


@patch("src.enrich.requests.get")
def test_smoke_end_to_end(
    mock_enrich_get, large_enough_geotiff, tmp_path, mock_region, test_communes
):
    """Run the full pipeline with synthetic data and verify output JSON(s)."""
    output_dir = str(tmp_path / "output")

    # Mock OSM enrichment: return empty results
    mock_enrich_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"elements": []},
    )

    # Step 0
    alr_result = compute_alr(
        str(large_enough_geotiff), mock_region["equal_area_epsg"]
    )
    assert alr_result.data.shape == (700, 700)
    assert alr_result.data.dtype == np.float64

    # Step 1
    darkness = alr_to_darkness(alr_result.data)
    bortle = alr_to_bortle(alr_result.data)
    assert not np.all(np.isnan(darkness)), "Should have non-NaN pixels"

    # Step 2 (mesh scan only, skip NaN halo)
    points = mesh_minima(
        darkness,
        alr_result.profile["transform"],
        MESH_KM,
    )
    assert len(points) > 0, "Should find at least one dark point"

    # Step 3 (redundancy filter)
    for p in points:
        row, col = p["row"], p["col"]
        p["bortle"] = int(bortle[row, col])
    filtered = redundancy_filter(points)
    assert len(filtered) <= len(points)

    # Step 4 (coverage — use test communes)
    covered = ensure_coverage(filtered, points, communes=test_communes)

    # Step 5 (enrichment — mocked, so name=None)
    enriched = enrich_all(covered, mock_region)

    # Add spot IDs
    for s in enriched:
        s["id"] = spot_id(
            s["lat"],
            s["lon"],
            mock_region["osm_country_code"].lower(),
            s.get("name"),
        )

    # Step 6 (tile export + empty tiles)
    tiles = classify_spots_into_tiles(enriched)
    assert len(tiles) >= 1, "Should produce at least one tile"

    version = "2025.1"
    source = "VIIRS 2025 (NASA, CC0)"
    generated = "2026-02-15"

    for tid, spots in tiles.items():
        filepath = write_tile_file(
            tid, spots, output_dir, version, source, generated
        )
        assert Path(filepath).exists()

        with open(filepath, encoding="utf-8") as f:
            env = json.load(f)
        assert env["version"] == version
        assert env["tile"] == tid
        assert "spots" in env
        for spot in env["spots"]:
            for key in ("id", "lat", "lon", "darkness"):
                assert key in spot, f"Missing required spot field: {key}"

    # Write empty tiles for all tiles in the bbox not already present
    all_tiles = list(enumerate_tiles_in_bbox(tuple(mock_region["bbox"])))
    write_empty_tiles(all_tiles, output_dir, version, source, generated)

    # Assert every tile in the bbox has a file (Decision 4: empty tiles)
    for tid in all_tiles:
        tile_path = Path(output_dir) / "spots" / f"{tid}.json"
        assert tile_path.exists(), (
            f"Missing tile file for {tid} (empty tile required by Decision 4)"
        )
        with open(tile_path, encoding="utf-8") as f:
            env = json.load(f)
        assert isinstance(env["spots"], list), (
            f"spots must be a list for {tid}"
        )

    # Step 7 not tested here (git operations mocked separately in test_publish.py)
