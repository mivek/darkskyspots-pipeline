"""Atomic, retained local output generations for one region and data year.

This module provides process-interruption safety on local POSIX filesystems.
It deliberately does not promise power-loss durability (there is no fsync
protocol) and never chooses a generation by timestamp.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import uuid
from dataclasses import dataclass

SCHEMA = 1
_ID = re.compile(r"^[0-9]{4}-[0-9a-f]{32}$")
_IDENTITY_TMP = re.compile(r"^\.identity\.[0-9a-f]{32}\.tmp$")
_HELD_LOCKS: dict[str, tuple[int, int]] = {}


def _checkpoint(name: str) -> None:
    """Test seam for deterministic subprocess interruption; inert in production."""


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@dataclass(frozen=True)
class GenerationRef:
    output_dir: Path
    generation_id: str
    path: Path
    manifest: dict


class LocalGenerationError(RuntimeError):
    pass


def reject_managed_destination(path) -> None:
    """Fail before a legacy mutator touches a managed generation root/package."""
    target = Path(path).resolve()
    for parent in (target, *target.parents):
        if (parent / "identity.json").is_file() or (parent / "generation.json").is_file():
            raise LocalGenerationError(f"Refusing mutation of managed generation destination: {target}")


def _classify(root: Path) -> None:
    if not root.exists():
        return
    if not root.is_dir() or root.is_symlink():
        raise LocalGenerationError(f"Output destination is not a regular directory: {root}")
    names = {p.name for p in root.iterdir()}
    if "identity.json" in names:
        identity_path = root / "identity.json"
        if identity_path.is_symlink() or not identity_path.is_file():
            raise LocalGenerationError(f"Unsafe established identity: {identity_path}")
        return
    allowed = {".writer.lock"} if ".writer.lock" in names else set()
    allowed |= {n for n in names if _IDENTITY_TMP.fullmatch(n)}
    for name in allowed:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise LocalGenerationError(f"Unsafe bootstrap entry: {p}")
        if name == ".writer.lock" and p.stat().st_size:
            raise LocalGenerationError(f"Writer lock must be empty: {p}")
    unexpected = names - allowed
    if unexpected:
        raise LocalGenerationError(
            f"Refusing unmanaged nonempty output directory {root}; use a fresh dedicated --output-dir"
        )


@contextmanager
def writer_lock(output_dir):
    root = Path(output_dir)
    key = str(root.resolve())
    held = _HELD_LOCKS.get(key)
    if held is not None:
        fd, depth = held
        _HELD_LOCKS[key] = (fd, depth + 1)
        try:
            yield fd
        finally:
            current_fd, current_depth = _HELD_LOCKS[key]
            _HELD_LOCKS[key] = (current_fd, current_depth - 1)
        return
    _classify(root)
    root.mkdir(parents=True, exist_ok=True)
    _checkpoint("output-created")
    lock = root / ".writer.lock"
    if lock.is_symlink():
        raise LocalGenerationError(f"Refusing symlink writer lock: {lock}")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _checkpoint("lock-created")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalGenerationError(f"Another writer holds {lock}") from exc
        _classify(root)
        _HELD_LOCKS[key] = (fd, 1)
        yield fd
    finally:
        _HELD_LOCKS.pop(key, None)
        os.close(fd)


def initialize(output_dir, region: str, year: int) -> dict:
    root = Path(output_dir)
    with writer_lock(root):
        identity_path = root / "identity.json"
        if identity_path.exists():
            try:
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LocalGenerationError("Malformed established identity; refusing repair") from exc
            if identity.get("schema") != SCHEMA:
                raise LocalGenerationError("Unsupported local output identity schema")
            if identity.get("region") != region or identity.get("year") != year:
                raise LocalGenerationError("Output identity does not match requested region/year")
            return identity
        if any((root / name).exists() for name in ("attempts", "generations", "current.json")):
            raise LocalGenerationError("Established output artifacts exist without identity; refusing repair")
        identity = {"schema": SCHEMA, "region": region, "year": year}
        tmp = root / f".identity.{uuid.uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        contents = _json_bytes(identity)
        with os.fdopen(fd, "wb") as stream:
            midpoint = max(1, len(contents) // 2)
            stream.write(contents[:midpoint])
            _checkpoint("identity-partial")
            stream.write(contents[midpoint:])
        _checkpoint("identity-temp-closed")
        os.replace(tmp, identity_path)
        _checkpoint("identity-installed")
        return identity


def new_attempt(output_dir, region: str, year: int, *, parent: str | None = None) -> tuple[str, Path]:
    root = Path(output_dir)
    identity = initialize(root, region, year)
    _checkpoint("before-attempt")
    attempt_id = f"{year}-{uuid.uuid4().hex}"
    attempt = root / "attempts" / attempt_id
    (attempt / "package").mkdir(parents=True)
    (attempt / "attempt.json").write_bytes(_json_bytes({"schema": 1, "id": attempt_id, "region": identity["region"], "year": year, "parent": parent, "status": "building"}))
    return attempt_id, attempt


def _inventory(package: Path) -> dict[str, str]:
    result = {}
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise LocalGenerationError(f"Symlink in candidate package: {path}")
        if path.is_file() and path != package / "generation.json":
            rel = path.relative_to(package).as_posix()
            if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
                raise LocalGenerationError(f"Unsafe package path: {rel}")
            result[rel] = _sha(path)
    return result


def validate_package(package, *, intended_tiles=None, clusters=False, year=None,
                     cluster_parameters=None) -> dict:
    package = Path(package)
    inventory = _inventory(package)
    spots = package / "spots"
    if not spots.is_dir():
        raise LocalGenerationError("Candidate is missing spots/")
    if any(path.is_file() and (path.parent != spots or path.suffix != ".json")
           for path in spots.rglob("*")):
        raise LocalGenerationError("Spot directory contains unexpected payload paths")
    tiles = sorted(p.stem for p in spots.glob("*.json"))
    if intended_tiles is not None and tiles != sorted(intended_tiles):
        raise LocalGenerationError("Spot tile inventory does not match recorded producer contract")
    spot_ids = set()
    for tile in tiles:
        payload = json.loads((spots / f"{tile}.json").read_text(encoding="utf-8"))
        if (payload.get("tile") != tile or not isinstance(payload.get("spots"), list)
                or not all(isinstance(payload.get(field), str) and payload[field]
                           for field in ("version", "source", "generated"))):
            raise LocalGenerationError(f"Invalid public spot envelope: {tile}")
        from .clusters import _normalize_spot
        for index, spot in enumerate(payload["spots"]):
            _normalize_spot(spot, spots / f"{tile}.json", index)
            spot_id = spot.get("id") if isinstance(spot, dict) else None
            if not isinstance(spot_id, str) or not spot_id:
                raise LocalGenerationError(f"Invalid spot ID in {tile}")
            if spot_id in spot_ids:
                raise LocalGenerationError(f"Duplicate spot ID in candidate package: {spot_id}")
            spot_ids.add(spot_id)
            accessibility = spot.get("accessibility")
            if accessibility is not None:
                source_id = accessibility.get("source")
                if not isinstance(source_id, str) or Path(source_id).name != source_id:
                    raise LocalGenerationError("Unsafe accessibility provenance reference")
                source_path = package / "accessibility" / "sources" / f"{source_id}.json"
                if not source_path.is_file():
                    raise LocalGenerationError(f"Missing accessibility provenance record: {source_id}")
                source_record = json.loads(source_path.read_text(encoding="utf-8"))
                if source_record.get("source_id") != source_id:
                    raise LocalGenerationError(f"Accessibility provenance identity mismatch: {source_id}")
    cluster_dir = package / "clusters-local"
    if clusters and not cluster_dir.is_dir():
        raise LocalGenerationError("Requested local clusters are missing")
    if not clusters and cluster_dir.exists():
        raise LocalGenerationError("Unexpected clusters-local payload when clusters are disabled")
    if clusters:
        if not isinstance(cluster_parameters, list) or len(cluster_parameters) != 6:
            raise LocalGenerationError("Cluster parameters must record all six levels")
        from .clusters import LevelSpec
        try:
            recorded_levels = tuple(
                LevelSpec(int(item["level"]), float(item["cell_deg"]),
                          tuple(int(value) for value in item["width_km"]))
                for item in cluster_parameters
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalGenerationError("Malformed recorded cluster parameters") from exc
        if ([spec.level for spec in recorded_levels] != list(range(1, 7))
                or any(len(spec.width_km) != 2 or spec.cell_deg <= 0 for spec in recorded_levels)):
            raise LocalGenerationError("Invalid recorded cluster parameters")
        index = json.loads((package / "clusters-local" / "index.json").read_text(encoding="utf-8"))
        levels = index.get("levels", [])
        if (index.get("schema") != 1 or [item.get("level") for item in levels] != list(range(1, 7))
                or (year is not None and index.get("data_year") != year)):
            raise LocalGenerationError("Cluster index must describe all six levels")
        index_parameters = [
            {"level": item.get("level"), "cell_deg": item.get("cell_deg"),
             "width_km": item.get("width_km")}
            for item in levels
        ]
        if index_parameters != cluster_parameters:
            raise LocalGenerationError("Cluster index parameters contradict the generation manifest")
        expected_cluster_files = {"index.json"}
        for level_number, level in enumerate(levels, start=1):
            entries = level.get("files")
            expected_name = f"L{level_number}.json"
            if not isinstance(entries, list) or len(entries) != 1:
                raise LocalGenerationError(f"Cluster level L{level_number} must reference exactly one file")
            entry = entries[0]
            if entry.get("path") != expected_name:
                raise LocalGenerationError(f"Cluster level L{level_number} references an invalid file")
            expected_cluster_files.add(expected_name)
            path = cluster_dir / expected_name
            if not path.is_file() or _sha(path) != entry.get("hash"):
                raise LocalGenerationError("Cluster index hash mismatch")
        if any(p.is_dir() for p in cluster_dir.iterdir()):
            raise LocalGenerationError("Cluster directory contains an unexplained subdirectory")
        actual_cluster_files = {p.name for p in cluster_dir.iterdir() if p.is_file()}
        if actual_cluster_files != expected_cluster_files:
            raise LocalGenerationError("Cluster directory contains missing or unexplained files")
        from .clusters import aggregate_spot_files
        aggregated = aggregate_spot_files(spots, levels=recorded_levels)
        for level in range(1, 7):
            payload = json.loads((package / "clusters-local" / f"L{level}.json").read_text(encoding="utf-8"))
            if payload != aggregated[level]:
                raise LocalGenerationError(f"Cluster level L{level} does not aggregate the package spots")
    for relative in inventory:
        parts = PurePosixPath(relative).parts
        recognized = (
            (len(parts) == 2 and parts[0] == "spots" and parts[1].endswith(".json"))
            or relative == "validation.json"
            or relative == "reviewed-water-rejections.jsonl"
            or (len(parts) == 3 and parts[:2] == ("accessibility", "sources") and parts[2].endswith(".json"))
            or (clusters and len(parts) == 2 and parts[0] == "clusters-local"
                and parts[1] in {"index.json", *(f"L{level}.json" for level in range(1, 7))})
        )
        if not recognized:
            raise LocalGenerationError(f"Unrecognized candidate payload path: {relative}")
        if len(parts) == 3 and parts[:2] == ("accessibility", "sources"):
            try:
                source_record = json.loads((package / relative).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LocalGenerationError(f"Invalid accessibility provenance payload: {relative}") from exc
            if (not isinstance(source_record, dict) or source_record.get("schema") != 1
                    or source_record.get("source_id") != Path(parts[2]).stem):
                raise LocalGenerationError(f"Invalid accessibility provenance payload: {relative}")
    return inventory


def _generation_ref(root: Path, generation_id: str) -> GenerationRef:
    """Validate one retained generation without consulting current.json."""
    if not isinstance(generation_id, str) or not _ID.fullmatch(generation_id):
        raise LocalGenerationError("Invalid generation ID")
    path = root / "generations" / generation_id
    manifest_path = path / "generation.json"
    if not path.is_dir() or path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise LocalGenerationError("Generation package or manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = json.loads((root / "identity.json").read_text(encoding="utf-8"))
    if (identity.get("schema") != SCHEMA or manifest.get("schema") != SCHEMA
            or manifest.get("id") != generation_id
            or manifest.get("region") != identity.get("region")
            or manifest.get("year") != identity.get("year")):
        raise LocalGenerationError("Generation manifest identity mismatch")
    expected = manifest.get("files")
    actual = _inventory(path)
    if not isinstance(expected, dict) or expected != actual:
        raise LocalGenerationError("Generation payload inventory/hash mismatch")
    validate_package(path, intended_tiles=manifest.get("intended_tiles", []),
                     clusters=(path / "clusters-local").is_dir(), year=manifest.get("year"),
                     cluster_parameters=manifest.get("cluster_parameters"))
    return GenerationRef(root, generation_id, path, manifest)


def promote(output_dir, attempt_id: str, *, region: str, year: int, context: dict,
            intended_tiles=None, clusters=False, cluster_parameters=None,
            parent: GenerationRef | None = None) -> GenerationRef:
    root = Path(output_dir)
    with writer_lock(root):
        identity_path = root / "identity.json"
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LocalGenerationError("Malformed established identity; refusing repair") from exc
        if identity.get("schema") != SCHEMA:
            raise LocalGenerationError("Unsupported local output identity schema")
        if identity.get("region") != region or identity.get("year") != year:
            raise LocalGenerationError("Output identity does not match requested region/year")
        if clusters:
            context_parameters = context.get("cluster_parameters")
            if context_parameters is None:
                operation = context.get("cluster_operation", {})
                context_parameters = operation.get("levels")
            if context_parameters != cluster_parameters:
                raise LocalGenerationError("Generation context contradicts recorded cluster parameters")
        attempt = root / "attempts" / attempt_id
        candidate = attempt / "package"
        inventory = validate_package(candidate, intended_tiles=intended_tiles, clusters=clusters,
                                     year=year, cluster_parameters=cluster_parameters)
        _checkpoint("package-validated")
        generation_id = attempt_id
        validation = {"schema": 1, "inventory_count": len(inventory), "valid": True}
        (candidate / "validation.json").write_bytes(_json_bytes(validation))
        inventory = _inventory(candidate)
        manifest = {"schema": 1, "id": generation_id, "region": region, "year": year,
                    "parent": parent.generation_id if parent else None,
                    "context": context,
                    "cluster_parameters": cluster_parameters if clusters else None,
                    "intended_tiles": sorted(intended_tiles if intended_tiles is not None else inventory),
                    "files": inventory}
        (candidate / "generation.json").write_bytes(_json_bytes(manifest))
        _checkpoint("manifest-written")
        generations = root / "generations"
        generations.mkdir(exist_ok=True)
        target = generations / generation_id
        os.replace(candidate, target)
        _checkpoint("candidate-installed")
        pointer = {"schema": 1, "generation": generation_id, "manifest_sha256": _sha(target / "generation.json")}
        tmp = root / f".current.{uuid.uuid4().hex}.tmp"
        with tmp.open("xb") as stream:
            stream.write(_json_bytes(pointer))
        _checkpoint("pointer-prepared")
        _checkpoint("before-pointer-replace")
        os.replace(tmp, root / "current.json")
        _checkpoint("pointer-replaced")
        try:
            status = json.loads((attempt / "attempt.json").read_text())
            status["status"] = "activated"
            (attempt / "attempt.json").write_bytes(_json_bytes(status))
        except Exception:
            pass
        return GenerationRef(root, generation_id, target, manifest)


def resolve_complete(output_dir) -> GenerationRef:
    root = Path(output_dir)
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    if pointer.get("schema") != SCHEMA:
        raise LocalGenerationError("Unsupported current generation pointer schema")
    generation_id = pointer.get("generation")
    if not isinstance(generation_id, str) or not _ID.fullmatch(generation_id):
        raise LocalGenerationError("Invalid current generation pointer")
    path = root / "generations" / generation_id
    manifest_path = path / "generation.json"
    if not path.is_dir() or path.is_symlink() or _sha(manifest_path) != pointer.get("manifest_sha256"):
        raise LocalGenerationError("Current generation manifest is missing or corrupt")
    ref = _generation_ref(root, generation_id)
    return ref


def resolve_spots_dir(path) -> Path:
    """Pin a managed root or finalized package; reject unsupported descendants."""
    supplied = Path(path).resolve()
    if (supplied / "identity.json").is_file():
        return resolve_complete(supplied).path / "spots"
    for root in supplied.parents:
        if (root / "identity.json").is_file():
            relative = supplied.relative_to(root)
            parts = relative.parts
            if parts == ("generations",):
                raise LocalGenerationError("The generations history directory is not a readable package")
            if len(parts) == 2 and parts[0] == "generations":
                return _generation_ref(root, parts[1]).path / "spots"
            if len(parts) == 3 and parts[0] == "generations" and parts[2] == "spots":
                return _generation_ref(root, parts[1]).path / "spots"
            raise LocalGenerationError(f"Unsupported path inside managed output: {supplied}")
    return supplied
