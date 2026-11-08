#!/usr/bin/env python3
"""Enrich a frozen published Canary snapshot and write local audit artifacts."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.accessibility import AccessibilityEnricher, PUBLIC_CLASSES, validate_accessibility_payload
from src.accessibility_index import AccessibilityIndex
from src.accessibility_prepare import (
    validate_accessibility_manifest,
    write_accessibility_source_record,
)
from src.clusters import aggregate_spot_files
from src.elevation import ElevationSampler
from src.geography import load_geography
from src.resource_metrics import PeakRssSampler
from src.accessibility_scope import GeographicSpecification


ISLAND_CENTERS = {
    "El Hierro": (-17.95, 27.75), "La Palma": (-17.87, 28.68),
    "La Gomera": (-17.23, 28.10), "Tenerife": (-16.60, 28.30),
    "Gran Canaria": (-15.60, 27.95), "Fuerteventura": (-14.00, 28.35),
    "Lanzarote": (-13.60, 29.00), "La Graciosa": (-13.50, 29.25),
}
FINGERPRINT_PATHS = (
    "config/accessibility.yaml", "docs/accessibility.md", "run.py", "src/accessibility.py",
    "src/accessibility_index.py", "src/accessibility_prepare.py", "src/accessibility_scope.py",
    "src/cli.py", "src/clusters.py", "src/elevation.py", "src/geography.py", "src/geographic_geometry.py",
    "src/regions.py", "tests/unit/test_accessibility_generalized.py", "tools/audit_accessibility.py",
    "tools/compare_accessibility_pilots.py", "tools/fetch_accessibility_baseline.py",
    "tools/pilot_accessibility.py", "tools/prepare_accessibility.py", "tools/run_accessibility_g1_tests.py",
    "docs/plans/2026-11-08-f02-1a-g1-canary-islands-generalization.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compact_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pretty_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def _island_component(spot, components):
    point = Point(float(spot["lon"]), float(spot["lat"]))
    matches = [component for component in components if component.covers(point)]
    if not matches:
        return "unmatched_land_component"
    component = matches[0]
    center_name = min(
        ISLAND_CENTERS,
        key=lambda name: component.centroid.distance(Point(*ISLAND_CENTERS[name])),
    )
    return center_name


def _land_components():
    land = load_geography().land.intersection(box(-19, 27, -13, 30))
    return list(land.geoms) if hasattr(land, "geoms") else [land]


def _cluster_index_payload(base_index: dict, cluster_paths: dict[str, dict]) -> dict:
    result = copy.deepcopy(base_index)
    levels_key = "levels" if isinstance(result.get("levels"), list) else None
    if levels_key:
        for level in result[levels_key]:
            for file_info in level.get("files", []):
                path = file_info.get("path") or file_info.get("name")
                if path and path in cluster_paths:
                    file_info["baseline_hash"] = file_info.get("hash")
                    file_info["hash"] = cluster_paths[path]["sha256"]
                    file_info["size"] = cluster_paths[path]["size"]
    return result


def _load_pinned_cluster_index(path: Path, expected: dict) -> dict:
    """Load the published index only when its original bytes match the pin."""
    if (not path.is_file() or path.stat().st_size != expected.get("size")
            or sha256_file(path) != expected.get("sha256")):
        raise ValueError(f"Frozen baseline integrity mismatch: clusters/index.json ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


def _implementation_fingerprint() -> dict:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    files = []
    for relative in FINGERPRINT_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        file_hash = sha256_file(path)
        digest.update(relative.encode("utf-8") + b"\0" + file_hash.encode("ascii") + b"\n")
        files.append({"path": relative, "sha256": file_hash})
    return {"algorithm": "sha256(path-nul-file-sha256-newline)", "sha256": digest.hexdigest(), "files": files}


def run_audit(*, spots_dir: Path, clusters_dir: Path, accessibility_manifest: Path,
              dem_manifest: Path, dem_cache_dir: Path, radius_m: int,
              output_dir: Path, test_results_path: Path | None = None,
              tenerife_regression_path: Path | None = None,
              geographic_config: Path = Path("config/accessibility.yaml")) -> dict:
    spots_dir = spots_dir.resolve()
    clusters_dir = clusters_dir.resolve()
    accessibility_manifest = accessibility_manifest.resolve()
    dem_manifest = dem_manifest.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Audit output already exists: {output_dir}")
    if radius_m not in {int(value) for value in json.loads(accessibility_manifest.read_text())["policies"]["radii_m"]}:
        raise ValueError("Requested radius is not supported by the accessibility manifest")
    manifest = validate_accessibility_manifest(accessibility_manifest)
    baseline_manifest_path = spots_dir.parent / "baseline.json"
    if not baseline_manifest_path.is_file():
        raise ValueError("Frozen snapshot has no baseline.json checksum inventory")
    baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    if baseline_manifest.get("commit") != "312bc366ccef674a232bde2f0a3a42067d083d19":
        raise ValueError("Audit input is not the reconciled pinned published baseline")
    expected_files = baseline_manifest["files"]
    for relative, info in expected_files.items():
        path = baseline_manifest_path.parent / relative
        if not path.is_file() or path.stat().st_size != info["size"] or sha256_file(path) != info["sha256"]:
            raise ValueError(f"Frozen baseline integrity mismatch: {relative}")

    baseline_tiles = sorted(spots_dir.glob("*.json"))
    if not baseline_tiles:
        raise ValueError(f"No frozen spot tiles found: {spots_dir}")
    tiles = {}
    spots = []
    for tile_path in baseline_tiles:
        envelope = json.loads(tile_path.read_text(encoding="utf-8"))
        tile_spots = envelope.get("spots")
        if not isinstance(tile_spots, list):
            raise ValueError(f"Frozen tile has no spot array: {tile_path}")
        inventory = expected_files.get(f"spots/{tile_path.name}")
        if inventory is None or len(tile_spots) != inventory.get("expected_spot_records"):
            raise ValueError(f"Frozen tile count differs from its pinned inventory: {tile_path}")
        ids = [spot.get("id") for spot in tile_spots]
        if any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"Frozen tile has missing or duplicate spot IDs: {tile_path}")
        tiles[tile_path.name] = envelope
        spots.extend(tile_spots)
    spot_ids = [spot["id"] for spot in spots]
    if len(spot_ids) != len(set(spot_ids)):
        raise ValueError("Frozen published baseline contains duplicate spot IDs")

    published_clusters = {}
    for level in range(1, 7):
        cluster_path = clusters_dir / f"L{level}.json"
        if not cluster_path.is_file():
            raise ValueError(f"Frozen cluster file missing: {cluster_path}")
        published_clusters[level] = json.loads(cluster_path.read_text(encoding="utf-8"))
        inventory = expected_files.get(f"clusters/L{level}.json")
        actual = published_clusters[level]
        canary_representatives = sum(record["rep"]["id"] in set(spot_ids) for record in actual)
        if (inventory is None or len(actual) != inventory.get("actual_records")
                or canary_representatives != inventory.get("expected_canary_records")):
            raise ValueError(f"Frozen cluster count differs from its pinned inventory: {cluster_path}")
    cluster_index_path = clusters_dir / "index.json"
    if not cluster_index_path.is_file():
        raise ValueError("Frozen cluster index missing")
    published_cluster_index = _load_pinned_cluster_index(
        cluster_index_path, expected_files["clusters/index.json"],
    )

    start = time.perf_counter()
    rss = PeakRssSampler().start()
    query_counts = {name: Counter() for name in PUBLIC_CLASSES}
    elevation_unknown_reasons = Counter()
    coverage_counts = Counter()
    coverage_unknown_reasons = Counter()
    unsupported_absence_claims = 0
    islands = Counter()
    enriched_by_id = {}
    accessibility_size = []
    payload_size_by_spot = {}
    query_seconds = 0.0
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        with AccessibilityIndex(accessibility_manifest) as index, \
                ElevationSampler(dem_manifest, dem_cache_dir) as sampler:
            if index.manifest.get("schema") != 2:
                raise ValueError("Archipelago audit requires the generalized schema 2 manifest")
            index.validate_dem_manifest(sampler.manifest_path)
            enricher = AccessibilityEnricher(index, sampler, radius_m)
            assignment = enricher.audit_assignments(spots)
            if assignment["assigned"] != len(spots) or assignment["excluded"]:
                raise ValueError(f"Not all frozen spots are assigned to the archipelago scope: {assignment}")
            components = _land_components()
            for original in spots:
                islands[_island_component(original, components)] += 1
                query_start = time.perf_counter()
                detailed = index.query_detailed(
                    float(original["lat"]), float(original["lon"]), radius_m,
                )
                coverage_counts[detailed["coverageStatus"]] += 1
                if detailed.get("coverageReason"):
                    coverage_unknown_reasons[detailed["coverageReason"]] += 1
                queries = index.public_from_detailed(
                    detailed, reference_altitude=original.get("altitude"), elevation_sampler=sampler,
                )
                query_seconds += time.perf_counter() - query_start
                payload = {"schema": 1, "source": index.source_id, "radiusM": radius_m, **queries}
                validate_accessibility_payload(payload)
                for feature_class in PUBLIC_CLASSES:
                    query_counts[feature_class][payload[feature_class]["status"]] += 1
                    query = payload[feature_class]
                    if (query.get("status") == "not_found_within_radius"
                            and (detailed["coverageStatus"] != "complete"
                                 or not index.manifest.get("class_completeness", {}).get(feature_class, False))):
                        unsupported_absence_claims += 1
                    if query.get("status") == "found" and query.get("elevationUnknownReason"):
                        elevation_unknown_reasons[f"{feature_class}:{query['elevationUnknownReason']}"] += 1
                encoded = compact_bytes(payload)
                if len(encoded) > 4096:
                    raise ValueError(f"Accessibility payload exceeds 4 KiB for {original['id']}: {len(encoded)}")
                accessibility_size.append(len(encoded))
                payload_size_by_spot[original["id"]] = len(encoded)
                enriched_by_id[original["id"]] = payload
            specification = GeographicSpecification.load(geographic_config, "canaries")
            projection_distortion = specification.validate_distortion(
                specification.assignment_core(), index.to_metric,
            )

        (stage / "tiles").mkdir()
        preserved_fields = True
        tile_payload_metrics = {}
        for filename, envelope in tiles.items():
            updated = dict(envelope)
            new_spots = []
            for original in envelope["spots"]:
                changed = dict(original)
                changed["accessibility"] = enriched_by_id[original["id"]]
                if {key: value for key, value in changed.items() if key != "accessibility"} != original:
                    preserved_fields = False
                new_spots.append(changed)
            updated["spots"] = new_spots
            tile_bytes = pretty_bytes(updated)
            baseline_bytes = pretty_bytes(envelope)
            (stage / "tiles" / filename).write_bytes(tile_bytes)
            tile_payload_metrics[filename] = {
                "spots": len(new_spots),
                "baseline_bytes": len(baseline_bytes),
                "enriched_bytes": len(tile_bytes),
                "delta_bytes": len(tile_bytes) - len(baseline_bytes),
                "baseline_gzip_bytes": len(gzip.compress(baseline_bytes, compresslevel=9, mtime=0)),
                "enriched_gzip_bytes": len(gzip.compress(tile_bytes, compresslevel=9, mtime=0)),
            }
        if not preserved_fields:
            raise ValueError("Enrichment changed an original published spot field")

        actual_clusters = aggregate_spot_files(spots_dir)
        total_cluster_records = 0
        cluster_paths = {}
        (stage / "clusters").mkdir()
        for level in range(1, 7):
            baseline = published_clusters[level]
            reproduced = actual_clusters[level]
            canary_representatives = [
                record for record in baseline if record["rep"]["id"] in enriched_by_id
            ]
            if canary_representatives != reproduced:
                raise ValueError(f"Published cluster membership/representatives differ from clustering logic at L{level}")
            enriched = []
            for record in baseline:
                item = dict(record)
                representative = dict(record["rep"])
                if representative["id"] in enriched_by_id:
                    representative["accessibility"] = enriched_by_id[representative["id"]]
                item["rep"] = representative
                enriched.append(item)
            for base_record, enriched_record in zip(baseline, enriched):
                unmodified = dict(enriched_record)
                enriched_rep = dict(unmodified["rep"])
                enriched_rep.pop("accessibility", None)
                unmodified["rep"] = enriched_rep
                if unmodified != base_record:
                    raise ValueError(f"Accessibility changed a published cluster record at L{level}")
            target = stage / "clusters" / f"L{level}.json"
            target.write_bytes(pretty_bytes(enriched))
            cluster_paths[f"L{level}.json"] = {"sha256": sha256_file(target), "size": target.stat().st_size}
            total_cluster_records += len(enriched)

        local_index = _cluster_index_payload(published_cluster_index, cluster_paths)
        if published_cluster_index != _load_pinned_cluster_index(
            cluster_index_path, expected_files["clusters/index.json"],
        ):
            raise ValueError("Published cluster index changed while building enriched metadata")
        index_target = stage / "clusters" / "index.json"
        index_target.write_bytes(pretty_bytes(local_index))
        cluster_paths["index.json"] = {"sha256": sha256_file(index_target), "size": index_target.stat().st_size}
        write_accessibility_source_record(stage / "package", manifest)
        (stage / "audit").mkdir()
        preserved_baseline = {
            "commit": baseline_manifest["commit"],
            "file_inventory": expected_files,
            "tiles": {name: {"sha256": sha256_file(spots_dir / name), "envelope": envelope}
                      for name, envelope in tiles.items()},
            "clusters": {
                f"L{level}.json": {
                    "sha256": sha256_file(clusters_dir / f"L{level}.json"),
                    "records": published_clusters[level],
                    "representative_ids": [record["rep"]["id"] for record in published_clusters[level]],
                }
                for level in range(1, 7)
            },
            "cluster_index_sha256": expected_files["clusters/index.json"]["sha256"],
            "cluster_index": published_cluster_index,
            "cluster_logic_reproduced_exactly": True,
        }
        (stage / "audit" / "preserved-baseline.json").write_bytes(pretty_bytes(preserved_baseline))
        peak_rss_mib = rss.stop()
        test_results = (json.loads(test_results_path.read_text(encoding="utf-8"))
                        if test_results_path else None)
        tenerife_regression = (json.loads(tenerife_regression_path.read_text(encoding="utf-8"))
                               if tenerife_regression_path else None)
        summary = {
            "schema": 1, "review_status": "pending_independent_review",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "baseline": {"repository": baseline_manifest["repository"], "commit": baseline_manifest["commit"],
                         "files": expected_files, "spot_count": len(spots),
                         "tile_count": len(baseline_tiles)},
            "source_id": index.source_id,
            "source_identity": manifest.get("identity"),
            "source_provenance": {"pbf": manifest.get("pbf_provenance"), "dem": manifest.get("dem")},
            "projection_validation": {
                "metric_crs": index.crs,
                "axis_order": "longitude_latitude",
                "maximum_distortion_fraction": projection_distortion,
                "accepted_maximum_fraction": 0.005,
            },
            "assignment": {**assignment, "enriched": len(enriched_by_id)},
            "spot_counts_by_island": dict(sorted(islands.items())),
            "query_status_counts": {key: dict(sorted(value.items())) for key, value in query_counts.items()},
            "search_coverage": {
                "status_counts": dict(sorted(coverage_counts.items())),
                "unknown_reasons": dict(sorted(coverage_unknown_reasons.items())),
            },
            "elevation_unknown_reasons": dict(sorted(elevation_unknown_reasons.items())),
            "class_completeness": manifest.get("class_completeness"),
            "counts_by_class": manifest.get("counts_by_class"),
            "errors_by_class": manifest.get("errors_by_class"),
            "indexed_features": manifest.get("indexed_features"),
            "payload_size_bytes": {"max": max(accessibility_size),
                                   "mean": sum(accessibility_size) / len(accessibility_size),
                                   "total": sum(accessibility_size),
                                   "by_spot_id": payload_size_by_spot,
                                   "tile_payloads": tile_payload_metrics,
                                   "limit": 4096},
            "preservation": {"original_spot_fields_and_order_unchanged": preserved_fields,
                             "published_cluster_structures_reproduced": True,
                             "cluster_records_checked": total_cluster_records,
                             "canary_cluster_records_reproduced": sum(
                                 sum(record["rep"]["id"] in enriched_by_id
                                     for record in published_clusters[level])
                                 for level in range(1, 7)
                             )},
            "resources": {"index_apparent_bytes": manifest["index"]["size"],
                          "index_allocated_bytes": manifest.get("metrics", {}).get("index_allocated_bytes"),
                          "preparation": manifest.get("metrics"),
                          "query_seconds": query_seconds, "total_audit_seconds": time.perf_counter() - start,
                          "peak_rss_mib": peak_rss_mib},
            "pipeline_revision": _git_revision(),
            "implementation_worktree_fingerprint": _implementation_fingerprint(),
            "tenerife_regression": tenerife_regression,
            "automated_test_results": test_results,
            "historical_reconciliation": {
                "baseline_commit": "312bc366ccef674a232bde2f0a3a42067d083d19",
                "shared_spot_ids": 24,
                "published_only_spot_ids": 42,
                "historical_only_spot_ids": 30,
                "shared_record_differences": ["altitude", "small floating-point darkness values"],
                "historical_altitude": "null in all shared historical records",
                "other_shared_property_differences": 0,
            },
            "acceptance": {
                "all_published_spots_assigned": assignment["assigned"] == len(spots),
                "all_assigned_spots_enriched": len(enriched_by_id) == assignment["assigned"],
                "original_spot_properties_and_order_preserved": preserved_fields,
                "published_cluster_representatives_verified": True,
                "all_payloads_within_4_kib": max(accessibility_size) <= 4096,
                "projection_within_0_5_percent": projection_distortion <= 0.005,
                "tenerife_query_regressions": (None if tenerife_regression is None else
                                                tenerife_regression["difference_count"] == 0),
                "unsupported_absence_claims": unsupported_absence_claims,
                "automated_tests_passed": (None if test_results is None else
                                           test_results.get("exit_code") == 0 and test_results.get("failed", 0) == 0),
                "independent_review": "pending",
            },
            "limitations": ["OSM evidence completeness is relative to the pinned extract and supported classes."],
        }
        acceptance = summary["acceptance"]
        unmet = []
        labels = {
            "all_published_spots_assigned": "All published spots assigned",
            "all_assigned_spots_enriched": "All assigned spots enriched",
            "original_spot_properties_and_order_preserved": "Original spot fields and ordering preserved",
            "published_cluster_representatives_verified": "Published cluster representatives verified",
            "all_payloads_within_4_kib": "All accessibility payloads within 4 KiB",
            "projection_within_0_5_percent": "Projection distortion within 0.5%",
            "tenerife_query_regressions": "Tenerife regression comparison has no differences",
            "automated_tests_passed": "Complete automated test suite passed",
        }
        for key, label in labels.items():
            if acceptance.get(key) is not True:
                unmet.append(label)
        if acceptance["unsupported_absence_claims"] != 0:
            unmet.append("No unsupported absence claims")
        unmet.append("Independent review")
        summary["unmet_acceptance_criteria"] = unmet
        (stage / "audit.json").write_bytes(pretty_bytes(summary))
        report = [
            "# Canary accessibility audit", "",
            f"- Baseline commit: `{summary['baseline']['commit']}`",
            f"- Accessibility source: `{index.source_id}`",
            f"- Spots: {len(spots)} assigned and enriched; {assignment['excluded']} excluded; {assignment['missing']} missing.",
            f"- Preserved published tile fields and ordering: {preserved_fields}.",
            f"- Preserved/reproduced cluster structure: {total_cluster_records} records checked.",
            f"- Payload size: max {max(accessibility_size)} bytes; mean {sum(accessibility_size) / len(accessibility_size):.1f}; limit 4096.",
            f"- Query time: {query_seconds:.2f} s; total audit time: {summary['resources']['total_audit_seconds']:.2f} s.",
            f"- Pipeline revision: `{summary['pipeline_revision']}`",
            f"- Implementation worktree fingerprint: `{summary['implementation_worktree_fingerprint']['sha256']}`",
            "", "## Spot counts by island", "",
        ]
        report.extend(f"- {name}: {count}" for name, count in sorted(islands.items()))
        report.extend(["", "## Query status counts", ""])
        report.append(
            "- Search coverage: " + ", ".join(
                f"{status}={count}" for status, count in summary["search_coverage"]["status_counts"].items()
            )
        )
        for feature_class, counts in summary["query_status_counts"].items():
            report.append(f"- {feature_class}: " + ", ".join(f"{status}={count}" for status, count in counts.items()))
        report.extend(["", "## Regression and automated tests", ""])
        report.append("- Unmet acceptance criteria: " + (", ".join(unmet) if unmet else "none"))
        if tenerife_regression is None:
            report.append("- Tenerife regression comparison: not supplied.")
        else:
            report.append(
                f"- Tenerife: {tenerife_regression['exact_match_count']}/{tenerife_regression['query_count']} exact query matches; "
                f"{tenerife_regression['difference_count']} differences."
            )
            report.append(
                "- Tenerife differing evidence fields: "
                + (", ".join(
                    f"{field}={count}"
                    for field, count in sorted(tenerife_regression.get("difference_fields", {}).items())
                ) or "none")
                + "."
            )
        if test_results is None:
            report.append("- Automated suite results: not supplied.")
        else:
            report.append(
                f"- Automated suite: `{test_results.get('command')}`; "
                f"passed={test_results.get('passed')}, failed={test_results.get('failed')}, "
                f"exit_code={test_results.get('exit_code')}."
            )
        report.extend(["", "This is a local review artifact. It does not authorize publication.", ""])
        (stage / "audit.md").write_text("\n".join(report), encoding="utf-8")
        stage.rename(output_dir)
        return summary
    except BaseException:
        rss.stop()
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _git_revision() -> str | None:
    try:
        import subprocess
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=Path(__file__).resolve().parents[1]).strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spots-dir", required=True)
    parser.add_argument("--clusters-dir", required=True)
    parser.add_argument("--accessibility-manifest", required=True)
    parser.add_argument("--dem-manifest", required=True)
    parser.add_argument("--dem-cache-dir", default="input/elevation")
    parser.add_argument("--radius-m", required=True, type=int)
    parser.add_argument("--config", default="config/accessibility.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--test-results", type=Path)
    parser.add_argument("--tenerife-regression", type=Path)
    args = parser.parse_args()
    result = run_audit(
        spots_dir=Path(args.spots_dir), clusters_dir=Path(args.clusters_dir),
        accessibility_manifest=Path(args.accessibility_manifest), dem_manifest=Path(args.dem_manifest),
        dem_cache_dir=Path(args.dem_cache_dir), radius_m=args.radius_m, output_dir=Path(args.output_dir),
        test_results_path=args.test_results, tenerife_regression_path=args.tenerife_regression,
        geographic_config=Path(args.config),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
