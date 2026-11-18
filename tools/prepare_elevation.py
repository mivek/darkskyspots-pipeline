#!/usr/bin/env python3
"""Prepare local Copernicus DEM/WBM pairs from CDSE object storage."""
import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import boto3
import rasterio
import requests
from pyproj import Geod
from botocore.config import Config
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.elevation import ElevationSampler, sha256
from src.accessibility_baseline import validate_approved_g2_baseline
from src.accessibility_dem import validate_accessibility_dem_manifest
from src.accessibility_scope import GeographicSpecification
from src.geography import load_geography
from src.regions import get_region

BUCKET = 'eodata'
ENDPOINT = 'https://eodata.dataspace.copernicus.eu'
CATALOG = 'https://catalogue.dataspace.copernicus.eu/odata/v1/Products'
PATTERN = re.compile(r'_(N|S)(\d{2})_\d{2}_(E|W)(\d{3})_\d{2}_(DEM|WBM)\.tif$', re.I)
ROOT = Path(__file__).resolve().parents[1]


def _g2_dem_policy(geographic_config='config/accessibility.yaml'):
    specification = GeographicSpecification.load(geographic_config, 'great_britain')
    if specification.dem_manifest_policy is None:
        raise ValueError('Great Britain has no configured G2 DEM manifest policy')
    return specification.dem_manifest_policy


def accessibility_halo_filenames(cells=None):
    """Return the exact DEM/WBM tile basenames required for G2's northern halo."""
    if cells is None:
        cells = _g2_dem_policy()['required_additional_cells']
    names = []
    for lat, lon in sorted(cells):
        ns = 'N' if lat >= 0 else 'S'
        ew = 'E' if lon >= 0 else 'W'
        cell = f'{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00'
        names.extend((f'Copernicus_DSM_10_{cell}_DEM.tif',
                      f'Copernicus_DSM_10_{cell}_WBM.tif'))
    return names


def validate_accessibility_halo_samples(manifest_path, cache_dir, baseline_spots_dir,
                                        baseline_inventory_path=None, geographic_config='config/accessibility.yaml'):
    """Authenticate the complete pinned baseline, then qualify its DEM samples."""
    baseline = validate_approved_g2_baseline(baseline_spots_dir, baseline_inventory_path)
    validate_accessibility_dem_manifest(
        manifest_path, cache_dir, 'great_britain', 'great-britain-g2-v1',
        geographic_config=geographic_config,
    )
    return _sample_accessibility_halo(manifest_path, cache_dir, baseline, geographic_config)


def _sample_accessibility_halo(manifest_path, cache_dir, baseline, geographic_config):
    """Sample only records returned by the complete, authenticated baseline validator."""
    spots = baseline['g2_spots']
    policy = _g2_dem_policy(geographic_config)
    known_nonterrestrial_cells = {tuple(cell) for cell in policy['known_nonterrestrial_cells']}
    point_unknown = []
    endpoint_reasons = Counter()
    endpoint_cells = Counter()
    unexpected_out_of_coverage = []
    max_altitude_difference = 0.0
    geod = Geod(ellps='WGS84')
    endpoint_sample_count = 0
    with ElevationSampler(manifest_path, cache_dir) as sampler:
        for spot in spots:
            result = sampler.sample_detailed(float(spot['lat']), float(spot['lon']))
            if result['reason'] or result['elevationM'] is None:
                point_unknown.append({'id': spot.get('id'), 'reason': result['reason'],
                                      'tile': result.get('tile')})
            else:
                if spot.get('altitude') is None:
                    raise ValueError(f'Frozen published altitude is missing: {spot.get("id")}')
                difference = abs(float(spot['altitude']) - float(result['elevationM']))
                max_altitude_difference = max(max_altitude_difference, difference)
                if difference > 0.5:
                    raise ValueError(
                        f'Frozen published altitude differs by more than 0.5 m: {spot.get("id")} ({difference})'
                    )
            for bearing in range(0, 360, 15):
                lon, lat, _back = geod.fwd(float(spot['lon']), float(spot['lat']), bearing, 1000.0)
                endpoint = sampler.sample_detailed(lat, lon)
                endpoint_sample_count += 1
                cell = tuple(endpoint.get('tile') or ())
                reason = endpoint.get('reason') or 'land'
                endpoint_reasons[reason] += 1
                if cell:
                    endpoint_cells[str(cell)] += 1
                if reason == 'out_of_coverage' and cell not in known_nonterrestrial_cells:
                    unexpected_out_of_coverage.append({
                        'spot_id': spot.get('id'), 'bearing_deg': bearing,
                        'coordinate': [lon, lat], 'tile': list(cell),
                    })
        counts = dict(sampler.counts)
    if point_unknown:
        raise ValueError(f'Frozen published points have invalid DEM/WBM samples: {point_unknown[:20]}')
    if unexpected_out_of_coverage:
        raise ValueError(
            "Unexpected out-of-coverage points in published 1 km search neighborhoods: "
            + json.dumps(unexpected_out_of_coverage[:20])
        )
    return {
        'schema': 1,
        'baseline_inventory_sha256': baseline['inventory_sha256'],
        'published_spot_samples': len(spots),
        'valid_published_spot_samples': len(spots) - len(point_unknown),
        'max_abs_difference_from_preserved_altitude_m': max_altitude_difference,
        'search_radius_m': 1000,
        'endpoint_bearings_per_spot': 24,
        'endpoint_samples': endpoint_sample_count,
        'endpoint_status_counts': dict(sorted(endpoint_reasons.items())),
        'endpoint_tile_sample_counts': dict(sorted(endpoint_cells.items())),
        'known_nonterrestrial_cells': [list(cell) for cell in sorted(known_nonterrestrial_cells)],
        'unexpected_out_of_coverage_samples': unexpected_out_of_coverage,
        'sampler_counts': counts,
        'limitation': 'Actual selected OSM feature endpoints remain unverified until indexes are prepared.',
    }


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


def validate_cached_manifest(manifest_path, root, region_name, region, needed, version, allow_glo90):
    """Validate metadata, coverage, and raster integrity without a network client."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest['schema'] != 1:
            raise ValueError('unsupported or missing schema')
        if manifest.get('region') != region_name:
            raise ValueError('region does not match')
        if manifest.get('bbox') != region['bbox']:
            raise ValueError('region bbox does not match')
        tiles = manifest.get('tiles')
        if not isinstance(tiles, list) or not tiles:
            raise ValueError('tiles must be a non-empty list')
        cells = set()
        for tile in tiles:
            if not isinstance(tile, dict) or type(tile.get('lat')) is not int or type(tile.get('lon')) is not int:
                raise ValueError('tile coordinates must be integers')
            cell = (tile['lat'], tile['lon'])
            if cell in cells:
                raise ValueError(f'duplicate cell {cell}')
            cells.add(cell)
            if tile.get('version') != version:
                raise ValueError(f'product version mismatch for {cell}')
            if tile.get('product') not in ('GLO-30', 'GLO-90'):
                raise ValueError(f'invalid product for {cell}')
            if tile['product'] == 'GLO-90' and not allow_glo90:
                raise ValueError(f'GLO-90 cell {cell} requires --allow-glo90')
            for layer in ('dem', 'wbm'):
                info = tile.get(layer)
                if (not isinstance(info, dict) or not isinstance(info.get('path'), str)
                        or not info['path'] or type(info.get('size')) is not int or info['size'] <= 0
                        or not isinstance(info.get('sha256'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', info['sha256'])
                        or not isinstance(info.get('source'), str) or not info['source']):
                    raise ValueError(f'invalid {layer} metadata for {cell}')
        if cells != needed:
            raise ValueError(f'cell coverage mismatch: missing={sorted(needed - cells)}, extra={sorted(cells - needed)}')
        # This is the same file, hash, and raster-grid validation used at runtime.
        with ElevationSampler(manifest_path, root):
            pass
        return manifest
    except (OSError, ValueError, KeyError, TypeError, rasterio.errors.RasterioError) as exc:
        raise ValueError(f'Invalid or incompatible elevation cache {manifest_path}: {exc}') from exc


def download_file(client, obj, path):
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
            *, dry_run=False, allow_glo90=False, catalog_session=None, refresh=False):
    if not re.fullmatch(r'\d{4}_\d+', version):
        raise ValueError(f'Invalid product version: {version}')
    if not needed:
        raise ValueError(f'No terrestrial cells for region {region_name}')
    root = Path(cache_dir).resolve()
    manifest_path = Path(manifest_dir) / f'{region_name}.json'
    try:
        manifest_path.lstat()
    except FileNotFoundError:
        manifest_exists = False
    else:
        manifest_exists = True
    if manifest_exists:
        try:
            cached = validate_cached_manifest(manifest_path, root, region_name, region, needed, version, allow_glo90)
        except ValueError as exc:
            if not refresh:
                raise ValueError(f'{exc}; use --refresh to explicitly replace it') from exc
            print(f'Cache validation failed; explicit refresh requested: {exc}', file=sys.stderr)
        else:
            if not refresh:
                total = sum(tile[layer]['size'] for tile in cached['tiles'] for layer in ('dem', 'wbm'))
                print(f'Validated local cache: {len(cached["tiles"])} cells, {2 * len(cached["tiles"])} files, {total} bytes')
                return None if dry_run else cached
    # Neither credentials nor network clients are needed until the online path.
    if client is None:
        client = client_from_environment()
    selected = select_tiles(client, catalog_session if catalog_session is not None else requests.Session(), version, needed, allow_glo90)
    objects = [obj for _, layers in selected.values() for obj in layers.values()]
    print(f'{len(selected)} terrestrial cells, {len(objects)} files, {sum(obj["size"] for obj in objects)} bytes')
    for cell, (product, layers) in selected.items():
        for layer in ('DEM', 'WBM'):
            obj = layers[layer]
            print(f'{product} {cell} {layer} s3://{BUCKET}/{obj["key"]} ({obj["size"]} bytes)')
    if dry_run:
        return None
    generations = root / region_name / 'generations'
    generations.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix='run-', dir=generations))
    published = False
    try:
        tiles = []
        for (lat, lon), (product, layers) in selected.items():
            entry = {'lat': lat, 'lon': lon, 'product': product, 'version': version}
            for layer in ('DEM', 'WBM'):
                obj = layers[layer]
                path = generation / product / Path(*obj['relative'].parts)
                digest = download_file(client, obj, path)
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
            published = True
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        print(f'{manifest_path}: {len(tiles)} cells, {sum(obj["size"] for obj in objects)} bytes')
        return manifest
    finally:
        if not published:
            shutil.rmtree(generation)


def prepare_accessibility_halo(client, region, cache_dir, manifest_dir, version,
                               *, dry_run=False, catalog_session=None,
                               baseline_spots_dir='input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19-g2-full/spots',
                               baseline_inventory_path=None,
                               geographic_config='config/accessibility.yaml'):
    """Create a separate G2 DEM manifest from the configured complete inventory."""
    if not re.fullmatch(r'\d{4}_\d+', version):
        raise ValueError(f'Invalid product version: {version}')
    # Authenticate before any DEM sampling, cache validation, resource staging,
    # or CDSE work. The returned spot records are the exact verified bytes.
    baseline = validate_approved_g2_baseline(baseline_spots_dir, baseline_inventory_path)
    policy = _g2_dem_policy(geographic_config)
    if version != policy['version']:
        raise ValueError(f'G2 accessibility halo requires product version {policy["version"]}')
    root = Path(cache_dir).resolve()
    manifest_dir = Path(manifest_dir).resolve()
    base_path = Path(policy['base_manifest_path']).resolve()
    if base_path != (manifest_dir / Path(policy['base_manifest']).name).resolve():
        raise ValueError(f'G2 accessibility base manifest must be the configured file: {base_path}')
    output_path = manifest_dir / Path(policy['manifest']).name
    if output_path.exists():
        raise FileExistsError(f'Refusing to overwrite immutable accessibility DEM manifest: {output_path}')
    geography = load_geography()
    base_needed = required_cells(region, geography)
    base = validate_cached_manifest(base_path, root, 'uk_ireland', region, base_needed, version, False)
    extra = {tuple(cell) for cell in policy['required_additional_cells']}
    base_cells = {(int(tile['lat']), int(tile['lon'])) for tile in base['tiles']}
    if base_cells & extra:
        raise ValueError('Original uk_ireland DEM manifest unexpectedly contains accessibility halo cells')

    pending_files = accessibility_halo_filenames(extra)
    disk_path = root if root.exists() else root.parent
    free_bytes = shutil.disk_usage(disk_path).free
    if dry_run and client is None:
        report = {
            'status': 'source_credentials_or_catalogue_required',
            'base_manifest': str(base_path), 'base_manifest_sha256': sha256(base_path),
            'base_pairs_reused': len(base['tiles']),
            'required_additional_cells': [list(cell) for cell in sorted(extra)],
            'required_additional_files': pending_files,
            'resource_admission': {'free_disk_bytes': free_bytes},
        }
        print(json.dumps(report, indent=2))
        return report

    if client is None:
        try:
            client = client_from_environment()
        except ValueError as exc:
            raise ValueError(
                f'{exc}; missing GLO-30 2024_1 accessibility halo cells '
                f'{sorted(extra)} and files {pending_files}'
            ) from exc
    selected = select_tiles(client, catalog_session if catalog_session is not None else requests.Session(),
                            version, extra, False)
    objects = [obj for _product, layers in selected.values() for obj in layers.values()]
    download_bytes = sum(obj['size'] for obj in objects)
    free_bytes = shutil.disk_usage(disk_path).free
    admission_bytes = download_bytes + max(1024**3, download_bytes // 10)
    if free_bytes < admission_bytes:
        raise OSError(f'Insufficient ext4 disk for row-55 DEM halo: need {admission_bytes} bytes, have {free_bytes}')
    print(f'G2 accessibility halo: {len(extra)} GLO-30 cells, {len(objects)} files, {download_bytes} bytes')
    if dry_run:
        return None

    generation_root = root / 'uk_ireland-accessibility-g2' / 'generations'
    generation_root.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix='run-', dir=generation_root))
    manifest_written = False
    try:
        tiles = [dict(tile) for tile in base['tiles']]
        for (lat, lon), (product, layers) in sorted(selected.items()):
            entry = {'lat': lat, 'lon': lon, 'product': product, 'version': version}
            for layer in ('DEM', 'WBM'):
                obj = layers[layer]
                path = generation / product / Path(*obj['relative'].parts)
                digest = download_file(client, obj, path)
                entry[layer.lower()] = {
                    'path': path.relative_to(root).as_posix(), 'size': obj['size'],
                    'sha256': digest, 'source': f's3://{BUCKET}/{obj["key"]}',
                }
            with rasterio.open(root / entry['dem']['path']) as dem, rasterio.open(root / entry['wbm']['path']) as wbm:
                if (dem.count != 1 or wbm.count != 1 or dem.crs is None or dem.crs != wbm.crs
                        or dem.transform != wbm.transform or dem.width != wbm.width or dem.height != wbm.height):
                    raise ValueError(f'Invalid or mismatched accessibility DEM/WBM grids for {(lat, lon)}')
            tiles.append(entry)
        if len(base['tiles']) != policy['expected_base_pairs'] or len(tiles) != len(base_needed) + len(extra):
            raise ValueError('Accessibility DEM manifest does not contain the complete configured source inventory')
        manifest = {
            'schema': 1, 'region': policy['region'],
            'bbox': policy['bbox'], 'base_manifest': Path(policy['base_manifest']).name,
            'base_manifest_sha256': sha256(base_path), 'tiles': tiles,
        }
        temporary = manifest_dir / f'.{output_path.name}.{os.getpid()}.tmp'
        manifest_dir.mkdir(parents=True, exist_ok=True)
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write('\n')
            validate_accessibility_dem_manifest(
                temporary, root, 'great_britain', 'great-britain-g2-v1',
                geographic_config=geographic_config,
            )
            qualification = _sample_accessibility_halo(
                temporary, root, baseline, geographic_config,
            )
            manifest['accessibility_qualification'] = qualification
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write('\n')
            # Hard-link publication is atomic and refuses a concurrent/existing
            # immutable target; it never replaces the original region manifest.
            os.link(temporary, output_path)
            manifest_written = True
        finally:
            temporary.unlink(missing_ok=True)
        print(f'{output_path}: {len(tiles)} verified DEM/WBM pairs; original manifest retained')
        return manifest
    finally:
        if not manifest_written:
            # Keep the isolated generation for diagnosis; it is not referenced
            # by an authoritative manifest and can be removed by its run ID.
            pass

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True)
    parser.add_argument('--cache-dir', default='input/elevation')
    parser.add_argument('--manifest-dir', default='data/elevation/manifests')
    parser.add_argument('--version', default='2024_1')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='Explicitly refresh from CDSE instead of using the local cache')
    parser.add_argument('--allow-glo90', action='store_true', help='Use GLO-90 for GLO-30 cells absent from the catalogue')
    parser.add_argument('--accessibility-halo', action='store_true',
                        help='Create the separate G2 uk_ireland accessibility halo manifest')
    parser.add_argument('--baseline-spots-dir',
                        default='input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19-g2-full/spots',
                        help='Frozen GB/IE spot tiles used for accessibility DEM qualification')
    parser.add_argument('--baseline-inventory', type=Path,
                        help='Trusted pinned source inventory for the frozen GB/IE baseline')
    parser.add_argument('--geographic-config', default='config/accessibility.yaml')
    args = parser.parse_args()
    region = get_region(args.region)
    if args.accessibility_halo:
        if args.region != 'uk_ireland' or args.allow_glo90 or args.refresh:
            parser.error('--accessibility-halo requires --region uk_ireland and forbids GLO-90/refresh')
        prepare_accessibility_halo(None, region, args.cache_dir, args.manifest_dir,
                                   args.version, dry_run=args.dry_run,
                                   baseline_spots_dir=args.baseline_spots_dir,
                                   baseline_inventory_path=args.baseline_inventory,
                                   geographic_config=args.geographic_config)
        return
    needed = required_cells(region, load_geography())
    prepare(None, args.region, region, needed, args.cache_dir, args.manifest_dir,
            args.version, dry_run=args.dry_run, allow_glo90=args.allow_glo90, refresh=args.refresh)


if __name__ == '__main__':
    main()
