import json
from unittest.mock import patch

import pytest
from shapely.geometry import Polygon, box

from src.geographic_data import (GeographicDataset, file_digest, geometry_digest, spatial_context,
                                 validate_review_document, POLICY)
from tests.geographic_helpers import SPOT, REGION, decision, feature, make_dataset


def test_only_reviewed_water_interior_excluded_hole_and_boundary_preserved(tmp_path):
    pond = Polygon([(-1,44),(1,44),(1,46),(-1,46)],
                   holes=[[(-.1,44.9),(.1,44.9),(.1,45.1),(-.1,45.1)]])
    dataset = make_dataset(tmp_path, extra=[feature(pond,'water', id='pond')], decisions=[decision(pond)])
    candidates = [dict(SPOT),dict(SPOT,lon=.5),dict(SPOT,lon=1)]
    rejected=[]
    assert dataset.qualify(candidates,rejected=rejected)==[candidates[0],candidates[2]]
    assert rejected==[dict(point=candidates[1],decisions=['reviewed-pond'],reason='corroborated_water_interior')]
    assert dataset.audit(candidates)['findings'][1]['status']=='reject_water'


def test_unreviewed_river_and_island_remain_admissible_but_final_gate_holds(tmp_path):
    river=feature(box(-1,44,1,46),'water',id='river-with-missing-island',**{'class':'riverbank'})
    dataset=make_dataset(tmp_path,extra=[river])
    assert dataset.qualify([SPOT])==[SPOT]
    report=dataset.audit([SPOT])
    assert report['status']=='blocked'
    assert report['findings'][0]['reasons']==['inland_water_disagreement']
    assert dataset.audit([])['status']=='passed'  # intermediate point is absent


def test_explicit_acceptance_preserves_uncertainty_without_dry_claim(tmp_path):
    pond=box(-1,44,1,46)
    dataset=make_dataset(tmp_path,extra=[feature(pond,'water')],
                         decisions=[decision(pond,'accept_uncertainty')])
    report=dataset.audit([SPOT])
    assert report['status']=='passed'
    assert report['findings'][0]['status']=='accepted_uncertainty'


def test_missing_extract_coverage_cannot_be_waived(tmp_path):
    dataset=make_dataset(tmp_path,evidence=[feature(box(-6,41,8,51),'marine_land')],
                         decisions=[decision(box(-1,44,1,46),'accept_uncertainty')])
    assert dataset.audit([SPOT])['findings'][0]['status']=='hold'
    assert 'incomplete_source_extract_coverage' in dataset.audit([SPOT])['findings'][0]['reasons']


def test_wet_meadow_or_bog_is_not_automatically_an_open_water_disagreement(tmp_path):
    wetland=feature(box(-1,44,1,46),'wetland',**{'class':'wetland_bog'})
    dataset=make_dataset(tmp_path,extra=[wetland])
    report=dataset.audit([SPOT])
    assert report['status']=='passed'
    assert report['findings'][0]['wetland_features']==[wetland['properties']]
    assert report['findings'][0]['reasons']==[]
    assert dataset.qualify([SPOT])==[SPOT]


def test_tidalflat_is_review_uncertainty_not_automatic_exclusion(tmp_path):
    wetland=feature(box(-1,44,1,46),'wetland',**{'class':'wetland_tidalflat'})
    dataset=make_dataset(tmp_path,extra=[wetland])
    assert dataset.qualify([SPOT])==[SPOT]
    assert dataset.audit([SPOT])['findings'][0]['reasons']==['tidalflat_uncertainty']


def test_changed_geometry_outside_extent_does_not_invalidate_human_review(tmp_path):
    extent=box(-1,44,1,46)
    evidence=[feature(box(-6,41,8,51),'marine_land'),feature(box(-6,41,8,51),'inland_coverage'),
              feature(extent,'water')]
    original=make_dataset(tmp_path/'a',evidence=evidence,decisions=[decision(extent,'accept_uncertainty')])
    raw=json.loads(original.entries['reviews'].read_text())['decisions']
    changed=make_dataset(tmp_path/'b',evidence=[*evidence,feature(box(3,47,4,48),'water')],
                         decisions=raw,approve_decisions=False)
    assert original.revision!=changed.revision
    assert changed.decisions[0]['active']
    assert changed.audit([SPOT])['status']=='passed'
    local=make_dataset(tmp_path/'c',evidence=[*evidence,feature(box(.2,44.5,.3,44.6),'wetland')],
                       decisions=raw,approve_decisions=False)
    assert not local.decisions[0]['active']
    assert local.audit([SPOT])['findings'][0]['status']=='hold'


def test_explicit_evidence_hold_blocks_even_without_mapped_disagreement(tmp_path):
    extent=box(-1,44,1,46)
    dataset=make_dataset(tmp_path/'held',decisions=[decision(extent,'hold')])
    result=dataset.audit([SPOT])
    assert result['status']=='blocked'
    finding=result['findings'][0]
    assert finding['status']=='hold'
    assert finding['reasons']==['reviewed_geographic_hold']
    assert finding['decisions']==['reviewed-pond']
    assert finding['stale_decisions']==[]


@pytest.mark.parametrize('additional_disagreement',[False,True])
def test_stale_explicit_hold_remains_blocking(tmp_path,additional_disagreement):
    extent=box(-1,44,1,46)
    original=make_dataset(tmp_path/'original',decisions=[decision(extent,'hold')])
    reviews=json.loads(original.entries['reviews'].read_text())['decisions']
    # A real source change inside the review extent, away from the point,
    # invalidates its context without creating a disagreement at the point.
    extra=[feature(box(.2,45.2,.3,45.3),'wetland',**{'class':'wetland_bog'})]
    if additional_disagreement:
        extra.append(feature(extent,'water'))
    stale=make_dataset(tmp_path/'changed',extra=extra,decisions=reviews,approve_decisions=False)
    assert not stale.decisions[0]['active']
    result=stale.audit([SPOT])
    finding=result['findings'][0]
    assert result['status']=='blocked'
    assert finding['status']=='hold'
    expected={'reviewed_geographic_hold'}
    if additional_disagreement:
        expected.add('inland_water_disagreement')
    assert set(finding['reasons'])==expected
    assert finding['wbm_class']==0
    assert finding['decisions']==[]
    assert finding['stale_decisions']==['reviewed-pond']


def test_explicit_hold_removal_requires_a_new_reviewed_revision(tmp_path):
    from src.geographic_prepare import revise_reviews

    extent=box(-1,44,1,46)
    original=make_dataset(tmp_path/'original',decisions=[decision(extent,'hold')])
    reviews=json.loads(original.entries['reviews'].read_text())['decisions']
    stale=make_dataset(tmp_path/'stale',decisions=reviews,approve_decisions=False,
        extra=[feature(box(.2,45.2,.3,45.3),'wetland',**{'class':'wetland_bog'})])
    assert not stale.decisions[0]['active']
    assert stale.audit([SPOT])['status']=='blocked'
    before=stale.entries['reviews'].read_bytes()
    reviewed_removal=dict(schema=1,policy=POLICY,decisions=[],
        reviewed_by='Synthetic reviewer',rationale='Explicitly reviewed withdrawal of the hold',
        supersedes=stale.decisions_revision)
    output=tmp_path/'reviewed-removal'
    revise_reviews(stale.path,reviewed_removal,output,regions={'france':REGION})
    updated=GeographicDataset(output/'manifest.json',regions={'france':REGION})
    assert updated.revision!=stale.revision
    assert updated.decisions_revision!=stale.decisions_revision
    assert updated.manifest['files']['france']==stale.manifest['files']['france']
    report=updated.audit([SPOT])
    assert report['status']=='passed'
    finding=report['findings'][0]
    assert finding['status']=='no_additional_objection'
    assert finding['reasons']==[]
    assert finding['decisions']==finding['stale_decisions']==[]
    assert stale.entries['reviews'].read_bytes()==before
    assert stale.audit([SPOT])['status']=='blocked'


def test_stale_water_decision_is_a_blocking_qualification_error(tmp_path):
    pond=box(-1,44,1,46)
    with pytest.raises(ValueError,match='Stale corroborated-water'):
        make_dataset(tmp_path,extra=[feature(pond,'water')],decisions=[decision(pond)],approve_decisions=False)


def test_cannot_promote_source_land_to_water_or_omit_license_assessment(tmp_path):
    with pytest.raises(ValueError,match='outside source water'):
        make_dataset(tmp_path,decisions=[decision(box(-1,44,1,46))])


def test_integrity_schema_and_offline_reuse(tmp_path):
    dataset=make_dataset(tmp_path)
    with patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network')):
        from tests.geographic_helpers import REGION
        reloaded=GeographicDataset(dataset.path,regions={'france':REGION})
        assert reloaded.audit([SPOT])['status']=='passed'
    dataset.entries['france'].write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='integrity failure'):
        GeographicDataset(dataset.path)


def test_holes_and_classifications_affect_local_context_not_feature_numbers():
    extent=box(-1,44,1,46)
    one=[feature(extent,'water',record=1)]
    two=[feature(extent,'water',record=999)]
    assert spatial_context(one,extent)==spatial_context(two,extent)
    assert spatial_context(one,extent)!=spatial_context([feature(extent,'wetland')],extent)


def test_shoreline_acceptance_does_not_waive_unreviewed_inland_water(tmp_path):
    extent=box(-1,44,1,46)
    evidence=[feature(box(*REGION['bbox']),'inland_coverage'),feature(extent,'water')]
    dataset=make_dataset(tmp_path,evidence=evidence,decisions=[decision(
        extent,'accept_uncertainty',reasons=['marine_disagreement'])])
    finding=dataset.audit([SPOT])['findings'][0]
    assert finding['status']=='hold'
    assert finding['accepted_reasons']==['marine_disagreement']
    assert finding['unreviewed_reasons']==['inland_water_disagreement']


def test_each_disagreement_requires_its_own_evidence_bound_acceptance(tmp_path):
    extent=box(-1,44,1,46)
    evidence=[feature(box(*REGION['bbox']),'inland_coverage'),feature(extent,'water')]
    review=decision(extent,'accept_uncertainty',reasons=['marine_disagreement','inland_water_disagreement'])
    dataset=make_dataset(tmp_path,evidence=evidence,decisions=[review])
    assert dataset.audit([SPOT])['findings'][0]['status']=='accepted_uncertainty'
    # Even an active parent decision cannot use a stale category fingerprint.
    dataset.decisions[0]['acceptances'][1]['context_sha256']='a'*64
    assert dataset.audit([SPOT])['findings'][0]['unreviewed_reasons']==['inland_water_disagreement']


@pytest.mark.parametrize('value',[1,2,3,255])
def test_new_wbm_water_or_nodata_cannot_be_waived_by_shoreline_review(tmp_path,value):
    extent=box(-1,44,1,46)
    evidence=[feature(box(*REGION['bbox']),'inland_coverage')]
    dataset=make_dataset(tmp_path/'old',evidence=evidence,
        decisions=[decision(extent,'accept_uncertainty',reasons=['marine_disagreement'])])
    assert dataset.audit([SPOT])['status']=='passed'
    reviews=json.loads(dataset.entries['reviews'].read_text())['decisions']
    changed=make_dataset(tmp_path/'new',evidence=evidence,decisions=reviews,
                         approve_decisions=False,wbm_value=value)
    assert changed.decisions[0]['active']  # same cartographic context; different WBM source
    finding=changed.audit([SPOT])['findings'][0]
    assert finding['status']=='hold'
    assert finding['wbm_class']==(-1 if value==255 else value)
    assert finding['unreviewed_reasons']==['wbm_nonland_disagreement']


def test_new_local_water_geometry_invalidates_the_reviewed_context(tmp_path):
    extent=box(-1,44,1,46)
    evidence=[feature(box(*REGION['bbox']),'marine_land'),feature(box(*REGION['bbox']),'inland_coverage'),
              feature(box(-.5,44.5,.5,45.5),'water')]
    old=make_dataset(tmp_path/'old',evidence=evidence,decisions=[decision(extent,'accept_uncertainty')])
    reviews=json.loads(old.entries['reviews'].read_text())['decisions']
    new=make_dataset(tmp_path/'new',evidence=[*evidence,feature(box(-.2,44.8,.2,45.2),'water')],
                     decisions=reviews,approve_decisions=False)
    finding=new.audit([SPOT])['findings'][0]
    assert finding['status']=='hold' and finding['stale_decisions']==['reviewed-pond']


def test_unscoped_acceptance_and_nonwaivable_categories_are_invalid(tmp_path):
    extent=box(-1,44,1,46)
    review=decision(extent,'accept_uncertainty');review.pop('acceptances')
    with pytest.raises(ValueError,match='Unscoped'):
        make_dataset(tmp_path/'unscoped',decisions=[review])
    with pytest.raises(ValueError,match='acceptance category'):
        make_dataset(tmp_path/'wbm',decisions=[decision(extent,'accept_uncertainty',reasons=['wbm_nonland_disagreement'])])


@pytest.mark.parametrize('terrain',[{}, {'france':'unregistered-file'}])
def test_missing_or_unverified_terrain_reference_blocks_audit(tmp_path,terrain):
    dataset=make_dataset(tmp_path)
    manifest=json.loads(dataset.path.read_text());manifest['terrain']=terrain
    dataset.path.write_text(json.dumps(manifest))
    dataset=GeographicDataset(dataset.path,regions={'france':REGION})
    with pytest.raises(ValueError,match='Missing verified WBM terrain'):
        dataset.audit([SPOT])


@pytest.mark.parametrize('values',[None,[],[None],[8]])
def test_missing_or_invalid_samples_never_default_to_land(tmp_path,values):
    dataset=make_dataset(tmp_path)
    with patch('src.elevation.ElevationSampler.wbm_classes',return_value=values):
        with pytest.raises(ValueError,match='WBM audit sample'):
            dataset.audit([SPOT])


@pytest.mark.parametrize('failure',['missing','corrupt','unreadable'])
def test_wbm_technical_failures_block_approval(tmp_path,failure):
    dataset=make_dataset(tmp_path)
    if failure=='missing':
        dataset.entries['wbm:france'].unlink()
    elif failure=='corrupt':
        dataset.entries['wbm:france'].write_bytes(b'corrupt')
    else:
        with patch('src.elevation.ElevationSampler.wbm_classes',side_effect=OSError('Raster read failed')):
            with pytest.raises(OSError,match='Raster read failed'):dataset.audit([SPOT])
        return
    with pytest.raises(ValueError,match='(Missing|Corrupt) elevation wbm'):
        dataset.audit([SPOT])


def test_each_audited_region_needs_its_own_wbm_reference(tmp_path):
    registry={'france':REGION,'uk_ireland':dict(bbox=[-11,49,2,55],osm_country_code=['GB'])}
    dataset=make_dataset(tmp_path,regions=registry)
    dataset.manifest['terrain'].pop('uk_ireland')
    with pytest.raises(ValueError,match='Missing verified WBM terrain reference: uk_ireland'):
        dataset.audit([SPOT,dict(SPOT,id='uk',country='GB',lat=52)])


def test_wbm_manifest_for_another_region_cannot_supply_samples(tmp_path):
    dataset=make_dataset(tmp_path)
    manifest=dataset.entries['terrain:france']
    contents=json.loads(manifest.read_text());contents['region']='canaries'
    manifest.write_text(json.dumps(contents))
    with pytest.raises(ValueError,match='WBM terrain region mismatch'):
        dataset.audit([SPOT])


@pytest.mark.parametrize('changed', [{'source':'independent-provider'}, {'id':'another-water-feature'}])
def test_new_source_or_feature_with_identical_geometry_does_not_inherit_acceptance(tmp_path,changed):
    extent=box(-1,44,1,46)
    base=[feature(box(*REGION['bbox']),'marine_land'),feature(box(*REGION['bbox']),'inland_coverage')]
    water=feature(extent,'water',source='reviewed-provider',id='reviewed-river',record=1)
    old=make_dataset(tmp_path/'old',evidence=[*base,water],decisions=[decision(extent,'accept_uncertainty')])
    assert old.audit([SPOT])['status']=='passed'
    reviews=json.loads(old.entries['reviews'].read_text())['decisions']
    new_water=dict(water,properties=dict(water['properties'],**changed))
    new=make_dataset(tmp_path/'new',evidence=[*base,new_water],decisions=reviews,approve_decisions=False)
    assert new.decisions[0]['active']  # generic geometry did not change
    finding=new.audit([SPOT])['findings'][0]
    assert finding['status']=='hold'
    assert finding['unreviewed_reasons']==['inland_water_disagreement']


def test_shapefile_renumbering_does_not_invalidate_scoped_review(tmp_path):
    extent=box(-1,44,1,46)
    base=[feature(box(*REGION['bbox']),'marine_land'),feature(box(*REGION['bbox']),'inland_coverage')]
    water=feature(extent,'water',source='reviewed-provider',id='stable-feature',record=1)
    old=make_dataset(tmp_path/'old',evidence=[*base,water],decisions=[decision(extent,'accept_uncertainty')])
    reviews=json.loads(old.entries['reviews'].read_text())['decisions']
    new_water=dict(water,properties=dict(water['properties'],record=999))
    new=make_dataset(tmp_path/'new',evidence=[*base,new_water],decisions=reviews,approve_decisions=False)
    assert new.audit([SPOT])['status']=='passed'


def test_legacy_schema_cannot_encode_new_scoped_acceptances():
    document=dict(schema=1,policy=POLICY,decisions=[decision(box(-1,44,1,46),'accept_uncertainty')])
    with pytest.raises(ValueError,match='require review schema 2'):
        validate_review_document(document)
    document['schema']=2
    validate_review_document(document)
