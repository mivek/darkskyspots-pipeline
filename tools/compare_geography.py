#!/usr/bin/env python3
"""Compare saved D with reviewed-water and blanket-OSM qualification offline.

No ALR recomputation, source-data modification, commit or network publication.
Every additional examined/rejected centre is retained, not just final counts.
"""
import argparse
from collections import Counter
import gc
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import rasterio
from shapely import points
from shapely.geometry import shape
from shapely.strtree import STRtree

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.coverage import ensure_coverage,load_places
from src.elevation import ElevationSampler,sha256
from src.extract import mesh_darkest,redundancy_filter
from src.geographic_data import GeographicDataset,records
from src.geographic_publication import approve_tree,GeographicApprovalError
from src.geonames import GeoNamesIndex,NAMING_FEATURE_CODES
from src.enrich import enrich_all
from src.coverage import attach_near_town
from src.clusters import write_cluster_files
from src.publish import merge_publication_envelopes
from src.regions import get_region,load_regions,build_region_ownerships
from src.selection import CandidateQualifier
from src.tile_export import classify_spots_into_tiles
from tools.compare_wbm import read_reference,transitions,point_key,coverage_counts,git


def write(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


class BlanketMask:
    """Deliberately aggressive comparator, never used for publication."""
    def __init__(self,dataset,name):
        land,water=[],[]
        for f in records(dataset.entries[dataset.regions[name]['evidence']]):
            kind=f['properties']['kind']
            if kind=='marine_land':land.append(shape(f['geometry']))
            elif kind=='water':water.append(shape(f['geometry']))
        self.land,self.water=STRtree(land),STRtree(water)

    def qualify(self,candidates,*,rejected=None):
        if not candidates:return []
        p=points([(c['lon'],c['lat']) for c in candidates])
        land=set(map(int,self.land.query(p,predicate='covered_by')[0]))
        water=set(map(int,self.water.query(p,predicate='covered_by')[0]))
        keep=land-water
        if rejected is not None:
            rejected.extend(dict(point=dict(c),reason='blanket_OSM_exclusion',decisions=[])
                            for i,c in enumerate(candidates) if i not in keep)
        return [c for i,c in enumerate(candidates) if i in keep]


def selected(d,b,tr,region,sampler,mask):
    stats={}
    qualifier=CandidateQualifier(region,tr,'EPSG:4326',sampler,reviewed_water=mask)
    start=time.perf_counter()
    result=mesh_darkest(d,tr,qualify=qualifier,stats=stats)
    elapsed=time.perf_counter()-start
    for p in result:p['bortle']=int(b[p['row'],p['col']])
    return result,dict(seconds=elapsed,stats=stats,qualification=dict(qualifier.counts)),qualifier.reviewed_rejections


def snapshot(output,variant,results,published,args):
    ownerships=build_region_ownerships(load_regions())
    envelopes=published
    for name,variants in results.items():
        candidates=[dict(p) for p in variants[variant]['covered']]
        region=get_region(name)
        attach_near_town(candidates,load_places(region))
        index=GeoNamesIndex.from_filtered_extracts(data_dir='data',countries=region['osm_country_code'],
             bbox=region['bbox'],feature_codes=NAMING_FEATURE_CODES)
        with ElevationSampler(args.dem_manifests/f'{name}.json',args.dem_cache) as sampler:
            enriched=enrich_all(index.enrich_spots(candidates),elevation_sampler=sampler)
        grouped=classify_spots_into_tiles(enriched)
        incoming={tile:dict(tile=tile,spots=spots,source=f'VIIRS/2025/{name}',version='2025.0',generated='2026-11-01T00:00:00Z')
                  for tile,spots in grouped.items()}
        envelopes=merge_publication_envelopes(envelopes,incoming,ownerships[name])
    directory=output/'snapshots'/variant
    for tile,envelope in envelopes.items():write(directory/'spots'/f'{tile}.json',envelope)
    write_cluster_files(directory/'spots',directory/'clusters',data_year=2025,generated='2026-11-01T00:00:00Z')
    subprocess.run(['git','init','-q',str(directory)],check=True)
    git(directory,'add','.')
    return directory


def compare(args):
    output=args.output.resolve();source=args.data_repo.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Simulation output must be separate from the source data repository')
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty comparison output directory')
    output.mkdir(parents=True,exist_ok=True)
    before=git(source,'status','--porcelain')
    commit,published=read_reference(source,args.reference)
    dataset=GeographicDataset(args.manifest)
    controls=json.loads((ROOT/'tests/fixtures/geographic_review_controls.json').read_text())['controls']
    controls=[dict(p,rejected=not bool(dataset.qualify([p]))) for p in controls]
    for p in controls:p['passed']=(p['rejected']==(p['expected']=='reject_water'))
    write(output/'controls.json',controls)
    results,summaries={},{}
    for name in ['france','uk_ireland','canaries']:
        directory=args.simulation/name
        metadata=json.loads((directory/'arrays/metadata.json').read_text())
        for layer in ['darkness','bortle']:
            if sha256(directory/'arrays'/f'{layer}.npy')!=metadata[f'{layer}_sha256']:
                raise ValueError('Saved ALR-derived arrays failed integrity verification')
        d=np.load(directory/'arrays/darkness.npy',mmap_mode='r');b=np.load(directory/'arrays/bortle.npy',mmap_mode='r')
        tr=rasterio.Affine(*metadata['transform']);region=get_region(name)
        baseline=json.loads((directory/'D.json').read_text())
        original=json.loads((directory/'A.json').read_text())
        places=load_places(region)
        results[name]={}
        with ElevationSampler(args.dem_manifests/f'{name}.json',args.dem_cache) as sampler:
            replay,timing,_=selected(d,b,tr,region,sampler,None)
            if replay!=baseline['selected']:raise ValueError(f'D baseline selection changed before new filtering: {name}')
            akeys={point_key(p) for p in original['covered']}
            historical=[p for p in replay if point_key(p) in akeys]
            historical_rejections=[];dataset.qualify(historical,rejected=historical_rejections)
            write(output/name/'historical-rejections.json',historical_rejections)
            diagnostics=dataset.audit(replay)
            write(output/name/'full-D-pool-audit.json',diagnostics)
            for variant in ['reviewed','blanket']:
                print(name,variant,'selection',flush=True)
                mask=dataset if variant=='reviewed' else BlanketMask(dataset,name)
                chosen,metrics,rejected=selected(d,b,tr,region,sampler,mask)
                for r in rejected:r['point']['bortle']=int(b[r['point']['row'],r['point']['col']])
                dedup=redundancy_filter(chosen);covered=ensure_coverage(dedup,chosen,places)
                values=sampler.wbm_classes([p['lat'] for p in covered],[p['lon'] for p in covered])
                if np.any(values!=0):raise ValueError('Non-land WBM result survived new qualification')
                pool_counts=coverage_counts(places,chosen);final_counts=coverage_counts(places,covered)
                if np.any(final_counts<np.minimum(4,pool_counts)):raise ValueError('Coverage contract violated')
                baseline_counts=coverage_counts(places,baseline['covered'])
                decreases=[dict(place=places[i],before=int(baseline_counts[i]),after=int(final_counts[i]),pool=int(pool_counts[i]))
                        for i in np.flatnonzero(final_counts<baseline_counts)]
                losses=[p for p in decreases if p['before']>=4 and p['after']<4]
                result=dict(selected=chosen,deduplicated=dedup,covered=covered,rejections=rejected,
                    changes={stage:transitions(baseline[stage],points,tr) for stage,points in
                             [('selected',chosen),('deduplicated',dedup),('covered',covered)]},
                    coverage_losses=losses,coverage_count_decreases=decreases,
                    metrics=metrics,wbm_final=dict(Counter(map(int,values))))
                results[name][variant]=result
                write(output/name/f'{variant}.json',result)
                del mask;gc.collect()
            summaries[name]=dict(baseline_exact=True,D_seconds=timing['seconds'],historical_controls=len(historical),
                historical_rejected=len(historical_rejections),
                variants={v:dict(counts={k:len(result[k]) for k in ['selected','deduplicated','covered']},
                    rejections=len(result['rejections']),selection_seconds=result['metrics']['seconds'],
                    changes={s:x['counts'] for s,x in result['changes'].items()},coverage_losses=len(result['coverage_losses']))
                    for v,result in results[name].items()})
            print(name,summaries[name],flush=True)
    # Bootstrap the exact existing publication, not a synthetic approximation.
    bootstrap=output/'bootstrap-published'
    subprocess.run(['git','clone','--quiet','--no-hardlinks',str(source),str(bootstrap)],check=True)
    git(bootstrap,'checkout','--quiet',commit)
    audits={}
    for variant,directory in [('published',bootstrap),
                              *[(v,snapshot(output,v,results,published,args)) for v in ['reviewed','blanket']]]:
        try:audits[variant]=approve_tree(directory,dataset=dataset,report_dir=output/'gate-reports')
        except GeographicApprovalError as exc:audits[variant]=exc.report
        write(output/f'gate-{variant}.json',audits[variant])
    if git(source,'status','--porcelain')!=before:raise ValueError('Source data repository changed')
    report=dict(reference_commit=commit,source_repository_unchanged=True,
        geographic_revision=dataset.revision,controls_passed=all(p['passed'] for p in controls),
        controls_failures=[p for p in controls if not p['passed']],regions=summaries,
        gates={v:dict(status=r['status'],counts=r['counts'],tree_oid=r['tree_oid']) for v,r in audits.items()},
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        note='Blanket OSM is diagnostic only. Intermediate pool audit findings do not require publication acceptance.')
    write(output/'report.json',report)
    print(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--simulation',required=True,type=Path)
    p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--data-repo',required=True,type=Path)
    p.add_argument('--reference',default='1502e35')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--dem-manifests',default=Path('data/elevation/manifests'),type=Path)
    p.add_argument('--dem-cache',default=Path('input/elevation'),type=Path)
    report=compare(p.parse_args())
    return 0 if report['controls_passed'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(f'Error: {exc}',file=sys.stderr)
        raise
