"""Tests for run.py (orchestrator)."""
from contextlib import ExitStack
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
    if overrides.get("no_clusters", False):
        cmd.append("--no-clusters")
    if overrides.get("migrate_country_tags", False):
        cmd.append("--migrate-country-tags")
    if overrides.get("prune_orphan_spots", False):
        cmd.append("--prune-orphan-spots")
    return parse_args(cmd)


def _write_input(tmp_path, region="france"):
    """Create a tiny input file; raster work is mocked by orchestration tests."""
    input_dir = tmp_path / "input" / region
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "2025.tif").write_bytes(b"input")


def _mock_raster_steps(transform):
    """Return patches that retain orchestration while avoiding raster/OSM work."""
    slice_result = MagicMock(
        data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:2154"
    )
    return (
        patch("run.slice_and_compute", return_value=slice_result),
        patch("run.alr_to_darkness", return_value=np.full((2, 2), 0.5)),
        patch("run.alr_to_bortle", return_value=np.full((2, 2), 3, dtype=int)),
        patch("run.mesh_minima", return_value=[]),
        patch("run.redundancy_filter", return_value=[]),
        patch("run.load_places", return_value=[]),
        patch("run.ensure_coverage", return_value=[]),
        patch("run.attach_near_town", return_value=[]),
        patch("run.enrich_all", return_value=[]),
        patch("run.filter_sea_spots", return_value=[]),
    )


def _write_envelope(directory, tile_id, spots):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tile_id}.json").write_text(
        json.dumps(
            {
                "version": "2025.1",
                "source": "test",
                "generated": "2026-08-03T00:00:00Z",
                "tile": tile_id,
                "spots": spots,
            }
        ),
        encoding="utf-8",
    )


def _spot(spot_id, lat, lon):
    return {
        "id": spot_id,
        "lat": lat,
        "lon": lon,
        "darkness": 0.8,
        "bortle": 3,
        "near": "Test",
        "altitude": None,
    }


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
        patch("run.filter_sea_spots", side_effect=tracker("filter_sea_spots", [])), \
        patch("run.classify_spots_into_tiles", side_effect=tracker("classify_spots_into_tiles", {})), \
        patch("run.compute_new_version", side_effect=tracker("compute_new_version", ("2025.1", True))), \
        patch("run.write_tile_file", side_effect=tracker("write_tile_file", "/tmp/dummy.json")), \
        patch("run.clone_data_repo"), \
        patch("run.copy_spots_to_repo", side_effect=tracker("copy_spots_to_repo", None)), \
        patch("run.commit_and_push", side_effect=tracker("commit_and_push", None)):
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
        "filter_sea_spots",
        "classify_spots_into_tiles",
        "compute_new_version",
        "write_tile_file",
        "copy_spots_to_repo",
        "commit_and_push",
    ], f"Unexpected call order: {call_order}"


def test_run_merges_current_country_and_preserves_other_countries(tmp_path):
    """Version comparison keeps another country's block in the same repo."""
    from run import run
    import rasterio
    from rasterio.transform import from_bounds

    input_dir = tmp_path / "input" / "france"
    input_dir.mkdir(parents=True)
    transform = from_bounds(-5, 41, 10, 51, 20, 20)
    profile = {
        "driver": "GTiff", "height": 20, "width": 20, "count": 1,
        "dtype": "float64", "crs": "EPSG:4326", "transform": transform,
    }
    with rasterio.open(input_dir / "2025.tif", "w", **profile) as dst:
        dst.write(np.full((20, 20), 1.0, dtype=np.float64), 1)

    regions = {
        "france": {"bbox": [-6, 41, 8, 51], "osm_country_code": ["FR"]},
        "neighbour": {"bbox": [0, 51, 1, 52], "osm_country_code": ["GB"]},
    }
    old_owned = {"version": "2025.1", "tile": "N050E001", "spots": [{"id": "old", "country": "FR"}]}
    old_non_owned = {"version": "2025.1", "tile": "N051E000", "spots": [{"id": "keep", "country": "GB"}]}

    def clone_with_existing_tiles(_url, _branch, target_dir):
        spots_dir = Path(target_dir) / "spots"
        spots_dir.mkdir()
        (spots_dir / "N050E001.json").write_text(json.dumps(old_owned), encoding="utf-8")
        (spots_dir / "N051E000.json").write_text(json.dumps(old_non_owned), encoding="utf-8")

    args = _make_args(tmp_path, no_push=False)
    slice_result = MagicMock(
        data=np.full((20, 20), 1.0), transform=transform, crs="EPSG:2154"
    )
    with \
        patch("run.load_regions", return_value=regions), \
        patch("run.audit_country_spots", return_value={
            "missing": [], "invalid": [], "unconfigured": [], "mismatched": [],
            "ambiguous": [], "valid": 2,
        }), \
        patch("run.slice_and_compute", return_value=slice_result), \
        patch("run.alr_to_darkness", return_value=np.full((20, 20), 0.5)), \
        patch("run.alr_to_bortle", return_value=np.full((20, 20), 3, dtype=int)), \
        patch("run.mesh_minima", return_value=[]), \
        patch("run.redundancy_filter", return_value=[]), \
        patch("run.load_places", return_value=[]), \
        patch("run.ensure_coverage", return_value=[]), \
        patch("run.attach_near_town", return_value=[]), \
        patch("run.enrich_all", return_value=[]), \
        patch("run.filter_sea_spots", return_value=[]), \
        patch("run.classify_spots_into_tiles", return_value={"N050E001": [{"id": "new", "country": "FR"}]}), \
        patch("run.enumerate_tiles_in_bbox", return_value=["N050E001", "N050E002"]), \
        patch("run.clone_data_repo", side_effect=clone_with_existing_tiles), \
        patch("run.compute_new_version", return_value=("2025.2", True)) as mock_version, \
        patch("run.write_tile_file"), \
        patch("run.write_cluster_files"), \
        patch("run.copy_spots_to_repo") as mock_copy, \
        patch("run.commit_and_push"):
        assert run(args) == 0

    old_arg, new_arg, year_arg = mock_version.call_args.args
    assert old_arg == {"N050E001": old_owned, "N051E000": old_non_owned}
    assert set(new_arg) == {"N050E001", "N050E002", "N051E000"}
    assert new_arg["N050E001"]["spots"] == [{"id": "new", "country": "FR"}]
    assert new_arg["N050E002"]["spots"] == []
    assert new_arg["N051E000"] == old_non_owned
    assert year_arg == 2025
    assert mock_copy.call_args.kwargs["country_codes"] == ["FR"]


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

    args = _make_args(tmp_path)  # default: --no-push is set

    with \
        patch("run.clone_data_repo") as mock_clone, \
        patch("run.copy_spots_to_repo") as mock_copy, \
        patch("run.commit_and_push") as mock_commit:
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    mock_clone.assert_not_called()
    mock_copy.assert_not_called()
    mock_commit.assert_not_called()

    # Verify tile files were written locally
    spots_dir = tmp_path / "output" / "spots"
    tile_files = list(spots_dir.glob("*.json"))
    assert len(tile_files) > 0, "Expected tile files to be written even with --no-push"


def test_run_filters_unassigned_spots_before_tile_classification(tmp_path, mock_region):
    """Verify that filter_sea_spots removes empty-near spots before they reach
    classify_spots_into_tiles."""
    from run import run
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

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

    sea_spot = {"lat": 43.5, "lon": -1.8, "near": "", "bortle": 4, "darkness": 0.5, "id": "S1", "altitude": None}
    inland_spot = {"lat": 43.4, "lon": -1.5, "near": "Bayonne", "bortle": 3, "darkness": 0.8, "id": "S2", "altitude": None}

    captured_input = None

    def capture_classify_input(spots, *args, **kwargs):
        nonlocal captured_input
        captured_input = list(spots)
        return {}

    args = _make_args(tmp_path)
    with \
        patch("run.enrich_all", return_value=[sea_spot, inland_spot]), \
        patch("run.classify_spots_into_tiles", side_effect=capture_classify_input), \
        patch("run.compute_new_version", return_value=("2025.1", True)), \
        patch("run.write_tile_file"), \
        patch("run.clone_data_repo"), \
        patch("run.copy_spots_to_repo"), \
        patch("run.commit_and_push"):
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    assert captured_input is not None, "classify_spots_into_tiles was never called"
    # Only the Bayonne spot should reach tile classification
    assert len(captured_input) == 1, f"Expected 1 spot, got {len(captured_input)}: {captured_input}"
    assert captured_input[0]["near"] == "Bayonne"


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

    # NOTE: patch run.mesh_minima / run.redundancy_filter, not src.extract.*,
    # because run.py does ``from src.extract import mesh_minima`` at module
    # level, binding a local reference. Patching ``src.extract.mesh_minima``
    # would not affect the already-imported reference in run().
    with patch("run.mesh_minima", side_effect=mock_mesh_minima), \
         patch("run.redundancy_filter", side_effect=mock_filter):
        args = _make_args(tmp_path)
        run(args)

    # Every candidate seen by the filter must have a non-None bortle
    for cand in captured["filtered_input"]:
        assert cand.get("bortle") is not None, f"Candidate missing bortle: {cand}"
        assert isinstance(cand["bortle"], int)


def test_publishing_audits_the_clone_before_raster_work(tmp_path):
    """A country-tag audit aborts before any raster collaborator is used."""
    from rasterio.transform import from_bounds
    from run import run

    _write_input(tmp_path)
    events = []
    transform = from_bounds(-5, 41, 10, 51, 2, 2)
    slice_result = MagicMock(data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:2154")

    def clone(_url, _branch, target_dir):
        events.append("clone")
        (Path(target_dir) / "spots").mkdir()

    def audit(_spots_dir, _regions):
        events.append("audit")
        return {
            "missing": [{"tile": "N051E000", "index": 0}],
            "invalid": [], "unconfigured": [], "mismatched": [],
            "ambiguous": [], "valid": 0,
        }

    def raster(*_args, **_kwargs):
        events.append("raster")
        return slice_result

    args = _make_args(tmp_path, no_push=False)
    with ExitStack() as stack:
        stack.enter_context(patch("run.clone_data_repo", side_effect=clone))
        stack.enter_context(patch("run.audit_country_spots", side_effect=audit))
        stack.enter_context(patch("run.slice_and_compute", side_effect=raster))
        assert run(args) == 1

    assert events == ["clone", "audit"]


def test_country_pruning_requires_explicit_migration_flag(tmp_path):
    """Deletion is a separate explicit authorization from the read-only audit."""
    from src.cli import parse_args

    args = parse_args([
        "--migrate-country-tags", "--prune-orphan-spots", "--no-push",
        "--output-dir", str(tmp_path / "output"),
    ])
    assert args.migrate_country_tags is True
    assert args.prune_orphan_spots is True


def test_run_uses_bbox_only_to_enumerate_working_tiles(tmp_path):
    """Overlapping working envelopes do not invoke tile ownership arbitration."""
    from run import run
    from rasterio.transform import from_bounds

    _write_input(tmp_path)
    args = _make_args(tmp_path, no_clusters=True)
    transform = from_bounds(-5, 41, 10, 51, 2, 2)
    with ExitStack() as stack:
        for mocked_step in _mock_raster_steps(transform):
            stack.enter_context(mocked_step)
        enumerate_tiles = stack.enter_context(
            patch("run.enumerate_tiles_in_bbox", return_value=["N048E002"])
        )
        stack.enter_context(patch("run.classify_spots_into_tiles", return_value={}))
        stack.enter_context(patch("run.write_tile_file"))
        assert run(args) == 0
    enumerate_tiles.assert_called_once()


def test_no_push_clusters_include_all_staged_country_tiles(tmp_path):
    """Local cluster artifacts consume the complete staged spot repository."""
    from rasterio.transform import from_bounds
    from run import run

    _write_input(tmp_path)
    spots_dir = tmp_path / "output" / "spots"
    _write_envelope(spots_dir, "N048E002", [_spot("inside", 48.2, 2.2)])
    _write_envelope(spots_dir, "N051E000", [_spot("outside", 51.2, 0.2)])
    transform = from_bounds(-5, 41, 10, 51, 2, 2)

    args = _make_args(tmp_path)
    with ExitStack() as stack:
        for mocked_step in _mock_raster_steps(transform):
            stack.enter_context(mocked_step)
        stack.enter_context(patch("run.classify_spots_into_tiles", return_value={}))
        stack.enter_context(patch("run.write_tile_file"))
        assert run(args) == 0

    clusters = json.loads((tmp_path / "output" / "clusters-local" / "L1.json").read_text())
    representative_ids = {cluster["rep"]["id"] for cluster in clusters}
    assert "inside" in representative_ids
    assert "outside" in representative_ids


def test_published_regeneration_audits_country_tags_before_commit(tmp_path):
    """Cluster regeneration audits the complete repository before writing."""
    from src.cli import parse_args
    from run import run_regenerate_clusters

    args = parse_args(["--regenerate-clusters", "--year", "2025", "--data-repo-url", "git@example:data.git"])
    committed = {}

    def clone(_url, _branch, target_dir):
        _write_envelope(Path(target_dir) / "spots", "N051E000", [])

    def commit(data_repo_dir, _message):
        assert (Path(data_repo_dir) / "spots" / "N051E000.json").exists()
        committed["called"] = True

    with patch("run.clone_data_repo", side_effect=clone), \
         patch("run.audit_country_spots", return_value={
             "missing": [], "invalid": [], "unconfigured": [], "mismatched": [],
             "ambiguous": [], "valid": 0,
         }) as audit, \
         patch("run.write_cluster_files"), \
         patch("run.commit_and_push", side_effect=commit) as mock_commit:
        assert run_regenerate_clusters(args) == 0
    audit.assert_called_once()
    mock_commit.assert_called_once()
    assert committed == {"called": True}


def test_no_push_writer_failure_returns_error_status(tmp_path):
    """Local cluster-write errors stay within the integer-status contract."""
    from run import run

    args = _make_args(tmp_path)
    with patch("run._legacy_run", return_value=0), \
         patch("run.write_cluster_files", side_effect=OSError("disk full")):
        assert run(args) == 1


def test_local_regeneration_requires_existing_staged_spots(tmp_path, caplog):
    """Offline regeneration rejects a missing staging source instead of an empty repo."""
    from src.cli import parse_args
    from run import run_regenerate_clusters

    args = parse_args(["--regenerate-clusters", "--year", "2025", "--no-push", "--output-dir", str(tmp_path / "missing-output")])
    with patch("run.write_cluster_files") as writer:
        assert run_regenerate_clusters(args) == 1
    writer.assert_not_called()
    assert "output/spots" in caplog.text
    assert "does not exist" in caplog.text
