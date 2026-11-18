#!/usr/bin/env python3
"""Run the local Tenerife accessibility pilot and write reviewable evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.accessibility import PUBLIC_CLASSES, validate_accessibility_payload
from src.accessibility_index import AccessibilityIndex
from src.accessibility_prepare import sha256_file, validate_accessibility_manifest
from src.accessibility_prepare import PILOT_OUTPUT_RADIUS_M, PILOT_RADII_M
from src.accessibility_prepare import write_accessibility_source_record
from src.clusters import write_cluster_files
from src.elevation import ElevationSampler
from src.resource_metrics import PeakRssSampler

EXPECTED_BASELINE_SHA = "01e1b4d340a6ac1d4aadb6149aa2621fbfae67bb74f67b2fc6b22a835e1099fe"


def compact_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def pretty_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=4, allow_nan=False) + "\n").encode("utf-8")


def gzip_size(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def _pipeline_state() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        changed = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
        return {"pipeline_commit": commit, "pipeline_worktree_clean": not bool(changed.strip())}
    except (OSError, subprocess.CalledProcessError):
        return {"pipeline_commit": None, "pipeline_worktree_clean": False}


def _copy_with_hash(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {"path": str(destination.relative_to(destination.parents[1])),
            "size": destination.stat().st_size, "sha256": sha256_file(destination)}


def _safe_md(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _access_counts(candidates: list[dict]) -> dict:
    counts = {mode: Counter() for mode in ("foot", "motorcar")}
    for candidate in candidates:
        for mode, evidence in candidate["access"].items():
            counts[mode][evidence["state"]] += 1
    return {mode: dict(sorted(values.items())) for mode, values in counts.items()}


def _report_location_radius(detailed: dict) -> dict:
    """Keep only report counters and nearest IDs after the full audit is streamed."""
    classes = {}
    for feature_class, query in detailed["classes"].items():
        candidate = query["nearestCandidate"]
        classes[feature_class] = {
            "status": query["status"],
            "reason": query.get("reason"),
            "nearestCandidate": None if candidate is None else {
                "osm": candidate["osm"],
                "distanceMExact": candidate["distanceMExact"],
                "access": candidate["access"],
            },
            "nearestElevation": query.get("nearestElevation"),
            "candidateCount": query["candidateCount"],
            "rtreeCandidates": query["rtreeCandidates"],
            "geometryReads": query["geometryReads"],
        }
    return {
        "classes": classes,
        "nearbyRestrictionCount": len(detailed["nearbyRestrictions"]),
        "nearbyAccessTaggedCount": len(detailed["nearbyAccessTaggedFeatures"]),
        "metrics": detailed["metrics"],
    }


def _write_cluster_comparison(stage: Path, envelope: dict, variant: str) -> dict:
    root = stage / "clusters" / variant
    spots_dir = root / "spots"
    spots_dir.mkdir(parents=True)
    (spots_dir / "N028W017.json").write_bytes(pretty_bytes(envelope))
    clusters_dir = root / "clusters"
    write_cluster_files(
        spots_dir, clusters_dir, data_year=2025,
        generated="2026-11-03T00:00:00Z",
    )
    results = {}
    representative_bytes = {}
    for level in range(1, 7):
        path = clusters_dir / f"L{level}.json"
        raw = path.read_bytes()
        clusters = json.loads(raw)
        reps = [item["rep"] for item in clusters]
        representative_bytes[str(level)] = sum(len(compact_bytes(rep)) for rep in reps)
        results[str(level)] = {
            "file": str(path.relative_to(stage)),
            "clusters": len(clusters),
            "uncompressedBytes": len(raw),
            "gzipBytes": gzip_size(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {"levels": results, "representativeBytesByLevel": representative_bytes,
            "representativeBytes": sum(representative_bytes.values()),
            "uncompressedBytes": sum(item["uncompressedBytes"] for item in results.values()),
            "gzipBytes": sum(item["gzipBytes"] for item in results.values())}


def _assert_cluster_shape_preserved(stage: Path) -> dict:
    """Verify optional evidence leaves cluster membership and representatives stable."""
    invariant_keys = ("id", "bbox", "count", "lat", "lon")
    checked = 0
    for level in range(1, 7):
        baseline_path = stage / "clusters" / "baseline" / "clusters" / f"L{level}.json"
        enriched_path = stage / "clusters" / "enriched" / "clusters" / f"L{level}.json"
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
        if len(baseline) != len(enriched):
            raise ValueError(f"Accessibility changed cluster count at level {level}")
        for base, candidate in zip(baseline, enriched):
            if any(base.get(key) != candidate.get(key) for key in invariant_keys):
                raise ValueError(f"Accessibility changed cluster membership or geometry at level {level}")
            enriched_rep = dict(candidate["rep"])
            enriched_rep.pop("accessibility", None)
            if enriched_rep != base["rep"]:
                raise ValueError(f"Accessibility changed a cluster representative at level {level}")
            checked += 1
    return {
        "membership_and_geometry_unchanged": True,
        "representative_ids_unchanged": True,
        "cluster_records_checked": checked,
    }


def run_pilot(*, spots_path: Path, accessibility_manifest: Path, dem_manifest: Path,
              dem_cache_dir: Path, controls_path: Path, radii_m: list[int],
              output_radius_m: int, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Pilot output already exists; use a new run directory: {output_dir}")
    if len(set(radii_m)) != len(radii_m) or any(isinstance(radius, bool) or radius <= 0 for radius in radii_m):
        raise ValueError("Radii must be distinct positive integers")
    if output_radius_m not in radii_m:
        raise ValueError("output_radius_m must be one of the comparison radii")
    if tuple(radii_m) != PILOT_RADII_M or output_radius_m != PILOT_OUTPUT_RADIUS_M:
        raise ValueError("The approved Tenerife pilot compares 500, 1000, 3000 and 5000 m and uses 1000 m output")

    controls_spec = json.loads(controls_path.read_text(encoding="utf-8"))
    baseline_spec = controls_spec["baseline"]
    spots_path = spots_path.resolve()
    baseline_hash = sha256_file(spots_path)
    if baseline_hash != EXPECTED_BASELINE_SHA or baseline_hash != baseline_spec["sha256"]:
        raise ValueError(f"Frozen tile checksum mismatch: {baseline_hash}")
    baseline = json.loads(spots_path.read_text(encoding="utf-8"))
    generated_spec = controls_spec["generated_spots"]
    expected_ids = [record["id"] for record in generated_spec]
    spots = baseline.get("spots")
    if baseline.get("version") != baseline_spec["data_version"] or len(spots or []) != 16:
        raise ValueError("Frozen tile version or generated spot count changed")
    if [spot.get("id") for spot in spots] != expected_ids:
        raise ValueError("Frozen tile spot IDs/order do not match the approved allowlist")
    for spot, frozen in zip(spots, generated_spec):
        if spot["lat"] != frozen["lat"] or spot["lon"] != frozen["lon"]:
            raise ValueError(f"Frozen coordinates changed for {frozen['id']}")
    if len({spot["id"] for spot in spots}) != 16:
        raise ValueError("Frozen generated spot IDs are not unique")

    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    start = time.perf_counter()
    rss_sampler = PeakRssSampler().start()
    query_seconds = 0.0
    enrichment_seconds = 0.0
    serialization_seconds = 0.0
    try:
        input_root = stage / "inputs"
        copied = {}
        copied["frozen_tile"] = _copy_with_hash(spots_path, input_root / "N028W017.json")
        copied["controls_spec"] = _copy_with_hash(controls_path.resolve(), input_root / "tenerife-controls.json")
        copied["dem_manifest"] = _copy_with_hash(dem_manifest.resolve(), input_root / "canaries-dem-manifest.json")
        copied["accessibility_manifest"] = _copy_with_hash(accessibility_manifest.resolve(), input_root / "accessibility-manifest.json")
        manifest = validate_accessibility_manifest(accessibility_manifest)
        source_id = manifest["dataset_id"]
        locations = []
        for spot in spots:
            locations.append({"id": spot["id"], "role": "generated", "name": spot["name"],
                              "lat": spot["lat"], "lon": spot["lon"], "altitude": spot.get("altitude")})
        for control in controls_spec["controls"]:
            locations.append({**control, "altitude": None})
        if len(locations) != 18:
            raise ValueError("Pilot requires exactly 16 generated spots and two controls")

        audit_dir = stage / "audit"
        audit_dir.mkdir()
        public_by_radius: dict[int, list[dict]] = {radius: [] for radius in radii_m}
        status_counts = {radius: {feature_class: Counter() for feature_class in PUBLIC_CLASSES} for radius in radii_m}
        location_summaries = []
        with AccessibilityIndex(accessibility_manifest) as index, ElevationSampler(dem_manifest, dem_cache_dir) as sampler, \
                (audit_dir / "queries.jsonl").open("w", encoding="utf-8") as query_log:
            index.validate_dem_manifest(sampler.manifest_path)
            if len(spots) != 16:
                raise ValueError("Controls must remain separate from tile spots")
            for location in locations:
                reference_sample = sampler.sample_detailed(location["lat"], location["lon"])
                reference_altitude = location["altitude"]
                if location["role"] == "external_control" and reference_sample["reason"] is None:
                    reference_altitude = reference_sample["elevationM"]
                radius_details = {}
                for radius in radii_m:
                    before_query = time.perf_counter()
                    detailed = index.query_detailed(location["lat"], location["lon"], radius)
                    query_seconds += time.perf_counter() - before_query
                    detailed["location"]["id"] = location["id"]
                    detailed["location"]["role"] = location["role"]
                    detailed["referenceElevation"] = reference_sample
                    detailed["referenceAltitudeUsedM"] = reference_altitude
                    detailed["comparison"] = {"radiusAreaRatioVs1km": (radius / 1000) ** 2}
                    detailed["auditNotes"] = [
                        "Straight-line proximity does not establish connection or reachability.",
                        "Access tags are feature-specific mapped evidence, not legal permission certification.",
                        "Road class does not identify a safe stopping place; parking has no nighttime availability claim.",
                    ]
                    detailed["accessEvidenceCounts"] = {}
                    nearby_access_tagged = []
                    for feature_class in PUBLIC_CLASSES:
                        query = detailed["classes"][feature_class]
                        status_counts[radius][feature_class][query["status"]] += 1
                        candidates = query["candidates"]
                        detailed["accessEvidenceCounts"][feature_class] = _access_counts(candidates)
                        for candidate in candidates:
                            if any(
                                evidence.get("state") != "unknown"
                                or evidence.get("reason") != "missing_tag"
                                or audit.get("conditional")
                                or audit.get("unsupportedScoped")
                                for mode, evidence in candidate["access"].items()
                                for audit in (candidate["accessAudit"][mode],)
                            ):
                                nearby_access_tagged.append({
                                    "featureClass": feature_class,
                                    "osm": candidate["osm"],
                                    "distanceMExact": candidate["distanceMExact"],
                                })
                        nearest = query["nearestCandidate"]
                        if nearest is not None:
                            delta, reason, elevation_audit = index.elevation_delta_for_candidate(
                                nearest, reference_lat=location["lat"], reference_lon=location["lon"],
                                reference_altitude=reference_altitude, elevation_sampler=sampler,
                            )
                            query["nearestElevation"] = {
                                "elevationDeltaM": delta,
                                "elevationUnknownReason": reason,
                                "evidence": elevation_audit,
                            }
                    detailed["nearbyAccessTaggedFeatures"] = nearby_access_tagged
                    query_log.write(json.dumps(detailed, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":"), allow_nan=False) + "\n")
                    if location["role"] == "generated":
                        before_enrich = time.perf_counter()
                        query_payload = index.public_from_detailed(
                            detailed, reference_altitude=reference_altitude,
                            elevation_sampler=sampler,
                        )
                        payload = {"schema": 1, "source": source_id, "radiusM": radius, **query_payload}
                        validate_accessibility_payload(payload)
                        public_by_radius[radius].append({**location, "accessibility": payload})
                        enrichment_seconds += time.perf_counter() - before_enrich
                    radius_details[radius] = _report_location_radius(detailed)
                    del detailed
                location_summaries.append({
                    "id": location["id"], "role": location["role"], "name": location["name"],
                    "lat": location["lat"], "lon": location["lon"],
                    "referenceElevation": reference_sample,
                    "radii": radius_details,
                })

        serialization_start = time.perf_counter()
        baseline_compact = compact_bytes(baseline)
        payload_measurements = {}
        enriched_envelopes = {}
        for radius in radii_m:
            spot_payloads = {row["id"]: row["accessibility"] for row in public_by_radius[radius]}
            enriched_spots = []
            delta_bytes = []
            for original in spots:
                changed = dict(original)
                changed["accessibility"] = spot_payloads[original["id"]]
                enriched_spots.append(changed)
                delta_bytes.append(len(compact_bytes(changed)) - len(compact_bytes(original)))
            if [spot["id"] for spot in enriched_spots] != [spot["id"] for spot in spots] or any(
                {key: value for key, value in enriched.items() if key != "accessibility"} != original
                for original, enriched in zip(spots, enriched_spots)
            ):
                raise ValueError("Accessibility changed an existing spot field, ID or order")
            envelope = dict(baseline)
            envelope["spots"] = enriched_spots
            enriched_envelopes[radius] = envelope
            enriched_compact = compact_bytes(envelope)
            payload_measurements[str(radius)] = {
                "experimentalPublicRadius": radius == output_radius_m,
                "medianAdditionalCompactBytesPerSpot": median(delta_bytes),
                "maximumAdditionalCompactBytesPerSpot": max(delta_bytes),
                "totalTileCompactBytes": len(enriched_compact),
                "totalTileCompactGzipBytes": gzip_size(enriched_compact),
                "baselineTileCompactBytes": len(baseline_compact),
                "baselineTileCompactGzipBytes": gzip_size(baseline_compact),
                "tileCompactDeltaBytes": len(enriched_compact) - len(baseline_compact),
                "tileCompactGzipDeltaBytes": gzip_size(enriched_compact) - gzip_size(baseline_compact),
            }
        serialization_seconds = time.perf_counter() - serialization_start

        # Write the experimental output radius tile and a generated-only cluster
        # comparison. External controls never enter either output.
        tiles_dir = stage / "tiles"
        tiles_dir.mkdir()
        enriched_tile = enriched_envelopes[output_radius_m]
        (tiles_dir / "N028W017.json").write_bytes(pretty_bytes(enriched_tile))
        cluster_base = _write_cluster_comparison(stage, baseline, "baseline")
        cluster_enriched = _write_cluster_comparison(stage, enriched_tile, "enriched")
        cluster_preservation = _assert_cluster_shape_preserved(stage)
        cluster_measurements = {
            "baseline": cluster_base,
            "enriched": cluster_enriched,
            "uncompressedDeltaBytes": cluster_enriched["uncompressedBytes"] - cluster_base["uncompressedBytes"],
            "gzipDeltaBytes": cluster_enriched["gzipBytes"] - cluster_base["gzipBytes"],
            "representativeDeltaBytes": cluster_enriched["representativeBytes"] - cluster_base["representativeBytes"],
            "representativeDeltaByLevel": {
                str(level): cluster_enriched["representativeBytesByLevel"][str(level)] - cluster_base["representativeBytesByLevel"][str(level)]
                for level in range(1, 7)
            },
        }

        write_accessibility_source_record(stage / "package", manifest)

        status_summary = {
            str(radius): {feature_class: dict(sorted(counts.items()))
                          for feature_class, counts in by_class.items()}
            for radius, by_class in status_counts.items()
        }
        pipeline = _pipeline_state()
        audit_completeness = manifest.get("audit_completeness", {
            "restriction": "restriction:unlocated_restriction" not in manifest.get("errors_by_class", {}),
        })
        source_disk = manifest["pbf_provenance"]["pbf_size"] + manifest["pbf_provenance"]["coverage_polygon_size"]
        output_files = [path for path in stage.rglob("*") if path.is_file()]
        pilot_output_bytes = sum(path.stat().st_size for path in output_files)
        peak_rss_mib = rss_sampler.stop()
        peak_rss_delta_mib = rss_sampler.peak_delta_mib
        summary = {
            "schema": 1,
            "run_id": output_dir.name,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_id": source_id,
            "baseline": {"tile": "N028W017.json", "sha256": baseline_hash,
                         "version": baseline["version"], "spot_count": len(spots)},
            "controls": {"count": 2, "ids": [control["id"] for control in controls_spec["controls"]],
                         "included_in_tiles": False, "included_in_clusters": False},
            "radii_m": radii_m,
            "output_radius_m": output_radius_m,
            "query_status_counts": status_summary,
            "audit_completeness": audit_completeness,
            "payload_measurements": payload_measurements,
            "cluster_measurements": cluster_measurements,
            "preservation": {
                "spot_fields_ids_order_unchanged": True,
                "spot_count": len(spots),
                "cluster_structure": cluster_preservation,
                "controls_excluded": True,
            },
            "resources": {
                "index_bytes": manifest["index"]["size"],
                "retained_source_bytes": source_disk,
                "pilot_output_bytes": pilot_output_bytes,
                "query_seconds": query_seconds,
                "enrichment_seconds": enrichment_seconds,
                "serialization_seconds": serialization_seconds,
                "total_workload_seconds": time.perf_counter() - start,
                "peak_rss_mib": peak_rss_mib,
                "peak_rss_delta_mib": peak_rss_delta_mib,
                "preparation_metrics": manifest.get("metrics"),
            },
            "input_copies": copied,
            "pipeline": pipeline,
            "review_status": "pending_maintainer_review",
            "coverage_note": manifest["coverage_note"],
            "known_exclusions": [
                "Only the 16 frozen Tenerife generated references and two audit-only controls are evaluated.",
                "No other Canary Islands are enriched; no new spots are created.",
                "Cycleways, barriers, parking spaces/entrances, access areas, and restrictions are audit-only.",
                "OSM coverage is not a complete inventory of real-world features.",
            ],
            "locations": [
                {"id": item["id"], "role": item["role"], "name": item["name"],
                 "lat": item["lat"], "lon": item["lon"],
                 "nearest_by_radius": {
                     str(radius): {
                         feature_class: {
                             "status": item["radii"][radius]["classes"][feature_class]["status"],
                             "reason": item["radii"][radius]["classes"][feature_class].get("reason"),
                             "nearest": (item["radii"][radius]["classes"][feature_class]["nearestCandidate"] or {}).get("osm"),
                             "distanceMExact": (item["radii"][radius]["classes"][feature_class]["nearestCandidate"] or {}).get("distanceMExact"),
                             "candidateCount": item["radii"][radius]["classes"][feature_class]["candidateCount"],
                            "nearestElevation": item["radii"][radius]["classes"][feature_class].get("nearestElevation"),
                            "access": (item["radii"][radius]["classes"][feature_class]["nearestCandidate"] or {}).get("access"),
                         }
                         for feature_class in PUBLIC_CLASSES
                     }
                     for radius in radii_m
                 }}
                for item in location_summaries
            ],
        }
        (audit_dir / "summary.json").write_bytes(pretty_bytes(summary))
        report = _render_report(summary, location_summaries, manifest)
        (audit_dir / "report.md").write_text(report, encoding="utf-8")
        for _ in range(3):
            exact_output_bytes = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
            if exact_output_bytes == summary["resources"]["pilot_output_bytes"]:
                break
            summary["resources"]["pilot_output_bytes"] = exact_output_bytes
            (audit_dir / "summary.json").write_bytes(pretty_bytes(summary))
            report = _render_report(summary, location_summaries, manifest)
            (audit_dir / "report.md").write_text(report, encoding="utf-8")
        else:
            raise ValueError("Pilot output byte measurement did not stabilize")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(output_dir)
        return summary
    except BaseException:
        rss_sampler.stop()
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _render_report(summary: dict, locations: list[dict], manifest: dict) -> str:
    parking_found = [
        summary["query_status_counts"][str(radius)].get("parking", {}).get("found", 0)
        for radius in summary["radii_m"]
    ]
    lines = [
        "# Tenerife accessibility pilot",
        "",
        f"Run: `{summary['run_id']}`  ",
        f"Accessibility source: `{summary['source_id']}`  ",
        f"Frozen tile SHA-256: `{summary['baseline']['sha256']}`  ",
        f"Pipeline commit: `{summary['pipeline']['pipeline_commit']}`  ",
        f"Pipeline worktree clean: `{str(summary['pipeline']['pipeline_worktree_clean']).lower()}`  ",
        f"Review status: **{summary['review_status']}**",
        "",
        "## Source and completeness",
        "",
        f"- OSM: {manifest['pbf_provenance']['source_url']} ({manifest['pbf_provenance']['pbf_size']:,} bytes; SHA-256 `{manifest['pbf_provenance']['pbf_sha256']}`).",
        f"- PBF snapshot metadata: `{json.dumps(manifest['pbf_provenance']['pbf_header'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Prepared domain: EPSG:32628; core bbox `{manifest['scope_bbox']}` plus {manifest['preparation_buffer_m']:,} m.",
        f"- Feature inventory: `{json.dumps(manifest['counts_by_class'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Extraction errors: `{json.dumps(manifest['errors_by_class'], ensure_ascii=False, sort_keys=True)}`.",
        f"- Class completeness: `{json.dumps(manifest['class_completeness'], sort_keys=True)}`.",
        f"- Restriction-relation audit complete: `{summary['audit_completeness']['restriction']}`; {manifest['errors_by_class'].get('restriction:unlocated_restriction', 0)} relations had no located via member and could not be placed. Nearby relation lists may omit them; this does not affect road, track, path or parking search completeness.",
        f"- {manifest['coverage_note']}",
        "- Controls are included in audit records only. They do not enter the output tile or cluster data.",
        "",
        "## Radius comparison",
        "",
        "| Radius | Relative area | Status counts (road / track / path / parking) | Median added bytes/spot | Max added bytes/spot | Tile gzip delta |",
        "|---:|---:|---|---:|---:|---:|",
    ]
    for radius in summary["radii_m"]:
        counts = summary["query_status_counts"][str(radius)]
        status = " / ".join(
            f"{feature_class}: " + ", ".join(f"{key} {value}" for key, value in counts[feature_class].items())
            for feature_class in PUBLIC_CLASSES
        )
        payload = summary["payload_measurements"][str(radius)]
        lines.append(
            f"| {radius:,} m | {radius**2/1_000_000:.2f}× | {_safe_md(status)} | "
            f"{payload['medianAdditionalCompactBytesPerSpot']} | {payload['maximumAdditionalCompactBytesPerSpot']} | "
            f"{payload['tileCompactGzipDeltaBytes']:+,} |"
        )
    lines.extend([
        "",
        "The 1,000 m radius is the initial experiment only. Wider radii include more unrelated context; proximity does not establish a route, connection, stopping place, or legal permission.",
        "",
        "## All 18 locations and four radii",
        "",
        "Each cell shows the nearest mapped feature within that class and radius. `unknown` means the search cannot establish a nearest or empty result. Restriction relations are separate from access tags on road, track, path, and parking candidates. Full raw tags, access expressions, nearest points, all candidates, restriction evidence, timings, and elevation samples are in `queries.jsonl`.",
        "",
        "| Location | Role | Radius | Road | Track | Path | Parking | Restriction relations | Access-tagged candidates | Query seconds |",
        "|---|---|---:|---|---|---|---:|---:|---:|---:|",
    ])
    for location in locations:
        for radius, detail in location["radii"].items():
            values = []
            for feature_class in PUBLIC_CLASSES:
                query = detail["classes"][feature_class]
                candidate = query["nearestCandidate"]
                if query["status"] == "unknown":
                    values.append(f"unknown ({query.get('reason')})")
                elif candidate:
                    values.append(f"{candidate['osm']} @ {candidate['distanceMExact']:.2f} m")
                else:
                    values.append("none")
            lines.append(
                f"| {_safe_md(location['name'])} (`{location['id']}`) | {location['role']} | {radius:,} m | "
                f"{values[0]} | {values[1]} | {values[2]} | {values[3]} | "
                f"{detail['nearbyRestrictionCount']} | {detail['nearbyAccessTaggedCount']} | "
                f"{detail['metrics']['querySeconds']:.4f} |"
            )
    lines.extend([
        "",
        "## Focus locations at 1 km",
        "",
        "The generated Pico Viejo reference, mapped summit control, and Minas de San José control are distinct coordinates. Los Juncos and La Era de la Casa are included as generated spots. Controls are audit-only and never enter tiles or clusters.",
        "",
        "The table reports the current nearest candidate in each physical class, its straight-line distance, elevation difference or unknown reason, and path-specific access evidence.",
        "",
        "## Access evidence and practical limits",
        "",
        f"Across the 18 locations, mapped parking is found at {parking_found[0]} within 500 m, {parking_found[1]} within 1 km, {parking_found[2]} within 3 km, and {parking_found[3]} within 5 km. That increase gives broader context while raising the chance that a distant feature is unrelated. The 1 km radius remains an initial experiment; no radius establishes a route, connection, safe stopping place, or legal permission.",
        "",
        "## Elevation semantics",
        "",
        "`elevationDeltaM = preserved reference altitude − DEM at the nearest point on the selected feature`. Positive means the reference point is higher. Null values carry a reason such as water, NoData, out-of-coverage, non-ground level, unknown reference altitude, or `spot_dem_mismatch`. The Copernicus surface raster is not a trail survey.",
        "",
        "## Payload and cluster measurements",
        "",
        f"- Index: {summary['resources']['index_bytes']:,} bytes; retained PBF plus coverage polygon: {summary['resources']['retained_source_bytes']:,} bytes; pilot artifacts: {summary['resources']['pilot_output_bytes']:,} bytes.",
        f"- Query {summary['resources']['query_seconds']:.2f} s; enrichment projection {summary['resources']['enrichment_seconds']:.2f} s; serialization {summary['resources']['serialization_seconds']:.2f} s; total {summary['resources']['total_workload_seconds']:.2f} s.",
        f"- Sampled peak pilot RSS: {summary['resources']['peak_rss_mib']:.1f} MiB total, +{summary['resources']['peak_rss_delta_mib']:.1f} MiB over its initialized-process baseline. Preparation metrics: `{json.dumps(summary['resources']['preparation_metrics'], sort_keys=True)}`.",
        f"- Preparation temporary-disk high-water: {summary['resources']['preparation_metrics']['temporary_disk_high_water_bytes']:,} bytes. Cluster preservation: `{json.dumps(summary['preservation']['cluster_structure'], sort_keys=True)}`. See `summary.json` for per-level cluster hashes and byte counts.",
        f"- Cluster files across six levels: baseline {summary['cluster_measurements']['baseline']['uncompressedBytes']:,} bytes / {summary['cluster_measurements']['baseline']['gzipBytes']:,} gzip bytes; enriched {summary['cluster_measurements']['enriched']['uncompressedBytes']:,} bytes / {summary['cluster_measurements']['enriched']['gzipBytes']:,} gzip bytes.",
        "",
        "## Licensing and review limits",
        "",
        "Source attribution: © OpenStreetMap contributors. OSM-derived structured evidence is data. Any later distribution requires the applicable ODbL 1.0 attribution, derivative database/data offer and preparation recipe. Retain the existing Copernicus notices. This local pilot does not grant distribution or publication approval.",
        "",
        "Maintainer review is pending. The chosen 1,000 m output radius and public fields remain provisional.",
        "",
    ])
    focus_ids = {
        "28.2479_-16.6563", "control:pico-viejo-summit", "control:minas-de-san-jose",
        "28.3896_-16.3479", "28.2979_-16.4396",
    }
    focus_lines = [
        "",
        "| Location | Role | Road | Track | Path | Parking |",
        "|---|---|---|---|---|---|",
    ]
    for location in locations:
        if location["id"] not in focus_ids:
            continue
        classes = location["radii"][1000]["classes"]
        cells = []
        for feature_class in PUBLIC_CLASSES:
            query = classes[feature_class]
            candidate = query["nearestCandidate"]
            if candidate is None:
                cells.append("none")
                continue
            elevation = query.get("nearestElevation") or {}
            delta = elevation.get("elevationDeltaM")
            delta_text = (f"Δ {delta:+d} m" if delta is not None
                          else f"Δ unknown ({elevation.get('elevationUnknownReason')})")
            cell = f"{candidate['osm']} @ {candidate['distanceMExact']:.2f} m; {delta_text}"
            if feature_class == "path":
                access = candidate["access"]
                cell += f"; foot {access['foot']['state']}, car {access['motorcar']['state']}"
            cells.append(cell)
        focus_lines.append(
            f"| {_safe_md(location['name'])} (`{location['id']}`) | {location['role']} | "
            + " | ".join(cells) + " |"
        )
    focus_heading = lines.index("## Access evidence and practical limits")
    lines[focus_heading:focus_heading] = focus_lines
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spots", required=True)
    parser.add_argument("--accessibility-manifest", required=True)
    parser.add_argument("--dem-manifest", required=True)
    parser.add_argument("--dem-cache-dir", default="input/elevation")
    parser.add_argument("--controls", default="validation/accessibility/tenerife-controls.json")
    parser.add_argument("--radii-m", nargs="+", required=True, type=int)
    parser.add_argument("--output-radius-m", required=True, type=int)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = run_pilot(
        spots_path=Path(args.spots), accessibility_manifest=Path(args.accessibility_manifest),
        dem_manifest=Path(args.dem_manifest), dem_cache_dir=Path(args.dem_cache_dir),
        controls_path=Path(args.controls), radii_m=args.radii_m,
        output_radius_m=args.output_radius_m, output_dir=Path(args.output_dir),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
