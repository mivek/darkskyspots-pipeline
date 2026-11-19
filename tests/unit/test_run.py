"""Tests for run.py (orchestrator)."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from src.tile_export import write_tile_file as real_write_tile_file
from src.clusters import write_cluster_files as real_write_cluster_files


def test_f01_run_audit_records_verified_e3_manifest_digest():
    import run

    spots = [{"id": "46.6729_5.5562", "label": True}]
    document = run._f01_audit_document(
        "france", 2025, "34cc9365f131d3c6a0abaf2d7c83e3bc74d38630cd6b9787ed278893e25caaa6", spots,
    )
    assert document == {
        "schema": 1,
        "region": "france",
        "year": 2025,
        "e3_manifest_sha256": "34cc9365f131d3c6a0abaf2d7c83e3bc74d38630cd6b9787ed278893e25caaa6",
        "spots": spots,
    }


@pytest.fixture(autouse=True)
def mock_filtered_geonames_api(monkeypatch):
    """Keep orchestrator tests independent from the GeoNames data checkout.

    The production path is intentionally strict: it calls the filtered-extract
    API and has no ZIP fallback.  These tests replace that API with a small
    index double; tests exercising the guard override the validator locally.
    """
    import run

    monkeypatch.setattr(run, "measure_sky_darkness_area", lambda spot, *_args: (
        None, {"id": spot.get("id"), "label": False, "radiusM": None}
    ))

    monkeypatch.setattr(run, "validate_geonames_manifest", lambda **_kwargs: None)
    sampler = MagicMock()
    sampler.__enter__.return_value = sampler
    sampler.counts = {}
    sampler.sample.return_value = 123
    sampler.wbm_classes.side_effect = lambda lats, lons: np.zeros(len(lats), dtype=np.int16)
    sampler.wbm_io = {}
    monkeypatch.setattr(run, "ElevationSampler", lambda *args, **kwargs: sampler)
    geographic = MagicMock()
    geographic.is_e3 = True
    geographic.revision = "a" * 64
    geographic.classify.side_effect = lambda points, *_args: [
        dict(spot=dict(p), confidence='A', reasons=[], base_evidence={'wbm_class': 0}) for p in points
    ]
    geographic.qualify.side_effect = lambda points, **_kwargs: points
    monkeypatch.setattr(run, 'GeographicDataset', lambda *_args, **_kwargs: geographic)

    index = MagicMock()
    index.enrich_spots.side_effect = lambda spots: [
        dict(
            spot,
            name=spot.get("name", "Test landmark"),
            nameDistanceKm=spot.get("nameDistanceKm", 1.0),
            nameFeatureCode=spot.get("nameFeatureCode", "PPL"),
            nameFeatureClass=spot.get("nameFeatureClass", "P"),
            nameGeoNameId=spot.get("nameGeoNameId", 1),
        )
        for spot in spots
    ]

    def factory(_cls, **_kwargs):
        return index

    monkeypatch.setattr(
        run.GeoNamesIndex,
        "from_filtered_extracts",
        classmethod(factory),
        raising=False,
    )
    return index


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


def _write_valid_dispatch_input(tmp_path, region="canaries"):
    import rasterio
    from rasterio.transform import from_bounds

    input_dir = tmp_path / "input" / region
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / "2025.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=2, height=2, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_bounds(-17, 27, -16, 29, 2, 2),
    ) as dataset:
        dataset.write(np.ones((2, 2), dtype=np.float32), 1)
    return path


def _dispatch_args(tmp_path, *, mode="normal", no_push=False, region="canaries"):
    from src.cli import parse_args

    argv = [
        "--year", "2025", "--region", region,
        "--data-repo-url", "git@example:user/data.git",
        "--input-dir", str(tmp_path / "input"),
        "--output-dir", str(tmp_path / "output"),
    ]
    mode_flags = {
        "normal": [],
        "regenerate_clusters": ["--regenerate-clusters"],
        "migrate_country_tags": ["--migrate-country-tags"],
        "prune_country_tags": ["--migrate-country-tags", "--prune-orphan-spots"],
        "audit_country_tags": ["--audit-country-tags"],
        "list_orphans": ["--list-orphans"],
    }
    argv.extend(mode_flags[mode])
    if no_push:
        argv.append("--no-push")
    return parse_args(argv)


@pytest.mark.parametrize(
    ("mode", "no_push"),
    [
        ("normal", False),
        ("regenerate_clusters", False),
        ("migrate_country_tags", False),
        ("prune_country_tags", False),
        ("audit_country_tags", False),
        ("list_orphans", True),
        ("regenerate_clusters", True),
    ],
)
def test_public_dispatcher_rejects_accessibility_before_every_side_effect(
    tmp_path, monkeypatch, mode, no_push
):
    import run as pipeline

    args = _dispatch_args(tmp_path, mode=mode, no_push=no_push)
    args.accessibility_manifest = "input/accessibility/datasets/tenerife/manifest.json"
    _write_valid_dispatch_input(tmp_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True)
    sentinel = output_dir / "pre-existing.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    before = {path.relative_to(output_dir): path.read_bytes()
              for path in output_dir.rglob("*") if path.is_file()}

    side_effects = [
        "_legacy_run", "run_regenerate_clusters", "run_list_orphans",
        "run_country_migration", "load_regions", "_audit_before_write",
        "_validate_geonames_inputs", "ElevationSampler", "GeographicDataset",
        "_load_geonames_index", "enrich_all", "write_cluster_files",
        "clone_data_repo", "copy_spots_to_repo", "commit_and_push",
    ]
    mocks = {}
    for name in side_effects:
        mock = MagicMock(name=name)
        mocks[name] = mock
        monkeypatch.setattr(pipeline, name, mock)

    with pytest.raises(ValueError, match="--accessibility-manifest"):
        pipeline.run(args)
    for name, mock in mocks.items():
        mock.assert_not_called()
    after = {path.relative_to(output_dir): path.read_bytes()
             for path in output_dir.rglob("*") if path.is_file()}
    assert after == before
    assert (Path(args.input_dir) / "canaries" / "2025.tif").is_file()


@pytest.mark.parametrize("with_accessibility", [False, True])
def test_public_dispatcher_keeps_supported_local_and_legacy_runs(
    tmp_path, with_accessibility
):
    import run as pipeline

    region = "canaries" if with_accessibility else "france"
    args = _dispatch_args(tmp_path, no_push=True, region=region)
    args.no_clusters = True
    _write_valid_dispatch_input(tmp_path, region=region)
    if with_accessibility:
        args.accessibility_manifest = "input/accessibility/datasets/tenerife/manifest.json"
    with patch("run._legacy_run", return_value=0) as legacy, \
         patch("run.clone_data_repo") as clone, \
         patch("run.commit_and_push") as commit:
        assert pipeline.run(args) == 0
    legacy.assert_called_once_with(args)
    clone.assert_not_called()
    commit.assert_not_called()


@pytest.mark.parametrize(
    ("no_push", "datasets", "message"),
    [
        (False, {"great_britain": "gb.json", "ireland_northern_ireland": "ie.json"}, "--accessibility-dataset"),
        (True, {"great_britain": "gb.json"}, "both G2 scopes"),
        (True, {"unknown": "bad.json"}, "unknown scope"),
    ],
)
def test_direct_dispatch_rejects_invalid_g2_mappings_before_side_effects(
    tmp_path, monkeypatch, no_push, datasets, message,
):
    import run as pipeline

    args = _dispatch_args(tmp_path, no_push=no_push, region="uk_ireland")
    args.accessibility_datasets = datasets
    side_effects = ["_legacy_run", "ElevationSampler", "GeographicDataset", "clone_data_repo"]
    mocks = []
    for name in side_effects:
        mock = MagicMock(name=name)
        monkeypatch.setattr(pipeline, name, mock)
        mocks.append(mock)
    with pytest.raises(ValueError, match=message):
        pipeline.run(args)
    for mock in mocks:
        mock.assert_not_called()


def _write_input(tmp_path, region="france"):
    """Create a tiny input file; raster work is mocked by orchestration tests."""
    input_dir = tmp_path / "input" / region
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "2025.tif").write_bytes(b"input")


def test_normal_generation_passes_mandatory_wbm_qualification(tmp_path):
    import run as pipeline
    from rasterio.transform import from_bounds
    from src.selection import CandidateQualifier
    _write_input(tmp_path)
    args = _make_args(tmp_path, no_clusters=True)
    with ExitStack() as stack:
        collaborators = [stack.enter_context(p) for p in _mock_raster_steps(from_bounds(-5, 41, 10, 51, 2, 2))]
        assert pipeline.run(args) == 0
    qualification = collaborators[3].call_args.kwargs['qualify']
    assert isinstance(qualification, CandidateQualifier)
    assert qualification.water is True and qualification.sampler is not None
    assert collaborators[3].call_args.kwargs['crs'] == qualification.crs


@pytest.mark.parametrize("writer_failure", [False, True])
def test_local_dispatcher_does_not_export_clusters_outside_activated_package(tmp_path, writer_failure):
    """The dispatcher delegates local clusters to the atomic candidate producer once."""
    import run as pipeline
    from rasterio.transform import from_bounds
    from src.local_generation import resolve_complete
    _write_input(tmp_path)
    args = _make_args(tmp_path)
    spot = dict(_spot("dispatcher-spot", 48.2, 2.2), country="FR")
    class Sampler:
        counts = {}
        wbm_io = {}
        def __init__(self, *_args, **_kwargs): pass
        def wbm_classes(self, lats, _lons): return np.zeros(len(lats), dtype=np.int16)
        def close(self): pass
    class Geography:
        def __init__(self, *_args, **_kwargs): pass
        def require_region(self, *_args): pass
        def classify(self, *_args):
            self._e3_engine = SimpleNamespace(for_region=lambda _region: object())
        revision = "a" * 64
        def close(self): pass
    class Naming:
        def enrich_spots(self, spots): return spots
    with ExitStack() as stack:
        for step in _mock_raster_steps(from_bounds(-5, 41, 10, 51, 2, 2)):
            stack.enter_context(step)
        stack.enter_context(patch("run.ElevationSampler", Sampler))
        stack.enter_context(patch("run.GeographicDataset", Geography))
        stack.enter_context(patch("run._load_geonames_index", return_value=Naming()))
        stack.enter_context(patch("run.enumerate_tiles_in_bbox", return_value=["N048E002"]))
        stack.enter_context(patch("run.classify_spots_into_tiles", return_value={"N048E002": [spot]}))
        stack.enter_context(patch("run.write_tile_file", wraps=real_write_tile_file))
        clusters = stack.enter_context(patch("run.write_cluster_files", wraps=real_write_cluster_files))
        if writer_failure:
            clusters.side_effect = OSError("candidate cluster write failed")
        assert pipeline.run(args) == (1 if writer_failure else 0)
    if writer_failure:
        assert clusters.call_count == 1
        assert not (tmp_path / "output" / "current.json").exists()
        assert not (tmp_path / "output" / "clusters-local").exists()
        return
    assert clusters.call_count == 1
    candidate_spots, candidate_clusters = clusters.call_args.args[:2]
    active = resolve_complete(tmp_path / "output")
    candidate_spots = Path(candidate_spots)
    candidate_clusters = Path(candidate_clusters)
    assert "attempts" in candidate_spots.parts and candidate_spots.name == "spots"
    assert candidate_spots.parent.name == "package"
    assert candidate_clusters == candidate_spots.parent / "clusters-local"
    assert json.loads((active.path / "clusters-local" / "L1.json").read_text())[0]["rep"]["id"] == "dispatcher-spot"
    assert not (tmp_path / "output" / "clusters-local").exists()


def test_corrupt_terrain_stops_before_alr_without_legacy_fallback(tmp_path, monkeypatch, caplog):
    import run as pipeline
    _write_input(tmp_path)
    def corrupt(*_args, **_kwargs):
        raise ValueError('Corrupt elevation wbm: test')
    monkeypatch.setattr(pipeline, 'ElevationSampler', corrupt)
    with patch('run.slice_and_compute') as alr:
        assert pipeline.run(_make_args(tmp_path, no_clusters=True)) == 1
    alr.assert_not_called()
    assert 'Corrupt elevation wbm' in caplog.text


def _mock_raster_steps(transform):
    """Return patches that retain orchestration while avoiding raster/OSM work."""
    slice_result = MagicMock(
        data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:2154"
    )
    return (
        patch("run.slice_and_compute", return_value=slice_result),
        patch("run.alr_to_darkness", return_value=np.full((2, 2), 0.5)),
        patch("run.alr_to_bortle", return_value=np.full((2, 2), 3, dtype=int)),
        patch("run.mesh_darkest", return_value=[]),
        patch("run.redundancy_filter", return_value=[]),
        patch("run.load_places", return_value=[]),
        patch("run.ensure_coverage", return_value=[]),
        patch("run.attach_near_town", return_value=[]),
        patch("run.enrich_all", return_value=[]),
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
        "name": "Test landmark",
        "nameDistanceKm": 1.0,
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
        patch("run.mesh_darkest", side_effect=tracker("mesh_darkest", [])), \
        patch("run.redundancy_filter", side_effect=tracker("redundancy_filter", [])), \
        patch("run.load_places", side_effect=tracker("load_places", [])), \
        patch("run.ensure_coverage", side_effect=tracker("ensure_coverage", [])), \
        patch("run.attach_near_town", side_effect=tracker("attach_near_town", [])), \
        patch("run.enrich_all", side_effect=tracker("enrich_all", [])), \
        patch("run.classify_spots_into_tiles", side_effect=tracker("classify_spots_into_tiles", {})), \
        patch("run.compute_new_version", side_effect=tracker("compute_new_version", ("2025.1", True))), \
        patch("run.write_tile_file", side_effect=lambda *a, **kw: (tracker("write_tile_file", None)(*a, **kw), real_write_tile_file(*a, **kw))[1]), \
        patch("run.clone_data_repo"), \
        patch("run.copy_spots_to_repo", side_effect=tracker("copy_spots_to_repo", None)), \
        patch("run.commit_and_push", side_effect=tracker("commit_and_push", None)):
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    assert call_order == [
        "slice_and_compute",
        "alr_to_darkness",
        "alr_to_bortle",
        "mesh_darkest",
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
    old_spot = dict(_spot("old", 48.5, 2.5), country="FR")
    keep_spot = dict(_spot("keep", 51.5, 0.0), country="GB")
    old_owned = {"version": "2025.1", "tile": "N050E001", "spots": [old_spot]}
    old_non_owned = {"version": "2025.1", "tile": "N051E000", "spots": [keep_spot]}

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
        patch("run.mesh_darkest", return_value=[]), \
        patch("run.redundancy_filter", return_value=[]), \
        patch("run.load_places", return_value=[]), \
        patch("run.ensure_coverage", return_value=[]), \
        patch("run.attach_near_town", return_value=[]), \
        patch("run.enrich_all", return_value=[]), \
        patch("run.classify_spots_into_tiles", return_value={"N050E001": [dict(_spot("new", 48.5, 2.5), country="FR")]}), \
        patch("run.enumerate_tiles_in_bbox", return_value=["N050E001", "N050E002"]), \
        patch("run.clone_data_repo", side_effect=clone_with_existing_tiles), \
        patch("run.compute_new_version", return_value=("2025.2", True)) as mock_version, \
        patch("run.write_tile_file", wraps=real_write_tile_file), \
        patch("run.write_cluster_files", wraps=real_write_cluster_files), \
        patch("run.copy_spots_to_repo") as mock_copy, \
        patch("run.commit_and_push"):
        assert run(args) == 0

    old_arg, new_arg, year_arg = mock_version.call_args.args
    assert old_arg == {"N050E001": old_owned, "N051E000": old_non_owned}
    assert set(new_arg) == {"N050E001", "N050E002", "N051E000"}
    assert new_arg["N050E001"]["spots"] == [dict(_spot("new", 48.5, 2.5), country="FR")]
    assert new_arg["N050E002"]["spots"] == []
    assert new_arg["N051E000"] == old_non_owned
    assert year_arg == 2025
    assert mock_copy.call_args.kwargs["ownership"].owns(old_owned["spots"][0])


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

    args = _make_args(tmp_path, no_clusters=True)  # explicit prepublication mode

    with \
        patch("run.clone_data_repo") as mock_clone, \
        patch("run.copy_spots_to_repo") as mock_copy, \
        patch("run.commit_and_push") as mock_commit, \
        patch("run.write_cluster_files") as mock_clusters:
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    mock_clone.assert_not_called()
    mock_copy.assert_not_called()
    mock_commit.assert_not_called()
    mock_clusters.assert_not_called()

    # Verify tile files were written locally
    from src.local_generation import resolve_complete
    spots_dir = resolve_complete(tmp_path / "output").path / "spots"
    tile_files = list(spots_dir.glob("*.json"))
    assert len(tile_files) > 0, "Expected tile files to be written even with --no-push"


def test_multi_country_manifest_validation_precedes_raster(tmp_path):
    """A GB+IE manifest mismatch aborts before any raster collaborator runs."""
    from src.geonames import NAMING_FEATURE_CODES
    from run import run

    _write_input(tmp_path, region="uk_ireland")
    args = _make_args(tmp_path, region="uk_ireland", no_clusters=True)
    validation = RuntimeError("GeoNames manifest codes differ; re-extract IE")

    with patch("run.validate_geonames_manifest", side_effect=validation) as manifest, \
         patch("run.slice_and_compute") as raster:
        assert run(args) == 1

    manifest.assert_called_once()
    assert manifest.call_args.kwargs["countries"] == ["GB", "IE"]
    assert manifest.call_args.kwargs["feature_codes"] is NAMING_FEATURE_CODES
    raster.assert_not_called()


def test_multi_country_runtime_uses_one_filtered_index_and_only_configured_spots(
    tmp_path,
):
    """The full orchestrator path carries GB+IE through clip, naming and tiles."""
    from rasterio.transform import from_bounds
    from src.geonames import NAMING_FEATURE_CODES
    from run import run

    _write_input(tmp_path, region="uk_ireland")
    args = _make_args(tmp_path, region="uk_ireland", no_push=False)
    transform = from_bounds(-11, 49, 2, 55, 2, 2)
    candidates = [
        {
            "id": "gb-spot",
            "lat": 51.2,
            "lon": -1.4,
            "row": 0,
            "col": 0,
            "darkness": 0.8,
            "bortle": 3,
            "country": "GB",
        },
        {
            "id": "ie-spot",
            "lat": 53.3,
            "lon": -8.1,
            "row": 1,
            "col": 1,
            "darkness": 0.9,
            "bortle": 3,
            "country": "IE",
        },
    ]
    slice_result = MagicMock(
        data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:4326"
    )
    naming_index = MagicMock()
    naming_index.enrich_spots.side_effect = lambda spots: [
        dict(spot, name=f"{spot['country']} landmark") for spot in spots
    ]

    def clone(_url, _branch, target_dir):
        (Path(target_dir) / "spots").mkdir()

    with ExitStack() as stack:
        stack.enter_context(patch("run.validate_geonames_manifest"))
        loader = stack.enter_context(
            patch("run.GeoNamesIndex.from_filtered_extracts", return_value=naming_index)
        )
        stack.enter_context(patch("run.slice_and_compute", return_value=slice_result))
        stack.enter_context(patch("run.alr_to_darkness", return_value=np.full((2, 2), 0.5)))
        stack.enter_context(
            patch("run.alr_to_bortle", return_value=np.full((2, 2), 3, dtype=int))
        )
        stack.enter_context(patch("run.mesh_darkest", return_value=candidates))
        clip = stack.enter_context(
            patch("run.classify_candidates", return_value=(candidates, {}))
        )
        stack.enter_context(patch("run.redundancy_filter", return_value=candidates))
        places = stack.enter_context(patch("run.load_places", return_value=[]))
        stack.enter_context(patch("run.ensure_coverage", return_value=candidates))
        stack.enter_context(patch("run.attach_near_town", return_value=candidates))
        stack.enter_context(patch("run.enrich_all", side_effect=lambda spots, **kwargs: spots))
        tile_export = stack.enter_context(
            patch("run.classify_spots_into_tiles", return_value={})
        )
        stack.enter_context(patch("run.write_tile_file", wraps=real_write_tile_file))
        stack.enter_context(patch("run.clone_data_repo", side_effect=clone))
        stack.enter_context(
            patch(
                "run.audit_country_spots",
                return_value={
                    "missing": [],
                    "invalid": [],
                    "unconfigured": [],
                    "mismatched": [],
                    "ambiguous": [],
                    "unassignable": [],
                    "valid": 0,
                },
            )
        )
        copy = stack.enter_context(patch("run.copy_spots_to_repo"))
        commit = stack.enter_context(patch("run.commit_and_push"))
        assert run(args) == 0

    loader.assert_called_once_with(
        data_dir="data",
        countries=["GB", "IE"],
        feature_codes=NAMING_FEATURE_CODES,
        bbox=[-11, 49, 2, 55],
        margin_km=40,
    )
    clip.assert_called_once()
    assert clip.call_args.args[1] == ["GB", "IE"]
    places.assert_called_once_with(
        {
            "bbox": [-11, 49, 2, 55],
            "equal_area_epsg": 3035,
            "admin_level": 8,
            "osm_country_code": ["GB", "IE"],
            "name": "Angleterre, Pays de Galles et Irlande (≤55°N)",
        }
    )
    naming_index.enrich_spots.assert_called_once()
    assert {spot["country"] for spot in naming_index.enrich_spots.call_args.args[0]} == {
        "GB",
        "IE",
    }
    assert all(
        spot["country"] in {"GB", "IE"}
        for spot in tile_export.call_args.args[0]
    )
    assert set(copy.call_args.kwargs["ownership"].countries) == {"GB", "IE"}
    commit.assert_called_once()


def test_nominal_bbox_filter_blocks_halo_candidates_from_all_publish_steps(
    tmp_path, caplog
):
    """A mesh darkest pixel in the ALR halo never reaches publication."""
    from rasterio.transform import from_bounds
    from run import run

    _write_input(tmp_path, region="uk_ireland")
    args = _make_args(tmp_path, region="uk_ireland", no_clusters=True)
    transform = from_bounds(-11, 49, 2, 55, 2, 2)
    halo_candidate = {
        "id": "gb-halo",
        "lat": 56.0,
        "lon": -1.4,
        "row": 0,
        "col": 0,
        "darkness": 0.7,
        "bortle": 3,
        "country": "GB",
    }
    gb_candidate = {
        "id": "gb-inside",
        "lat": 51.2,
        "lon": -1.4,
        "row": 0,
        "col": 1,
        "darkness": 0.8,
        "bortle": 3,
        "country": "GB",
    }
    ie_candidate = {
        "id": "ie-inside",
        "lat": 53.3,
        "lon": -8.1,
        "row": 1,
        "col": 0,
        "darkness": 0.9,
        "bortle": 3,
        "country": "IE",
    }
    candidates = [halo_candidate, gb_candidate, ie_candidate]
    slice_result = MagicMock(
        data=np.full((2, 2), 1.0), transform=transform, crs="EPSG:4326"
    )
    redundancy_input = []
    coverage_mesh_input = []
    naming_input = []
    tile_input = []

    def capture_redundancy(spots, *args, **kwargs):
        redundancy_input.extend(spots)
        return spots

    def capture_coverage(filtered, all_mesh_points, *args, **kwargs):
        coverage_mesh_input.extend(all_mesh_points)
        return filtered

    def capture_naming(spots):
        naming_input.extend(spots)
        return [dict(spot, name=f"{spot['country']} landmark") for spot in spots]

    def capture_tiles(spots, *args, **kwargs):
        tile_input.extend(spots)
        return {}

    with ExitStack() as stack:
        stack.enter_context(patch("run.validate_geonames_manifest"))
        stack.enter_context(patch("run.slice_and_compute", return_value=slice_result))
        stack.enter_context(
            patch("run.alr_to_darkness", return_value=np.full((2, 2), 0.5))
        )
        stack.enter_context(
            patch("run.alr_to_bortle", return_value=np.full((2, 2), 3, dtype=int))
        )
        stack.enter_context(patch("run.mesh_darkest", return_value=candidates))
        stack.enter_context(
            patch("run.classify_candidates", return_value=(candidates, {}))
        )
        stack.enter_context(
            patch("run.redundancy_filter", side_effect=capture_redundancy)
        )
        stack.enter_context(patch("run.load_places", return_value=[]))
        stack.enter_context(patch("run.ensure_coverage", side_effect=capture_coverage))
        stack.enter_context(
            patch(
                "run.attach_near_town",
                side_effect=lambda spots, _places: spots,
            )
        )
        naming_index = MagicMock()
        naming_index.enrich_spots.side_effect = capture_naming
        stack.enter_context(
            patch("run.GeoNamesIndex.from_filtered_extracts", return_value=naming_index)
        )
        stack.enter_context(patch("run.enrich_all", side_effect=lambda spots, **kwargs: spots))
        stack.enter_context(
            patch("run.classify_spots_into_tiles", side_effect=capture_tiles)
        )
        stack.enter_context(patch("run.write_tile_file", wraps=real_write_tile_file))
        with caplog.at_level("INFO", logger="pipeline"):
            assert run(args) == 0

    assert [spot["id"] for spot in redundancy_input] == ["gb-inside", "ie-inside"]
    assert [spot["id"] for spot in coverage_mesh_input] == ["gb-inside", "ie-inside"]
    assert [spot["id"] for spot in naming_input] == ["gb-inside", "ie-inside"]
    assert [spot["id"] for spot in tile_input] == ["gb-inside", "ie-inside"]
    assert "Nominal bbox filter: 2 kept, 1 rejected" in caplog.text


def test_run_keeps_unassigned_spots_before_tile_classification(tmp_path, mock_region):
    """An empty ``near`` does not remove a valid land spot anymore."""
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
        patch("run.write_tile_file", wraps=real_write_tile_file), \
        patch("run.clone_data_repo"), \
        patch("run.copy_spots_to_repo"), \
        patch("run.commit_and_push"):
        rc = run(args)

    assert rc == 0, f"run() returned {rc}, expected 0"
    assert captured_input is not None, "classify_spots_into_tiles was never called"
    assert len(captured_input) == 2, f"Expected 2 spots, got {len(captured_input)}: {captured_input}"
    assert {spot["near"] for spot in captured_input} == {"", "Bayonne"}


@patch("run.load_places", return_value=[])
def test_orchestrator_attaches_bortle_before_redundancy_filter(mock_load_places, tmp_path, mock_region):
    """Regression test for the Step 2b bug: candidates must have bortle set
    before redundancy_filter is called. We patch mesh_darkest to return
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

    def mock_mesh_darkest(*args, **kwargs):
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

    # NOTE: patch run.mesh_darkest / run.redundancy_filter, not src.extract.*,
    # because run.py does ``from src.extract import mesh_darkest`` at module
    # level, binding a local reference. Patching ``src.extract.mesh_darkest``
    # would not affect the already-imported reference in run().
    with patch("run.mesh_darkest", side_effect=mock_mesh_darkest), \
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
    from src.local_generation import writer_lock
    with writer_lock(Path(args.output_dir)):
        pass


def test_competing_published_run_fails_at_output_lock_before_preflight(tmp_path):
    import subprocess
    import sys
    from src.local_generation import initialize, writer_lock
    _write_input(tmp_path)
    output = tmp_path / "output"
    initialize(output, "france", 2025)
    script = r'''
from argparse import Namespace
import sys, run
run.load_regions = lambda: {}
run.get_region = lambda _name: {"bbox": []}
run.clone_data_repo = lambda *_args: print("CLONE", flush=True)
run._audit_before_write = lambda *_args: print("AUDIT", flush=True) or True
run._legacy_run = lambda _args: 0
args = Namespace(region="france", year=2025, no_push=False,
 regenerate_clusters=False, list_orphans=False, audit_country_tags=False,
 migrate_country_tags=False, prune_orphan_spots=False,
 data_repo_url="fixture", data_repo_branch="main", input_dir=sys.argv[2],
 output_dir=sys.argv[1])
print("STATUS", run.run(args), flush=True)
'''
    with writer_lock(output):
        child = subprocess.run(
            [sys.executable, "-c", script, str(output), str(tmp_path / "input")],
            cwd=Path(__file__).resolve().parents[2], capture_output=True,
            text=True, timeout=10,
        )
    assert "STATUS 1" in child.stdout
    assert "CLONE" not in child.stdout
    assert "AUDIT" not in child.stdout
    with writer_lock(output):
        pass


def test_published_competing_invocation_is_rejected_before_clone_and_audit(tmp_path):
    import subprocess
    import sys
    from src.local_generation import initialize, writer_lock

    output = tmp_path / "output"
    initialize(output, "france", 2025)
    _write_input(tmp_path)
    script = r'''
from argparse import Namespace
import run
run.load_regions = lambda: {}
run.get_region = lambda _name: {"bbox": []}
run.clone_data_repo = lambda *_args: print("CLONE_CALLED", flush=True)
run._audit_before_write = lambda *_args: print("AUDIT_CALLED", flush=True) or True
run._legacy_run = lambda _args: 0
args = Namespace(region="france", year=2025, no_push=False,
    regenerate_clusters=False, list_orphans=False, audit_country_tags=False,
    migrate_country_tags=False, prune_orphan_spots=False,
    data_repo_url="unused", data_repo_branch="main", input_dir=sys.argv[2],
    output_dir=sys.argv[1])
print("STATUS", run.run(args), flush=True)
'''.replace("import run\n", "import run\nimport sys\n")
    with writer_lock(output):
        child = subprocess.run(
            [sys.executable, "-c", script, str(output), str(tmp_path / "input")],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
            timeout=10,
        )
        assert "STATUS 1" in child.stdout
        assert "CLONE_CALLED" not in child.stdout
        assert "AUDIT_CALLED" not in child.stdout
    # The failed contender did not damage or unlink the persistent lock.
    with writer_lock(output):
        pass


@pytest.mark.parametrize("problem_key", [
    "ambiguous", "unassignable", "outside_declared_extents", "ambiguous_ownership",
])
def test_audit_modes_reject_new_country_anomaly_categories(tmp_path, problem_key):
    """Ambiguous and unassignable spots are blocking audit findings."""
    from run import _audit_before_write, run_list_orphans

    audit = {
        "missing": 0,
        "invalid": 0,
        "unconfigured": 0,
        "mismatched": 0,
        "ambiguous": 0,
        "unassignable": 0,
    }
    audit[problem_key] = 1
    with patch("run.audit_country_spots", return_value=audit):
        assert _audit_before_write(tmp_path / "spots", {}) is False
        args = MagicMock(data_repo_url=None, output_dir=str(tmp_path))
        assert run_list_orphans(args) == 1


def test_local_country_audit_pins_explicit_generation_and_fails_on_missing_tile(tmp_path):
    from types import SimpleNamespace
    from run import run_list_orphans
    from src.local_generation import initialize, new_attempt
    from src.tile_export import write_tile_file
    from src.local_generation import promote
    output = tmp_path / "output"
    initialize(output, "france", 2025)
    attempt_id, attempt = new_attempt(output, "france", 2025)
    tile_spot = _spot("audited", 48.2, 2.2)
    write_tile_file("N048E002", [tile_spot], str(attempt / "package"),
                    "2025.1", "fixture", "A")
    generation = promote(output, attempt_id, region="france", year=2025,
                         context={"fixture": "A"}, intended_tiles=["N048E002"])
    args = SimpleNamespace(data_repo_url=None, output_dir=str(generation.path))
    with patch("run.audit_country_spots", return_value={"valid": 1}) as audit:
        assert run_list_orphans(args) == 0
        assert audit.call_args.args[0] == generation.path / "spots"
    (generation.path / "spots" / "N048E002.json").unlink()
    with patch("run.audit_country_spots", return_value={"valid": 0}) as audit:
        assert run_list_orphans(args) == 1
        audit.assert_not_called()


def test_local_country_audit_keeps_historical_empty_directory_behavior(tmp_path):
    from types import SimpleNamespace
    from run import run_list_orphans
    historical = tmp_path / "historical"
    (historical / "spots").mkdir(parents=True)
    args = SimpleNamespace(data_repo_url=None, output_dir=str(historical))
    with patch("run.audit_country_spots", return_value={"valid": 0}) as audit:
        assert run_list_orphans(args) == 0
    assert audit.call_args.args[0] == historical / "spots"


def test_local_country_audit_canonicalizes_external_aliases_and_uses_real_auditor(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shapely.geometry import box
    from src.local_generation import initialize, new_attempt, promote
    from src.publish import audit_country_spots as real_audit_country_spots
    from src.tile_export import write_tile_file
    from run import run_list_orphans

    class FixtureGeography:
        countries = {"FR": box(-5, 40, 10, 52)}

        @staticmethod
        def country_candidates(point):
            return ["FR"] if FixtureGeography.countries["FR"].covers(point) else []

    regions = {"fixture": {"bbox": [-5, 40, 10, 52], "osm_country_code": ["FR"]}}
    geography = FixtureGeography()
    monkeypatch.setattr("run.load_regions", lambda: regions)
    monkeypatch.setattr(
        "run.audit_country_spots",
        lambda spots_dir, _regions: real_audit_country_spots(
            spots_dir, regions, geography=geography
        ),
    )

    def managed_package(label):
        root = tmp_path / f"managed-{label}"
        initialize(root, "france", 2025)
        attempt_id, attempt = new_attempt(root, "france", 2025)
        write_tile_file(
            "N048E002", [_spot(f"{label}-spot", 48.2, 2.2) | {"country": "FR"}],
            str(attempt / "package"), "2025.1", "fixture", label,
        )
        ref = promote(root, attempt_id, region="france", year=2025,
                      context={"fixture": label}, intended_tiles=["N048E002"])
        return root, ref

    aliases = tmp_path / "outside-aliases"
    aliases.mkdir()

    # Both the canonical package and an external symlink alias are audited by
    # the real country auditor, using a deterministic fixture geometry.
    root, valid = managed_package("valid")
    valid_alias = aliases / "valid-package"
    valid_alias.symlink_to(valid.path, target_is_directory=True)
    for path in (valid.path, valid_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 0

    # Recorded-tile loss is caught for both spellings before the auditor can
    # turn the missing content into a successful empty audit.
    (valid.path / "spots" / "N048E002.json").unlink()
    for path in (valid.path, valid_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 1

    # Managed history and unfinished candidates are unsupported read paths,
    # including when their lexical path begins outside the managed root.
    history_alias = aliases / "history"
    history_alias.symlink_to(root / "generations", target_is_directory=True)
    for path in (root / "generations", history_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 1

    attempt_id, attempt = new_attempt(root, "france", 2025)
    unfinished = attempt / "package"
    write_tile_file(
        "N048E002", [_spot("unfinished-spot", 48.2, 2.2) | {"country": "FR"}],
        str(unfinished), "2025.1", "fixture", "unfinished",
    )
    unfinished_alias = aliases / "unfinished-package"
    unfinished_alias.symlink_to(unfinished, target_is_directory=True)
    for path in (unfinished, unfinished_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 1

    # A true historical path remains read-only and auditable, including via
    # an external alias; it is not rejected merely for being a symlink.
    historical = tmp_path / "historical" / "spots"
    historical.mkdir(parents=True)
    write_tile_file("N048E002", [_spot("historic", 48.2, 2.2) | {"country": "FR"}],
                    str(historical.parent), "2025.1", "fixture", "historic")
    historical_alias = aliases / "historical"
    historical_alias.symlink_to(historical.parent, target_is_directory=True)
    for path in (historical.parent, historical_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 0


def test_local_country_audit_rejects_external_alias_to_managed_history(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from shapely.geometry import box
    from src.local_generation import initialize, new_attempt, promote
    from src.publish import audit_country_spots as real_audit_country_spots
    from src.tile_export import write_tile_file
    from run import run_list_orphans

    class FixtureGeography:
        countries = {"FR": box(-5, 40, 10, 52)}

        @staticmethod
        def country_candidates(point):
            return ["FR"] if FixtureGeography.countries["FR"].covers(point) else []

    regions = {"fixture": {"bbox": [-5, 40, 10, 52], "osm_country_code": ["FR"]}}
    geography = FixtureGeography()
    monkeypatch.setattr("run.load_regions", lambda: regions)
    monkeypatch.setattr(
        "run.audit_country_spots",
        lambda spots_dir, _regions: real_audit_country_spots(
            spots_dir, regions, geography=geography
        ),
    )
    root = tmp_path / "managed"
    initialize(root, "france", 2025)
    attempt_id, attempt = new_attempt(root, "france", 2025)
    write_tile_file("N048E002", [_spot("history-parent", 48.2, 2.2) | {"country": "FR"}],
                    str(attempt / "package"), "2025.1", "fixture", "A")
    promote(root, attempt_id, region="france", year=2025,
            context={"fixture": "A"}, intended_tiles=["N048E002"])
    alias_parent = tmp_path / "external-aliases"
    alias_parent.mkdir()
    history_alias = alias_parent / "generations"
    history_alias.symlink_to(root / "generations", target_is_directory=True)
    for path in (root / "generations", history_alias):
        args = SimpleNamespace(data_repo_url=None, output_dir=str(path))
        assert run_list_orphans(args) == 1


@pytest.mark.parametrize("problem_key", [
    "ambiguous", "unassignable", "outside_declared_extents", "ambiguous_ownership",
])
def test_migration_guard_rejects_new_country_anomaly_categories(tmp_path, problem_key):
    from run import run_country_migration

    audit = {
        "missing": 0,
        "invalid": 0,
        "unconfigured": 0,
        "mismatched": 0,
        "ambiguous": 0,
        "unassignable": 0,
    }
    audit[problem_key] = 1
    regions = {"france": {"osm_country_code": ["FR"]}}

    def clone(_url, _branch, target):
        (Path(target) / "spots").mkdir()

    args = MagicMock(
        no_push=False,
        data_repo_url="git@example.invalid:data.git",
        data_repo_branch="main",
        year=2025,
        prune_orphan_spots=False,
    )
    with patch("run.load_regions", return_value=regions), \
         patch("run.clone_data_repo", side_effect=clone), \
         patch("run.migrate_country_tags", return_value={}), \
         patch("run.audit_country_spots", return_value=audit), \
         patch("run.commit_and_push") as commit:
        assert run_country_migration(args) == 1
    commit.assert_not_called()


@pytest.mark.parametrize("prune_orphan_spots", [False, True])
def test_published_migration_regenerates_clusters_before_commit_and_preserves_data_year(
    tmp_path, prune_orphan_spots
):
    """Published migrations update the cluster contract in the same commit."""
    from run import run_country_migration

    regions = {"france": {"osm_country_code": ["FR"]}}
    events = []

    def clone(_url, _branch, target):
        clone_dir = Path(target)
        (clone_dir / "spots").mkdir()
        clusters_dir = clone_dir / "clusters"
        clusters_dir.mkdir()
        (clusters_dir / "index.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "generated": "2026-08-03T00:00:00.000000000Z",
                    "data_year": 2021,
                    "levels": [],
                }
            ),
            encoding="utf-8",
        )
        events.append("clone")

    def migrate(*_args, **_kwargs):
        events.append("migration")
        return {"changed": 1}

    def audit(*_args, **_kwargs):
        events.append("audit")
        return {
            "missing": [],
            "invalid": [],
            "unconfigured": [],
            "mismatched": [],
            "ambiguous": [],
            "unassignable": [],
            "valid": 1,
        }

    def clusters(*args, **kwargs):
        events.append("clusters")
        assert args[0].name == "spots"
        assert args[1].name == "clusters"
        assert kwargs["data_year"] == 2021
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z",
            kwargs["generated"],
        )

    def commit(*_args, **_kwargs):
        events.append("commit")

    args = MagicMock(
        no_push=False,
        data_repo_url="git@example.invalid:data.git",
        data_repo_branch="main",
        year=2025,
        prune_orphan_spots=prune_orphan_spots,
    )
    with patch("run.load_regions", return_value=regions), \
         patch("run.clone_data_repo", side_effect=clone), \
         patch("run.migrate_country_tags", side_effect=migrate), \
         patch("run.audit_country_spots", side_effect=audit), \
         patch("run.write_cluster_files", side_effect=clusters), \
         patch("run.commit_and_push", side_effect=commit):
        assert run_country_migration(args) == 0

    assert events == ["clone", "migration", "audit", "clusters", "commit"]


def test_published_migration_uses_year_when_cluster_manifest_is_unusable(tmp_path):
    """The informational data_year falls back to the migration year."""
    from run import run_country_migration

    regions = {"france": {"osm_country_code": ["FR"]}}

    def clone(_url, _branch, target):
        clone_dir = Path(target)
        (clone_dir / "spots").mkdir()
        (clone_dir / "clusters").mkdir()
        (clone_dir / "clusters" / "index.json").write_text(
            '{"schema": 99, "data_year": 2010}', encoding="utf-8"
        )

    args = MagicMock(
        no_push=False,
        data_repo_url="git@example.invalid:data.git",
        data_repo_branch="main",
        year=2025,
        prune_orphan_spots=False,
    )
    audit = {
        "missing": [], "invalid": [], "unconfigured": [], "mismatched": [],
        "ambiguous": [], "unassignable": [], "valid": 0,
    }
    with patch("run.load_regions", return_value=regions), \
         patch("run.clone_data_repo", side_effect=clone), \
         patch("run.migrate_country_tags", return_value={}), \
         patch("run.audit_country_spots", return_value=audit), \
         patch("run.write_cluster_files") as clusters, \
         patch("run.commit_and_push"):
        assert run_country_migration(args) == 0

    assert clusters.call_args.kwargs["data_year"] == 2025


def test_published_migration_without_manifest_or_year_fails_before_commit(tmp_path):
    """A missing informational value cannot produce a publishable migration."""
    from run import run_country_migration

    regions = {"france": {"osm_country_code": ["FR"]}}
    args = MagicMock(
        no_push=False,
        data_repo_url="git@example.invalid:data.git",
        data_repo_branch="main",
        year=None,
        prune_orphan_spots=False,
    )
    audit = {
        "missing": [], "invalid": [], "unconfigured": [], "mismatched": [],
        "ambiguous": [], "unassignable": [], "valid": 0,
    }
    with patch("run.load_regions", return_value=regions), \
         patch("run.clone_data_repo", side_effect=lambda _u, _b, target: (Path(target) / "spots").mkdir()), \
         patch("run.migrate_country_tags", return_value={}), \
         patch("run.audit_country_spots", return_value=audit), \
         patch("run.write_cluster_files") as clusters, \
         patch("run.commit_and_push") as commit:
        assert run_country_migration(args) == 1

    clusters.assert_not_called()
    commit.assert_not_called()


def test_published_migration_cluster_failure_never_reaches_commit(tmp_path):
    """Cluster generation is a hard pre-commit step."""
    from run import run_country_migration

    regions = {"france": {"osm_country_code": ["FR"]}}
    args = MagicMock(
        no_push=False,
        data_repo_url="git@example.invalid:data.git",
        data_repo_branch="main",
        year=2025,
        prune_orphan_spots=False,
    )
    audit = {
        "missing": [], "invalid": [], "unconfigured": [], "mismatched": [],
        "ambiguous": [], "unassignable": [], "valid": 0,
    }
    with patch("run.load_regions", return_value=regions), \
         patch("run.clone_data_repo", side_effect=lambda _u, _b, target: (Path(target) / "spots").mkdir()), \
         patch("run.migrate_country_tags", return_value={}), \
         patch("run.audit_country_spots", return_value=audit), \
         patch("run.write_cluster_files", side_effect=OSError("cluster failure")), \
         patch("run.commit_and_push") as commit:
        assert run_country_migration(args) == 1

    commit.assert_not_called()


def test_local_migration_stages_clusters_without_publication(tmp_path):
    """Local migration prepares its complete payload in a temporary copy."""
    from run import run_country_migration

    spots_dir = tmp_path / "output" / "spots"
    spots_dir.mkdir(parents=True)
    args = MagicMock(
        no_push=True,
        output_dir=str(tmp_path / "output"),
        prune_orphan_spots=True,
    )
    with patch("run.load_regions", return_value={"france": {"osm_country_code": ["FR"]}}), \
         patch("run.migrate_country_tags", return_value={}) as migrate, \
         patch("run.write_cluster_files") as clusters, \
         patch("run.commit_and_push") as commit:
        assert run_country_migration(args) == 0

    migrate.assert_called_once()
    clusters.assert_called_once()
    assert clusters.call_args.args[0] != spots_dir
    assert clusters.call_args.args[0].name == 'spots'
    commit.assert_not_called()


def test_generated_timestamp_is_nanosecond_precise_and_monotonic(monkeypatch):
    """Equal clock readings still produce distinct ordered manifest values."""
    import run

    base = time.time_ns()
    monkeypatch.setattr(run, "_LAST_GENERATED_NS", 0)
    monkeypatch.setattr(run.time, "time_ns", lambda: base)

    first = run._generated_date()
    second = run._generated_date()

    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z"
    assert re.fullmatch(pattern, first)
    assert re.fullmatch(pattern, second)
    assert first < second


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
        stack.enter_context(patch("run.write_tile_file", wraps=real_write_tile_file))
        assert run(args) == 0
    enumerate_tiles.assert_called_once()


def test_no_push_clusters_include_all_staged_country_tiles(tmp_path):
    """Historical output is preserved and refused instead of inherited."""
    from run import run

    spots_dir = tmp_path / "output" / "spots"
    _write_envelope(spots_dir, "N048E002", [_spot("legacy", 48.2, 2.2)])
    before = (spots_dir / "N048E002.json").read_bytes()
    _write_input(tmp_path)
    args = _make_args(tmp_path)
    assert run(args) == 1
    assert (spots_dir / "N048E002.json").read_bytes() == before
    assert not (tmp_path / "output" / "current.json").exists()


def test_published_regeneration_audits_country_tags_before_commit(tmp_path):
    """Cluster regeneration audits the complete repository before writing."""
    from src.cli import parse_args
    from run import run_regenerate_clusters

    args = parse_args(["--regenerate-clusters", "--year", "2025", "--data-repo-url", "git@example:data.git"])
    committed = {}

    def clone(_url, _branch, target_dir):
        _write_envelope(Path(target_dir) / "spots", "N051E000", [])

    def commit(data_repo_dir, _message, **kwargs):
        assert kwargs['geographic_dataset'] is not None
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


def test_local_dispatcher_returns_legacy_pipeline_failure_status(tmp_path):
    """The local dispatcher propagates the atomic producer's failure status."""
    from run import run

    args = _make_args(tmp_path)
    with patch("run._legacy_run", return_value=1), \
         patch("run.write_cluster_files") as clusters:
        assert run(args) == 1
    clusters.assert_not_called()


def test_local_regeneration_requires_existing_staged_spots(tmp_path, caplog):
    """Offline regeneration rejects a missing staging source instead of an empty repo."""
    from src.cli import parse_args
    from run import run_regenerate_clusters

    args = parse_args(["--regenerate-clusters", "--year", "2025", "--no-push", "--output-dir", str(tmp_path / "missing-output")])
    with patch("run.write_cluster_files") as writer:
        assert run_regenerate_clusters(args) == 1
    writer.assert_not_called()
    assert "fresh dedicated --output-dir" in caplog.text
