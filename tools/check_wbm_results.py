#!/usr/bin/env python3
"""Independently verify saved WBM comparisons and disposable global snapshots."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import rasterio
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.clusters import SPOT_FIELDS
from src.elevation import sha256
from src.enrich import spot_id
from src.publish import audit_country_spots
from src.regions import load_regions
from src.utils import haversine_km


def require(condition, message):
    if not condition:
        raise ValueError(message)


def direct_classes(points, region, manifest_dir, cache_dir):
    """Independent scalar reads, deliberately not the batched production reader."""
    manifest = json.loads((manifest_dir / f'{region}.json').read_text())
    tiles = {(t['lat'], t['lon']): t for t in manifest['tiles']}
    datasets, values = {}, {}
    try:
        for p in points:
            key = math.floor(p['lat']), math.floor(p['lon'])
            if key not in datasets:
                info = tiles[key]['wbm']
                path = cache_dir / info['path']
                require(path.stat().st_size == info['size'] and sha256(path) == info['sha256'],
                        f'Corrupt verification WBM: {path}')
                datasets[key] = rasterio.open(path)
            ds = datasets[key]
            x, y = p['lon'], p['lat']
            if ds.crs != rasterio.crs.CRS.from_epsg(4326):
                xs, ys = rasterio.warp.transform('EPSG:4326', ds.crs, [x], [y])
                x, y = xs[0], ys[0]
            row, col = ds.index(x, y)
            require(0 <= row < ds.height and 0 <= col < ds.width, 'Verification coordinate outside WBM')
            value = ds.read(1, window=Window(col, row, 1, 1), masked=True)[0, 0]
            if np.ma.is_masked(value):
                value = -1
            else:
                require(value in (0, 1, 2, 3), f'Invalid scalar WBM class: {value}')
            values[p['row'], p['col']] = int(value)
    finally:
        for ds in datasets.values():
            ds.close()
    return values


def verify_results(output, data_repo, manifest_dir, cache_dir):
    regions = json.loads((output / 'report.json').read_text())['regions']
    summary, source_counts, lost_land = {}, {}, []
    total_positive = total_original_positive = 0
    for name in regions:
        stages = {s: json.loads((output / name / f'{s}.json').read_text()) for s in 'ABCD'}
        a, d = stages['A'], stages['D']
        old_mask = direct_classes(a['selected'], name, manifest_dir, cache_dir)
        new_mask = direct_classes(d['selected'], name, manifest_dir, cache_dir)
        require(all(v == 0 for v in new_mask.values()), f'Non-land D candidate in {name}')
        source_counts[name] = dict(Counter(new_mask.values()))
        index = {(p['row'], p['col']): p for p in d['selected']}
        final = {(p['row'], p['col']) for p in d['covered']}
        require(final <= index.keys(), 'Coverage introduced a point outside D selection')
        fields = ('row', 'col', 'lat', 'lon', 'darkness', 'bortle', 'country')
        positives = [p for p in a['selected'] if old_mask[p['row'], p['col']] == 0]
        for p in positives:
            current = index.get((p['row'], p['col']))
            require(current is not None and all(current[k] == p[k] for k in fields),
                    f'Historical land maximum changed: {name}, {p}')
        total_positive += len(positives)
        published_positives = [p for p in a['covered'] if old_mask[p['row'], p['col']] == 0]
        total_original_positive += len(published_positives)
        for p in a['covered']:
            if old_mask[p['row'], p['col']] != 0:
                require((p['row'], p['col']) not in final, 'Historical water/NoData point retained in D')
        for p in published_positives:
            if (p['row'], p['col']) in final:
                continue
            suppressors = [s for s in d['deduplicated'] if s['bortle'] == p['bortle']
                           and haversine_km(p['lat'], p['lon'], s['lat'], s['lon']) < 15]
            require(suppressors and suppressors[0]['darkness'] >= p['darkness'],
                    f'Unexplained loss of historical land point: {p}')
            s = suppressors[0]
            lost_land.append(dict(region=name, id=spot_id(p['lat'], p['lon']), point=p,
                                  suppressor=s, distance_km=haversine_km(p['lat'], p['lon'], s['lat'], s['lon']),
                                  reason='Still eligible in D selection; removed by existing greedy redundancy and not restored by coverage.'))
        summary[name] = {s: dict(stages[s]['counts'], countries=dict(Counter(p['country'] for p in stages[s]['covered'])),
                                 coverage_added=len(stages[s]['coverage_added'])) for s in 'ABCD'}
        print('verified', name, 'D scalar classes', len(new_mask), 'A land maxima preserved', len(positives), flush=True)
    for scenario in 'ABCD':
        directory = output / 'snapshots' / scenario
        audit = audit_country_spots(directory / 'spots', load_regions())
        problems = ['missing', 'invalid', 'unconfigured', 'mismatched', 'ambiguous', 'unassignable',
                    'outside_declared_extents', 'ambiguous_ownership']
        require(all(not audit.get(k) for k in problems), f'Snapshot ownership audit failed: {scenario}')
        spots = [s for p in (directory / 'spots').glob('*.json') for s in json.loads(p.read_text())['spots']]
        require(len(spots) == sum(summary[n][scenario]['covered'] for n in summary), 'Snapshot lost or added spots')
        by_id = {s['id']: s for s in spots}
        require(len(spots) == len(by_id), 'Duplicate snapshot IDs')
        require(all(k in s for s in spots for k in (*SPOT_FIELDS, 'country')), 'Missing public snapshot fields')
        cluster_index = json.loads((directory / 'clusters/index.json').read_text())
        for level in cluster_index['levels']:
            for entry in level['files']:
                path = directory / 'clusters' / entry['path']
                require(sha256(path) == entry['hash'], 'Cluster hash mismatch')
                clusters = json.loads(path.read_text())
                require(sum(c['count'] for c in clusters) == len(spots), 'Cluster point count mismatch')
                for c in clusters:
                    source = by_id.get(c['rep']['id'])
                    require(source is not None and all(c['rep'][k] == source[k] for k in SPOT_FIELDS),
                            'Cluster representative differs from snapshot')
        require(len(cluster_index['levels']) == 6, 'Missing cluster levels')
        print('snapshot audit passed', scenario, len(spots), flush=True)
    for extra in [[], ['--cached']]:
        require(subprocess.run(['git', '-C', str(data_repo), 'diff', *extra, '--quiet']).returncode == 0,
                'Published data repository has tracked changes')
    return dict(status='passed', D_scalar_wbm_checks=source_counts,
                A_land_mesh_maxima_preserved_exactly=total_positive,
                A_published_land_controls=total_original_positive,
                published_land_removed_after_redundancy=len(lost_land), lost_land=lost_land,
                summary=summary, source_data_repository_unchanged=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--simulation', type=Path, required=True)
    p.add_argument('--data-repo', type=Path, required=True)
    p.add_argument('--dem-manifest-dir', type=Path, default=Path('data/elevation/manifests'))
    p.add_argument('--dem-cache-dir', type=Path, default=Path('input/elevation'))
    args = p.parse_args()
    source, output = args.data_repo.resolve(), args.simulation.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Verification output must be separate from the data repository')
    before = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'])
    result = verify_results(output, source, args.dem_manifest_dir, args.dem_cache_dir)
    require(subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain']) == before,
            'Source repository status changed during verification')
    (output / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
    print('All individual point and snapshot checks passed')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise
