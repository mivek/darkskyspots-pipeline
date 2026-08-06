"""Publication orchestration around the complete cloned spot repository."""
import json
import re
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from rasterio.transform import from_bounds


def _spot(spot_id, lat, lon):
    return {"id": spot_id, "lat": lat, "lon": lon, "darkness": 0.8, "bortle": 3, "near": "Test", "altitude": None}


def _write_envelope(directory, tile_id, spots):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tile_id}.json").write_text(json.dumps({"version": "2025.1", "source": "test", "generated": "2026-08-03T00:00:00Z", "tile": tile_id, "spots": spots}))


def test_published_clusters_read_spots_after_current_region_copy(tmp_path):
    """The cluster input contains a tile written by the current raster run."""
    from src.cli import parse_args
    from run import run

    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    (input_dir / "2025.tif").write_bytes(b"input")
    args = parse_args(["--year", "2025", "--region", "france", "--data-repo-url", "git@example:data.git", "--input-dir", str(tmp_path / "input"), "--output-dir", str(tmp_path / "output")])
    transform = from_bounds(-5, 41, 10, 51, 2, 2)
    slice_result = MagicMock(data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:2154")
    generated = _spot("new", 48.3, 3.3)
    seen_ids = []
    cluster_calls = []

    def clone(_url, _branch, target_dir):
        _write_envelope(Path(target_dir) / "spots", "N048E002", [_spot("old", 48.2, 2.2)])

    def clusters(spots_dir, clusters_dir, *, data_year, generated, allowed_tile_ids=None):
        cluster_calls.append((spots_dir, clusters_dir, data_year, generated, allowed_tile_ids))
        assert Path(clusters_dir) == Path(spots_dir).parent / "clusters"
        assert data_year == 2025
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", generated)
        assert allowed_tile_ids is None
        for path in Path(spots_dir).glob("*.json"):
            seen_ids.extend(spot["id"] for spot in json.loads(path.read_text())["spots"])
        assert (Path(spots_dir) / "N048E003.json").exists()
        return {}

    with ExitStack() as stack:
        stack.enter_context(patch("run.clone_data_repo", side_effect=clone))
        stack.enter_context(patch("run.scan_orphan_tiles", return_value={}))
        stack.enter_context(patch("run.slice_and_compute", return_value=slice_result))
        stack.enter_context(patch("run.alr_to_darkness", return_value=np.full((2, 2), 0.5)))
        stack.enter_context(patch("run.alr_to_bortle", return_value=np.full((2, 2), 3, dtype=int)))
        stack.enter_context(patch("run.mesh_minima", return_value=[]))
        stack.enter_context(patch("run.redundancy_filter", return_value=[]))
        stack.enter_context(patch("run.load_places", return_value=[]))
        stack.enter_context(patch("run.ensure_coverage", return_value=[]))
        stack.enter_context(patch("run.attach_near_town", return_value=[]))
        stack.enter_context(patch("run.enrich_all", return_value=[]))
        stack.enter_context(patch("run.filter_sea_spots", return_value=[]))
        stack.enter_context(patch("run.classify_spots_into_tiles", return_value={"N048E003": [generated]}))
        stack.enter_context(patch("run.owned_tile_ids", return_value={"N048E003"}))
        stack.enter_context(patch("run.enumerate_tiles_in_bbox", return_value=["N048E003"]))
        stack.enter_context(patch("run.write_cluster_files", side_effect=clusters))
        stack.enter_context(patch("run.commit_and_push"))
        assert run(args) == 0

    assert "new" in seen_ids
    assert len(cluster_calls) == 1


def test_a_then_b_scoped_publication_preserves_a_tiles(tmp_path):
    """A later region copy cannot remove an earlier region's owned tile."""
    from src.publish import copy_spots_to_repo

    staging_a = tmp_path / "staging-a"
    staging_b = tmp_path / "staging-b"
    clone = tmp_path / "clone"
    _write_envelope(staging_a, "N048E002", [_spot("a", 48.2, 2.2)])
    _write_envelope(staging_b, "N048E003", [_spot("b", 48.2, 3.2)])

    copy_spots_to_repo(staging_a, clone, {"N048E002"})
    copy_spots_to_repo(staging_b, clone, {"N048E003"})

    a_envelope = json.loads((clone / "spots" / "N048E002.json").read_text())
    b_envelope = json.loads((clone / "spots" / "N048E003.json").read_text())
    assert a_envelope["spots"][0]["id"] == "a"
    assert b_envelope["spots"][0]["id"] == "b"
