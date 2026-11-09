"""Authentication of the approved published baseline used for G2 qualification."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


APPROVED_BASELINE_REPOSITORY = "mivek/darkskyspots-data"
APPROVED_BASELINE_COMMIT = "312bc366ccef674a232bde2f0a3a42067d083d19"
APPROVED_G2_INVENTORY_SHA256 = "90135ea586d26e2eb38c36476862d6dd45d4c62982923e902b1d55149aa80880"
APPROVED_G2_SPOT_COUNT = 1217


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _inventory_path(baseline_root: Path, explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    package_inventory = baseline_root / "source-inventory.json"
    adjacent_inventory = baseline_root.with_name(baseline_root.name + "-inventory.json")
    if package_inventory.is_file():
        return package_inventory
    return adjacent_inventory


def _safe_inventory_records(inventory: Any) -> dict[str, tuple[int, str]]:
    if (not isinstance(inventory, dict)
            or inventory.get("commit") != APPROVED_BASELINE_COMMIT
            or inventory.get("repository") != APPROVED_BASELINE_REPOSITORY
            or not isinstance(inventory.get("files"), dict)):
        raise ValueError("Approved baseline inventory has an invalid repository, commit, or file map")
    records: dict[str, tuple[int, str]] = {}
    for relative, item in inventory["files"].items():
        if not isinstance(relative, str) or not isinstance(item, dict):
            raise ValueError(f"Invalid approved baseline inventory entry: {relative!r}")
        relative_path = Path(relative)
        if (relative_path.is_absolute() or ".." in relative_path.parts
                or relative_path.as_posix() != relative):
            raise ValueError(f"Unsafe approved baseline path: {relative!r}")
        size, sha = item.get("size"), item.get("sha256")
        if (type(size) is not int or size < 0 or not isinstance(sha, str)
                or len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha)):
            raise ValueError(f"Invalid approved baseline size or hash: {relative}")
        records[relative] = size, sha
    if (not any(path.startswith("spots/") for path in records)
            or not any(path.startswith("clusters/") for path in records)
            or "clusters/index.json" not in records):
        raise ValueError("Approved baseline inventory is missing spot or cluster evidence")
    return records


def parse_inventory_bytes(
    payload: bytes,
    expected_sha256: str,
    expected_commit: str,
    expected_repository: str = APPROVED_BASELINE_REPOSITORY,
) -> tuple[dict[str, Any], dict[str, tuple[int, str]]]:
    """Hash and parse a complete immutable repository file inventory."""
    actual = _sha256_bytes(payload)
    if actual != expected_sha256:
        raise ValueError(f"Frozen baseline inventory checksum mismatch: {actual}")
    try:
        inventory = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Frozen baseline inventory is invalid JSON") from exc
    if (not isinstance(inventory, dict) or inventory.get("commit") != expected_commit
            or inventory.get("repository") != expected_repository):
        raise ValueError("Frozen baseline inventory repository or commit is invalid")
    return inventory, _safe_inventory_records(inventory)


def _parse_published_spots(payloads: dict[str, bytes], expected_g2_count: int) -> list[dict[str, Any]]:
    all_ids = set()
    g2_spots = []
    for relative, payload in payloads.items():
        if not relative.startswith("spots/") or not relative.endswith(".json"):
            continue
        try:
            envelope = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Approved spot tile is not valid JSON: {relative}") from exc
        spots = envelope.get("spots") if isinstance(envelope, dict) else None
        if not isinstance(spots, list):
            raise ValueError(f"Approved spot tile has no spots array: {relative}")
        for spot in spots:
            if not isinstance(spot, dict) or not isinstance(spot.get("id"), str) or not spot["id"]:
                raise ValueError(f"Approved spot has no valid ID: {relative}")
            spot_id = spot["id"]
            if spot_id in all_ids:
                raise ValueError(f"Duplicate published spot ID in approved baseline: {spot_id}")
            all_ids.add(spot_id)
            country = str(spot.get("country", "")).upper()
            if country in {"GB", "IE"}:
                for key in ("lat", "lon"):
                    value = spot.get(key)
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value)):
                        raise ValueError(f"Published GB/IE spot has invalid {key}: {spot_id}")
                g2_spots.append(spot)
    if len(g2_spots) != expected_g2_count:
        raise ValueError(
            f"Approved baseline contains {len(g2_spots)} GB/IE spots; expected {expected_g2_count}"
        )
    return g2_spots


def validate_approved_g2_baseline(
    baseline_spots_dir: str | Path,
    inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the pinned repository snapshot, all bytes, and complete G2 spot set.

    Returned spot records come from the exact bytes validated here.
    """
    spots_dir = Path(baseline_spots_dir).resolve()
    baseline_root = spots_dir.parent
    if spots_dir.name != "spots" or not baseline_root.is_dir():
        raise ValueError(f"Invalid frozen baseline package path: {spots_dir}")
    trusted_inventory_sha = APPROVED_G2_INVENTORY_SHA256
    inventory_file = _inventory_path(baseline_root, inventory_path)
    if not inventory_file.is_file():
        raise FileNotFoundError(f"Trusted approved baseline inventory is required: {inventory_file}")
    inventory_bytes = inventory_file.read_bytes()
    actual_inventory_sha = _sha256_bytes(inventory_bytes)
    if actual_inventory_sha != trusted_inventory_sha:
        raise ValueError(f"Approved baseline inventory SHA-256 mismatch: {actual_inventory_sha}")
    inventory, records = parse_inventory_bytes(
        inventory_bytes, APPROVED_G2_INVENTORY_SHA256,
        APPROVED_BASELINE_COMMIT, APPROVED_BASELINE_REPOSITORY,
    )

    baseline_manifest_path = baseline_root / "baseline.json"
    if not baseline_manifest_path.is_file() or baseline_manifest_path.is_symlink():
        raise ValueError("Frozen baseline package has no regular baseline.json")
    try:
        baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Frozen baseline manifest is invalid: {exc}") from exc
    if (not isinstance(baseline_manifest, dict)
            or baseline_manifest.get("repository") != APPROVED_BASELINE_REPOSITORY
            or baseline_manifest.get("commit") != APPROVED_BASELINE_COMMIT
            or baseline_manifest.get("inventory_sha256") != actual_inventory_sha
            or baseline_manifest.get("g2_spot_records") != APPROVED_G2_SPOT_COUNT):
        raise ValueError("Frozen baseline package does not identify the approved complete G2 snapshot")
    manifest_files = baseline_manifest.get("files")
    if not isinstance(manifest_files, dict) or set(manifest_files) != set(records):
        raise ValueError("Frozen baseline file inventory differs from the trusted approved inventory")
    for relative, (size, sha) in records.items():
        item = manifest_files.get(relative)
        if (not isinstance(item, dict) or item.get("size") != size or item.get("sha256") != sha):
            raise ValueError(f"Frozen baseline manifest contradicts trusted source inventory: {relative}")

    expected_files = set(records) | {"baseline.json"}
    if inventory_file == baseline_root / "source-inventory.json":
        expected_files.add("source-inventory.json")
    actual_files = set()
    payloads = {}
    for path in baseline_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink in frozen baseline package: {path.relative_to(baseline_root)}")
        if path.is_file():
            actual_files.add(path.relative_to(baseline_root).as_posix())
    if actual_files != expected_files:
        raise ValueError(
            "Frozen baseline package file set differs from the trusted inventory: "
            f"missing={sorted(expected_files - actual_files)}, extra={sorted(actual_files - expected_files)}"
        )
    for relative, (size, sha) in records.items():
        path = (baseline_root / relative).resolve()
        if not path.is_relative_to(baseline_root) or not path.is_file():
            raise ValueError(f"Missing required frozen baseline file: {relative}")
        payload = path.read_bytes()
        if len(payload) != size or _sha256_bytes(payload) != sha:
            raise ValueError(f"Frozen baseline integrity mismatch against approved inventory: {relative}")
        payloads[relative] = payload

    spot_tiles = {path for path in records if path.startswith("spots/") and path.endswith(".json")}
    if not spot_tiles or len(spot_tiles) != sum(path.startswith("spots/") for path in records):
        raise ValueError("Trusted baseline inventory contains non-JSON or missing spot tiles")
    cluster_files = {path for path in records if path.startswith("clusters/")}
    if not cluster_files or "clusters/index.json" not in cluster_files:
        raise ValueError("Trusted baseline inventory has incomplete cluster evidence")
    g2_spots = _parse_published_spots(payloads, APPROVED_G2_SPOT_COUNT)
    return {
        "repository": APPROVED_BASELINE_REPOSITORY,
        "commit": APPROVED_BASELINE_COMMIT,
        "inventory_sha256": actual_inventory_sha,
        "files": records,
        "spot_tiles": sorted(spot_tiles),
        "cluster_files": sorted(cluster_files),
        "g2_spots": g2_spots,
    }


__all__ = [
    "APPROVED_BASELINE_REPOSITORY", "APPROVED_BASELINE_COMMIT",
    "APPROVED_G2_INVENTORY_SHA256", "APPROVED_G2_SPOT_COUNT",
    "parse_inventory_bytes", "validate_approved_g2_baseline",
]
