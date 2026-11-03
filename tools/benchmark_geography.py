#!/usr/bin/env python3
"""Isolated D/reviewed selection timings and RSS on frozen real region arrays."""
import argparse
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

import numpy as np
import rasterio

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.elevation import ElevationSampler
from src.geographic_data import GeographicDataset
from src.regions import get_region
from src.selection import CandidateQualifier
from src.extract import mesh_darkest


def worker(args):
    metadata=json.loads((args.simulation/args.region/'arrays/metadata.json').read_text())
    d=np.load(args.simulation/args.region/'arrays/darkness.npy',mmap_mode='r')
    b=np.load(args.simulation/args.region/'arrays/bortle.npy',mmap_mode='r')
    tr=rasterio.Affine(*metadata['transform']);region=get_region(args.region)
    start=time.perf_counter()
    dataset=GeographicDataset(args.manifest) if args.variant=='reviewed' else None
    dataset_load=time.perf_counter()-start
    expected_path=(args.comparison/args.region/'reviewed.json') if dataset else args.simulation/args.region/'D.json'
    expected=json.loads(expected_path.read_text())['selected']
    rounds=[]
    start=time.perf_counter()
    with ElevationSampler(args.dem_manifests/f'{args.region}.json',args.dem_cache) as sampler:
        terrain_verification=time.perf_counter()-start
        for trial in range(4):
            q=CandidateQualifier(region,tr,'EPSG:4326',sampler,reviewed_water=dataset)
            start=time.perf_counter()
            actual=mesh_darkest(d,tr,qualify=q)
            for p in actual:p['bortle']=int(b[p['row'],p['col']])
            classes=sampler.wbm_classes([p['lat'] for p in actual],[p['lon'] for p in actual])
            elapsed=time.perf_counter()-start
            if np.any(classes!=0) or actual!=expected:raise ValueError('Benchmark output differs from frozen scenario')
            rounds.append(dict(trial=trial,seconds=elapsed,selected=len(actual),additional_rejections=len(q.reviewed_rejections)))
    return dict(variant=args.variant,region=args.region,dataset_load_seconds=dataset_load,
        terrain_verification_seconds=terrain_verification,rounds=rounds,
        warm_median_seconds=statistics.median(r['seconds'] for r in rounds[1:]),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        note='First round excluded from warm median. Filesystem caches are not flushed. Outputs verified exactly; native WBM guard included.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--simulation',required=True,type=Path)
    p.add_argument('--comparison',required=True,type=Path)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--region',default='france')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--variant',choices=['D','reviewed'])
    p.add_argument('--dem-manifests',default=Path('data/elevation/manifests'),type=Path)
    p.add_argument('--dem-cache',default=Path('input/elevation'),type=Path)
    args=p.parse_args()
    if args.variant:
        print(json.dumps(worker(args)));return 0
    results=[]
    for variant in ['D','reviewed']:
        command=[sys.executable,__file__,*sys.argv[1:],'--variant',variant]
        results.append(json.loads(subprocess.check_output(command,text=True)))
    baseline,new=results
    ratio=new['warm_median_seconds']/baseline['warm_median_seconds']
    delta=new['peak_rss_kib']-baseline['peak_rss_kib']
    report=dict(results=results,warm_selection_ratio=ratio,additional_peak_rss_mib=delta/1024,
                time_budget_passed=ratio<=1.25,memory_budget_passed=delta<=256*1024)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['time_budget_passed'] and report['memory_budget_passed'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(f'Error: {exc}',file=sys.stderr)
        raise
