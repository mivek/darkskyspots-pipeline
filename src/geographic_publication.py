"""Final-payload approval and transactional local migration staging."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid

from .geographic_data import GeographicDataset, digest, file_digest

DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / 'darksky-geographic-audits'


class GeographicApprovalError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(f'Error: geographic publication blocked: {report["counts"]}')


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def staged_tree(repo):
    """Create an actual immutable Git tree, not a working-tree checksum."""
    return git(repo, 'write-tree').decode().strip()


def implementation_revision():
    root=Path(__file__).resolve().parents[1]
    paths=['src/geographic_data.py','src/geographic_publication.py','src/elevation.py',
           'src/geographic_e3.py','src/geographic_confidence.py','src/geographic_geometry.py',
           'src/geographic_index.py','src/worldcover.py',
           'src/geographic_subdivision.py',
           'src/regions.py','src/geography.py','regions.yaml']
    return digest({p:file_digest(root/p) for p in paths})


def tree_spots(repo, tree):
    # A complete immutable tree includes preserved regions and unstaged HEAD
    # files as represented by the index. Never read spots from the worktree.
    listing = git(repo, 'ls-tree', '--name-only', tree).decode().splitlines()
    if 'spots' not in listing:
        return []
    archive = git(repo, 'archive', tree, 'spots')
    spots = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Non-regular spot file in geographic publication tree')
            if not member.name.endswith('.json'):
                continue
            envelope = json.load(stream.extractfile(member))
            if any(any(k.startswith('_geographic_') for k in p) for p in envelope['spots']):
                raise ValueError('Internal geographic confidence in public publication payload')
            spots.extend(envelope['spots'])
    identifiers = [p['id'] for p in spots]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Duplicate spot identifiers in final geographic publication payload')
    return spots


def approve_tree(repo, *, dataset=None, report_dir=None):
    dataset = dataset if dataset is not None else GeographicDataset()
    dataset.require_e3()
    dataset.verify()
    tree = staged_tree(repo)
    report = dataset.audit(tree_spots(repo, tree))
    report.update(schema=1, tree_oid=tree,
                  attempt_id=uuid.uuid4().hex,implementation_revision=implementation_revision(),
                  identity='Immutable Git tree object produced by git write-tree',
                  geometry_distribution_assessment=dataset.manifest['licensing'].get('geometry_distribution'),
                  spot_distribution_assessment=dataset.manifest['licensing'].get('spot_distribution', 'pending'))
    # Geometry preparation and spot publication are distinct licensing steps.
    if report['spot_distribution_assessment'] != 'assessed':
        report['status'] = 'blocked'
        report['counts']['licensing_assessment_pending'] = 1
    if report_dir is not None:
        directory = Path(report_dir)
        if directory.resolve().is_relative_to(Path(repo).resolve()):
            raise ValueError('Geographic reports must be stored outside the publication repository')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{tree}-{report["attempt_id"]}.json'
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if report['status'] != 'passed':
        raise GeographicApprovalError(report)
    return report


def verify_approval(repo, report, dataset, *, committed=False):
    dataset.require_e3()
    dataset.verify()
    if (report.get('status') != 'passed' or report.get('geographic_revision') != dataset.revision
            or report.get('decisions_revision') != dataset.decisions_revision
            or report.get('implementation_revision') != implementation_revision()):
        raise ValueError('Error: missing or stale geographic approval')
    tree = git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip() if committed else staged_tree(repo)
    if tree != report.get('tree_oid'):
        raise ValueError('Error: publication tree changed after geographic approval')


def payload_files(root):
    root = Path(root)
    result = {}
    for folder in ['spots','clusters']:
        for path in sorted((root/folder).rglob('*')):
            if path.is_symlink():
                raise ValueError('Symlink in geographic migration payload')
            if path.is_file():
                result[str(path.relative_to(root))] = dict(sha256=file_digest(path),
                    executable=bool(path.stat().st_mode & 0o111))
    return result


def immutable_payload(repo, tree):
    roots = [name for name in git(repo, 'ls-tree', '--name-only', tree).decode().splitlines()
             if name in {'spots', 'clusters'}]
    if not roots:
        return {}
    result = {}
    with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', tree, *roots))) as stream:
        for member in stream:
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Non-regular file in geographic migration payload')
            result[member.name] = dict(data=stream.extractfile(member).read(),executable=bool(member.mode & 0o111))
    return result


def guarded_apply(destination, transform, *, dataset=None, report_dir=None):
    """Transform an isolated copy; validate before the first destination write.

    Only spots/clusters are applied. Caller-owned .git, source inputs and other
    untracked files remain untouched. Directories without Git remain supported.
    """
    destination = Path(destination)
    if report_dir is not None and Path(report_dir).resolve().is_relative_to(destination.resolve()):
        raise ValueError('Migration reports must be outside the destination repository')
    before = payload_files(destination)
    with tempfile.TemporaryDirectory(prefix='darksky-geographic-migration-') as temporary:
        stage = Path(temporary) / 'snapshot'
        stage.mkdir()
        for folder in ['spots', 'clusters']:
            if (destination / folder).exists():
                shutil.copytree(destination / folder, stage / folder)
        result = transform(stage)
        after = payload_files(stage)
        if before == after:
            return result
        subprocess.run(['git', 'init', '-q', str(stage)], check=True, capture_output=True)
        git(stage, 'add', '.')
        active = dataset if dataset is not None else GeographicDataset()
        report = approve_tree(stage, dataset=active, report_dir=report_dir)
        verify_approval(stage, report, active)
        if payload_files(stage) != after:
            raise ValueError('Error: migration worktree changed after geographic approval')
        approved_files = immutable_payload(stage, report['tree_oid'])
        if payload_files(destination) != before:
            raise ValueError('Error: migration destination changed while staging')
        # Back up all changed paths before applying, allowing rollback even
        # after a filesystem failure. Validation errors never reach this block.
        changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        backup = Path(temporary) / 'backup'
        for relative in changed:
            original = destination / relative
            if original.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, saved)
        try:
            for relative in changed:
                target = destination / relative
                if relative not in after:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                        pending = Path(stream.name)
                    try:
                        approved=approved_files[relative]
                        pending.write_bytes(approved['data'])
                        mode=stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
                        mode=(mode|0o111) if approved['executable'] else (mode&~0o111)
                        pending.chmod(mode)
                        os.replace(pending, target)
                    finally:
                        pending.unlink(missing_ok=True)
        except BaseException:
            for relative in changed:
                original, saved = destination / relative, backup / relative
                if saved.exists():
                    shutil.copy2(saved, original)
                else:
                    original.unlink(missing_ok=True)
            raise
        return dict(result, geographic_approval=report)
