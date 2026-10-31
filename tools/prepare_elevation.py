#!/usr/bin/env python3
"""Prepare local Copernicus DEM/WBM pairs from CDSE object storage."""
import argparse
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import boto3
import rasterio
import requests
from botocore.config import Config
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.elevation import sha256
from src.geography import load_geography
from src.regions import get_region

BUCKET = 'eodata'
ENDPOINT = 'https://eodata.dataspace.copernicus.eu'
CATALOG = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
PATTERN = re.compile(r'_(N|S)(\d{2})_\d{2}_(E|W)(\d{3})_\d{2}_(DEM|WBM)\.tif$', re.I)


def client_from_environment():
    access_key = os.environ.get('CDSE_ACCESS_KEY')
    secret_key = os.environ.get('CDSE_SECRET_KEY')
    if not access_key or not secret_key:
        raise ValueError('Set CDSE_ACCESS_KEY and CDSE_SECRET_KEY to CDSE S3 credentials')
    return boto3.client('s3', endpoint_url=ENDPOINT, region_name='default',
                        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                        config=Config(s3={'addressing_style': 'path'}))


def required_cells(region, geography):
    bbox = region['bbox']
    shapes = [geography.countries[code] for code in region['osm_country_code']]
    extent = box(*bbox)
    return {(lat, lon) for lat in range(math.floor(bbox[1]), math.ceil(bbox[3]))
            for lon in range(math.floor(bbox[0]), math.ceil(bbox[2]))
            if any(shape.intersects(box(lon, lat, lon + 1, lat + 1).intersection(extent))
                   for shape in shapes)}


def grid_id(cell):
    lat, lon = cell
    return f'{"N" if lat >= 0 else "S"}{abs(lat):02d}_{"E" if lon >= 0 else "W"}{abs(lon):03d}'


def catalogue_products(session, product, version, needed):
    """Resolve the requested delivery and cells to the catalogue's S3 paths."""
    cells_by_id = {grid_id(cell): cell for cell in needed}
    products = {}
    names = sorted(cells_by_id)
    # CDSE does not reliably evaluate OR-ed grid IDs inside one any() filter.
    for start in range(len(names)):
        if start % 20 == 0:
            print(f'Querying CDSE catalogue for {product}: {start}/{len(names)} cells', file=sys.stderr)
        group = names[start:start + 1]
        dataset = f'COP-DEM_{product}-DGED/{version}'
        grids = ' or '.join(
            f"att/OData.CSC.StringAttribute/Value eq '{name}'" for name in group
        )
        query = ("Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'dataset' and "
                 f"att/OData.CSC.StringAttribute/Value eq '{dataset}') and "
                 "Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'gridId' and "
                 f"({grids}))")
        url = CATALOG
        params = {'$filter': query, '$expand': 'Attributes', '$top': 100}
        seen = set()
        while url:
            parsed = urlparse(url)
            if (parsed.scheme, parsed.netloc) != ('https', 'catalogue.dataspace.copernicus.eu') or url in seen:
                raise ValueError(f'Invalid or repeated catalogue page: {url}')
            seen.add(url)
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload.get('value'), list):
                raise ValueError('Invalid CDSE catalogue response')
            for item in payload['value']:
                attrs = {attr.get('Name'): attr.get('Value') for attr in item.get('Attributes', [])}
                name = attrs.get('gridId')
                if name not in group or attrs.get('dataset') != dataset:
                    raise ValueError(f'Unexpected CDSE catalogue product: {name}')
                cell = cells_by_id[name]
                if cell in products:
                    raise ValueError(f'Duplicate {product} catalogue products for {cell}')
                if item.get('Online') is not True:
                    raise ValueError(f'CDSE product is not online: {name}')
                path = item.get('S3Path')
                if not isinstance(path, str) or not path.startswith('/eodata/'):
                    raise ValueError(f'Invalid CDSE S3Path for {name}')
                key = path[len('/eodata/'):].rstrip('/')
                if not key or '..' in PurePosixPath(key).parts:
                    raise ValueError(f'Invalid CDSE S3Path for {name}')
                products[cell] = key
            url = payload.get('@odata.nextLink')
            params = None
    return products


def list_tiles(client, session, product, version, needed):
    """Resolve catalogue paths then list each product completely on S3."""
    products = catalogue_products(session, product, version, needed)
    tiles = {}
    paginator = client.get_paginator('list_objects_v2')
    for cell, prefix in products.items():
        layers = {}
        object_count = 0
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                object_count += 1
                key = obj['Key']
                if not (key.startswith(prefix + '/') or key.startswith(prefix + '.DEM/')):
                    continue
                match = PATTERN.search(key)
                if not match:
                    continue
                ns, lat, ew, lon, layer = match.groups()
                actual = (int(lat) * (-1 if ns.upper() == 'S' else 1),
                          int(lon) * (-1 if ew.upper() == 'W' else 1))
                if actual != cell:
                    raise ValueError(f'Unexpected {product} raster cell: {actual}, expected {cell}')
                relative = PurePosixPath(key)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError(f'Unsafe S3 key: {key}')
                layer = layer.upper()
                if layer in layers:
                    raise ValueError(f'Duplicate {product} {layer} for {cell}')
                layers[layer] = {'key': key, 'size': obj['Size'], 'relative': relative}
        if not object_count:
            raise ValueError(
                f'CDSE catalogue lists {product} for {cell}, but S3 returned no objects '
                f'under s3://{BUCKET}/{prefix}. Check that your CDSE account is registered '
                'for Copernicus Contributing Missions (CCM) and that the product is available '
                'at its catalogue S3Path; an empty listing does not prove which condition failed.'
            )
        if set(layers) != {'DEM', 'WBM'}:
            raise ValueError(f'Missing DEM/WBM pair for {cell} ({product}); check CDSE S3 access')
        tiles[cell] = layers
    return tiles


def select_tiles(client, session, version, needed, allow_glo90):
    high = list_tiles(client, session, 'GLO-30', version, needed)
    missing = needed - high.keys()
    if missing and not allow_glo90:
        raise ValueError(f'GLO-30 cells missing: {sorted(missing)}; verify catalogue before --allow-glo90')
    low = list_tiles(client, session, 'GLO-90', version, missing) if missing else {}
    for cell in missing:
        if cell not in low:
            raise ValueError(f'Missing DEM/WBM pair for {cell} (GLO-90)')
    return {cell: ('GLO-30', high[cell]) if cell in high else ('GLO-90', low[cell])
            for cell in sorted(needed)}


def previous_files(manifest_path):
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['schema'] != 1:
            return {}
        return {entry[layer]['path']: entry[layer] for entry in manifest['tiles']
                for layer in ('dem', 'wbm')}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def download_file(client, obj, path, root, previous):
    relative = path.relative_to(root).as_posix()
    old = previous.get(relative, {})
    source = f's3://{BUCKET}/{obj["key"]}'
    if (path.is_file() and old.get('source') == source and old.get('size') == obj['size']
            and path.stat().st_size == obj['size'] and old.get('sha256') == sha256(path)):
        return old['sha256']
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.dem-', suffix='.part', dir=path.parent)
    os.close(fd)
    try:
        client.download_file(BUCKET, obj['key'], temp)
        if Path(temp).stat().st_size != obj['size']:
            raise ValueError(f'Incorrect size for s3://{BUCKET}/{obj["key"]}')
        digest = sha256(temp)
        os.replace(temp, path)
        return digest
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def prepare(client, region_name, region, needed, cache_dir, manifest_dir, version,
            *, dry_run=False, allow_glo90=False, catalog_session=None):
    if not re.fullmatch(r'\d{4}_\d+', version):
        raise ValueError(f'Invalid product version: {version}')
    if not needed:
        raise ValueError(f'No terrestrial cells for region {region_name}')
    selected = select_tiles(client, catalog_session or requests.Session(), version, needed, allow_glo90)
    objects = [obj for _, layers in selected.values() for obj in layers.values()]
    print(f'{len(selected)} terrestrial cells, {len(objects)} files, {sum(obj["size"] for obj in objects)} bytes')
    for cell, (product, layers) in selected.items():
        for layer in ('DEM', 'WBM'):
            obj = layers[layer]
            print(f'{product} {cell} {layer} s3://{BUCKET}/{obj["key"]} ({obj["size"]} bytes)')
    if dry_run:
        return None
    root = Path(cache_dir).resolve()
    manifest_path = Path(manifest_dir) / f'{region_name}.json'
    previous = previous_files(manifest_path)
    tiles = []
    for (lat, lon), (product, layers) in selected.items():
        entry = {'lat': lat, 'lon': lon, 'product': product, 'version': version}
        for layer in ('DEM', 'WBM'):
            obj = layers[layer]
            path = root / region_name / product / Path(*obj['relative'].parts)
            digest = download_file(client, obj, path, root, previous)
            entry[layer.lower()] = {'path': path.relative_to(root).as_posix(), 'size': obj['size'],
                                    'sha256': digest, 'source': f's3://{BUCKET}/{obj["key"]}'}
        with rasterio.open(root / entry['dem']['path']) as dem, rasterio.open(root / entry['wbm']['path']) as wbm:
            if (dem.count != 1 or wbm.count != 1 or dem.crs is None or dem.crs != wbm.crs
                    or dem.transform != wbm.transform or dem.width != wbm.width or dem.height != wbm.height):
                raise ValueError(f'Invalid or mismatched DEM/WBM grids for {(lat, lon)} ({product})')
        tiles.append(entry)
    manifest = {'schema': 1, 'region': region_name, 'bbox': region['bbox'], 'tiles': tiles}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.manifest-', suffix='.json', dir=manifest_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
        os.replace(temp, manifest_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print(f'{manifest_path}: {len(tiles)} cells, {sum(obj["size"] for obj in objects)} bytes')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True)
    parser.add_argument('--cache-dir', default='input/elevation')
    parser.add_argument('--manifest-dir', default='data/elevation/manifests')
    parser.add_argument('--version', default='2024_1')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--allow-glo90', action='store_true', help='Use GLO-90 for GLO-30 cells absent from the catalogue')
    args = parser.parse_args()
    region = get_region(args.region)
    needed = required_cells(region, load_geography())
    prepare(client_from_environment(), args.region, region, needed, args.cache_dir, args.manifest_dir,
            args.version, dry_run=args.dry_run, allow_glo90=args.allow_glo90)


if __name__ == '__main__':
    main()
