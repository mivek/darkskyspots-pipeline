import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import box

from src.geographic_prepare import prepare_dataset,revise_reviews,local_review_contexts
from src.geographic_data import POLICY,GeographicDataset,file_digest
from tools.prepare_geography import minimal_sources
from tools.fetch_geography_sources import RemoteZip,relevant_extracts
from tests.geographic_helpers import REGION,SPOT,feature,decision,make_dataset


def test_packaging_is_byte_reproducible_and_reuses_verified_files(tmp_path):
    evidence=[feature(box(*REGION['bbox']),'marine_land'),feature(box(*REGION['bbox']),'inland_coverage')]
    arguments=dict(regions={'france':REGION},evidence={'france':evidence},
        reviews=dict(schema=1,policy=POLICY,decisions=[]),provenance={'snapshot':'frozen'},
        licensing={'geometry_distribution':'ODbL-1.0','spot_distribution':'pending'})
    for name in ['a','b']:prepare_dataset(tmp_path/name,**arguments)
    assert (tmp_path/'a/manifest.json').read_bytes()==(tmp_path/'b/manifest.json').read_bytes()
    assert (tmp_path/'a/france.geojsonl.gz').read_bytes()==(tmp_path/'b/france.geojsonl.gz').read_bytes()
    prepare_dataset(tmp_path/'a',**arguments)
    (tmp_path/'a/france.geojsonl.gz').write_bytes(b'broken')
    with pytest.raises(ValueError,match='corrupt'):
        prepare_dataset(tmp_path/'a',**arguments)


def test_review_revision_reuses_evidence_without_reencoding_or_autoapproval(tmp_path):
    pond=box(-1,44,1,46)
    dataset=make_dataset(tmp_path/'base',extra=[feature(pond,'water')])
    reviews=dict(schema=2,policy=POLICY,decisions=[decision(pond,'accept_uncertainty')])
    contexts=local_review_contexts(dataset,reviews)
    assert 'context_sha256' not in reviews['decisions'][0]
    reviews['decisions'][0].update({k:contexts[0][k] for k in ['context_sha256','extent_sha256']})
    reviews['decisions'][0]['acceptances'][0]['context_sha256']=contexts[0]['acceptance_contexts']['inland_water_disagreement']
    manifest,_=revise_reviews(dataset.path,reviews,tmp_path/'new',regions={'france':REGION})
    updated=GeographicDataset(tmp_path/'new/manifest.json',regions={'france':REGION})
    assert updated.audit([SPOT])['status']=='passed'
    assert manifest['files']['france']==dataset.manifest['files']['france']
    assert updated.decisions[0]['active']


def test_parent_extract_prevents_duplicate_county_downloads():
    urls={'england':{'url':'https://download.geofabrik.de/europe/united-kingdom/england-261030-free.shp.zip'},
          'devon':{'url':'https://download.geofabrik.de/europe/united-kingdom/england/devon-261030-free.shp.zip'},
          'wales':{'url':'https://download.geofabrik.de/europe/united-kingdom/wales-261030-free.shp.zip'}}
    assert set(minimal_sources(urls))=={'england','wales'}


def test_collection_includes_neighbor_source_when_extract_border_differs():
    owned=box(0,0,2,2)
    index=dict(features=[dict(geometry=feature(box(0,0,1.9,2),'inland_coverage')['geometry'],
                properties=dict(id='primary',urls={'shp':'https://example.invalid/primary-latest-free.shp.zip'})),
        dict(geometry=feature(box(1.9,0,3,2),'inland_coverage')['geometry'],
                properties=dict(id='neighbor',urls={'shp':'https://example.invalid/neighbor-latest-free.shp.zip'})),
        dict(geometry=feature(box(10,10,12,12),'inland_coverage')['geometry'],
                properties=dict(id='unrelated',urls={'shp':'https://example.invalid/unrelated-latest-free.shp.zip'}))])
    selected=relevant_extracts(index,{'france':dict(bbox=[0,0,2,2],osm_country_code=['FR'])},
                               SimpleNamespace(countries={'FR':owned}))
    assert set(selected)=={'primary','neighbor'}


def test_longitude_first_wgs84_source_is_accepted_but_projected_source_is_not(tmp_path):
    import shapefile
    from rasterio.crs import CRS
    from tools.prepare_geography import source_members
    path=tmp_path/'gis_osm_water_a_free_1.shp'
    with shapefile.Writer(str(path)) as writer:
        writer.field('fclass','C');writer.poly([[(0,45),(0,46),(1,46),(1,45),(0,45)]])
        writer.record('water')
    prj=path.with_suffix('.prj')
    prj.write_text('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]')
    def metadata():
        files=[dict(path=p.name,size=p.stat().st_size,sha256=file_digest(p))
               for p in tmp_path.glob('gis_osm_water_a_free_1.*')]
        target=tmp_path/'source.json';target.write_text(json.dumps(dict(files=files)))
        return target
    source_members(metadata())
    prj.write_text(CRS.from_epsg(3857).to_wkt())
    with pytest.raises(ValueError,match='not WGS84'):
        source_members(metadata())


def test_range_reader_is_pinned_bounded_and_never_falls_back_to_full_download():
    data=bytes(range(250))*40
    class Session:
        def head(self,url,**kwargs):
            return SimpleNamespace(url=url,headers={'Content-Length':str(len(data)),'ETag':'frozen'},raise_for_status=lambda:None)
        def get(self,url,**kwargs):
            headers=kwargs['headers'];assert headers['If-Range']=='frozen'
            assert kwargs['stream'] is True
            start,stop=map(int,headers['Range'].split('=')[1].split('-'))
            return SimpleNamespace(status_code=206,headers={'Content-Range':f'bytes {start}-{stop}/{len(data)}'},
                                   content=data[start:stop+1],close=lambda:None)
    reader=RemoteZip('https://example.invalid/source.zip',session=Session())
    reader.block_size=100
    assert reader.read(9000)==data[:9000]
    assert len(reader.blocks)==16
    reader.seek(-5,2);assert reader.read()==data[-5:]
    session=Session()
    session.get=lambda *_a,**_k:SimpleNamespace(status_code=200,close=lambda:None)
    with pytest.raises(ValueError,match='range request failed'):
        RemoteZip('https://example.invalid/source.zip',session=session).read(1)
