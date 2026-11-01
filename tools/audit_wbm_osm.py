#!/usr/bin/env python3
"""Check simulated D points against the already downloaded OSM snapshots."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np
import shapefile
from shapely.geometry import Point, shape
from shapely.strtree import STRtree


def covered_points(geometry, tree):
    """Includes exterior boundaries, excludes island interiors (polygon holes)."""
    return tree.query(geometry, predicate='covers')


def audit(simulation, diagnostic):
    points, records = [], []
    for name in ['france', 'uk_ireland', 'canaries']:
        result = json.loads((simulation / name / 'D.json').read_text())
        baseline = json.loads((simulation / name / 'A.json').read_text())
        final = {(p['row'], p['col']) for p in result['covered']}
        original = {(p['row'], p['col']) for p in baseline['covered']}
        for p in result['selected']:
            records.append(dict(region=name, point=p, final=(p['row'], p['col']) in final,
                                newly_selected_final=(p['row'], p['col']) in final - original,
                                osm_water=[], osm_wetlands=[], osm_land_covers=False,
                                inland_extract_coverage=[]))
            points.append(Point(p['lon'], p['lat']))
    tree = STRtree(points)
    sources, skipped = [], []
    index = json.loads((diagnostic / 'sources/geofabrik-index.json').read_text())
    for path in sorted((diagnostic / 'sources/geofabrik').glob('*/*.shp')):
        region = path.parent.name
        footprints = [f for f in index['features'] if f['properties']['id'] == region]
        for f in footprints:
            for i in covered_points(shape(f['geometry']), tree):
                records[int(i)]['inland_extract_coverage'].append(region)
        metadata = json.loads((path.parent / 'source.json').read_text())
        sources.append(metadata)
        reader = shapefile.Reader(str(path))
        for record in reader.iterRecords():
            props = record.as_dict()
            cls = props['fclass']
            if cls not in {'water', 'reservoir', 'riverbank', 'river', 'dock'} and not cls.startswith('wetland'):
                continue
            try:
                item = reader.shape(record.oid)
            except shapefile.ShapefileException as exc:
                # Two verified empty England polygon records are also listed in
                # the original diagnostic. Never suppress other parser errors.
                if region == 'england' and record.oid in {177454, 193996} and 'non-empty' in str(exc):
                    skipped.append(dict(region=region, index=record.oid, reason=str(exc)))
                    continue
                raise
            if not item.points:
                skipped.append(dict(region=region, index=record.oid, reason='empty'))
                continue
            g = shape(item.__geo_interface__)
            if not g.is_valid:
                raise ValueError(f'Invalid OSM water geometry: {path}, {record.oid}')
            entry = dict(osm_id=props['osm_id'], fclass=cls, name=props['name'],
                         source=str(path.parent / 'source.json'))
            for i in covered_points(g, tree):
                field = 'osm_wetlands' if cls.startswith('wetland') else 'osm_water'
                records[int(i)][field].append(entry)
        print('OSM water checked', region, flush=True)
    land = next((diagnostic / 'sources/osm-land').glob('*.shp'))
    for item in shapefile.Reader(str(land)).iterShapes(bbox=(-20, 26, 9, 56)):
        g = shape(item.__geo_interface__)
        candidates = tree.query(g)
        if not len(candidates):
            continue
        if not g.is_valid:
            raise ValueError(f'Invalid relevant OSM land geometry: {item.oid}')
        for i in covered_points(g, tree):
            records[int(i)]['osm_land_covers'] = True
    sources.append(json.loads((diagnostic / 'sources/osm-land-source.json').read_text()))
    for r in records:
        r['osm_water_suspected'] = bool(r['osm_water']) or not r['osm_land_covers']
        r['inland_status'] = ('water_polygon' if r['osm_water'] else
                              'not_covered_by_selected_water_polygons' if r['inland_extract_coverage'] else
                              'outside_downloaded_extract_coverage')
    summary = {}
    for region in ['france', 'uk_ireland', 'canaries']:
        rows = [r for r in records if r['region'] == region]
        final = [r for r in rows if r['final']]
        summary[region] = dict(selected=len(rows), final=len(final),
                              selected_water_suspected=sum(r['osm_water_suspected'] for r in rows),
                              final_water_suspected=sum(r['osm_water_suspected'] for r in final),
                              new_final_water_suspected=sum(r['osm_water_suspected'] and r['newly_selected_final'] for r in final),
                              final_outside_inland_extract_coverage=sum(not r['inland_extract_coverage'] for r in final))
    return dict(method='Full D candidate pool, not only the original 73 points; polygon covers with island holes. Global OSM land is a marine mask only. Inland absence is never evidence of dry land.',
                attribution='© OpenStreetMap contributors, ODbL 1.0; Geofabrik and OSMCoastline snapshots from original diagnostic.',
                summary=summary, sources=sources, skipped_empty_records=skipped,
                shapefile_version=shapefile.__version__,
                suspected_final=[r for r in records if r['final'] and r['osm_water_suspected']],
                suspected_selected=[r for r in records if r['osm_water_suspected']],
                points=records)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--simulation', type=Path, required=True)
    p.add_argument('--diagnostic', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = audit(args.simulation, args.diagnostic)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise
