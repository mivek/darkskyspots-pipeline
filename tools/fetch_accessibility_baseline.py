#!/usr/bin/env python3
"""Freeze the reconciled Canary tiles and clusters from the pinned data commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY = "mivek/darkskyspots-data"
COMMIT = "312bc366ccef674a232bde2f0a3a42067d083d19"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.accessibility_baseline import parse_inventory_bytes

BASE = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}"
FILES = {
    "spots/N027W016.json": (8, "d8aae8127215f4bd61ceebd212c1caed5e3f0709be162234fd0ca52d4af2ee6f"),
    "spots/N027W018.json": (3, "a4002fbdecfd6830be74643bc5785a95d8e640b359b1bde23024b6c7b624dfa3"),
    "spots/N027W019.json": (1, "ed8f9ac6c86c7b499f5f94e374e04b06329b05324a7e0e474bb0a0ca3daa9ea5"),
    "spots/N028W014.json": (5, "c81dc53e04d03b2a2316bfc9acad740c67ce948e4149ace0e71b2f17738ae308"),
    "spots/N028W015.json": (9, "e96516fc15c21ea290b3596bdaa1e9bf143d3dc8ebe9c8fdadcb12a4f6217d21"),
    "spots/N028W016.json": (5, "3dd78745b22a41b77057849c7050c6ba820aafd2066537ff63d0ae49a5e72a52"),
    "spots/N028W017.json": (16, "01e1b4d340a6ac1d4aadb6149aa2621fbfae67bb74f67b2fc6b22a835e1099fe"),
    "spots/N028W018.json": (11, "7f0591d5815b9e73297349e555546bb2da3278d25e3d17a257ad184429ee33c4"),
    "spots/N029W014.json": (8, "0caaf4b84fce94dda288042a32e67a34ec936eb76dadd34a79259d4a091c31c1"),
    "clusters/L1.json": (26, "1c843f0169d744e1e07d94e03f9a1c642eaf81e55c1b35aee72dc982e24ebded"),
    "clusters/L2.json": (16, "d85e6e0579c5c9b102f1ad9c2b7ce2eac1c1d84e43a0c0fb60510bc0ed217387"),
    "clusters/L3.json": (7, "2507b1c475f4dbba2b8158a478a719fca8d131cb4f26bc3e24f56387bc911792"),
    "clusters/L4.json": (5, "8f617be92de54eb46f76044aef0af7d209b90698be6dbabe0a36afcf19ef89d6"),
    "clusters/L5.json": (4, "b84159d6f46004012cd57788ea10464edd33e15578d416aded4e9c5ecd56c5d9"),
    "clusters/L6.json": (2, "aa0f25365dae7e0284c34c4ebcd2fbd0ec4919451853715642c1c15b865f028a"),
    "clusters/index.json": (None, "f8319bfd5d2b6fce6f7413eadf7dbf5a29b78afb3f373758878745dd9ec67014"),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(path: str, expected: str, commit: str = COMMIT) -> bytes:
    base = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}"
    request = Request(f"{base}/{path}", headers={"User-Agent": "darkskyspots-accessibility-baseline/2"})
    with urlopen(request, timeout=60) as response:
        payload = response.read()
    actual = digest(payload)
    if actual != expected:
        raise ValueError(f"Pinned baseline checksum mismatch for {path}: {actual}")
    return payload


def _frozen_inventory(path: Path, expected_sha256: str, commit: str) -> dict[str, dict]:
    payload = path.read_bytes()
    _inventory, records = parse_inventory_bytes(payload, expected_sha256, commit, REPOSITORY)
    return records


def _inventory_records(stage: Path, expected_files: dict[str, tuple[int, str]]) -> dict:
    files = {}
    spot_ids = set()
    g2_ids = set()
    country_counts = {}
    for relative, (size, sha) in expected_files.items():
        payload = (stage / relative).read_bytes()
        if len(payload) != size or digest(payload) != sha:
            raise ValueError(f"Frozen baseline integrity mismatch after acquisition: {relative}")
        item = {"sha256": sha, "size": size}
        if relative.startswith("spots/") and relative.endswith(".json"):
            envelope = json.loads(payload)
            spots = envelope.get("spots")
            if not isinstance(spots, list):
                raise ValueError(f"Frozen spot tile has no spot array: {relative}")
            item["actual_records"] = len(spots)
            item["expected_spot_records"] = len(spots)
            for spot in spots:
                if not isinstance(spot.get("id"), str):
                    raise ValueError(f"Frozen spot is missing its string ID: {relative}")
                spot_ids.add(spot["id"])
                country = str(spot.get("country", "")).upper()
                country_counts[country] = country_counts.get(country, 0) + 1
                if country in {"GB", "IE"}:
                    g2_ids.add(spot["id"])
        elif relative.startswith("clusters/") and relative.endswith(".json") and relative != "clusters/index.json":
            clusters = json.loads(payload)
            if not isinstance(clusters, list):
                raise ValueError(f"Frozen cluster file is not an array: {relative}")
            item["actual_records"] = len(clusters)
            item["cluster_representatives"] = [record.get("rep", {}).get("id") for record in clusters]
        files[relative] = item
    # Recompute representative counts after the full spot ID inventory is known.
    for relative, item in files.items():
        if "cluster_representatives" in item:
            representatives = item.pop("cluster_representatives")
            item["g2_representatives"] = sum(value in g2_ids for value in representatives)
            item["published_representatives"] = sum(value in spot_ids for value in representatives)
    return {"files": files, "spot_ids": spot_ids, "g2_ids": g2_ids, "country_counts": country_counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=COMMIT, help="Immutable data-repository commit")
    parser.add_argument("--inventory", type=Path, help="Verified immutable repository file inventory JSON")
    parser.add_argument("--inventory-sha256", help="Required SHA-256 pin for --inventory")
    parser.add_argument("--output", type=Path,
                        default=None)
    args = parser.parse_args()
    commit = args.commit
    if len(commit) != 40 or any(value not in "0123456789abcdef" for value in commit.lower()):
        raise ValueError("--commit must be a full 40-character hexadecimal SHA")
    inventory_mode = args.inventory is not None
    if inventory_mode and not args.inventory_sha256:
        raise ValueError("--inventory-sha256 is required with --inventory")
    if not inventory_mode and args.commit != COMMIT:
        raise ValueError("A non-default commit requires a verified --inventory")
    expected_files = ( _frozen_inventory(args.inventory, args.inventory_sha256, commit)
                       if inventory_mode else {path: (size, sha) for path, (size, sha) in FILES.items()} )
    inventory_payload = None
    if inventory_mode:
        inventory_payload = args.inventory.read_bytes()
        if digest(inventory_payload) != args.inventory_sha256:
            raise ValueError("Frozen baseline inventory changed during acquisition setup")
    output_arg = args.output or ROOT / "input/accessibility/baselines" / commit
    output = output_arg.resolve()
    ignored_root = (ROOT / "input/accessibility/baselines").resolve()
    if not output.is_relative_to(ignored_root):
        raise ValueError(f"Baseline must be stored under ignored input/: {output}")
    if output.exists():
        raise FileExistsError(f"Immutable baseline already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{commit}-", dir=output.parent))
    try:
        records = {}
        if inventory_mode:
            for relative, (expected_size, expected_hash) in expected_files.items():
                payload = fetch(relative, expected_hash, commit)
                if len(payload) != expected_size:
                    raise ValueError(f"Pinned baseline size mismatch for {relative}: {len(payload)}")
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            derived = _inventory_records(stage, expected_files)
            records = derived["files"]
            manifest_extra = {
                "inventory_sha256": args.inventory_sha256,
                "country_counts": derived["country_counts"],
                "spot_records": len(derived["spot_ids"]),
                "g2_spot_records": len(derived["g2_ids"]),
                "g2_representative_records": sum(
                    info.get("g2_representatives", 0) for path, info in records.items()
                    if path.startswith("clusters/") and path != "clusters/index.json"
                ),
            }
        else:
            for relative, (count, expected_hash) in FILES.items():
                payload = fetch(relative, expected_hash, commit)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                item = {"sha256": expected_hash, "size": len(payload)}
                if count is not None:
                    decoded = json.loads(payload)
                    item["actual_records"] = len(decoded.get("spots", [])) if relative.startswith("spots/") else len(decoded)
                    if relative.startswith("spots/"):
                        item["expected_spot_records"] = count
                        if item["actual_records"] != count:
                            raise ValueError(f"Pinned spot count mismatch for {relative}: {item['actual_records']}")
                    else:
                        item["expected_canary_records"] = count
                records[relative] = item
            canary_ids = set()
            for relative in FILES:
                if relative.startswith("spots/"):
                    tile = json.loads((stage / relative).read_text(encoding="utf-8"))
                    canary_ids.update(spot["id"] for spot in tile["spots"])
            for relative, (_count, _expected_hash) in FILES.items():
                if relative.startswith("clusters/") and relative != "clusters/index.json":
                    records[relative]["canary_representatives"] = sum(
                        cluster["rep"]["id"] in canary_ids
                        for cluster in json.loads((stage / relative).read_text(encoding="utf-8"))
                    )
                    if records[relative]["canary_representatives"] != records[relative]["expected_canary_records"]:
                        raise ValueError(f"Pinned Canary cluster count mismatch for {relative}")
            manifest_extra = {}
        manifest = {
            "schema": 1,
            "repository": REPOSITORY,
            "commit": commit,
            "source_url": f"https://github.com/{REPOSITORY}/tree/{commit}",
            "files": records,
            **manifest_extra,
        }
        if inventory_payload is not None:
            (stage / "source-inventory.json").write_bytes(inventory_payload)
        (stage / "baseline.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for file in stage.rglob("*"):
            if file.is_file():
                file.chmod(0o444)
        for directory in sorted((path for path in stage.rglob("*") if path.is_dir()), reverse=True):
            directory.chmod(0o555)
        stage.chmod(0o555)
        os.replace(stage, output)
        print(json.dumps({"path": str(output), "commit": commit, "files": len(records)}, indent=2))
        return 0
    except BaseException:
        # The private stage has not been renamed and is not an authoritative baseline.
        import shutil
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
