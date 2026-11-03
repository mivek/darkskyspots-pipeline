"""Explicit synthetic geographic evidence; never a production bypass."""
import copy
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from shapely.geometry import box, mapping

from src.geographic_data import GeographicDataset, POLICY, file_digest
from src.geographic_prepare import prepare_dataset


REGION = dict(bbox=[-6, 41, 8, 51], osm_country_code=['FR'])
SPOT = dict(id='control', lat=45.0, lon=0.0, country='FR', altitude=None,
            darkness=.8, bortle=3, name='Control', near='Test locality')


def feature(geometry, kind, **properties):
    return dict(type='Feature', geometry=mapping(geometry), properties=dict(kind=kind, **properties))


def decision(geometry, conclusion='corroborated_water', identifier='reviewed-pond',
             reasons=('inland_water_disagreement',)):
    result = dict(id=identifier, region='france', extent=mapping(geometry), conclusion=conclusion,
                rationale='Synthetic independently reviewed geographic feature',
                evidence=[dict(source='synthetic orthophoto', date='2026-01-01')],
                provenance=[dict(source='synthetic water geometry', sha256='fixture')])
    if conclusion=='accept_uncertainty':
        result['acceptances']=[dict(reason=reason,context_sha256='0'*64,
            evidence=result['evidence'],provenance=result['provenance']) for reason in reasons]
    return result


def make_dataset(root, *, extra=(), decisions=(), assessed=True, evidence=None,
                 approve_decisions=True, regions=None, wbm_value=0, wbm_lat=45):
    root = Path(root)
    root.mkdir(parents=True,exist_ok=True)
    regions = regions or {'france': REGION}
    features = evidence if evidence is not None else [
        feature(box(*REGION['bbox']), 'marine_land'),
        feature(box(*REGION['bbox']), 'inland_coverage'), *extra]
    reviews = dict(schema=2 if any(d['conclusion']=='accept_uncertainty' for d in decisions) else 1,
                   policy=POLICY, decisions=copy.deepcopy(list(decisions)))
    licensing = dict(geometry_distribution='ODbL-1.0', spot_distribution='assessed' if assessed else 'pending')
    if assessed:
        licensing['spot_assessment_record']=dict(reference='Synthetic test data only',
            reviewed_by='Test fixture author',resulting_database_license='Synthetic fixture; no real-data licensing approval')
    assets,terrain={},{}
    for name in regions:
        water=root/f'{name}-wbm.tif'
        with rasterio.open(water,'w',driver='GTiff',width=4,height=4,count=1,dtype='uint8',
                           crs='EPSG:4326',transform=from_origin(-.5,wbm_lat+1.5,.75,.75),nodata=255) as out:
            out.write(np.full((4,4),wbm_value,dtype='uint8'),1)
        info=dict(path=water.name,size=water.stat().st_size,sha256=file_digest(water))
        manifest=root/f'{name}-terrain.json'
        manifest.write_text(json.dumps(dict(schema=1,region=name,tiles=[
            dict(lat=wbm_lat,lon=lon,product='GLO-30',wbm=info) for lon in [0,1]])))
        key=f'terrain:{name}';terrain[name]=key
        assets[key]=dict(path=manifest.name,source=manifest,size=manifest.stat().st_size,sha256=file_digest(manifest))
        assets[f'wbm:{name}']=dict(info,source=water)
    _, contexts = prepare_dataset(root / 'draft', regions, {name: features for name in regions}, reviews,
                                  provenance={'source': 'synthetic fixture'}, licensing=licensing,assets=assets,terrain=terrain)
    if approve_decisions:
        by_id = {x['id']: x for x in contexts}
        for item in reviews['decisions']:
            item.update({key: by_id[item['id']][key] for key in ['context_sha256', 'extent_sha256']})
            for acceptance in item.get('acceptances',[]):
                acceptance['context_sha256']=by_id[item['id']]['acceptance_contexts'][acceptance['reason']]
    prepare_dataset(root / 'approved', regions, {name: features for name in regions}, reviews,
                    provenance={'source': 'synthetic fixture'}, licensing=licensing,assets=assets,terrain=terrain)
    return GeographicDataset(root / 'approved/manifest.json',regions=regions)


def write_spots(repo, spots=None):
    path = Path(repo) / 'spots' / 'N045E000.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(tile='N045E000', version='2025.1', source='fixture',
                                   generated='old', spots=[dict(SPOT)] if spots is None else spots)))
    return path


def make_e3_dataset(root, *, e3_decisions=None, degraded=None, availability=None,
                    approve_decisions=True, wc_value=30, **kwargs):
    """Explicitly authorized synthetic E3 fixtures, never real approvals."""
    from src.geographic_e3_prepare import prepare_e3
    from src.geographic_data import E3_POLICY
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    legacy=make_dataset(root/'legacy',**kwargs)
    cache=root/'worldcover';cache.mkdir()
    tiles={}
    for lat in [42,45]:
        for lon in [-3,0]:
            tile=f'N{lat:02d}{"W" if lon<0 else "E"}{abs(lon):03d}'
            path=cache/f'{tile}.tif'
            with rasterio.open(path,'w',driver='GTiff',width=4,height=4,count=1,dtype='uint8',
                               crs='EPSG:4326',transform=from_origin(lon,lat+3,.75,.75),nodata=0) as out:
                out.write(np.full((4,4),wc_value,dtype='uint8'),1)
            tiles[tile]=dict(path=path.name,size=path.stat().st_size,sha256=file_digest(path))
    manifest=cache/'manifest.json';manifest.write_text(json.dumps(dict(product='ESA WorldCover 2021 v200',
        licence='CC-BY-4.0',tiles=tiles,coverage=dict(south=-60,north=82.75))))
    decisions=copy.deepcopy(e3_decisions if e3_decisions is not None else legacy.review_document['decisions'])
    reviews=dict(schema=4 if any(d['conclusion']=='resolve_risk' for d in decisions) else 3,
                 policy=E3_POLICY,decisions=decisions)
    # The fixture author explicitly approves generated contexts in two phases.
    m,contexts=prepare_e3(legacy.path,manifest,root/'e3-draft',reviews=reviews,
        degraded=degraded,availability=availability,regions=kwargs.get('regions',{'france':REGION}))
    if approve_decisions:
        by_id={c['id']:c for c in contexts}
        for d in reviews['decisions']:
            d.update({k:by_id[d['id']][k] for k in ['context_sha256','extent_sha256']})
            for part in d.get('acceptances',d.get('resolutions',[])):
                part['context_sha256']=by_id[d['id']]['acceptance_contexts'][part['reason']]
        if degraded:
            degraded=copy.deepcopy(degraded)
            for p in degraded['policies']:p['context_sha256']=m['degraded_contexts'][p['id']]
    prepare_e3(legacy.path,manifest,root/'e3-approved',reviews=reviews,degraded=degraded,
        availability=availability,regions=kwargs.get('regions',{'france':REGION}))
    return GeographicDataset(root/'e3-approved/manifest.json',regions=kwargs.get('regions',{'france':REGION}),require_e3=True)
