"""Deterministic regional packaging; no network access or geometry repair."""
import gzip
import json
from pathlib import Path
import re
import shutil
import tempfile

from shapely.geometry import box,shape,mapping
from shapely.ops import unary_union
from .geographic_geometry import domain,canonical

from .geographic_data import (POLICY, REGIONS, digest, file_digest, geometry_digest,
                              valid_geometry, validate_licensing, acceptance_part,
                              validate_review_document)


def minimal_sources(metadata):
    prefixes={key:item['url'].rsplit('/',1)[0]+'/'+re.sub(
        r'-(?:latest|\d{6})-free\.shp\.zip$','',item['url'].rsplit('/',1)[1])+'/'
        for key,item in metadata.items()}
    return {key:item for key,item in metadata.items()
            if not any(other!=key and item['url'].startswith(prefix) for other,prefix in prefixes.items())}


def relevant_extracts(index,registry,geography,*,unavailable=()):
    """Select collection inputs by geographic extent, not source country name."""
    domains=[unary_union([geography.countries[c] for c in region['osm_country_code']]).intersection(domain(region['bbox']))
             for region in registry.values()]
    selected={}
    for feature in index['features']:
        properties=feature['properties'];url=properties.get('urls',{}).get('shp')
        if not url or properties['id'] in unavailable:continue
        footprint=shape(feature['geometry'])
        for footprint_domain in domains:
            if not footprint.envelope.intersects(footprint_domain.envelope):continue
            if footprint.intersection(footprint_domain).area>0:
                selected[properties['id']]=dict(url=url)
                break
    return {name:item['url'] for name,item in minimal_sources(selected).items()}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False) + '\n', encoding='utf-8')


def preparation_domain(region, terrain=None, cache=None):
    """Retain evidence for actual native raster support, never a buffer."""
    frame=domain(region['bbox'])
    if terrain is None:return frame
    import rasterio
    manifest=json.loads(Path(terrain).read_text())
    footprints=[frame]
    for tile in manifest['tiles']:
        with rasterio.open(Path(cache)/tile['wbm']['path']) as raster:
            if str(raster.crs)!='EPSG:4326':raise ValueError('Preparation requires native WGS84 WBM')
            footprints.append(canonical(box(*raster.bounds)))
    return unary_union(footprints)


def prepare_dataset(output, regions, evidence, reviews, *, provenance, licensing,
                    assets=None, terrain=None):
    """Package iterables of OSM-derived features and bind reviews to local context.

    Human-provided fingerprints are never filled in or approved automatically.
    Output contexts let a reviewer check and explicitly record a decision.
    Missing extraction coverage is preserved for the final audit to detect.
    """
    if not regions:
        raise ValueError('Preparation requires a regional registry')
    validate_review_document(reviews)
    validate_licensing(licensing)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.geography-') as temporary:
        stage = Path(temporary)/'package'
        stage.mkdir()
        contexts = {d['id']: set() for d in reviews['decisions']}
        scoped = {d['id']: {a['reason']: set() for a in d.get('acceptances', [])}
                  for d in reviews['decisions']}
        local = {d['id']: dict(land=[], water=[]) for d in reviews['decisions']}
        extents = {d['id']: valid_geometry(d['extent'], polygon=True) for d in reviews['decisions']}
        files, registry, counts = {}, {}, {}
        for key, asset in (assets or {}).items():
            target = stage / asset['path']
            if not target.resolve().is_relative_to(stage):
                raise ValueError('Geographic asset escapes package')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset['source'], target)
            if file_digest(target) != asset['sha256'] or target.stat().st_size != asset['size']:
                raise ValueError(f'Corrupt geographic asset: {key}')
            files[key] = {k: asset[k] for k in ['path','size','sha256']}
        for name in sorted(regions):
            region = regions[name]
            terrain_path=stage/files[terrain[name]]['path'] if terrain and name in terrain else None
            frame = preparation_domain(region,terrain_path,stage)
            path = stage / f'{name}.geojsonl.gz'
            counts[name] = 0
            # mtime and gzip filename are fixed; identical input order gives
            # identical archives. Source converters supply stable ordering.
            with path.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as target:
                for feature in evidence[name]:
                    geometry = canonical(valid_geometry(feature['geometry'], polygon=True))
                    props = feature['properties']
                    kind = props['kind']
                    if kind not in {'marine_land', 'inland_coverage', 'water', 'wetland'}:
                        raise ValueError(f'Unsupported geographic evidence: {kind}')
                    if not geometry.intersects(frame):
                        continue
                    # Keep complete original geometries: regional bounding
                    # boxes must not manufacture shorelines or fill holes.
                    feature = dict(feature,bbox=list(geometry.bounds))
                    target.write((json.dumps(feature, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False, allow_nan=False) + '\n').encode())
                    counts[name] += 1
                    for d in reviews['decisions']:
                        identifier = d['id']
                        if d['region'] != name or not geometry.intersects(extents[identifier]):
                            continue
                        clipped = geometry.intersection(extents[identifier])
                        if clipped.is_empty:
                            continue
                        contexts[identifier].add((kind, props.get('class', ''), geometry_digest(clipped)))
                        for reason, parts in scoped[identifier].items():
                            part = acceptance_part(reason, props, clipped)
                            if part is not None:
                                parts.add(part)
                        if kind in {'marine_land', 'water'}:
                            local[identifier]['land' if kind == 'marine_land' else 'water'].append(clipped)
            files[name] = dict(path=path.name, size=path.stat().st_size, sha256=file_digest(path))
            registry[name] = dict(bbox=region['bbox'], countries=region['osm_country_code'], evidence=name,
                                  evidence_domain=mapping(frame))
        fingerprints = {key: digest(sorted(value)) for key, value in contexts.items()}
        review_status = []
        for d in reviews['decisions']:
            identifier = d['id']
            if d['region'] not in regions:
                raise ValueError(f'Review refers to an unprepared region: {identifier}')
            extent = extents[identifier]
            if not domain(regions[d['region']]['bbox']).covers(extent):
                raise ValueError(f'Review extent is outside its regional dataset: {identifier}')
            if d['conclusion'] == 'corroborated_water':
                source_water = extent.difference(unary_union(local[identifier]['land'])).union(
                    unary_union(local[identifier]['water']))
                if not extent.difference(source_water).is_empty:
                    raise ValueError(f'Exclusion extends outside source water geometry: {identifier}')
            review_status.append(dict(id=identifier, context_sha256=fingerprints[identifier],
                                      acceptance_contexts={reason:digest(sorted(parts))
                                          for reason,parts in scoped[identifier].items()},
                                      extent_sha256=geometry_digest(extent),
                                      active=d.get('context_sha256') == fingerprints[identifier]
                                      and d.get('extent_sha256') == geometry_digest(extent)))
        write_json(stage / 'reviews.json', reviews)
        files['reviews'] = dict(path='reviews.json', size=(stage / 'reviews.json').stat().st_size,
                                sha256=file_digest(stage / 'reviews.json'))
        manifest = dict(schema=1, policy=POLICY, regions=registry, files=files,
                        contexts=fingerprints, provenance=provenance, licensing=licensing,
                        acceptance_contexts={identifier:{reason:digest(sorted(parts))
                            for reason,parts in reasons.items()} for identifier,reasons in scoped.items() if reasons},
                        evidence_counts=counts,
                        terrain=terrain or {},
                        coverage_note='Extract collection footprints do not guarantee completeness of OSM mapping.')
        write_json(stage / 'manifest.json', manifest)
        write_json(stage / 'review-contexts.json', review_status)
        if output.exists():
            # Existing immutable packages are reusable only when byte-identical.
            if (output / 'manifest.json').read_bytes() != (stage / 'manifest.json').read_bytes():
                raise ValueError('Output already contains another dataset; use a new revision directory')
            for info in files.values():
                existing = output / info['path']
                if not existing.is_file() or file_digest(existing) != info['sha256']:
                    raise ValueError(f'Existing geographic package is corrupt: {existing}')
        else:
            stage.rename(output)
    return manifest, review_status


def local_review_contexts(dataset, reviews):
    """Rebind human reviews without downloading/re-encoding regional evidence."""
    from .geographic_data import records
    from shapely.strtree import STRtree
    contexts = {d['id']:set() for d in reviews['decisions']}
    scoped = {d['id']:{a['reason']:set() for a in d.get('acceptances',[])} for d in reviews['decisions']}
    extents = {d['id']:valid_geometry(d['extent'],polygon=True) for d in reviews['decisions']}
    local = {d['id']:dict(land=[],water=[]) for d in reviews['decisions']}
    for name in dataset.regions:
        relevant=[d for d in reviews['decisions'] if d['region']==name]
        if not relevant:continue
        tree=STRtree([extents[d['id']] for d in relevant])
        for feature in records(dataset.entries[dataset.regions[name]['evidence']]):
            geometry=None
            if 'bbox' in feature:
                indexes=tree.query(box(*feature['bbox']))
                if not len(indexes):continue
            else:
                geometry=valid_geometry(feature['geometry'],polygon=True)
                indexes=tree.query(geometry)
            if not len(indexes):continue
            geometry=geometry or valid_geometry(feature['geometry'],polygon=True)
            props=feature['properties'];kind=props['kind']
            for index in indexes:
                d=relevant[int(index)];identifier=d['id']
                clipped=geometry.intersection(extents[identifier])
                if clipped.is_empty:continue
                contexts[identifier].add((kind,props.get('class',''),geometry_digest(clipped)))
                for reason,parts in scoped[identifier].items():
                    part=acceptance_part(reason,props,clipped)
                    if part is not None:parts.add(part)
                if kind in {'water','marine_land'}:
                    local[identifier]['water' if kind=='water' else 'land'].append(clipped)
    result=[]
    for d in reviews['decisions']:
        identifier=d['id'];extent=extents[identifier]
        if d['region'] not in dataset.regions:raise ValueError('Review outside prepared regional registry')
        if not dataset.regions[d['region']]['domain'].covers(extent):raise ValueError('Review outside prepared region')
        if d['conclusion']=='corroborated_water':
            water=extent.difference(unary_union(local[identifier]['land'])).union(unary_union(local[identifier]['water']))
            if not extent.difference(water).is_empty:raise ValueError('Exclusion extends outside source water geometry')
        result.append(dict(id=identifier,context_sha256=digest(sorted(contexts[identifier])),
                           acceptance_contexts={reason:digest(sorted(parts))
                               for reason,parts in scoped[identifier].items()},
                           extent_sha256=geometry_digest(extent)))
    return result


def revise_reviews(base, reviews, output, *, regions=None,licensing=None):
    """Copy unchanged verified assets and package explicitly fingerprinted reviews."""
    from .geographic_data import GeographicEvidence
    dataset=GeographicEvidence(base,regions=regions)
    if dataset.manifest.get('policy') == 'geographic-confidence-e3-v1':
        from .geographic_e3_prepare import prepare_e3
        degraded=json.loads(dataset.entries[dataset.manifest['degraded_policies']].read_text())
        return prepare_e3(base,dataset.entries[dataset.manifest['worldcover']],output,
                          reviews=reviews,degraded=degraded,regions=regions,licensing=licensing)
    validate_review_document(reviews)
    contexts=local_review_contexts(dataset,reviews)
    output=Path(output)
    if output.exists():raise ValueError('Use a new geographic revision directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(dataset.path.read_text())
    if licensing is not None:
        validate_licensing(licensing)
        manifest['licensing']=licensing
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='.reviews-') as temporary:
        stage=Path(temporary)/'package'
        stage.mkdir()
        for name,info in manifest['files'].items():
            if name=='reviews':continue
            target=stage/info['path'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(dataset.entries[name],target)
            if target.stat().st_size!=info['size'] or file_digest(target)!=info['sha256']:
                raise ValueError('Geographic asset changed while revising reviews')
        write_json(stage/'reviews.json',reviews)
        manifest['files']['reviews']=dict(path='reviews.json',size=(stage/'reviews.json').stat().st_size,
                                         sha256=file_digest(stage/'reviews.json'))
        manifest['contexts']={d['id']:d['context_sha256'] for d in contexts}
        manifest['acceptance_contexts']={d['id']:d['acceptance_contexts'] for d in contexts if d['acceptance_contexts']}
        write_json(stage/'manifest.json',manifest)
        write_json(stage/'review-contexts.json',contexts)
        stage.rename(output)
    return manifest,contexts
