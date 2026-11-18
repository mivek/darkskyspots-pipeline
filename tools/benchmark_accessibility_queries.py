#!/usr/bin/env python3
"""Measure geometry-query distributions on retained G2.0 SQLite benchmark indexes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from pyproj import Transformer
from shapely import transform as shapely_transform
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.accessibility_index import AccessibilityIndex
from src.resource_metrics import PeakRssSampler


CONTROLS = (
    ("dense-las-palmas", (-15.48, 28.08, -15.39, 28.16), -15.43, 28.12),
    ("rural-la-palma", (-17.95, 28.65, -17.78, 28.80), -17.86, 28.72),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def sample_index(name: str, bbox, lon: float, lat: float, database: Path,
                 samples: int) -> dict:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32628", always_xy=True)
    domain = shapely_transform(
        box(*bbox), transformer.transform, interleaved=False,
    ).buffer(6000)
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"Retained benchmark SQLite integrity failed for {name}: {integrity}")
        index = AccessibilityIndex.__new__(AccessibilityIndex)
        index.connection = connection
        index.to_metric = transformer
        index.from_metric = Transformer.from_crs("EPSG:32628", "EPSG:4326", always_xy=True)
        index.manifest = {
            "schema": 1,
            "class_completeness": {"road": True, "track": True, "path": True, "parking": True},
            "errors_by_class": {},
        }
        index._domain = domain
        index._coverage = domain
        row_count = connection.execute("SELECT count(*) FROM features").fetchone()[0]
        result = {"name": name, "database": str(database), "database_sha256": sha256(database),
                  "database_bytes": database.stat().st_size, "indexed_features": row_count,
                  "integrity_check": integrity, "controls": []}
        for radius in (1000, 5000):
            # Warm the SQLite/geometry path, then retain each measured runtime.
            index.query_detailed(lat, lon, radius)
            rss = PeakRssSampler(interval_seconds=0.01).start()
            elapsed = []
            last_query = None
            for _ in range(samples):
                started = time.perf_counter()
                last_query = index.query_detailed(lat, lon, radius)
                elapsed.append(time.perf_counter() - started)
            peak_rss = rss.stop()
            candidate_counts = {
                feature_class: last_query["classes"][feature_class]["candidateCount"]
                for feature_class in ("road", "track", "path", "parking")
            }
            result["controls"].append({
                "radius_m": radius,
                "sample_count": samples,
                "warmup_queries_excluded": 1,
                "percentile_method": "nearest rank: sorted[ceil(p*n)-1]",
                "latency_seconds": {
                    "p50": percentile_nearest_rank(elapsed, 0.50),
                    "p95": percentile_nearest_rank(elapsed, 0.95),
                    "p99": percentile_nearest_rank(elapsed, 0.99),
                    "max": max(elapsed),
                },
                "candidate_counts_by_class": candidate_counts,
                "rtree_candidates": last_query["metrics"]["rtreeCandidates"],
                "geometry_reads": last_query["metrics"]["geometryReads"],
                "sampled_process_peak_rss_mib": peak_rss,
                "samples_seconds": elapsed,
            })
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=24)
    args = parser.parse_args()
    if args.samples < 12:
        parser.error("--samples must be at least 12")
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite query qualification: {args.output}")
    results = [
        sample_index(name, bbox, lon, lat, args.benchmark_dir / f"{name}.sqlite", args.samples)
        for name, bbox, lon, lat in CONTROLS
    ]
    output = {
        "schema": 1,
        "source": "retained G2.0 bounded Tenerife benchmark SQLite indexes",
        "query_samples_per_control_radius": args.samples,
        "percentile_method": "nearest rank: sorted[ceil(p*n)-1]",
        "limitations": [
            "Repeated measurements use one fixed location per retained dense/rural control.",
            "SQLite pages and geometries are warm after one excluded query; this is not cold-start latency.",
            "This harness excludes manifest/source-lock startup validation and DEM endpoint elevation sampling.",
            "The small control sample is not a full-region latency or memory requirement estimate.",
            "RSS sampling at 10 ms may miss shorter allocation peaks.",
        ],
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "indexes": len(results),
                      "samples_per_radius": args.samples}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
