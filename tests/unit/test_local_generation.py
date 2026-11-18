import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from src.local_generation import (
    LocalGenerationError, initialize, new_attempt, promote, resolve_complete,
)


def _candidate(root, generation, *, tile="N048E002", marker="A"):
    attempt_id, attempt = new_attempt(root, "france", 2025)
    package = attempt / "package"
    spots = package / "spots"
    spots.mkdir()
    payload = {"version": "2025.0", "source": "fixture", "generated": marker,
               "tile": tile, "spots": []}
    (spots / f"{tile}.json").write_text(json.dumps(payload))
    return promote(root, attempt_id, region="france", year=2025,
                   context={"fixture": generation}, intended_tiles=[tile])


def _spot(spot_id, lat, lon):
    return {"id": spot_id, "lat": lat, "lon": lon, "darkness": 0.8,
            "bortle": 3, "near": "Fixture", "name": "Fixture place",
            "nameDistanceKm": 1.0, "altitude": 123}


def _candidate_with_spots(root, generation, spots):
    from src.tile_export import write_tile_file
    attempt_id, attempt = new_attempt(root, "france", 2025)
    package = attempt / "package"
    by_tile = {}
    from src.tile_id import tile_id
    for spot in spots:
        by_tile.setdefault(tile_id(spot["lat"], spot["lon"]), []).append(spot)
    for tile, records in by_tile.items():
        write_tile_file(tile, records, str(package), "2025.1", "fixture", generation)
    return promote(root, attempt_id, region="france", year=2025,
                   context={"fixture": generation}, intended_tiles=sorted(by_tile))


def _clustered_candidate(root, generation, spots, levels):
    from src.clusters import write_cluster_files
    from src.local_generation import promote
    from src.tile_export import write_tile_file
    from src.tile_id import tile_id
    attempt_id, attempt = new_attempt(root, "france", 2025)
    package = attempt / "package"
    by_tile = {}
    for spot in spots:
        by_tile.setdefault(tile_id(spot["lat"], spot["lon"]), []).append(spot)
    for tile, records in by_tile.items():
        write_tile_file(tile, records, str(package), "2025.1", "fixture", generation)
    params = [{"level": item.level, "cell_deg": item.cell_deg,
               "width_km": list(item.width_km)} for item in levels]
    write_cluster_files(package / "spots", package / "clusters-local",
                        data_year=2025, generated=generation, levels=levels)
    return promote(root, attempt_id, region="france", year=2025,
                   context={"fixture": generation, "cluster_parameters": params},
                   intended_tiles=sorted(by_tile),
                   clusters=True, cluster_parameters=params)


def test_promotes_and_resolves_immutable_generation(tmp_path):
    a = _candidate(tmp_path, "A")
    old = (a.path / "spots/N048E002.json").read_bytes()
    b = _candidate(tmp_path, "B", marker="B")
    assert resolve_complete(tmp_path).generation_id == b.generation_id
    assert (a.path / "spots/N048E002.json").read_bytes() == old


def test_spots_reader_pins_one_generation_across_activation(tmp_path):
    from src.local_generation import resolve_spots_dir
    a = _candidate_with_spots(tmp_path, "A", [_spot("A-only", 48.2, 2.2)])
    already_pinned_path = resolve_spots_dir(a.path)
    from measure_landmarks import load_spots
    from calibrate_exact import load_spot_points
    b = _candidate_with_spots(tmp_path, "B", [_spot("B-only", 49.2, 3.2)])
    assert [item["id"] for item in load_spots(already_pinned_path)] == ["A-only"]
    assert [item["label"] for item in load_spot_points(already_pinned_path)] == ["A-only"]
    reader_path = resolve_spots_dir(a.path)
    assert reader_path == a.path / "spots"
    assert [item["id"] for item in load_spots(reader_path)] == ["A-only"]
    assert [item["label"] for item in load_spot_points(reader_path)] == ["A-only"]
    assert all(item["id"] != "B-only" for item in load_spots(reader_path))
    assert resolve_spots_dir(tmp_path) == b.path / "spots"


def test_real_readers_reject_managed_history_and_unfinished_candidates(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import calibrate_exact
    import measure_landmarks
    from src.local_generation import resolve_spots_dir
    _candidate_with_spots(tmp_path, "A", [_spot("A-only", 48.2, 2.2)])
    _candidate_with_spots(tmp_path, "B", [_spot("B-only", 49.2, 3.2)])
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    unfinished_spots = attempt / "package" / "spots"
    unfinished_spots.mkdir()
    unsupported = [tmp_path / "generations", unfinished_spots]
    for path in unsupported:
        with pytest.raises(LocalGenerationError, match="managed|history"):
            resolve_spots_dir(path)

        monkeypatch.setattr(
            measure_landmarks, "build_parser",
            lambda: SimpleNamespace(parse_args=lambda _argv: SimpleNamespace(spots_dir=str(path))),
        )
        monkeypatch.setattr(measure_landmarks, "run_measurement",
                            lambda _args: pytest.fail("landmark reader consumed unsupported managed path"))
        with pytest.raises(LocalGenerationError):
            measure_landmarks.main([])

        monkeypatch.setattr(calibrate_exact.argparse.ArgumentParser, "parse_args",
                            lambda _self, _argv=None: SimpleNamespace(
                                spots_dir=str(path), natural_sqm=22.0,
                                foreign_samples=None, sky_brightness=None,
                            ))
        with pytest.raises(LocalGenerationError):
            calibrate_exact.main()


def test_nested_generation_json_provenance_is_inventoried_and_hash_checked(tmp_path):
    from src.accessibility import PUBLIC_CLASSES
    from src.tile_export import write_tile_file
    spot = _spot("with-accessibility", 48.2, 2.2)
    evidence = {"schema": 1, "source": "generation", "radiusM": 1000}
    evidence.update({name: {"status": "not_found_within_radius"} for name in PUBLIC_CLASSES})
    spot["accessibility"] = evidence
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    package = attempt / "package"
    write_tile_file("N048E002", [spot], str(package), "2025.1", "fixture", "A")
    provenance = package / "accessibility" / "sources" / "generation.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(json.dumps({"schema": 1, "source_id": "generation"}))
    ref = promote(tmp_path, attempt_id, region="france", year=2025,
                  context={"fixture": "A"}, intended_tiles=["N048E002"])
    relative = "accessibility/sources/generation.json"
    assert ref.manifest["files"][relative]
    assert resolve_complete(tmp_path).generation_id == ref.generation_id
    provenance = ref.path / relative
    provenance.write_text(json.dumps({"schema": 1, "source_id": "modified"}))
    with pytest.raises(LocalGenerationError, match="inventory/hash"):
        resolve_complete(tmp_path)


def test_local_regeneration_validates_historical_clusters_with_recorded_parameters(tmp_path, monkeypatch):
    import argparse
    import run
    from src.clusters import LEVELS, LevelSpec
    spots = [
        _spot("A-west", 48.21, 2.09) | {"darkness": 0.7},
        _spot("B-east", 48.23, 2.11) | {"darkness": 0.9},
    ]
    a = _clustered_candidate(tmp_path, "A", spots, LEVELS)
    parent_spots = {p.name: p.read_bytes() for p in (a.path / "spots").glob("*.json")}
    parent_clusters = {p.name: p.read_bytes() for p in (a.path / "clusters-local").glob("*.json")}
    changed_levels = (LevelSpec(1, 0.1, LEVELS[0].width_km), *LEVELS[1:])
    monkeypatch.setattr(run, "LEVELS", changed_levels)
    assert resolve_complete(tmp_path).generation_id == a.generation_id
    run._regenerate_local_clusters(
        argparse.Namespace(no_clusters=False, year=2025, region=None), tmp_path
    )
    b = resolve_complete(tmp_path)
    assert b.generation_id != a.generation_id
    assert {p.name: p.read_bytes() for p in (a.path / "spots").glob("*.json")} == parent_spots
    assert {p.name: p.read_bytes() for p in (a.path / "clusters-local").glob("*.json")} == parent_clusters
    assert {p.name: p.read_bytes() for p in (b.path / "spots").glob("*.json")} == parent_spots
    b_index = json.loads((b.path / "clusters-local" / "index.json").read_text())
    assert b_index["levels"][0]["cell_deg"] == 0.1
    assert b.manifest["cluster_parameters"][0]["cell_deg"] == 0.1
    assert b_index["levels"][0]["files"][0]["hash"] == __import__("hashlib").sha256(
        (b.path / "clusters-local/L1.json").read_bytes()
    ).hexdigest()

    # Independent oracle: at 0.1 degrees the spots occupy (20, 482) and
    # (21, 482). Their exact centers, bounds, counts, and representatives are
    # specified here rather than obtained from the production aggregator.
    expected_l1 = [
        {
            "id": "L1_20_482", "lat": 48.21, "lon": 2.09, "count": 1,
            "bbox": [2.09, 48.21, 2.09, 48.21],
            "rep": {
                "id": "A-west", "lat": 48.21, "lon": 2.09,
                "darkness": 0.7, "bortle": 3, "near": "Fixture",
                "name": "Fixture place", "nameDistanceKm": 1.0,
                "altitude": 123,
            },
        },
        {
            "id": "L1_21_482", "lat": 48.23, "lon": 2.11, "count": 1,
            "bbox": [2.11, 48.23, 2.11, 48.23],
            "rep": {
                "id": "B-east", "lat": 48.23, "lon": 2.11,
                "darkness": 0.9, "bortle": 3, "near": "Fixture",
                "name": "Fixture place", "nameDistanceKm": 1.0,
                "altitude": 123,
            },
        },
    ]
    actual_l1 = json.loads((b.path / "clusters-local" / "L1.json").read_text())
    assert actual_l1 == expected_l1


def test_refuses_unmanaged_output_without_modification(tmp_path):
    marker = tmp_path / "legacy.txt"
    marker.write_text("keep")
    with pytest.raises(LocalGenerationError, match="fresh dedicated"):
        initialize(tmp_path, "france", 2025)
    assert marker.read_text() == "keep"
    assert not (tmp_path / "identity.json").exists()


@pytest.mark.parametrize("kind", ["malformed", "unsupported", "symlink", "artifacts"])
def test_established_bootstrap_states_fail_closed(tmp_path, kind):
    identity = tmp_path / "identity.json"
    if kind == "malformed":
        identity.write_text("{")
    elif kind == "unsupported":
        identity.write_text(json.dumps({"schema": 999, "region": "france", "year": 2025}))
    elif kind == "symlink":
        target = tmp_path / "identity-target.json"
        target.write_text(json.dumps({"schema": 1, "region": "france", "year": 2025}))
        identity.symlink_to(target)
    else:
        (tmp_path / "attempts").mkdir()
    before = {p.relative_to(tmp_path): (p.readlink() if p.is_symlink() else p.read_bytes())
              for p in tmp_path.iterdir() if (p.is_file() or p.is_symlink()) and p.name != ".writer.lock"}
    with pytest.raises((LocalGenerationError, OSError)):
        initialize(tmp_path, "france", 2025)
    after = {p.relative_to(tmp_path): (p.readlink() if p.is_symlink() else p.read_bytes())
             for p in tmp_path.iterdir() if (p.is_file() or p.is_symlink()) and p.name != ".writer.lock"}
    assert after == before


def test_temporary_identity_does_not_override_requested_identity(tmp_path):
    import uuid
    (tmp_path / f".identity.{uuid.uuid4().hex}.tmp").write_text(
        json.dumps({"schema": 1, "region": "uk_ireland", "year": 1999})
    )
    identity = initialize(tmp_path, "france", 2025)
    assert identity["region"] == "france"


def test_managed_mutation_destination_is_rejected(tmp_path):
    from src.local_generation import reject_managed_destination
    ref = _candidate(tmp_path, "A")
    with pytest.raises(LocalGenerationError, match="managed generation"):
        reject_managed_destination(tmp_path)
    with pytest.raises(LocalGenerationError, match="managed generation"):
        reject_managed_destination(ref.path)


def test_second_process_writer_is_rejected_promptly(tmp_path):
    from src.local_generation import writer_lock
    script = r'''
import sys
from src.local_generation import writer_lock
try:
    with writer_lock(sys.argv[1]):
        print("acquired", flush=True)
except Exception as exc:
    print(type(exc).__name__ + ":" + str(exc), flush=True)
'''
    with writer_lock(tmp_path):
        proc = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                              capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[2], timeout=10)
    assert "Another writer holds" in proc.stdout


def test_mismatched_identity_does_not_change_active_generation(tmp_path):
    ref = _candidate(tmp_path, "A")
    pointer = (tmp_path / "current.json").read_bytes()
    with pytest.raises(LocalGenerationError, match="does not match"):
        initialize(tmp_path, "uk_ireland", 2025)
    assert (tmp_path / "current.json").read_bytes() == pointer
    assert resolve_complete(tmp_path).generation_id == ref.generation_id


def test_pointer_preparation_failure_keeps_previous_generation(tmp_path, monkeypatch):
    import src.local_generation as generations
    a = _candidate(tmp_path, "A")
    before = (tmp_path / "current.json").read_bytes()
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    (attempt / "package/spots").mkdir()
    (attempt / "package/spots/N048E002.json").write_text(json.dumps({
        "version": "2025.0", "source": "fixture", "generated": "B",
        "tile": "N048E002", "spots": [],
    }))
    replace = generations.os.replace
    def fail_pointer(source, destination):
        if Path(destination) == tmp_path / "current.json":
            raise OSError("injected pointer replacement failure")
        return replace(source, destination)
    monkeypatch.setattr(generations.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="injected"):
        promote(tmp_path, attempt_id, region="france", year=2025,
                context={"fixture": "B"}, intended_tiles=["N048E002"])
    assert (tmp_path / "current.json").read_bytes() == before
    assert resolve_complete(tmp_path).generation_id == a.generation_id


def test_reporting_failure_after_activation_keeps_generation_usable(tmp_path, monkeypatch):
    import src.local_generation as generations
    _candidate(tmp_path, "A")
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    spots = attempt / "package/spots"
    spots.mkdir()
    (spots / "N048E002.json").write_text(json.dumps({
        "version": "2025.0", "source": "B", "generated": "B",
        "tile": "N048E002", "spots": [],
    }))
    serializer = generations._json_bytes
    def fail_status(value):
        if value.get("status") == "activated":
            raise OSError("attempt bookkeeping unavailable")
        return serializer(value)
    monkeypatch.setattr(generations, "_json_bytes", fail_status)
    activated = generations.promote(tmp_path, attempt_id, region="france", year=2025,
                                    context={"fixture": "B"}, intended_tiles=["N048E002"])
    assert resolve_complete(tmp_path).generation_id == activated.generation_id


@pytest.mark.parametrize("active_exists", [False, True])
@pytest.mark.parametrize("defect", [
    "duplicate-ids", "unexpected-file", "clusters-when-absent", "empty-cluster-index-files",
])
def test_invalid_candidate_is_rejected_without_activation_or_parent_change(
    tmp_path, active_exists, defect,
):
    from src.clusters import write_cluster_files
    from src.tile_export import write_tile_file
    if active_exists:
        parent = _candidate(tmp_path, "A")
        pointer_before = (tmp_path / "current.json").read_bytes()
        payload_before = {p.relative_to(parent.path): p.read_bytes()
                          for p in parent.path.rglob("*") if p.is_file()}
    else:
        initialize(tmp_path, "france", 2025)

    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    package = attempt / "package"
    clusters = defect != "clusters-when-absent"
    records = [_spot("duplicate", 48.2, 2.2)]
    if defect == "duplicate-ids":
        records = [_spot("duplicate", 48.2, 2.2), _spot("duplicate", 49.2, 3.2)]
    from src.tile_id import tile_id
    by_tile = {}
    for spot in records:
        by_tile.setdefault(tile_id(spot["lat"], spot["lon"]), []).append(spot)
    for tile, spots in by_tile.items():
        write_tile_file(tile, spots, str(package), "2025.1", "fixture", "B")
    intended = sorted(by_tile)
    if clusters:
        write_cluster_files(package / "spots", package / "clusters-local",
                            data_year=2025, generated="cluster-B")
    cluster_parameters = None
    if clusters:
        from src.clusters import LEVELS
        cluster_parameters = [{"level": level.level, "cell_deg": level.cell_deg,
                               "width_km": list(level.width_km)} for level in LEVELS]
    if defect == "unexpected-file":
        nested = package / "accessibility" / "sources"
        nested.mkdir(parents=True)
        (nested / "unexplained.json").write_text("unreferenced provenance payload")
    elif defect == "clusters-when-absent":
        (package / "clusters-local").mkdir()
    elif defect == "empty-cluster-index-files":
        index_path = package / "clusters-local" / "index.json"
        index = json.loads(index_path.read_text())
        index["levels"][0]["files"] = []
        index_path.write_text(json.dumps(index))

    with pytest.raises(LocalGenerationError):
        promote(tmp_path, attempt_id, region="france", year=2025,
                context=({"fixture": "invalid-B", "cluster_parameters": cluster_parameters}
                         if clusters else {"fixture": "invalid-B"}), intended_tiles=intended,
                clusters=clusters, cluster_parameters=cluster_parameters)
    if active_exists:
        assert (tmp_path / "current.json").read_bytes() == pointer_before
        assert resolve_complete(tmp_path).generation_id == parent.generation_id
        assert {p.relative_to(parent.path): p.read_bytes()
                for p in parent.path.rglob("*") if p.is_file()} == payload_before
    else:
        assert not (tmp_path / "current.json").exists()


@pytest.mark.parametrize(("checkpoint", "expect_b"), [
    ("before-pointer-replace", False), ("pointer-replaced", True),
])
def test_sigkill_at_activation_boundary_is_old_or_new_complete(tmp_path, checkpoint, expect_b):
    a = _candidate(tmp_path, "A")
    script = r'''
import json, sys
from pathlib import Path
import src.local_generation as g
root = Path(sys.argv[1])
stage = sys.argv[2]
original = g._checkpoint
def stop(name):
    if name == stage:
        print(name, flush=True)
        sys.stdin.readline()
g._checkpoint = stop
attempt_id, attempt = g.new_attempt(root, "france", 2025)
spots = attempt / "package" / "spots"
spots.mkdir()
(spots / "N048E002.json").write_text(json.dumps({"version":"2025.0","source":"B","generated":"B","tile":"N048E002","spots":[]}))
g.promote(root, attempt_id, region="france", year=2025, context={"fixture":"B"}, intended_tiles=["N048E002"])
'''
    proc = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), checkpoint],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, cwd=Path(__file__).resolve().parents[2])
    assert proc.stdout.readline().strip() == checkpoint
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    active = resolve_complete(tmp_path)
    assert (active.generation_id != a.generation_id) is expect_b
    assert (active.path / "spots/N048E002.json").read_bytes()
    # The OS releases the persistent advisory lock after SIGKILL.
    retry_id, retry = new_attempt(tmp_path, "france", 2025)
    assert retry_id != a.generation_id


@pytest.mark.parametrize("checkpoint", [
    "output-created", "lock-created", "identity-partial", "identity-temp-closed",
    "identity-installed", "before-attempt",
])
def test_sigkill_bootstrap_retries_same_directory(tmp_path, checkpoint):
    script = r'''
import sys
from pathlib import Path
import src.local_generation as g
root = Path(sys.argv[1]); stage = sys.argv[2]
def stop(name):
    if name == stage:
        print(name, flush=True); sys.stdin.readline()
g._checkpoint = stop
g.new_attempt(root, "france", 2025)
'''
    proc = subprocess.Popen([sys.executable, "-c", script, str(tmp_path), checkpoint],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, cwd=Path(__file__).resolve().parents[2])
    assert proc.stdout.readline().strip() == checkpoint
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    identity = initialize(tmp_path, "france", 2025)
    assert identity == {"schema": 1, "region": "france", "year": 2025}
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    assert attempt_id and (attempt / "package").is_dir()


def test_resolver_rejects_payload_corruption(tmp_path):
    ref = _candidate(tmp_path, "A")
    (ref.path / "spots/N048E002.json").write_text("{}")
    with pytest.raises(LocalGenerationError, match="inventory/hash"):
        resolve_complete(tmp_path)


def test_derivative_copy_preserves_parent_payloads(tmp_path, monkeypatch):
    import argparse
    import run
    a = _candidate(tmp_path, "A")
    original = {p.relative_to(a.path): p.read_bytes() for p in a.path.rglob("*")
                if p.is_file() and p.name not in {"validation.json", "generation.json"}}
    args = argparse.Namespace(no_clusters=False, year=2025, region=None)
    monkeypatch.setattr(run, "load_regions", lambda: (_ for _ in ()).throw(AssertionError("regional configuration consulted")))
    run._regenerate_local_clusters(args, tmp_path)
    b = resolve_complete(tmp_path)
    assert b.generation_id != a.generation_id
    assert b.manifest["parent"] == a.generation_id
    for name, content in original.items():
        assert (b.path / name).read_bytes() == content
    assert (b.path / "clusters-local/index.json").is_file()


@pytest.mark.parametrize("failure", ["validation", "manifest", "candidate-install", "pointer-preparation"])
def test_pre_activation_failures_preserve_active_package(tmp_path, monkeypatch, failure):
    import src.local_generation as generations
    a = _candidate(tmp_path, "A")
    pointer = (tmp_path / "current.json").read_bytes()
    original_replace = generations.os.replace
    if failure == "validation":
        monkeypatch.setattr(generations, "validate_package",
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("injected")))
    elif failure == "manifest":
        original_checkpoint = generations._checkpoint
        def checkpoint(name):
            if name == "manifest-written":
                raise OSError("injected manifest boundary")
            original_checkpoint(name)
        monkeypatch.setattr(generations, "_checkpoint", checkpoint)
    elif failure == "candidate-install":
        def replace(source, destination):
            if Path(destination).parent.name == "generations":
                raise OSError("injected candidate install")
            original_replace(source, destination)
        monkeypatch.setattr(generations.os, "replace", replace)
    else:
        original_checkpoint = generations._checkpoint
        def checkpoint(name):
            if name == "pointer-prepared":
                raise OSError("injected pointer preparation")
            original_checkpoint(name)
        monkeypatch.setattr(generations, "_checkpoint", checkpoint)
    attempt_id, attempt = new_attempt(tmp_path, "france", 2025)
    spots = attempt / "package/spots"
    spots.mkdir()
    (spots / "N048E002.json").write_text(json.dumps({
        "version": "2025.0", "source": "B", "generated": "B",
        "tile": "N048E002", "spots": [],
    }))
    with pytest.raises((ValueError, OSError)):
        generations.promote(tmp_path, attempt_id, region="france", year=2025,
                            context={"fixture": "B"}, intended_tiles=["N048E002"])
    monkeypatch.undo()
    assert (tmp_path / "current.json").read_bytes() == pointer
    assert resolve_complete(tmp_path).generation_id == a.generation_id
