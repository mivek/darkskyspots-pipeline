import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from shapely.geometry import box

from src.geographic_publication import (approve_tree, verify_approval, tree_spots,
    staged_tree, guarded_apply, payload_files, GeographicApprovalError)
from src.publish import commit_and_push
from tests.geographic_helpers import SPOT, make_e3_dataset as make_dataset, write_spots, feature, decision


def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.PIPE).decode().strip()


def repository(path):
    path.mkdir();git(path,'init','-q');git(path,'config','user.name','Test')
    git(path,'config','user.email','test@example.invalid')
    git(path,'config','commit.gpgsign','false')
    write_spots(path)
    (path/'clusters').mkdir();(path/'clusters/index.json').write_text('{"generated":"old"}')
    git(path,'add','.');git(path,'commit','-qm','test: initial')
    return path


def test_actual_tree_is_identity_and_worktree_is_not_audit_source(tmp_path):
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo')
    report=approve_tree(repo,dataset=dataset)
    assert git(repo,'cat-file','-t',report['tree_oid'])=='tree'
    assert report['tree_oid']==git(repo,'write-tree')
    write_spots(repo,[dict(SPOT,lon=100)])  # not staged, never audited/published
    assert tree_spots(repo,report['tree_oid'])==[SPOT]
    verify_approval(repo,report,dataset)
    git(repo,'add','spots')
    with pytest.raises(ValueError,match='tree changed after'):
        verify_approval(repo,report,dataset)


def test_preserved_final_water_is_checked_not_just_incoming_region(tmp_path):
    repo=repository(tmp_path/'repo');pond=box(-1,44,1,46)
    dataset=make_dataset(tmp_path/'geo',extra=[feature(pond,'water')])
    with pytest.raises(GeographicApprovalError) as error:
        approve_tree(repo,dataset=dataset,report_dir=tmp_path/'reports')
    assert error.value.report['final_spot_count']==1
    write_spots(repo,[]);git(repo,'add','spots')
    assert approve_tree(repo,dataset=dataset)['status']=='passed'


def test_spot_license_assessment_separate_from_geometry_distribution(tmp_path):
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo',assessed=False)
    with pytest.raises(GeographicApprovalError) as error:
        approve_tree(repo,dataset=dataset)
    assert error.value.report['counts']['licensing_assessment_pending']==1
    assert error.value.report['geometry_distribution_assessment']=='ODbL-1.0'


def test_index_change_after_approval_prevents_commit_and_push(tmp_path,monkeypatch):
    import src.geographic_publication as module
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo')
    original=git(repo,'rev-parse','HEAD');approve=module.approve_tree
    (repo/'README.md').write_text('initial change')
    def change_after_approval(*args,**kwargs):
        report=approve(*args,**kwargs)
        (repo/'README.md').write_text('changed after approval');git(repo,'add','README.md')
        return report
    monkeypatch.setattr(module,'approve_tree',change_after_approval)
    with pytest.raises(ValueError,match='tree changed after'):
        commit_and_push(str(repo),'test: mutation',geographic_dataset=dataset,
                        geographic_report_dir=tmp_path/'reports')
    assert git(repo,'rev-parse','HEAD')==original


def test_commit_hook_change_prevents_push(tmp_path):
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo')
    hook=repo/'.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nprintf altered > README.md\ngit add README.md\n')
    hook.chmod(0o755)
    (repo/'README.md').write_text('approved version')
    with patch('src.publish.subprocess.run',wraps=subprocess.run) as commands:
        with pytest.raises(ValueError,match='tree changed after'):
            commit_and_push(str(repo),'test: hook mutation',geographic_dataset=dataset,
                            geographic_report_dir=tmp_path/'reports')
    assert not any(call.args[0][:2]==['git','push'] for call in commands.call_args_list)


def test_cluster_only_publication_cannot_bypass_gate(tmp_path):
    repo=repository(tmp_path/'repo');pond=box(-1,44,1,46)
    dataset=make_dataset(tmp_path/'geo',extra=[feature(pond,'water')])
    old=git(repo,'rev-parse','HEAD')
    (repo/'clusters/index.json').write_text('{"generated":"new"}')
    with pytest.raises(GeographicApprovalError):
        commit_and_push(str(repo),'test: clusters',geographic_dataset=dataset,
                        geographic_report_dir=tmp_path/'reports')
    assert git(repo,'rev-parse','HEAD')==old


def test_migration_validation_failure_leaves_destination_bytes_unchanged(tmp_path):
    repo=repository(tmp_path/'repo');pond=box(-1,44,1,46)
    dataset=make_dataset(tmp_path/'geo',extra=[feature(pond,'water')])
    before=payload_files(repo);head=git(repo,'rev-parse','HEAD')
    def migration(stage):
        write_spots(stage,[dict(SPOT,altitude=-12)])
        (stage/'clusters/index.json').write_text('{"generated":"new"}')
        return {'changed_tiles':1}
    with pytest.raises(GeographicApprovalError):
        guarded_apply(repo,migration,dataset=dataset,report_dir=tmp_path/'reports')
    assert payload_files(repo)==before and git(repo,'rev-parse','HEAD')==head
    assert git(repo,'status','--porcelain')==''


def test_approved_local_migration_applies_spots_and_clusters(tmp_path):
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo')
    head=git(repo,'rev-parse','HEAD')
    def migration(stage):
        assert stage!=repo
        write_spots(stage,[dict(SPOT,altitude=-12)])
        (stage/'clusters/index.json').write_text('{"generated":"new"}')
        return {'changed_tiles':1}
    result=guarded_apply(repo,migration,dataset=dataset)
    assert json.loads((repo/'spots/N045E000.json').read_text())['spots'][0]['altitude']==-12
    assert json.loads((repo/'clusters/index.json').read_text())['generated']=='new'
    assert result['geographic_approval']['tree_oid']
    assert git(repo,'rev-parse','HEAD')==head


def test_migration_worktree_change_after_approval_is_not_applied(tmp_path,monkeypatch):
    import src.geographic_publication as module
    repo=repository(tmp_path/'repo');dataset=make_dataset(tmp_path/'geo')
    before=payload_files(repo);approve=module.approve_tree
    def mutated_approval(stage,**kwargs):
        report=approve(stage,**kwargs)
        write_spots(stage,[dict(SPOT,altitude=999)])
        return report
    monkeypatch.setattr(module,'approve_tree',mutated_approval)
    with pytest.raises(ValueError,match='worktree changed after'):
        guarded_apply(repo,lambda stage:(write_spots(stage,[dict(SPOT,altitude=12)]) and {}),dataset=dataset)
    assert payload_files(repo)==before


def test_wbm_water_blocks_migration_without_any_osm_disagreement(tmp_path):
    repo=repository(tmp_path/'repo')
    dataset=make_dataset(tmp_path/'geo',wbm_value=1)
    before=payload_files(repo)
    def migration(stage):
        write_spots(stage,[dict(SPOT,altitude=None,darkness=.9)])
        return {'changed_tiles':1}
    with pytest.raises(GeographicApprovalError) as error:
        guarded_apply(repo,migration,dataset=dataset)
    assert error.value.report['findings'][0]['reasons']==['WBM_water']
    assert payload_files(repo)==before
