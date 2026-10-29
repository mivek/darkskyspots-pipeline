#!/usr/bin/env python3
"""Prepare DEM/WBM pairs obtained using CopernicusDemDownloader.

The downloader is invoked for GLO-30; missing terrestrial cells are requested
as a batch from GLO-90 after a catalogue check. S3 credentials are read by that program from its env.
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapely.geometry import box
from src.elevation import sha256
from src.geography import load_geography
from src.regions import get_region

PATTERN = re.compile(r'_(N|S)(\d{2})_\d{2}_(E|W)(\d{3})_\d{2}_(DEM|WBM)\.tif$', re.I)


def collect(directory):
    result = {}
    for path in Path(directory).rglob('*.tif'):
        match = PATTERN.search(path.name)
        if not match:
            continue
        ns, lat, ew, lon, layer = match.groups()
        key = (int(lat) * (-1 if ns.upper() == 'S' else 1), int(lon) * (-1 if ew.upper() == 'W' else 1))
        layers = result.setdefault(key, {})
        if layer.upper() in layers:
            raise ValueError(f'Duplicate {layer} for {key}')
        layers[layer.upper()] = path
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True)
    parser.add_argument('--cache-dir', default='input/elevation')
    parser.add_argument('--manifest-dir', default='data/elevation/manifests')
    parser.add_argument('--downloader', default='CopernicusDemDownloader')
    parser.add_argument('--version', default='2024_1')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-glo90', action='store_true', help='Use GLO-90 for missing GLO-30 cells after checking the CDSE catalogue')
    args = parser.parse_args()
    region = get_region(args.region)
    bbox = region['bbox']
    root = Path(args.cache_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    geo = load_geography()
    shapes = [geo.countries[code] for code in region['osm_country_code']]
    needed = {(lat, lon) for lat in range(math.floor(bbox[1]), math.ceil(bbox[3]))
              for lon in range(math.floor(bbox[0]), math.ceil(bbox[2]))
              if any(shape.intersects(box(lon, lat, lon + 1, lat + 1).intersection(box(*bbox))) for shape in shapes)}
    print(f'{len(needed)} terrestrial cells; download size is reported by the downloader', flush=True)
    def download(product, extent, output, dry=False):
        cmd = [args.downloader, '--batch', '--dataset', product, '--version-year', args.version,
               '--bbox', ','.join(map(str, extent)), '--masks', 'DEM,WBM', '--output', str(output)]
        if dry:
            cmd.append('--dry-run')
        result = subprocess.run(cmd, text=True, capture_output=True)
        print(result.stdout, end="")
        if (result.returncode or "Error:" in result.stderr or "Error:" in result.stdout
                or re.search(r"Failed:\s*[1-9][0-9]*", result.stdout)):
            raise RuntimeError(f"DEM downloader failed: {result.stderr.strip()}")
    directory30 = root / args.region / 'GLO-30'
    download('GLO-30', bbox, directory30, args.dry_run)
    if args.dry_run:
        return
    high = collect(directory30)
    missing = needed - high.keys()
    if missing and not args.allow_glo90:
        raise ValueError(f'GLO-30 cells missing: {sorted(missing)}; verify catalogue before --allow-glo90')
    if missing:
        download('GLO-90', bbox, root / args.region / 'GLO-90')
    low = collect(root / args.region / 'GLO-90')
    tiles = []
    for lat, lon in sorted(needed):
        key = (lat, lon)
        product, layers = ('GLO-30', high[key]) if key in high else ('GLO-90', low.get(key, {}))
        if set(layers) != {'DEM', 'WBM'}:
            raise ValueError(f'Missing DEM/WBM pair for {key} ({product})')
        entry = {'lat': lat, 'lon': lon, 'product': product, 'version': args.version}
        for layer in ('DEM', 'WBM'):
            path = layers[layer]
            product_root = root / args.region / product
            object_key = f'auxdata/CopDEM/COP-DEM_{product}-DGED/{args.version}/' + path.relative_to(product_root).as_posix()
            entry[layer.lower()] = {'path': str(path.relative_to(root)), 'size': path.stat().st_size,
                                    'sha256': sha256(path), 'source': 's3://eodata/' + object_key}
        tiles.append(entry)
    output = Path(args.manifest_dir) / f'{args.region}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'schema': 1, 'region': args.region, 'bbox': bbox, 'tiles': tiles}, indent=2) + '\n')
    print(f'{output}: {len(tiles)} cells, {sum(t[l]["size"] for t in tiles for l in ("dem", "wbm"))} bytes')


if __name__ == '__main__':
    main()
