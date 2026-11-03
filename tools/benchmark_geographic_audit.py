#!/usr/bin/env python3
"""Measure only the final geographic audit on a preloaded staged Git payload.

Run this in a fresh process against a disposable three-region snapshot. Git,
payload loading, package verification, migrations and cluster generation are
outside the measured interval. Runtime evidence is strictly local.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.geographic_data import GeographicDataset
from src.geographic_publication import staged_tree, tree_spots, implementation_revision


def current_rss():
    # Linux process RSS includes native GEOS/GDAL memory, unlike tracemalloc.
    return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')


def benchmark(repo, manifest):
    before = time.perf_counter()
    tree = staged_tree(repo)
    spots = tree_spots(repo, tree)
    dataset = GeographicDataset(manifest, require_e3=True)
    code_revision = implementation_revision()
    preparation_seconds = time.perf_counter() - before
    baseline = current_rss()
    peak = [baseline]
    stop = threading.Event()
    errors = []

    def observe():
        while not stop.wait(.01):
            try:
                peak[0] = max(peak[0], current_rss())
            except Exception as exc:
                errors.append(exc)
                return

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    start = time.perf_counter()
    try:
        dataset.verify()
        verified = time.perf_counter()
        report = dataset.audit(spots)
    finally:
        seconds = time.perf_counter() - start
        peak[0] = max(peak[0], current_rss())
        stop.set()
        observer.join()
    if errors:
        raise RuntimeError('Audit RSS measurement failed') from errors[0]
    result = dict(schema=1, tree_oid=tree, geographic_revision=dataset.revision,
        implementation_revision=code_revision,
        decisions_revision=dataset.decisions_revision,
        regions=sorted(name for name, region in dataset.regions.items()
                       if any(p['country'] in region['countries'] for p in spots)),
        countries=sorted({p['country'] for p in spots}),
        final_spot_count=len(spots), audit_status=report['status'], counts=report['counts'],
        lookups=dict(dataset._e3_engine.metrics),
        audit_seconds=seconds, package_reverification_seconds=verified-start,
        geographic_evaluation_seconds=seconds-(verified-start), audit_peak_rss_mib=peak[0]/2**20,
        audit_baseline_rss_mib=baseline/2**20,
        audit_additional_peak_rss_mib=(peak[0]-baseline)/2**20,
        process_high_water_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        preparation_seconds=preparation_seconds,
        method='Fresh Linux process; 10-ms /proc RSS sampling during final verification and dataset.audit only. '
               'RSS includes preloaded inputs and native allocations; sampling may miss shorter peaks. '
               'Process high-water RSS includes preparation and is reported separately. '
               'Git, payload loading, initial package loading, migration and clusters excluded from audit time. '
               'Audit time includes final package integrity verification, WBM verification and raster reads. '
               'Filesystem caches are not flushed; a measured audit is not publication approval.')
    dataset.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path, help='Disposable staged final publication copy')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = benchmark(args.repo, args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
