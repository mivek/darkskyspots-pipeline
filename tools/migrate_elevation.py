#!/usr/bin/env python3
"""Update existing spot tiles and regenerate global clusters in a local data repo."""
import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.clusters import write_cluster_files
from src.elevation import ElevationSampler
from src.regions import build_region_ownerships, load_regions
from run import _generated_date, _migration_data_year


def migrate(repo, manifest_dir, cache_dir, year=None):
    repo = Path(repo)
    if not (repo / "spots").is_dir():
        raise ValueError(f"Missing spots directory in {repo}")
    regions = load_regions()
    ownerships = build_region_ownerships(regions)
    updates = []
    counts = {}
    with ExitStack() as stack:
        samplers = {}
        for path in sorted((repo / 'spots').glob('*.json')):
            envelope = json.loads(path.read_text(encoding='utf-8'))
            changed = False
            for spot in envelope['spots']:
                owners = [name for name, ownership in ownerships.items() if ownership.owns(spot)]
                if len(owners) != 1:
                    raise ValueError(f'Spot {spot.get("id")} has {len(owners)} region owners')
                name = owners[0]
                if name not in samplers:
                    samplers[name] = stack.enter_context(ElevationSampler(Path(manifest_dir) / f'{name}.json', cache_dir))
                value = samplers[name].sample(spot['lat'], spot['lon'])
                if spot.get('altitude') != value:
                    spot['altitude'] = value
                    changed = True
            if changed:
                updates.append((path, envelope))
        counts = {name: dict(sampler.counts) for name, sampler in samplers.items()}
    if not updates:
        return {'changed_tiles': 0, 'samples': counts}
    from argparse import Namespace
    data_year = _migration_data_year(repo, Namespace(year=year))
    for path, envelope in updates:
        path.write_text(json.dumps(envelope, indent=4, ensure_ascii=False) + '\n', encoding='utf-8')
    write_cluster_files(repo / 'spots', repo / 'clusters', data_year=data_year, generated=_generated_date())
    return {'changed_tiles': len(updates), 'samples': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='Local data repository to update')
    parser.add_argument('--manifest-dir', default='data/elevation/manifests')
    parser.add_argument('--cache-dir', default='input/elevation')
    parser.add_argument('--year', type=int, help='Fallback if cluster manifest has no data year')
    args = parser.parse_args()
    print(json.dumps(migrate(args.repo, args.manifest_dir, args.cache_dir, args.year), indent=2))

if __name__ == '__main__':
    main()
