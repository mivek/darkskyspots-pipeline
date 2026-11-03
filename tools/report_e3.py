#!/usr/bin/env python3
"""Compare production E3 replay artifacts and frozen geographic controls offline."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.geographic_data import GeographicDataset
from src.elevation import ElevationSampler, sha256
from src.enrich import spot_id
from src.regions import get_region
from src.coverage import load_places
from tools.compare_wbm import read_reference, cell_key, coverage_counts, transitions
from tools.validate_e3 import measured, write


def identifier(point):
    return spot_id(point["lat"], point["lon"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--frozen", required=True, type=Path)
    parser.add_argument("--source-data-repo", required=True, type=Path)
    parser.add_argument("--reference", default="1502e35")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    dataset = GeographicDataset(args.manifest, require_e3=True)
    summary = dict(
        geographic_revision=dataset.revision, regions={}, historical={}, controls=[]
    )
    historic = json.loads((args.study / "historical-controls.json").read_text())
    controls = json.loads((args.study / "regression-controls.json").read_text())
    chains = json.loads((args.study / "harmful-chain-comparison.json").read_text())
    tradeoffs = json.loads(
        (args.study / "final-alternatives-losing-Bortle.json").read_text()
    )
    final_population = []
    regressions = []
    for name in ["france", "uk_ireland", "canaries"]:
        scenarios = {
            s: json.loads((args.results / name / (s + ".json")).read_text())
            for s in ["D", "E2", "E3"]
        }
        stage_ids = {
            s: {
                stage: {identifier(p) for p in result[stage]}
                for stage in ["selected", "deduplicated", "covered"]
            }
            for s, result in scenarios.items()
        }
        print(name, "evidence", flush=True)
        meta = json.loads((args.frozen / name / "arrays/metadata.json").read_text())
        transform = rasterio.Affine(*meta["transform"])
        targets = {}
        expected = {}
        labels = {}

        def add(point, evidence=None, label=None):
            key = identifier(point)
            targets[key] = dict(targets.get(key, {}), **point)
            if evidence is not None:
                expected[key] = evidence
            if label:
                labels.setdefault(key, []).append(label)

        for c in historic:
            if c["region"] == name:
                add(c["point"], c["evidence"], "historical_WBM_land")
        for c in controls:
            if c["control"]["region"] == name:
                add(c["control"], c["evidence"], "review_control")
        for c in chains:
            if c["region"] == name:
                for field in ["historical", "original_harmful", "E2_suppressor"]:
                    if c.get(field):
                        add(c[field]["point"], c[field], "harmful_chain:" + field)
        for c in tradeoffs:
            if c["region"] == name:
                for s in ["D", "E2", "E3"]:
                    add(c[s]["point"], c[s], "final_Bortle_tradeoff:" + s)
        for scenario, result in scenarios.items():
            for point in result["covered"]:
                add(point, label=scenario + "_final")
        # Every changed mesh alternative, not just final positions.
        old_ids = {identifier(p) for p in scenarios["E2"]["selected"]}
        for point in scenarios["E3"]["selected"]:
            if identifier(point) not in old_ids:
                add(point, label="new_E3_mesh_alternative")
        with (
            gzip.open(args.study / name / "evidence.jsonl.gz", "rt")
            if (args.study / name / "evidence.jsonl.gz").exists()
            else gzip.open(args.study / "results" / name / "evidence.jsonl.gz", "rt")
        ) as stream:
            for line in stream:
                e = json.loads(line)
                key = e.get("id", identifier(e["point"]))
                if key in targets:
                    expected[key] = e
        with ElevationSampler.water_masks(
            dataset.entries[dataset.manifest["terrain"][name]], dataset.path.parent
        ) as sampler:
            findings = dataset.classify(list(targets.values()), name, sampler)
        by_id = {}
        for finding in findings:
            key = identifier(finding["spot"])
            e = expected.get(key)
            if e is None:
                raise ValueError(f"Control lacks frozen evidence: {name} {key}")
            exact = dict(
                confidence=finding["confidence"] == e["E3"]["level"],
                reasons=set(finding["reasons"]) == set(e["E3"]["reasons"]),
                WorldCover=finding["worldcover"] == e["WorldCover"],
                WBM=finding["base_evidence"]["wbm_class"]
                == e["base_evidence"]["wbm_class"],
            )
            finding.update(
                labels=labels.get(key, []),
                frozen_exact=exact,
                stages={
                    s: {
                        stage: key in stage_ids[s][stage]
                        for stage in ["selected", "deduplicated", "covered"]
                    }
                    for s in scenarios
                },
            )
            regressions.append(finding)
            by_id[key] = finding
            if not all(exact.values()):
                write(
                    args.output / "regression-deviation.json",
                    dict(actual=finding, frozen=e),
                )
                raise ValueError(f"Frozen geographic evidence deviation: {name} {key}")
        region_summary = {}
        places = load_places(get_region(name))
        for scenario, result in scenarios.items():
            selected = result["selected"]
            final = result["covered"]
            finals = {identifier(p) for p in final}
            historical_ids = {
                identifier(c["point"]) for c in historic if c["region"] == name
            }
            region_summary[scenario] = dict(
                final=len(final),
                final_confidence=dict(
                    Counter(by_id[identifier(p)]["confidence"] for p in final)
                ),
                historical_retained=len(finals & historical_ids),
                final_Bortle=dict(Counter(p["bortle"] for p in final)),
                final_darkness_mean=float(np.mean([p["darkness"] for p in final])),
            )
        for baseline in ["D", "E2"]:
            old = {identifier(p) for p in scenarios[baseline]["covered"]}
            new = {identifier(p) for p in scenarios["E3"]["covered"]}
            old_coverage = coverage_counts(places, scenarios[baseline]["covered"])
            new_coverage = coverage_counts(places, scenarios["E3"]["covered"])
            region_summary[baseline + "_to_E3"] = dict(
                retained=len(old & new),
                removed=sorted(old - new),
                added=sorted(new - old),
                new_or_worsened_deficits=[
                    dict(
                        place=places[i],
                        before=int(old_coverage[i]),
                        after=int(new_coverage[i]),
                    )
                    for i in np.flatnonzero(
                        (new_coverage < 4) & (new_coverage < old_coverage)
                    )
                ],
            )
            changed = transitions(
                scenarios[baseline]["selected"], scenarios["E3"]["selected"], transform
            )
            changed["cells"] = [c for c in changed["cells"] if c["kind"] != "retained"]
            write(args.output / name / (baseline + "-E3-mesh-changes.json"), changed)
        summary["regions"][name] = region_summary
        final_population.extend(
            {k: v for k, v in p.items() if not k.startswith("_geographic_")}
            for p in scenarios["E3"]["covered"]
        )
    summary["historical"] = dict(
        count=sum("historical_WBM_land" in f["labels"] for f in regressions),
        confidence=dict(
            Counter(
                f["confidence"]
                for f in regressions
                if "historical_WBM_land" in f["labels"]
            )
        ),
        all_frozen_evidence_exact=all(
            all(f["frozen_exact"].values()) for f in regressions
        ),
    )
    summary["controls"] = [
        dict(
            id=identifier(f["spot"]),
            name=f["spot"].get("name"),
            confidence=f["confidence"],
            reasons=f["reasons"],
            stages=f["stages"],
        )
        for f in regressions
        if "review_control" in f["labels"]
    ]
    write(args.output / "regressions.json", regressions)
    write(args.output / "tradeoffs.json", tradeoffs)
    write(args.output / "harmful-chains.json", chains)
    dataset.close()
    # Fresh reader; timing excludes Git, migration, clustering, initial package
    # verification and preceding evidence lookups. Cold E3 reader setup is in
    # the audit, matching the real publication entry point.
    dataset = GeographicDataset(args.manifest, require_e3=True)
    with measured() as metric:
        audit = dataset.audit(final_population)
    if audit["status"] != "passed":
        raise ValueError("Complete E3 final audit failed")
    write(args.output / "merged-E3-audit.json", dict(audit, measurement=metric))
    summary["merged_audit"] = dict(
        measurement=metric, counts=audit["counts"], final=len(final_population)
    )
    commit, envelopes = read_reference(args.source_data_repo, args.reference)
    published = [p for env in envelopes.values() for p in env["spots"]]
    with measured() as metric:
        bootstrap = dataset.audit(published)
    write(
        args.output / "published-bootstrap-audit.json",
        dict(bootstrap, measurement=metric, source_commit=commit),
    )
    summary["published_bootstrap"] = dict(
        status=bootstrap["status"],
        counts=bootstrap["counts"],
        final=len(published),
        source_commit=commit,
    )
    summary["artifact_inputs"] = {
        str(p): sha256(p)
        for p in [
            args.study / "historical-controls.json",
            args.study / "regression-controls.json",
            args.study / "CLASSIFICATION.md",
        ]
    }
    write(args.output / "summary.json", summary)
    dataset.close()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
