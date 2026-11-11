"""Small geometry, resource failure, staging and direct-CLI regressions."""
import errno
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box

from src import accessibility_prepare as preparation
from src.accessibility_scope import GeographicSpecification
from tests.unit.test_accessibility import _synthetic_osm
from tools import prepare_accessibility as cli


def disk_full():
    exc = sqlite3.OperationalError('database or disk is full')
    exc.sqlite_errorcode = sqlite3.SQLITE_FULL
    return exc


FAILURES = [MemoryError, lambda: OSError(errno.ENOSPC, 'full'), disk_full,
            lambda: sqlite3.OperationalError('database is locked'),
            lambda: OSError(errno.EIO, 'I/O failure'), lambda: RuntimeError('unexpected')]


@pytest.fixture
def stream(tmp_path):
    pytest.importorskip('osmium')
    source = tmp_path / 'synthetic.osm'
    source.write_text(_synthetic_osm())
    return source


def extract(source, tmp_path):
    return preparation._extract_osm(source, tmp_path / 'index.sqlite', tmp_path / 'nodes',
                                    preparation._domain_geometry())


@pytest.mark.parametrize('operation', ['create_linestring', 'create_multipolygon'])
@pytest.mark.parametrize('failure', FAILURES)
def test_geometry_operations_propagate_operational_errors(stream, tmp_path, monkeypatch, operation, failure):
    import osmium
    factory = osmium.geom.WKBFactory()
    class Factory:
        def __getattr__(self, name):
            if name == operation:
                def fail(*args):
                    raise failure()
                return fail
            return getattr(factory, name)
    monkeypatch.setattr(osmium.geom, 'WKBFactory', Factory)
    with pytest.raises(type(failure())):
        extract(stream, tmp_path)


@pytest.mark.parametrize('feature_class', ['road', 'parking'])
@pytest.mark.parametrize('failure', FAILURES)
def test_storage_errors_are_never_geometry_errors(stream, tmp_path, monkeypatch, feature_class, failure):
    original = preparation._insert_feature
    def insert(*args, **kwargs):
        if kwargs['feature_class'] == feature_class:
            raise failure()
        return original(*args, **kwargs)
    monkeypatch.setattr(preparation, '_insert_feature', insert)
    with pytest.raises(type(failure())):
        extract(stream, tmp_path)


def test_degenerate_lines_remain_recoverable(stream, tmp_path):
    extra = '<way id="401"><nd ref="1"/><nd ref="1"/><tag k="highway" v="track"/></way>'
    stream.write_text(_synthetic_osm().replace('</osm>', extra + '</osm>'))
    stats = extract(stream, tmp_path)
    assert stats['errors_by_class']['track:missing_node_or_geometry'] == 1
    assert stats['class_completeness']['track'] is False
    assert stats['class_completeness']['road'] is True


def test_malformed_area_wkb_remains_recoverable(stream, tmp_path, monkeypatch):
    import osmium
    factory = osmium.geom.WKBFactory()
    class Factory:
        def create_linestring(self, obj):
            return factory.create_linestring(obj)
        def create_multipolygon(self, obj):
            return 'not hex'
    monkeypatch.setattr(osmium.geom, 'WKBFactory', Factory)
    stats = extract(stream, tmp_path)
    assert stats['errors_by_class']['parking:area_assembly_error'] > 0
    assert stats['class_completeness']['parking'] is False


@pytest.fixture
def inputs(stream, tmp_path, monkeypatch):
    polygon = tmp_path / 'coverage.poly'
    polygon.write_text('test\n1\n -18 27\n -15 27\n -15 30\n -18 30\n -18 27\nEND\nEND\n')
    dem = tmp_path / 'dem.json'
    dem.write_text('{}')
    metadata = {name: {'retrieved_at_utc': 'test', 'sha256': preparation.sha256_file(path),
                       'size': path.stat().st_size} for name, path in [('pbf', stream), ('coverage_polygon', polygon)]}
    stream.with_name(stream.name + '.http.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(preparation, '_read_pbf_header', lambda _: {})
    monkeypatch.setattr(preparation, 'validate_accessibility_dem_manifest', lambda *a, **k: {'region': 'canaries'})
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    output = tmp_path / 'output'
    def run(scope='tenerife'):
        return preparation.prepare_accessibility(stream, preparation.SOURCE_URL, scope, dem, output,
                                                  coverage_polygon=polygon)
    return SimpleNamespace(run=run, output=output, source=stream, polygon=polygon, dem=dem)


@pytest.mark.parametrize('failure', FAILURES)
def test_failed_extraction_does_not_publish(inputs, monkeypatch, failure):
    def fail(*args, **kwargs):
        Path(args[1]).write_bytes(b'partial index')
        raise failure()
    monkeypatch.setattr(preparation, '_extract_osm', fail)
    with pytest.raises(type(failure())):
        inputs.run()
    assert not inputs.output.exists()
    assert not list(inputs.output.parent.glob('.output-*'))
    assert not list(inputs.output.parent.glob('tfe-node-cache-*'))


def test_failed_manifest_validation_does_not_publish(inputs, monkeypatch):
    def fail(path):
        assert json.loads(Path(path).read_text())['status'] == 'complete'
        assert not inputs.output.exists()
        raise ValueError('validation rejected')
    monkeypatch.setattr(preparation, 'validate_accessibility_manifest', fail)
    with pytest.raises(ValueError, match='validation rejected'):
        inputs.run()
    assert not inputs.output.exists()
    assert not list(inputs.output.parent.glob('.output-*'))


def test_successful_staging_and_existing_output_protection(inputs):
    manifest = inputs.run()
    assert preparation.validate_accessibility_manifest(inputs.output / 'manifest.json') == manifest
    before = {p.name: p.read_bytes() for p in inputs.output.iterdir()}
    with pytest.raises(FileExistsError):
        inputs.run()
    assert {p.name: p.read_bytes() for p in inputs.output.iterdir()} == before


@pytest.mark.parametrize('coverage', ['complete', 'missing_buffer', 'hole'])
@pytest.mark.parametrize('scope', ['canaries', 'great_britain', 'ireland_northern_ireland'])
def test_direct_preparation_checks_full_domain(inputs, monkeypatch, coverage, scope):
    domain = box(0, 0, 12000, 12000)
    provider = box(-1, -1, 12001, 12001)
    if coverage == 'missing_buffer':
        provider = box(100, 100, 11900, 11900)
    elif coverage == 'hole':
        provider = provider.difference(box(5000, 5000, 5001, 5001))
    spec = SimpleNamespace(source={'source_url': preparation.SOURCE_URL, 'pbf_path': str(inputs.source),
                                  'coverage_polygon_path': str(inputs.polygon)},
                           bbox=(0,0,1,1), preparation_buffer_m=6000, supported_radii_m=(1000,),
                           public_search_radius_m=1000, metric_crs='EPSG:32628', profile='test',
                           dem_manifest=str(inputs.dem),
                           projected_geometries=lambda _: (box(6000,6000,6001,6001), domain, provider, None, None, 0))
    monkeypatch.setattr(GeographicSpecification, 'load', lambda *a: spec)
    def header(_):
        raise RuntimeError('coverage passed before PBF read')
    monkeypatch.setattr(preparation, '_read_pbf_header', header)
    expected = RuntimeError if coverage == 'complete' else ValueError
    message = 'coverage passed' if coverage == 'complete' else 'complete preparation domain including buffer'
    with pytest.raises(expected, match=message):
        inputs.run(scope)
    assert not inputs.output.exists()


@pytest.mark.parametrize('failure,message', [(MemoryError, 'insufficient memory'),
                           (lambda: OSError(errno.ENOSPC, 'full'), 'insufficient disk space'),
                           (disk_full, 'insufficient disk space')])
def test_cli_reports_resource_failures(monkeypatch, capsys, failure, message):
    monkeypatch.setattr('sys.argv', ['prepare_accessibility', '--pbf','unused','--source-url','unused',
                        '--scope','tenerife','--dem-manifest','unused','--output-dir','unused'])
    def fail(*a, **k):
        raise failure()
    monkeypatch.setattr(cli, 'prepare_accessibility', fail)
    assert cli.main() != 0
    captured = capsys.readouterr()
    assert message in captured.err
    assert not captured.out


@pytest.mark.parametrize('failure', [FAILURES[3], FAILURES[4], FAILURES[5]])
def test_cli_preserves_unexpected_diagnostics(monkeypatch, failure):
    monkeypatch.setattr('sys.argv', ['prepare_accessibility', '--pbf','unused','--source-url','unused',
                        '--scope','tenerife','--dem-manifest','unused','--output-dir','unused'])
    exc = failure()
    def fail(*a, **k):
        raise exc
    monkeypatch.setattr(cli, 'prepare_accessibility', fail)
    with pytest.raises(type(exc)) as result:
        cli.main()
    assert result.value is exc


@pytest.mark.parametrize('scope', ['canaries', 'great_britain', 'ireland_northern_ireland'])
def test_retained_approved_provider_coverage(scope):
    spec = GeographicSpecification.load('config/accessibility.yaml', scope)
    path = Path(spec.source['coverage_polygon_path'])
    if not path.is_file():
        pytest.skip('Retained regional provider polygon is not installed')
    polygon = preparation.parse_geofabrik_poly(path)
    _core, domain, coverage, *_rest = spec.projected_geometries(polygon)
    assert coverage.covers(domain)
    assert spec.preparation_buffer_m == 6000
