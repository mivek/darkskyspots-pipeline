"""Tests for run.py (orchestrator)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _make_args(tmp_path, **overrides):
    """Build a minimal argparse.Namespace for run()."""
    from src.cli import parse_args
    cmd = [
        "--year", str(overrides.get("year", 2025)),
        "--region", overrides.get("region", "france"),
        "--data-repo-url", "git@example:user/data.git",
        "--input-dir", str(tmp_path / "input"),
        "--output-dir", str(tmp_path / "output"),
    ]
    no_push = overrides.get("no_push", True)  # default: skip git ops
    if no_push:
        cmd.append("--no-push")
    return parse_args(cmd)


@patch("run.load_places", return_value=[])
def test_run_returns_0_on_success(mock_load_places, tmp_path, mock_region):
    """End-to-end happy path: synthetic input, mocked OSM, returns 0."""
    from run import run
    # Create the input file
    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    import rasterio
    from rasterio.transform import from_bounds
    data = np.full((20, 20), 1.0, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    profile = {
        "driver": "GTiff", "height": 20, "width": 20, "count": 1,
        "dtype": "float64", "crs": "EPSG:4326", "transform": transform,
    }
    input_path = input_dir / "2025.tif"
    with rasterio.open(input_path, "w", **profile) as dst:
        dst.write(data, 1)

    # Mock OSM endpoints so we don't hit the network
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()
    with patch("src.enrich.requests.get", return_value=mock_response):
        args = _make_args(tmp_path)
        rc = run(args)
    assert rc == 0


def test_run_returns_1_on_input_not_found(tmp_path):
    """Nonexistent input -> return 1."""
    from run import run
    args = _make_args(tmp_path)
    # No input file exists at {input_dir}/france/2025.tif
    rc = run(args)
    assert rc == 1


def test_run_returns_1_on_error(tmp_path):
    """If a step raises, return 1."""
    from run import run
    args = _make_args(tmp_path)
    # Create the input dir+file so the input-not-found check passes
    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    (input_dir / "2025.tif").write_bytes(b"")  # empty file will cause rasterio to fail
    rc = run(args)
    assert rc == 1


def test_run_calls_steps_in_order(tmp_path, mock_region):
    """Verify the orchestrator calls all pipeline steps in the correct sequence."""
    from run import run
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    # Create the synthetic input file
    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    data = np.full((20, 20), 1.0, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    profile = {
        "driver": "GTiff", "height": 20, "width": 20, "count": 1,
        "dtype": "float64", "crs": "EPSG:4326", "transform": transform,
    }
    input_path = input_dir / "2025.tif"
    with rasterio.open(input_path, "w", **profile) as dst:
        dst.write(data, 1)

    call_order = []
    recorded = set()

    def tracker(name, return_value):
        """Return a side_effect that records the first call of *name*
        and then returns *return_value*."""
        def side_effect(*args, **kwargs):
            if name not in recorded:
                recorded.add(name)
                call_order.append(name)
            return return_value
        return side_effect

    args = _make_args(tmp_path, no_push=False)

    # Mock OSM endpoints (defensive — the mocked step functions below
    # replace the real implementations, but some real functions from
    # coverage/enrich might still resolve their imports internally).
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()

    # Return value for slice_and_compute
    mock_slice_result = MagicMock()
    mock_slice_result.data = np.full((20, 20), 1.0, dtype=np.float64)
    mock_slice_result.transform = transform
    mock_slice_result.crs = "EPSG:2154"

    with \
        patch("run.slice_and_compute", side_effect=tracker("slice_and_compute", mock_slice_result)), \
        patch("run.alr_to_darkness", side_effect=tracker("alr_to_darkness", np.full((20, 20), 0.5))), \
        patch("run.alr_to_bortle", side_effect=tracker("alr_to_bortle", np.full((20, 20), 3, dtype=int))), \
        patch("run.mesh_minima", side_effect=tracker("mesh_minima", [])), \
        patch("run.redundancy_filter", side_effect=tracker("redundancy_filter", [])), \
        patch("run.load_places", side_effect=tracker("load_places", [])), \
        patch("run.ensure_coverage", side_effect=tracker("ensure_coverage", [])), \
        patch("run.attach_near_town", side_effect=tracker("attach_near_town", [])), \
        patch("run.enrich_all", side_effect=tracker("enrich_all", [])), \
        patch("run.classify_spots_into_tiles", side_effect=tracker("classify_spots_into_tiles", {})), \
        patch("run.compute_new_version", side_effect=tracker("compute_new_version", ("2025.1", True))), \
        patch("run.write_tile_file", side_effect=tracker("write_tile_file", "/tmp/dummy.json")), \
        patch("run.clone_data_repo"), \
        patch("run.copy_spots_to_repo", side_effect=tracker("copy_spots_to_repo", None)), \
        patch("run.commit_and_push", side_effect=tracker("commit_and_push", None)), \
        patch("src.enrich.requests.get", return_value=mock_response):
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    assert call_order == [
        "slice_and_compute",
        "alr_to_darkness",
        "alr_to_bortle",
        "mesh_minima",
        "redundancy_filter",
        "load_places",
        "ensure_coverage",
        "attach_near_town",
        "enrich_all",
        "classify_spots_into_tiles",
        "compute_new_version",
        "write_tile_file",
        "copy_spots_to_repo",
        "commit_and_push",
    ], f"Unexpected call order: {call_order}"


@patch("run.load_places", return_value=[])
def test_run_skips_step_7_when_no_push(mock_load_places, tmp_path, mock_region):
    """With --no-push, git-related functions (clone, copy, commit) must NOT be called."""
    from run import run
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    # Create synthetic input
    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    data = np.full((20, 20), 1.0, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    profile = {
        "driver": "GTiff", "height": 20, "width": 20, "count": 1,
        "dtype": "float64", "crs": "EPSG:4326", "transform": transform,
    }
    input_path = input_dir / "2025.tif"
    with rasterio.open(input_path, "w", **profile) as dst:
        dst.write(data, 1)

    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()

    args = _make_args(tmp_path)  # default: --no-push is set

    with \
        patch("run.clone_data_repo") as mock_clone, \
        patch("run.copy_spots_to_repo") as mock_copy, \
        patch("run.commit_and_push") as mock_commit, \
        patch("src.enrich.requests.get", return_value=mock_response):
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    mock_clone.assert_not_called()
    mock_copy.assert_not_called()
    mock_commit.assert_not_called()

    # Verify tile files were written locally
    spots_dir = tmp_path / "output" / "spots"
    tile_files = list(spots_dir.glob("*.json"))
    assert len(tile_files) > 0, "Expected tile files to be written even with --no-push"


@patch("run.load_places", return_value=[])
def test_orchestrator_attaches_bortle_before_redundancy_filter(mock_load_places, tmp_path, mock_region):
    """Regression test for the Step 2b bug: candidates must have bortle set
    before redundancy_filter is called. We patch mesh_minima to return
    candidates WITHOUT a bortle field, run through the orchestrator
    but intercept before redundancy_filter. Then assert every candidate
    has a non-None bortle."""
    from run import run
    import rasterio
    from rasterio.transform import from_bounds

    # Create the input file
    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    data = np.full((20, 20), 1.0, dtype=np.float64)
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    profile = {
        "driver": "GTiff", "height": 20, "width": 20, "count": 1,
        "dtype": "float64", "crs": "EPSG:4326", "transform": transform,
    }
    input_path = input_dir / "2025.tif"
    with rasterio.open(input_path, "w", **profile) as dst:
        dst.write(data, 1)

    captured = {}

    def mock_mesh_minima(*args, **kwargs):
        # Return 3 candidates with NO bortle field
        captured["candidates"] = [
            {"lat": 42.0, "lon": 1.0, "darkness": 0.9, "row": 5, "col": 5},
            {"lat": 43.0, "lon": 2.0, "darkness": 0.8, "row": 8, "col": 8},
            {"lat": 44.0, "lon": 3.0, "darkness": 0.7, "row": 10, "col": 10},
        ]
        return captured["candidates"]

    def mock_filter(candidates, *args, **kwargs):
        # Capture the candidates as seen by the filter; assert bortle is set
        captured["filtered_input"] = [dict(c) for c in candidates]
        # Don't actually filter, just return them
        return candidates

    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_response.raise_for_status = MagicMock()

    # NOTE: patch run.mesh_minima / run.redundancy_filter, not src.extract.*,
    # because run.py does ``from src.extract import mesh_minima`` at module
    # level, binding a local reference. Patching ``src.extract.mesh_minima``
    # would not affect the already-imported reference in run().
    with patch("run.mesh_minima", side_effect=mock_mesh_minima), \
         patch("run.redundancy_filter", side_effect=mock_filter), \
        patch("src.enrich.requests.get", return_value=mock_response):
        args = _make_args(tmp_path)
        run(args)

    # Every candidate seen by the filter must have a non-None bortle
    for cand in captured["filtered_input"]:
        assert cand.get("bortle") is not None, f"Candidate missing bortle: {cand}"
        assert isinstance(cand["bortle"], int)
