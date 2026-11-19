"""Run the pinned eight-spot F01 acceptance panel through production code.

The plan's inline replay remains the independent reference implementation.
This command intentionally calls only ``measure_sky_darkness_area`` for its
reported results, after recomputing the identical pre-clip ALR inputs.
"""
from collections import Counter
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from src.alr import slice_and_compute
from src.elevation import ElevationSampler, sha256
from src.enrich import spot_id
from src.geographic_data import GeographicDataset
from src.sky_darkness_area import measure_sky_darkness_area

DATA_HASHES = {
    "canaries": "f1656c88ae5a1e29365df41bfb90429f54dea6cf53118568911c99110ec9b04d",
    "france": "a71d0015f63fdf5d24435736ac9209b513db4e2f210b124cf2a962524258c761",
}
DATA_REVISION = "312bc366ccef674a232bde2f0a3a42067d083d19"
EXPECTED_E3_MANIFEST_SHA256 = "34cc9365f131d3c6a0abaf2d7c83e3bc74d38630cd6b9787ed278893e25caaa6"
PANEL = {
    "canaries": {"N028W017": ["28.2479_-16.6563", "28.0021_-16.6938"]},
    "france": {
        "N046E005": ["46.6729_5.5562"], "N048E001": ["48.2271_1.6229"],
        "N048E004": ["48.6479_4.9646"], "N049E005": ["49.1437_5.5354"],
        "N044E001": ["44.8896_1.6187"], "N047E000": ["47.9021_0.8521"],
    },
}
EXPECTED = {
    "46.6729_5.5562": ("halo", 6000),
    "28.2479_-16.6563": ("label", None),
    "48.2271_1.6229": ("label", None),
    "48.6479_4.9646": ("label", None),
    "49.1437_5.5354": ("label", None),
    "44.8896_1.6187": ("label", None),
    "47.9021_0.8521": ("label", None),
    "28.0021_-16.6938": ("none", None),
}


def verify_pinned_e3_manifest(path: Path, expected_sha256: str = EXPECTED_E3_MANIFEST_SHA256) -> str:
    """Reject another internally valid E3 package before replaying outcomes."""
    manifest_bytes = path.read_bytes()
    json.loads(manifest_bytes)
    actual = hashlib.sha256(manifest_bytes).hexdigest()
    if actual != expected_sha256:
        raise ValueError(
            f"Pinned E3 root manifest digest mismatch: {actual} != {expected_sha256}"
        )
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("F01_DATA", "../darkskyspots-data")))
    parser.add_argument("--e3", type=Path, default=Path(os.environ.get("F01_E3", "input/geography/e3-p2-odbl-2026-11-04")))
    args = parser.parse_args()
    e3_manifest = args.e3 / "manifest.json"
    if not e3_manifest.is_file():
        raise FileNotFoundError(f"Missing pinned E3 package: {e3_manifest}")
    manifest_sha256 = verify_pinned_e3_manifest(e3_manifest)
    dataset = GeographicDataset(str(e3_manifest), require_e3=True)
    if dataset.revision != manifest_sha256:
        raise ValueError("E3 manifest changed after its pinned digest check")
    counts = Counter()
    try:
        for region in ("canaries", "france"):
            raster = Path("input") / region / "2025.tif"
            if sha256(raster) != DATA_HASHES[region]:
                raise ValueError(f"Acceptance radiance hash mismatch: {raster}")
            raw = slice_and_compute(str(raster), 6933 if region == "canaries" else 3035)
            terrain_key = dataset.manifest["terrain"][region]
            terrain_manifest = dataset.entries[terrain_key]
            with ElevationSampler.water_masks(terrain_manifest, args.e3) as sampler:
                dataset.classify([], region, sampler)
                index = dataset._e3_engine.for_region(region)
                for tile, ids in PANEL[region].items():
                    payload = json.loads(subprocess.check_output(
                        ["git", "-C", str(args.data), "show", f"{DATA_REVISION}:spots/{tile}.json"], text=True
                    ))
                    for identifier in ids:
                        spot = next(item for item in payload["spots"] if item["id"] == identifier)
                        field, audit = measure_sky_darkness_area(spot, raw.data, raw.transform, sampler, index)
                        exported_id = spot_id(float(spot["lat"]), float(spot["lon"]))
                        if audit["id"] != exported_id:
                            raise AssertionError(
                                f"F01 audit ID differs from exported spot for {identifier}: "
                                f"{audit['id']} != {exported_id}"
                            )
                        outcome = "none" if field is None else "halo" if "radiusM" in field else "label"
                        actual = (outcome, field.get("radiusM") if field else None)
                        expected = EXPECTED[identifier]
                        print(json.dumps({"id": identifier, "name": spot["name"], "field": field,
                                          "outcome": outcome, "audit": audit}, ensure_ascii=False), flush=True)
                        if actual != expected:
                            raise AssertionError(f"F01 outcome differs for {identifier}: {actual} != {expected}")
                        counts[outcome] += 1
            del raw
    finally:
        dataset.close()
    print("production panel:", dict(counts), flush=True)
    assert counts == {"halo": 1, "label": 6, "none": 1}


if __name__ == "__main__":
    main()
