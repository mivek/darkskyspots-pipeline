#!/usr/bin/env python3
"""Compare Tenerife query evidence between a validated and generalized index."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

COMPARISON_POLICY = {
    "compared": "All query and geographic evidence fields, including selected and candidate features, exact and rounded distances, access evidence, elevation samples and exact/rounded deltas, coverage, statuses, unknown reasons, restrictions, coordinates, and OSM metadata.",
    "excluded_paths": [
        "metrics.querySeconds",
        "sourceId",
        "source_id",
        "sourceIdentity",
        "source_identity",
        "datasetId",
    ],
    "exclusion_reason": "The listed fields describe the selected immutable source identity or execution timing. Candidate evidence, exact measurements, and all other fields remain under strict JSON equality.",
}
_EXCLUDED_PATHS = {
    ("metrics", "querySeconds"),
    ("sourceId",), ("source_id",), ("sourceIdentity",),
    ("source_identity",), ("datasetId",),
}
_MISSING = object()


def _read(path: Path) -> dict[tuple[str, str, int], dict]:
    rows = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        location = record["location"]
        key = (str(location["id"]), str(location["role"]), int(record["radiusM"]))
        if key in rows:
            raise ValueError(f"Duplicate pilot query key at {path}:{line_number}: {key}")
        rows[key] = record
    return rows


def _canonical(record: dict) -> dict:
    """Copy a query record while removing only documented non-geographic fields."""
    canonical = json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
    for path in _EXCLUDED_PATHS:
        parent = canonical
        for part in path[:-1]:
            if not isinstance(parent, dict) or part not in parent:
                break
            parent = parent[part]
        else:
            if isinstance(parent, dict):
                parent.pop(path[-1], None)
    return canonical


def _field_differences(reference, candidate, path: tuple[str, ...] = ()):
    if isinstance(reference, dict) and isinstance(candidate, dict):
        for key in sorted(reference.keys() | candidate.keys()):
            yield from _field_differences(
                reference.get(key, _MISSING), candidate.get(key, _MISSING), path + (str(key),),
            )
        return
    if isinstance(reference, list) and isinstance(candidate, list):
        for index in range(max(len(reference), len(candidate))):
            left = reference[index] if index < len(reference) else _MISSING
            right = candidate[index] if index < len(candidate) else _MISSING
            yield from _field_differences(left, right, path + (str(index),))
        return
    if type(reference) is not type(candidate) or reference != candidate:
        yield ".".join(path), reference, candidate


def _report_value(value):
    return {"present": False} if value is _MISSING else value


def compare(reference_path: Path, candidate_path: Path) -> dict:
    reference = _read(reference_path)
    candidate = _read(candidate_path)
    if reference.keys() != candidate.keys():
        missing = sorted(reference.keys() - candidate.keys())
        extra = sorted(candidate.keys() - reference.keys())
        raise ValueError(f"Pilot query sets differ: missing={missing[:5]}, extra={extra[:5]}")
    differences = []
    field_counts = Counter()
    for key in sorted(reference):
        old = _canonical(reference[key])
        new = _canonical(candidate[key])
        evidence_differences = list(_field_differences(old, new))
        if not evidence_differences:
            continue
        changed = []
        details = []
        for path, before, after in evidence_differences:
            field_counts[path] += 1
            changed.append(path)
            details.append({
                "path": path,
                "reference": _report_value(before),
                "candidate": _report_value(after),
            })
        differences.append({
            "location": {"id": key[0], "role": key[1], "radiusM": key[2]},
            "changed": changed, "evidence_differences": details,
        })
    return {
        "schema": 2,
        "comparison_policy": COMPARISON_POLICY,
        "reference": str(reference_path),
        "candidate": str(candidate_path),
        "query_count": len(reference),
        "exact_match_count": len(reference) - len(differences),
        "difference_count": len(differences),
        "difference_fields": dict(sorted(field_counts.items())),
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-queries", type=Path, required=True)
    parser.add_argument("--candidate-queries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.reference_queries, args.candidate_queries)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "tenerife-regression.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    report = [
        "# Tenerife accessibility regression comparison", "",
        "All query evidence is compared using strict JSON equality. Only the listed source identity fields and query execution time are excluded:",
        "", f"- Excluded paths: `{', '.join(COMPARISON_POLICY['excluded_paths'])}`",
        "", COMPARISON_POLICY["exclusion_reason"], "",
        f"- Queries compared: {result['query_count']}",
        f"- Exact evidence matches: {result['exact_match_count']}",
        f"- Queries with differences: {result['difference_count']}",
        f"- Differences by evidence field: `{json.dumps(result['difference_fields'], sort_keys=True)}`",
        "",
    ]
    for difference in result["differences"]:
        location = difference["location"]
        report.append(f"## {location['id']} ({location['role']}, {location['radiusM']} m)")
        report.append("")
        for item in difference["evidence_differences"]:
            report.append(
                f"- `{item['path']}`: `{json.dumps(item['reference'], sort_keys=True)}` → "
                f"`{json.dumps(item['candidate'], sort_keys=True)}`"
            )
        report.append("")
    (args.output_dir / "tenerife-regression.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "query_count", "exact_match_count", "difference_count", "difference_fields"
    )}, ensure_ascii=False, indent=2))
    return 0 if not result["differences"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
