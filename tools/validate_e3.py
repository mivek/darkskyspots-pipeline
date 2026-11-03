#!/usr/bin/env python3
"""Replay frozen D/E2/E3 using production operators, never publish data."""
import argparse
from collections import Counter
import json, math, resource, sys, time, threading
from pathlib import Path
from contextlib import contextmanager

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.geographic_data import GeographicDataset
from src.elevation import ElevationSampler, sha256
from src.selection import CandidateQualifier
from src.extract import mesh_darkest, redundancy_filter
from src.coverage import ensure_coverage, load_places
from src.geographic_confidence import priority
from src.regions import get_region
from src.enrich import spot_id
from tools.compare_wbm import transitions, coverage_counts, read_reference


@contextmanager
def measured():
    def rss():
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024

    stop = threading.Event()
    metric = dict(baseline_rss_mib=rss())
    peak = [metric["baseline_rss_mib"]]

    def watch():
        while not stop.wait(0.01):
            peak[0] = max(peak[0], rss())

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    start = time.perf_counter()
    cpu = time.process_time()
    try:
        yield metric
    finally:
        peak[0] = max(peak[0], rss())
        stop.set()
        thread.join()
        metric.update(
            seconds=time.perf_counter() - start,
            cpu_seconds=time.process_time() - cpu,
            peak_rss_mib=peak[0],
            additional_peak_mib=peak[0] - metric["baseline_rss_mib"],
        )


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def comparison_fields(points):
    return [
        {k: p[k] for k in ["row", "col", "lat", "lon", "darkness", "bortle", "country"]}
        for p in points
    ]


def validate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    with measured() as prep:
        dataset = GeographicDataset(args.manifest, require_e3=True)
    summary = dict(preparation=prep, geographic_revision=dataset.revision, regions={})
    final = []
    for name in args.regions:
        print(name, "start", flush=True)
        arrays = args.frozen / name / "arrays"
        meta = json.loads((arrays / "metadata.json").read_text())
        for key in ["darkness", "bortle"]:
            if sha256(arrays / f"{key}.npy") != meta[key + "_sha256"]:
                raise ValueError("Frozen array integrity failure")
        d = np.load(arrays / "darkness.npy", mmap_mode="r")
        b = np.load(arrays / "bortle.npy", mmap_mode="r")
        tr = rasterio.Affine(*meta["transform"])
        region = get_region(name)
        places = load_places(region)
        scenarios = {}
        metrics = {}
        for scenario in args.scenarios:
            lookup_before = (
                dict(dataset._e3_engine.metrics) if dataset._e3_engine else {}
            )
            with ElevationSampler.water_masks(
                dataset.entries[dataset.manifest["terrain"][name]], dataset.path.parent
            ) as sampler:
                if scenario == "D":
                    q = CandidateQualifier(region, tr, meta["crs"], sampler)
                    rank = None
                elif scenario == "E3":
                    q = CandidateQualifier(
                        region,
                        tr,
                        meta["crs"],
                        sampler,
                        reviewed_water=dataset,
                        region_name=name,
                    )
                    rank = priority
                else:
                    # Explicit historical comparator. Never a production fallback.
                    geo = CandidateQualifier(
                        region, tr, meta["crs"], sampler, water=False
                    )

                    class E2Qualifier:
                        possible_cells = geo.possible_cells

                        def __call__(self, points):
                            points = geo(points)
                            reports = dataset.classify(points, name, sampler)
                            result = []
                            for p, f in zip(points, reports):
                                if f["confidence"] == "C":
                                    continue
                                ev = f["base_evidence"]
                                a = (
                                    ev["marine_land"]
                                    and ev["marine_support_land"]
                                    and ev["inland_support_coverage"]
                                    and not ev["water_features"]
                                    and not ev["wetland_features"]
                                )
                                p["_geographic_level"] = "A" if a else "B1"
                                result.append(p)
                            return result

                    q = E2Qualifier()
                    rank = priority
                stats = {}
                with measured() as selection:
                    selected = mesh_darkest(
                        d, tr, qualify=q, priority_key=rank, stats=stats
                    )
                    for p in selected:
                        p["bortle"] = int(b[p["row"], p["col"]])
                if hasattr(q, "counts"):
                    stats["qualification"] = dict(q.counts)
                lookup_metrics = (
                    {
                        k: v - lookup_before.get(k, 0)
                        for k, v in dataset._e3_engine.metrics.items()
                    }
                    if dataset._e3_engine
                    else {}
                )
                with measured() as deduplication:
                    dedup = redundancy_filter(selected, priority_key=rank)
                with measured() as coverage:
                    additions = []
                    covered = ensure_coverage(
                        dedup,
                        selected,
                        places,
                        priority_key=rank if scenario == "E3" else None,
                        additions=additions,
                    )
                points = dict(selected=selected, deduplicated=dedup, covered=covered)
                refpath = (
                    args.frozen / name / "D.json"
                    if scenario == "D"
                    else (
                        args.e2 / name / "E2.json"
                        if scenario == "E2"
                        else args.e3 / name / "E3.json"
                    )
                )
                reference = json.loads(refpath.read_text())
                differences = {
                    stage: transitions(reference[stage], ps, tr)
                    for stage, ps in points.items()
                }
                equal = {
                    stage: comparison_fields(ps) == comparison_fields(reference[stage])
                    for stage, ps in points.items()
                }
                counts = coverage_counts(places, covered)
                poolcounts = coverage_counts(places, selected)
                if not np.all(counts >= np.minimum(4, poolcounts)):
                    raise ValueError("Coverage contract violated")
                result = dict(
                    **points,
                    exact=equal,
                    changes=differences,
                    coverage_counts=counts.tolist(),
                    pool_coverage_counts=poolcounts.tolist(),
                    coverage_additions=additions,
                    metrics=dict(
                        selection=selection,
                        redundancy=deduplication,
                        coverage=coverage,
                        stats=stats,
                        lookups=lookup_metrics,
                    ),
                )
                if scenario == "E3":
                    assert all(p["_geographic_level"] == "A" for p in covered)
                    actual_levels = {
                        spot_id(p["lat"], p["lon"]): p["_geographic_level"]
                        for p in selected
                    }
                    assert actual_levels == {
                        spot_id(p["lat"], p["lon"]): p["level"]
                        for p in reference["selected"]
                    }
                    final.extend(
                        {k: v for k, v in p.items() if not k.startswith("_geographic_")}
                        for p in covered
                    )
                write(args.output / name / (scenario + ".json"), result)
                scenarios[scenario] = result
                metrics[scenario] = dict(
                    exact=equal,
                    counts={s: len(ps) for s, ps in points.items()},
                    metrics=result["metrics"],
                    final_confidence=dict(
                        Counter(
                            p.get("_geographic_level", "unclassified_D")
                            for p in covered
                        )
                    ),
                )
                print(name, scenario, len(covered), "exact", equal, flush=True)
                if not all(equal.values()):
                    raise ValueError(
                        f"Frozen {scenario} deviation: {name}; inspect full per-cell report"
                    )
        summary["regions"][name] = metrics
        if dataset._e3_engine:
            summary["regions"][name]["lookups"] = dict(dataset._e3_engine.metrics)
        write(args.output / "summary.json", summary)
    if final:
        with measured() as auditing:
            dataset.verify()
            report = dataset.audit(final)
        write(args.output / "final-audit.json", dict(report, measurement=auditing))
        if report["status"] != "passed":
            raise ValueError("E3 final geographic audit did not pass")
    summary["max_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    write(args.output / "summary.json", summary)
    dataset.close()
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--frozen", required=True, type=Path)
    p.add_argument("--e2", required=True, type=Path)
    p.add_argument("--e3", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--regions", nargs="+", default=["france", "uk_ireland", "canaries"])
    p.add_argument(
        "--scenarios", nargs="+", choices=["D", "E2", "E3"], default=["D", "E2", "E3"]
    )
    validate(p.parse_args())


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
