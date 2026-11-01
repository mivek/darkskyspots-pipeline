#!/usr/bin/env python3
"""Offline A/B/C/D selection comparison; writes only to a separate output tree."""
import argparse
from collections import Counter
import io
import json
import math
from pathlib import Path
import resource
import subprocess
import sys
import tarfile
import time

import numpy as np
import rasterio
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.alr import slice_and_compute
from src.clusters import write_cluster_files
from src.convert import alr_to_bortle, alr_to_darkness
from src.coverage import attach_near_town, ensure_coverage, load_places
from src.elevation import ElevationSampler, sha256
from src.enrich import enrich_all, spot_id
from src.extract import mesh_darkest, redundancy_filter
from src.geography import load_geography, classify_candidates
from src.geonames import GeoNamesIndex
from src.geonames import NAMING_FEATURE_CODES
from src.publish import merge_tile_envelopes
from src.regions import get_region, load_regions, build_region_ownerships
from src.selection import CandidateQualifier
from src.tile_export import classify_spots_into_tiles


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def read_reference(repo, revision):
    """Read committed JSON blobs, never the potentially edited working tree."""
    commit = git(repo, 'rev-parse', revision + '^{commit}').decode().strip()
    archive = git(repo, 'archive', commit, 'spots')
    envelopes = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile() and member.name.endswith('.json'):
                value = json.load(stream.extractfile(member))
                envelopes[value['tile']] = value
    return commit, envelopes


def select_scenario(darkness, bortle, transform, crs, region, sampler, scenario,
                    *, geography=None, stats=None):
    if scenario not in 'ABCD' or len(scenario) != 1:
        raise ValueError(f'Unknown scenario: {scenario}')
    stats = stats if stats is not None else {}
    qualify = CandidateQualifier(region, transform, crs, sampler,
                                  water=scenario in 'BD', geography=geography)
    if scenario in 'CD':
        points = mesh_darkest(darkness, transform, qualify=qualify, crs=crs, stats=stats)
    else:
        points = qualify(mesh_darkest(darkness, transform))
    for p in points:
        p['bortle'] = int(bortle[p['row'], p['col']])
    stats['qualification'] = dict(qualify.counts)
    return points


def cell_key(p, transform):
    cy = max(1, int(round(config.MESH_KM / 111.32 / abs(transform.e))))
    cx = max(1, int(round(config.MESH_KM / 111.32 / abs(transform.a))))
    return f'{p["row"] // cy}:{p["col"] // cx}'


def point_key(p):
    return p['row'], p['col']


def transitions(before, after, transform):
    old = {cell_key(p, transform): p for p in before}
    new = {cell_key(p, transform): p for p in after}
    records = []
    for cell in sorted(old.keys() | new.keys()):
        a, b = old.get(cell), new.get(cell)
        kind = ('new' if a is None else 'removed' if b is None else
                'retained' if point_key(a) == point_key(b) else 'moved')
        records.append(dict(cell=cell, kind=kind, before=a, after=b,
                            darkness_delta=b['darkness'] - a['darkness'] if a and b else None,
                            bortle_changed=a['bortle'] != b['bortle'] if a and b else None))
    countries = {p['country'] for p in before + after}
    return {'counts': dict(Counter(p['kind'] for p in records)),
            'by_country': {country: dict(before=sum(p['country'] == country for p in before),
                                         after=sum(p['country'] == country for p in after))
                           for country in sorted(countries)}, 'cells': records}


def sphere(points):
    radians = np.radians([[p['lat'], p['lon']] for p in points]).reshape(-1, 2)
    lat, lon = radians[:, 0], radians[:, 1]
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def coverage_counts(places, points):
    if not points:
        return np.zeros(len(places), dtype=int)
    return cKDTree(sphere(points)).query_ball_point(
        sphere(places), 2 * math.sin(config.COVERAGE_RADIUS_KM / 6371 / 2), return_length=True)


def historical_check(published, generated):
    old = {p['id']: p for p in published}
    new = {spot_id(p['lat'], p['lon']): p for p in generated}
    differences = []
    for identifier in sorted(old.keys() & new.keys()):
        fields = {k: {'published': old[identifier][k], 'A': new[identifier][k]}
                  for k in ('lat', 'lon', 'darkness', 'bortle', 'country')
                  if old[identifier][k] != new[identifier][k]}
        if fields:
            differences.append({'id': identifier, 'fields': fields})
    compatible = (old.keys() == new.keys() and all(
        all(k == 'darkness' and abs(v['published'] - v['A']) <= 1e-12 for k, v in item['fields'].items())
        for item in differences))
    return dict(published_count=len(old), A_count=len(new), common=len(old.keys() & new.keys()),
                removed=sorted(old.keys() - new.keys()), added=sorted(new.keys() - old.keys()),
                differences=differences,
                exact=old.keys() == new.keys() and not differences,
                compatible=compatible, darkness_absolute_tolerance=1e-12,
                note='All non-darkness fields must match exactly. Last-bit darkness differences are recorded, never attributed to WBM.')


def array_inputs(args, region_name, region, context):
    directory = args.output_dir / region_name / 'arrays'
    directory.mkdir(parents=True, exist_ok=True)
    input_path = args.input_dir / region_name / f'{args.year}.tif'
    parameters = {k: v for k, v in vars(config).items() if k.startswith('ALR_')}
    parameters['ALR_MAX_KM'] = context
    expected = dict(input_sha256=sha256(input_path), parameters=parameters,
                    implementation={name: sha256(ROOT / 'src' / name) for name in ['alr.py', 'convert.py', 'config.py']})
    meta_path = directory / 'metadata.json'
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text())
        if any(metadata.get(k) != v for k, v in expected.items()):
            raise ValueError(f'Incompatible array cache: {directory}')
        for layer in ('darkness', 'bortle'):
            if sha256(directory / f'{layer}.npy') != metadata[layer + '_sha256']:
                raise ValueError(f'Corrupt comparison array: {layer}')
    else:
        start = time.perf_counter()
        result = slice_and_compute(str(input_path), region['equal_area_epsg'], max_km=context)
        np.save(directory / 'darkness.npy', alr_to_darkness(result.data))
        np.save(directory / 'bortle.npy', alr_to_bortle(result.data))
        metadata = dict(expected, transform=list(result.transform)[:6], crs=str(result.crs),
                        alr_seconds=time.perf_counter() - start,
                        darkness_sha256=sha256(directory / 'darkness.npy'),
                        bortle_sha256=sha256(directory / 'bortle.npy'))
        meta_path.write_text(json.dumps(metadata, indent=2) + '\n')
    return (np.load(directory / 'darkness.npy', mmap_mode='r'),
            np.load(directory / 'bortle.npy', mmap_mode='r'),
            rasterio.Affine(*metadata['transform']), metadata)


def run_region(args, name, published, context):
    region = get_region(name)
    output = args.output_dir / name
    output.mkdir(parents=True, exist_ok=True)
    d, b, transform, meta = array_inputs(args, name, region, context)
    geography = load_geography()
    places = load_places(region)
    historical = [p for env in published.values() for p in env['spots']
                  if p['country'] in region['osm_country_code']
                  and region['bbox'][0] <= p['lon'] <= region['bbox'][2]
                  and region['bbox'][1] <= p['lat'] <= region['bbox'][3]]
    scenarios = {}
    for scenario in 'ABCD':
        print(name, scenario, 'start', flush=True)
        stats = {}
        verified_start = time.perf_counter()
        with ElevationSampler(args.dem_manifest_dir / f'{name}.json', args.dem_cache_dir) as sampler:
            integrity_seconds = time.perf_counter() - verified_start
            start = time.perf_counter()
            selected = select_scenario(d, b, transform, meta['crs'], region, sampler,
                                       scenario, geography=geography, stats=stats)
            selection_seconds = time.perf_counter() - start
            selected_classes = sampler.wbm_classes([p['lat'] for p in selected], [p['lon'] for p in selected])
            if scenario == 'D' and np.any(selected_classes != 0):
                raise ValueError('Scenario D selected a non-land WBM point')
            start = time.perf_counter()
            deduplicated = redundancy_filter(selected)
            dedup_seconds = time.perf_counter() - start
            start = time.perf_counter()
            covered = ensure_coverage(deduplicated, selected, places)
            coverage_seconds = time.perf_counter() - start
            if scenario == 'A':
                a_check = historical_check(historical, covered)
                (output / 'historical-A.json').write_text(json.dumps(a_check, indent=2) + '\n')
                if not a_check['compatible'] and not args.allow_historical_drift:
                    raise ValueError(f'Scenario A differs from published data in {name}; see historical-A.json')
            classes = sampler.wbm_classes([p['lat'] for p in covered], [p['lon'] for p in covered])
            if scenario == 'D' and np.any(classes != 0):
                raise ValueError('Scenario D contains a non-land WBM point')
            stage_counts = {stage: coverage_counts(places, points).tolist()
                            for stage, points in [('pool', selected), ('deduplicated', deduplicated), ('covered', covered)]}
            if np.any(np.asarray(stage_counts['covered']) < np.minimum(4, stage_counts['pool'])):
                raise ValueError('Coverage guarantee violated')
            io_stats = dict(sampler.wbm_io)
        selected_keys = {point_key(p) for p in deduplicated}
        result = dict(selected=selected, deduplicated=deduplicated, covered=covered,
                      coverage_added=[p for p in covered if point_key(p) not in selected_keys],
                      counts=dict(selected=len(selected), deduplicated=len(deduplicated), covered=len(covered)),
                      seconds=dict(integrity=integrity_seconds, selection=selection_seconds,
                                   deduplication=dedup_seconds, coverage=coverage_seconds),
                      selection_stats=stats, wbm_final=dict(Counter(map(int, classes))), wbm_io=io_stats,
                      wbm_selected=dict(Counter(map(int, selected_classes))),
                      countries={stage: dict(Counter(p['country'] for p in points)) for stage, points in
                                 [('selected', selected), ('deduplicated', deduplicated), ('covered', covered)]},
                      coverage_counts=stage_counts)
        scenarios[scenario] = result
        (output / f'{scenario}.json').write_text(json.dumps(result, indent=2) + '\n')
        print(name, scenario, result['counts'], result['seconds'], flush=True)
    comparisons = {}
    for pair in ['AB', 'AC', 'AD', 'CD']:
        old, new = (scenarios[s] for s in pair)
        comparisons[pair] = {stage: transitions(old[stage], new[stage], transform)
                             for stage in ['selected', 'deduplicated', 'covered']}
        a, z = np.array(old['coverage_counts']['covered']), np.array(new['coverage_counts']['covered'])
        pool = np.array(new['coverage_counts']['pool'])
        comparisons[pair]['coverage_losses'] = [dict(place=places[i], before=int(a[i]), after=int(z[i]), available=int(pool[i]))
                                                 for i in np.flatnonzero((a >= 4) & (z < 4))]
    report = dict(region=name, context_km=context, provenance=meta, historical_A=a_check,
                  summaries={k: {'counts': v['counts'], 'seconds': v['seconds'],
                                 'wbm_final': v['wbm_final'], 'selection_stats': v['selection_stats'],
                                 'wbm_io': v['wbm_io']} for k, v in scenarios.items()},
                  comparisons=comparisons, places=places,
                  nodata_note='All WBM NoData candidates are excluded. Any nonzero count requires review; no normal regional rate is assumed.')
    (output / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    return report, scenarios, transform


def benchmark(args, name, repetitions=3):
    """Selection-only cold/warm measurements, checked against saved outputs."""
    region = get_region(name)
    directory = args.output_dir / name
    meta = json.loads((directory / 'arrays/metadata.json').read_text())
    d = np.load(directory / 'arrays/darkness.npy', mmap_mode='r')
    b = np.load(directory / 'arrays/bortle.npy', mmap_mode='r')
    tr = rasterio.Affine(*meta['transform'])
    geo = load_geography()
    expected = {s: json.loads((directory / f'{s}.json').read_text())['selected'] for s in 'AD'}
    observations = []
    start = time.perf_counter()
    with ElevationSampler(args.dem_manifest_dir / f'{name}.json', args.dem_cache_dir) as sampler:
        verification_seconds = time.perf_counter() - start
        for trial in range(repetitions + 1):
            for s in ('AD' if trial % 2 == 0 else 'DA'):
                before = Counter(sampler.wbm_io)
                stats = {}
                start = time.perf_counter()
                result = select_scenario(d, b, tr, meta['crs'], region, sampler, s, geography=geo, stats=stats)
                elapsed = time.perf_counter() - start
                if result != expected[s]:
                    raise ValueError(f'Benchmark output differs from simulation: {s}')
                guard_seconds = 0.0
                if s == 'D':
                    guard_start = time.perf_counter()
                    checked, _ = classify_candidates(result, region['osm_country_code'], geography=geo,
                                                       transform=tr, crs=meta['crs'], equal_area_epsg=region['equal_area_epsg'])
                    w, south, e, n = region['bbox']
                    checked = [p for p in checked if w <= p['lon'] <= e and south <= p['lat'] <= n]
                    values = sampler.wbm_classes([p['lat'] for p in checked], [p['lon'] for p in checked])
                    if checked != result or np.any(values != 0):
                        raise ValueError('Production pre-redundancy guard differs from selection')
                    guard_seconds = time.perf_counter() - guard_start
                observations.append(dict(scenario=s, cache='empty' if trial == 0 else 'warm', trial=trial,
                                         seconds=elapsed, production_guard_seconds=guard_seconds, stats=stats,
                                         io_delta=dict(Counter(sampler.wbm_io) - before)))
                print('benchmark', name, s, trial, elapsed, flush=True)
        cache_bytes = sampler.wbm_cache_size
        peak_cache_bytes = sampler.wbm_io['peak_cache_bytes']
    medians = {s: float(np.median([o['seconds'] for o in observations if o['scenario'] == s and o['trial'] > 0])) for s in 'AD'}
    effective = {s: float(np.median([o['seconds'] + o['production_guard_seconds'] for o in observations
                                    if o['scenario'] == s and o['trial'] > 0])) for s in 'AD'}
    report = dict(region=name, repetitions=repetitions, integrity_seconds=verification_seconds,
                  observations=observations, warm_median_seconds=medians,
                  ratio_D_over_A=medians['D'] / medians['A'],
                  generation_selection_median_seconds=effective,
                  generation_selection_ratio_D_over_A=effective['D'] / effective['A'],
                  cache_bytes=cache_bytes, peak_cache_bytes=peak_cache_bytes,
                  max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  note='Empty means application WBM cache, not the operating-system filesystem cache. ALR is not rerun here.')
    (args.output_dir / 'benchmark.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def snapshots(args, published, all_scenarios):
    """Build global disposable snapshots using the real ownership merger."""
    ownerships = build_region_ownerships(load_regions())
    enriched_by_region = {}
    naming_differences = {}
    published_points = {p['id']: p for env in published.values() for p in env['spots']}
    for name, scenarios in all_scenarios.items():
        unique = {point_key(p): dict(p) for scenario in scenarios.values() for p in scenario['covered']}
        spots = list(unique.values())
        print('snapshot enrichment', name, len(spots), flush=True)
        attach_near_town(spots, load_places(get_region(name)))
        index = GeoNamesIndex.from_filtered_extracts(data_dir='data', countries=get_region(name)['osm_country_code'],
                                                    bbox=get_region(name)['bbox'], feature_codes=NAMING_FEATURE_CODES)
        with ElevationSampler(args.dem_manifest_dir / f'{name}.json', args.dem_cache_dir) as sampler:
            enriched = enrich_all(index.enrich_spots(spots), elevation_sampler=sampler)
        enriched_by_region[name] = dict(zip(unique, enriched))
        differences = []
        for p in scenarios['A']['covered']:
            current = enriched_by_region[name][point_key(p)]
            old = published_points.get(current['id'])
            if old:
                fields = {k: dict(published=old.get(k), current=current.get(k)) for k in
                          ['near', 'name', 'nameDistanceKm', 'nameFeatureCode', 'nameFeatureClass', 'nameGeoNameId']
                          if old.get(k) != current.get(k)}
                if fields:
                    differences.append(dict(id=current['id'], fields=fields))
        naming_differences[name] = differences
    for scenario in 'ABCD':
        envelopes = json.loads(json.dumps(published))
        for name, scenarios in all_scenarios.items():
            enriched = [enriched_by_region[name][point_key(p)] for p in scenarios[scenario]['covered']]
            grouped = classify_spots_into_tiles(enriched)
            for tile in envelopes.keys() | grouped.keys():
                incoming = dict(tile=tile, spots=grouped.get(tile, []), source=f'VIIRS/{args.year}/{name}',
                                version=f'{args.year}.0', generated='2026-11-01T00:00:00Z')
                merged = merge_tile_envelopes(envelopes.get(tile), incoming, ownerships[name])
                if merged is not None:
                    envelopes[tile] = merged
        directory = args.output_dir / 'snapshots' / scenario
        (directory / 'spots').mkdir(parents=True, exist_ok=True)
        wanted = {tile for tile, env in envelopes.items() if env['spots']}
        for stale in (directory / 'spots').glob('*.json'):
            if stale.stem not in wanted:
                stale.unlink()
        for tile, env in envelopes.items():
            if env['spots']:
                (directory / 'spots' / f'{tile}.json').write_text(json.dumps(env, indent=2) + '\n')
        write_cluster_files(directory / 'spots', directory / 'clusters', data_year=args.year, generated='2026-11-01T00:00:00Z')
        print('snapshot complete', scenario, flush=True)
    (args.output_dir / 'historical-A-naming.json').write_text(json.dumps(naming_differences, indent=2) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-repo', type=Path, required=True)
    parser.add_argument('--reference', default='1502e35')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--input-dir', type=Path, default=Path('input'))
    parser.add_argument('--dem-manifest-dir', type=Path, default=Path('data/elevation/manifests'))
    parser.add_argument('--dem-cache-dir', type=Path, default=Path('input/elevation'))
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--regions', nargs='+', default=['france', 'uk_ireland', 'canaries'])
    parser.add_argument('--context-km', nargs='*', default=[], help='Explicit region=km historical context overrides')
    parser.add_argument('--allow-historical-drift', action='store_true', help='Record unexplained drift; results remain exploratory')
    parser.add_argument('--snapshots', action='store_true', help='Generate temporary global spot and cluster snapshots')
    parser.add_argument('--benchmark-region', help='Benchmark saved A/D outputs only, without rerunning ALR or coverage')
    args = parser.parse_args(argv)
    data = args.data_repo.resolve()
    output = args.output_dir.resolve()
    if output == data or output.is_relative_to(data) or data.is_relative_to(output):
        raise ValueError('Comparison output must be separate from the data repository')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.benchmark_region:
        benchmark(args, args.benchmark_region)
        return 0
    before = git(data, 'status', '--porcelain')
    commit, published = read_reference(data, args.reference)
    contexts = {k: int(v) for k, v in (s.split('=') for s in args.context_km)}
    reports, all_scenarios = {}, {}
    for name in args.regions:
        reports[name], all_scenarios[name], _ = run_region(args, name, published, contexts.get(name, config.ALR_MAX_KM))
    if args.snapshots:
        snapshots(args, published, all_scenarios)
    assert git(data, 'status', '--porcelain') == before
    result = dict(reference_commit=commit, source_repo_unchanged=True,
                  pipeline_commit=git(ROOT, 'rev-parse', 'HEAD').decode().strip(),
                  code_sha256={str(p.relative_to(ROOT)): sha256(p) for p in
                               [*sorted((ROOT / 'src').glob('*.py')), ROOT / 'run.py', ROOT / 'regions.yaml']},
                  auxiliary_sha256={str(p.resolve().relative_to(ROOT)): sha256(p) for p in
                                    [ROOT / 'data/cities500.txt', ROOT / 'data/geonames/manifest.yaml',
                                     *sorted((ROOT / 'data/natural_earth').glob('*.*')),
                                     *sorted(args.dem_manifest_dir.glob('*.json'))]},
                  historical_A_exact=all(v['historical_A']['exact'] for v in reports.values()),
                  historical_A_compatible=all(v['historical_A']['compatible'] for v in reports.values()),
                  max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  regions={name: {'summaries': r['summaries'],
                                  'AD': {stage: r['comparisons']['AD'][stage]['counts'] for stage in ['selected', 'deduplicated', 'covered']},
                                  'coverage_losses': len(r['comparisons']['AD']['coverage_losses'])}
                           for name, r in reports.items()})
    (args.output_dir / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise
