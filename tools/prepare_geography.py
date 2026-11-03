#!/usr/bin/env python3
"""Prepare the three regional offline evidence packages from pinned OSM inputs.

No network access is performed. Sources use the diagnostic cache layout, with
OSMCoastline's original archive and Geofabrik member sizes/SHA-256 manifests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import zipfile

import shapefile
from rasterio.crs import CRS
from shapely.geometry import mapping, shape, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geographic_data import file_digest
from src.geographic_prepare import prepare_dataset,revise_reviews,minimal_sources,relevant_extracts
from src.regions import load_regions
from src.geography import load_geography


def source_members(source):
    metadata = json.loads(source.read_text(encoding='utf-8'))
    for info in metadata['files']:
        path = source.parent / Path(info['path']).name
        if not path.is_file() or path.stat().st_size != info['size'] or file_digest(path) != info['sha256']:
            raise ValueError(f'Corrupt geographic source member: {path}')
    members = {Path(info['path']).name for info in metadata['files']}
    for filename in sorted(name for name in members if name.endswith('.shp')):
        stem = filename[:-4]
        if not {stem+suffix for suffix in ['.shp','.shx','.dbf','.prj']} <= members:
            raise ValueError(f'Unverified required shapefile members: {source}')
        # ESRI shapefile WKT commonly declares longitude-first axes. Resolve
        # the geographic CRS authority instead of comparing axis spelling.
        if CRS.from_wkt((source.parent/(stem+'.prj')).read_text()).to_epsg() != 4326:
            raise ValueError(f'Geographic source is not WGS84: {source}')
    return metadata


def terrain_assets(manifest_dir, cache_dir, directory, regions):
    assets, terrain = {}, {}
    cache_dir = Path(cache_dir).resolve()
    for name in regions:
        original = Path(manifest_dir) / f'{name}.json'
        manifest = json.loads(original.read_text())
        if manifest.get('schema') != 1 or manifest.get('region') != name:
            raise ValueError(f'Invalid source WBM manifest: {original}')
        for index,tile in enumerate(manifest['tiles']):
            info = tile['wbm']
            source = (cache_dir / info['path']).resolve()
            if not source.is_relative_to(cache_dir):raise ValueError('WBM source escapes cache')
            path = f'wbm/{name}/{index}.tif'
            assets[f'wbm:{name}:{index}'] = dict(path=path,source=source,size=info['size'],sha256=info['sha256'])
            info['path'] = path
        local = Path(directory) / f'{name}.json'
        local.write_text(json.dumps(manifest,sort_keys=True)+'\n')
        key = f'terrain:{name}'
        assets[key] = dict(path=f'terrain/{name}.json',source=local,size=local.stat().st_size,sha256=file_digest(local))
        terrain[name] = key
    return assets,terrain


def verify_marine(sources):
    metadata = json.loads((sources / 'osm-land-source.json').read_text())
    archive = sources / 'land-polygons-split-4326.zip'
    if archive.stat().st_size != metadata['size'] or file_digest(archive) != metadata['sha256']:
        raise ValueError('Corrupt original OSMCoastline source archive')
    members = []
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            if Path(info.filename).suffix.lower() not in {'.shp', '.shx', '.dbf', '.prj', '.cpg'}:
                continue
            path = sources / 'osm-land' / Path(info.filename).name
            checksum=hashlib.sha256()
            with zipped.open(info) as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    checksum.update(block)
            expected=checksum.hexdigest()
            if not path.is_file() or path.stat().st_size != info.file_size or file_digest(path) != expected:
                raise ValueError(f'Extracted OSMCoastline member differs from pinned archive: {path}')
            if path.suffix.lower()=='.prj' and CRS.from_wkt(path.read_text()).to_epsg()!=4326:
                raise ValueError('OSMCoastline source is not WGS84')
            members.append(dict(path=path.name, size=path.stat().st_size, sha256=expected))
    return dict(metadata, members=members)


def is_empty_polygon(path, oid):
    """Verify empty Polygon records when pyshp 3 rejects their zero parts.

    This is a narrowly checked empty-source record, not a parser-error fallback.
    Nonempty corrupt or invalid geometries always fail preparation.
    """
    with path.with_suffix('.shx').open('rb') as index:
        index.seek(100 + 8 * oid)
        offset, length = struct.unpack('>2i', index.read(8))
    if length not in {22,24}:
        return False
    with path.open('rb') as source:
        source.seek(offset * 2 + 8)
        content = source.read(length * 2)
    kind, *bounds, parts, points = struct.unpack('<i4d2i', content[:44])
    return (kind == 5 and points == 0 and not any(bounds)
            and ((parts == 0 and length == 22)
                 or (parts == 1 and length == 24 and content[44:] == b'\0\0\0\0')))


def water_features(path, skipped, *, bbox=None):
    reader = shapefile.Reader(str(path))
    for record in reader.iterRecords():
        props = record.as_dict()
        cls = props['fclass']
        if cls not in {'water', 'reservoir', 'river', 'riverbank', 'dock'} and not cls.startswith('wetland'):
            continue
        try:
            polygon = reader.shape(record.oid,bbox=bbox)
        except shapefile.ShapefileException:
            if not is_empty_polygon(path, record.oid):
                raise
            skipped.append(dict(source=path.parent.name,member=path.name, record=record.oid, reason='verified empty Polygon'))
            continue
        if polygon is None:
            continue
        if not polygon.points:
            skipped.append(dict(source=path.parent.name,member=path.name, record=record.oid, reason='empty geometry'))
            continue
        yield dict(type='Feature', geometry=polygon.__geo_interface__, properties=dict(
            kind='wetland' if cls.startswith('wetland') else 'water',
            id=str(props['osm_id']), **{'class': cls}, name=props.get('name', ''),
            source=path.parent.name, record=record.oid,
            **{k:props[k] for k in ['tidal','intermittent','wetland','water'] if k in props}))


def prepare(sources, reviews, output, *, regions=None,
            terrain_manifest_dir='data/elevation/manifests', terrain_cache='input/elevation',licensing=None):
    sources, reviews = Path(sources), Path(reviews)
    regions = regions or load_regions()
    if set(regions) != {'france', 'uk_ireland', 'canaries'}:
        raise ValueError('Prepare exactly the three initial configured regions')
    index = json.loads((sources / 'geofabrik-index.json').read_text())
    selection_path=sources/'source-selection.json'
    selection=json.loads(selection_path.read_text()) if selection_path.exists() else {}
    if selection and selection.get('index_sha256')!=file_digest(sources/'geofabrik-index.json'):
        raise ValueError('Source availability inventory belongs to another frozen index')
    required=relevant_extracts(index,regions,load_geography(),unavailable=selection.get('unavailable',{}))
    missing=[name for name in required if not (sources/'geofabrik'/name/'source.json').exists()]
    if missing:raise ValueError(f'Missing required regional/border extracts: {sorted(missing)}; run explicit source fetch')
    marine = verify_marine(sources)
    hydro={name:source_members(sources/'geofabrik'/name/'source.json') for name in sorted(required)}
    skipped = []

    def features(name):
        print('Preparing geographic region',name,flush=True)
        from src.geographic_prepare import preparation_domain
        frame = preparation_domain(regions[name],Path(terrain_manifest_dir)/f'{name}.json',terrain_cache)
        land_path = next((sources / 'osm-land').glob('*.shp'))
        reader = shapefile.Reader(str(land_path))
        for polygon in reader.iterShapes(bbox=frame.bounds):
            geometry = polygon.__geo_interface__
            yield dict(type='Feature', geometry=geometry, properties=dict(
                kind='marine_land', source='OSMCoastline', record=polygon.oid))
        for region_id in sorted(hydro):
            footprints = [f for f in index['features'] if f['properties']['id'] == region_id]
            relevant = [f for f in footprints if shape(f['geometry']).intersects(frame)]
            if not relevant:
                continue
            print('Reading regional water',name,region_id,flush=True)
            for footprint in relevant:
                yield dict(type='Feature', geometry=footprint['geometry'], properties=dict(
                    kind='inland_coverage', source=region_id))
            paths = [sources / 'geofabrik' / region_id / Path(item['path']).name
                     for item in hydro[region_id]['files'] if item['path'].endswith('.shp')]
            for path in sorted(paths):
                yield from water_features(path, skipped,bbox=frame.bounds)

    with tempfile.TemporaryDirectory(prefix='geography-wbm-manifests-') as temporary:
        assets, terrain = terrain_assets(terrain_manifest_dir,terrain_cache,temporary,regions)
        result = prepare_dataset(output, regions, {name: features(name) for name in regions},
                                 json.loads(reviews.read_text()),
                                 assets=assets,terrain=terrain,
                                 provenance=dict(marine=marine, hydro=hydro,
                                                 parser_version=shapefile.__version__,skipped_empty_records=skipped,
                                                 source_selection=selection,
                                                 geofabrik_index_sha256=file_digest(sources / 'geofabrik-index.json')),
                                 licensing=licensing or dict(geometry_distribution='ODbL-1.0', spot_distribution='pending',
                                                attribution='© OpenStreetMap contributors; Geofabrik; OSMCoastline; Copernicus DEM',
                                                assessment='docs/geographic-licensing.md',
                                                imagery_derivation='No imagery-derived geometry is automatically prepared'))
    print(json.dumps(dict(evidence_counts=result[0]['evidence_counts'],
                          review_contexts=result[1], skipped_empty=skipped), indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--sources', type=Path)
    mode.add_argument('--base-manifest',type=Path,help='Reuse unchanged verified evidence and update reviews only')
    parser.add_argument('--reviews', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--terrain-manifests',default='data/elevation/manifests',type=Path)
    parser.add_argument('--terrain-cache',default='input/elevation',type=Path)
    parser.add_argument('--licensing-assessment',type=Path,help='Separate explicit geometry/spot distribution assessment record')
    args = parser.parse_args()
    licensing=json.loads(args.licensing_assessment.read_text()) if args.licensing_assessment else None
    if args.base_manifest:
        print(json.dumps(revise_reviews(args.base_manifest,json.loads(args.reviews.read_text()),args.output,licensing=licensing)[1],indent=2))
    else:
        prepare(args.sources, args.reviews, args.output,
                terrain_manifest_dir=args.terrain_manifests,terrain_cache=args.terrain_cache,licensing=licensing)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
