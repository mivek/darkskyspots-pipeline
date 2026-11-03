#!/usr/bin/env python3
"""Generate all E3 regions offline and audit one complete temporary snapshot.

There is no apply, commit, push or per-region publication mode.
"""
import argparse, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.e3_migration import frozen_region, assemble_snapshot
from src.geographic_data import GeographicDataset
from src.geographic_publication import GeographicApprovalError
from src.regions import load_regions


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-data-repo", required=True, type=Path)
    p.add_argument("--reference", default="HEAD")
    p.add_argument("--geographic-manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--year", required=True, type=int)
    p.add_argument(
        "--generated", required=True, help="Fixed UTC timestamp for reproducibility"
    )
    p.add_argument(
        "--frozen-simulation",
        type=Path,
        help="Verified saved arrays; omit to run the normal full-raster ALR pipeline",
    )
    p.add_argument("--input-dir", default=Path("input"), type=Path)
    p.add_argument(
        "--dem-manifest-dir", default=Path("data/elevation/manifests"), type=Path
    )
    p.add_argument("--dem-cache-dir", default=Path("input/elevation"), type=Path)
    args = p.parse_args()
    source = args.source_data_repo.resolve()
    output = args.output.resolve()
    if (
        output.is_relative_to(source)
        or source.is_relative_to(output)
        or output.exists()
    ):
        raise ValueError(
            "Use a new output directory separate from the published repository"
        )
    dataset = GeographicDataset(args.geographic_manifest, require_e3=True)
    regions = load_regions()
    regional = {}
    output.mkdir(parents=True)
    try:
        for name, region in regions.items():
            target = output / "regions" / name
            target.mkdir(parents=True)
            if args.frozen_simulation:
                trace = {}
                regional[name] = frozen_region(
                    name,
                    region,
                    args.frozen_simulation / name / "arrays",
                    dataset,
                    args.dem_manifest_dir / f"{name}.json",
                    args.dem_cache_dir,
                    year=args.year,
                    generated=args.generated,
                    trace=trace,
                )
                (target / "selection.json").write_text(
                    json.dumps(trace, indent=2) + "\n"
                )
                (target / "envelopes.json").write_text(
                    json.dumps(regional[name], indent=2) + "\n"
                )
            else:
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "run.py"),
                        "--region",
                        name,
                        "--year",
                        str(args.year),
                        "--no-push",
                        "--no-clusters",
                        "--input-dir",
                        str(args.input_dir),
                        "--output-dir",
                        str(target),
                        "--geographic-manifest",
                        str(args.geographic_manifest),
                        "--dem-manifest-dir",
                        str(args.dem_manifest_dir),
                        "--dem-cache-dir",
                        str(args.dem_cache_dir),
                    ],
                    check=True,
                )
                regional[name] = {
                    path.stem: json.loads(path.read_text())
                    for path in (target / "spots").glob("*.json")
                }
        try:
            report = assemble_snapshot(
                source,
                args.reference,
                regional,
                output / "snapshot",
                dataset,
                year=args.year,
                generated=args.generated,
                regions=regions,
                report_dir=output / "gate-reports",
            )
        except GeographicApprovalError as exc:
            report = exc.report
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                dict(
                    status=report["status"],
                    counts=report["counts"],
                    tree_oid=report["tree_oid"],
                ),
                indent=2,
            )
        )
        return 0 if report["status"] == "passed" else 1
    finally:
        dataset.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
