import json

import pytest

from tools.audit_accessibility import _validate_local_tiles, add_accessibility_to_envelope


def test_serialized_tiles_and_optional_references(tmp_path):
    payload = {"schema": 1, "source": "test", "radiusM": 1000}
    payload.update({key: {"status": "not_found_within_radius"} for key in ("road", "track", "path", "parking")})
    original = {"version": 1, "spots": [{"id": "a", "lat": 1}]}
    tiles = {"a.json": original, "b.json": {"spots": [{"id": "b"}]}}
    enriched = {"a": payload}
    (tmp_path / "tiles").mkdir()
    updated, _ = add_accessibility_to_envelope(original, enriched)
    path = tmp_path / "tiles" / "a.json"
    path.write_text(json.dumps(updated))
    assert _validate_local_tiles(tmp_path, tiles, enriched, [], {}) == {}
    updated["spots"][0]["lat"] = True
    path.write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="preservation"):
        _validate_local_tiles(tmp_path, tiles, enriched, [], {})


def test_missing_reference_rejected(tmp_path):
    from tools.audit_accessibility import _run_multi_scope_audit
    with pytest.raises(ValueError, match="Missing reference evidence"):
        _run_multi_scope_audit(spots_dir=tmp_path / "baseline" / "spots", clusters_dir=tmp_path / "baseline" / "clusters",
            accessibility_datasets={"great_britain": tmp_path / "dataset" / "manifest"},
            baseline_inventory=tmp_path / "inventory", baseline_inventory_sha256="x",
            dem_manifest=None, dem_cache_dir=tmp_path / "dem", radius_m=1000,
            output_dir=tmp_path / "output", geographic_config=tmp_path / "config",
            tiles_only=True, reference_audits={"great_britain": tmp_path / "reference"})


def test_assignment_summary_uses_validated_results():
    from tools.audit_accessibility import _enrichment_assignment_summary
    assignment = {"assigned": 3, "enriched": 0, "by_scope": {
        "great_britain": {"assigned": 2, "enriched": 0},
        "ireland_northern_ireland": {"assigned": 1, "enriched": 0}}}
    result = _enrichment_assignment_summary(assignment, {"great_britain": 2, "ireland_northern_ireland": 1}, {"a": {}, "b": {}, "c": {}})
    assert result["enriched"] == 3
    assert result["by_scope"]["great_britain"]["enriched"] == 2
    assert result["by_scope"]["ireland_northern_ireland"]["enriched"] == 1
    assert assignment["enriched"] == 0


def test_overlap_and_existing_output_rejected(tmp_path):
    from tools.audit_accessibility import _run_multi_scope_audit
    args = dict(spots_dir=tmp_path / "baseline" / "spots", clusters_dir=tmp_path / "baseline" / "clusters",
        accessibility_datasets={"great_britain": tmp_path / "dataset" / "manifest"},
        baseline_inventory=tmp_path / "inventory", baseline_inventory_sha256="x",
        dem_manifest=None, dem_cache_dir=tmp_path / "dem", radius_m=1000,
        geographic_config=tmp_path / "config", tiles_only=True)
    with pytest.raises(ValueError, match="overlaps"):
        _run_multi_scope_audit(**args, output_dir=tmp_path / "dataset" / "out")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        _run_multi_scope_audit(**args, output_dir=output)


def test_reference_mismatch_fails_without_finalization(tmp_path):
    payload = {"schema": 1, "source": "test", "radiusM": 1000}
    payload.update({key: {"status": "not_found_within_radius"} for key in ("road", "track", "path", "parking")})
    tile = {"spots": [{"id": "a"}]}
    (tmp_path / "tiles").mkdir()
    (tmp_path / "tiles" / "a.json").write_text(json.dumps(add_accessibility_to_envelope(tile, {"a": payload})[0]))
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "queries.jsonl").write_text(json.dumps({"spot_id": "a", "query": {"radiusM": 1000, "value": 1}}) + "\n")
    (reference / "actual-endpoints.jsonl").write_text("")
    record = {"spot_id": "a", "scope": "gb", "query": {"radiusM": 1000, "value": 2}}
    with pytest.raises(ValueError, match="differs"):
        _validate_local_tiles(tmp_path, {"a.json": tile}, {"a": payload}, [record], {"gb": reference})
    assert not (tmp_path / "completed").exists()


def test_duplicate_and_missing_spot_ids_rejected(tmp_path):
    payload = {"schema": 1, "source": "test", "radiusM": 1000}
    payload.update({key: {"status": "not_found_within_radius"} for key in ("road", "track", "path", "parking")})
    (tmp_path / "tiles").mkdir()
    tile = {"spots": [{"id": "a"}, {"id": "a"}]}
    (tmp_path / "tiles" / "a.json").write_text(json.dumps(add_accessibility_to_envelope(tile, {"a": payload})[0]))
    with pytest.raises(ValueError, match="duplicate"):
        _validate_local_tiles(tmp_path, {"a.json": tile}, {"a": payload}, [], {})
    tile = {"spots": [{"id": "a"}]}
    (tmp_path / "tiles" / "a.json").write_text(json.dumps(add_accessibility_to_envelope(tile, {"a": payload})[0]))
    with pytest.raises(ValueError, match="IDs differ"):
        _validate_local_tiles(tmp_path, {"a.json": tile}, {"a": payload, "b": payload}, [], {})


def test_failure_retains_staging_and_does_not_finalize(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import tools.audit_accessibility as audit

    root = tmp_path / "baseline"
    spots = root / "spots"
    clusters = root / "clusters"
    spots.mkdir(parents=True)
    clusters.mkdir()
    tile = spots / "tile.json"
    tile.write_text(json.dumps({"spots": [{"id": "a", "country": "GB"}]}))
    files = {"spots/tile.json": {"size": tile.stat().st_size, "sha256": audit.sha256_file(tile), "expected_spot_records": 1}}
    for level in range(1, 7):
        path = clusters / f"L{level}.json"
        path.write_text("[]")
        files[f"clusters/L{level}.json"] = {"size": path.stat().st_size, "sha256": audit.sha256_file(path), "actual_records": 0}
    index = clusters / "index.json"
    index.write_text("{}")
    files["clusters/index.json"] = {"size": index.stat().st_size, "sha256": audit.sha256_file(index)}
    (root / "baseline.json").write_text(json.dumps({"files": files}))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"commit": "test", "files": {}}))
    pin = hashlib.sha256(inventory.read_bytes()).hexdigest()
    monkeypatch.setattr(audit, "validate_approved_g2_baseline", lambda *a: {"inventory_sha256": pin})
    dem = tmp_path / "dem.json"
    monkeypatch.setattr(audit.GeographicSpecification, "load", lambda *a: SimpleNamespace(dem_manifest=str(dem), profile="test"))
    monkeypatch.setattr(audit, "validate_accessibility_dem_manifest", lambda *a, **kw: None)
    class Sampler:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(audit, "ElevationSampler", Sampler)
    def fail(*args, **kwargs):
        raise ValueError("injected query setup failure")
    monkeypatch.setattr(audit, "AccessibilityResolver", fail)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="injected"):
        audit._run_multi_scope_audit(spots_dir=spots, clusters_dir=clusters,
            accessibility_datasets={"great_britain": tmp_path / "gb" / "manifest", "ireland_northern_ireland": tmp_path / "ie" / "manifest"},
            baseline_inventory=inventory, baseline_inventory_sha256=pin, dem_manifest=dem,
            dem_cache_dir=tmp_path / "cache", radius_m=1000, output_dir=output,
            geographic_config=tmp_path / "config", tiles_only=True)
    assert not output.exists()
    stages = list(tmp_path.glob(".out-*"))
    assert len(stages) == 1
    assert "injected query setup failure" in (stages[0] / "failure.txt").read_text()


def test_baseline_root_rejects_output_without_changing_authenticated_package(tmp_path, synthetic_baseline):
    from src.accessibility_baseline import validate_approved_g2_baseline
    from tools.audit_accessibility import _run_multi_scope_audit
    root, inventory, pin, _ = synthetic_baseline
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    validate_approved_g2_baseline(root / 'spots', inventory)
    link = tmp_path / 'baseline-link'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match='overlaps protected input'):
        _run_multi_scope_audit(spots_dir=root / 'spots', clusters_dir=root / 'clusters',
            accessibility_datasets={'great_britain': tmp_path / 'gb' / 'manifest'},
            baseline_inventory=inventory, baseline_inventory_sha256=pin, dem_manifest=None,
            dem_cache_dir=tmp_path / 'cache', radius_m=1000, output_dir=link / 'output',
            geographic_config=tmp_path / 'config', tiles_only=True)
    assert not (root / 'output').exists()
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert not list(root.glob('.output-*'))
    validate_approved_g2_baseline(root / 'spots', inventory)


def test_endpoint_boolean_is_not_numeric_zero(tmp_path):
    payload = {'schema': 1, 'source': 'test', 'radiusM': 1000}
    payload.update({key: {'status': 'not_found_within_radius'} for key in ('road', 'track', 'path', 'parking')})
    payload['road'] = {'status': 'found', 'osm': 'way/1', 'distanceM': 10, 'elevationDeltaM': 0,
                       'access': {'foot': {'state': 'unknown', 'reason': 'missing_tag'}, 'motorcar': {'state': 'unknown', 'reason': 'missing_tag'}}}
    tile = {'spots': [{'id': 'a'}]}
    (tmp_path / 'tiles').mkdir()
    (tmp_path / 'tiles' / 'a.json').write_text(json.dumps(add_accessibility_to_envelope(tile, {'a': payload})[0]))
    reference = tmp_path / 'reference'
    reference.mkdir()
    query = {'radiusM': 1000}
    (reference / 'queries.jsonl').write_text(json.dumps({'spot_id': 'a', 'query': query}) + '\n')
    endpoint = {'spot_id': 'a', 'role': 'selected', 'feature_class': 'road', 'osm': 'way/1', 'elevation_delta_m': 0, 'elevation_delta_reason': None}
    path = reference / 'actual-endpoints.jsonl'
    records = [{'spot_id': 'a', 'scope': 'gb', 'query': query}]
    path.write_text(json.dumps(endpoint) + '\n')
    assert _validate_local_tiles(tmp_path, {'a.json': tile}, {'a': payload}, records, {'gb': reference})['gb']['selected_endpoints_matched'] == 1
    endpoint['elevation_delta_m'] = False
    path.write_text(json.dumps(endpoint) + '\n')
    with pytest.raises(ValueError, match='Selected endpoint evidence differs'):
        _validate_local_tiles(tmp_path, {'a.json': tile}, {'a': payload}, records, {'gb': reference})


def test_tile_only_orchestration_finalizes_without_clusters(tmp_path, monkeypatch, synthetic_baseline):
    from types import SimpleNamespace
    import src.accessibility_prepare as prepare
    import tools.audit_accessibility as audit
    root, inventory, pin, envelopes = synthetic_baseline
    dem = tmp_path / 'dem.json'
    dem.write_text('{}')
    class Spec:
        dem_manifest = str(dem)
        profile = 'test'
        def projected_geometries(self, polygon): return (0.0,)
    monkeypatch.setattr(audit.GeographicSpecification, 'load', lambda *a: Spec())
    monkeypatch.setattr(audit, 'validate_accessibility_dem_manifest', lambda *a, **kw: None)
    monkeypatch.setattr(prepare, 'parse_geofabrik_poly', lambda path: None)
    monkeypatch.setattr(audit, 'write_accessibility_source_record', lambda *a: None)
    def forbidden(*args, **kwargs):
        pytest.fail('cluster processing invoked')
    monkeypatch.setattr(audit, 'aggregate_spot_files', forbidden)
    monkeypatch.setattr(audit, 'add_accessibility_to_clusters', forbidden)
    class Sampler:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(audit, 'ElevationSampler', Sampler)
    class Index:
        source_id = 'test-source'
        crs = 'EPSG:32630'
        root = tmp_path
        manifest = {'files': {'coverage_polygon': {'path': 'poly'}},
                    'class_completeness': {name: True for name in audit.PUBLIC_CLASSES},
                    'audit_completeness': {'restriction': False}}
        def query_detailed(self, lat, lon, radius):
            return {'coverageStatus': 'complete', 'radiusM': radius}
        def public_from_detailed(self, *args, **kwargs):
            return {name: {'status': 'not_found_within_radius'} for name in audit.PUBLIC_CLASSES}
    class Resolver:
        def __init__(self, *args, **kwargs):
            self.indices = {'great_britain': Index(), 'ireland_northern_ireland': Index()}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def audit_assignments(self, spots):
            return {'assigned': len(spots), 'enriched': 0, 'excluded': 0, 'missing': 0,
                    'by_scope': {'great_britain': {'assigned': len(spots), 'enriched': 0}, 'ireland_northern_ireland': {'assigned': 0, 'enriched': 0}}}
        def resolve_scope(self, spot): return 'great_britain'
    monkeypatch.setattr(audit, 'AccessibilityResolver', Resolver)
    datasets = {}
    for scope in ('great_britain', 'ireland_northern_ireland'):
        path = tmp_path / scope / 'manifest.json'
        path.parent.mkdir()
        path.write_text('{}')
        datasets[scope] = path
    output = tmp_path / 'output'
    result = audit._run_multi_scope_audit(spots_dir=root / 'spots', clusters_dir=root / 'clusters',
        accessibility_datasets=datasets, baseline_inventory=inventory, baseline_inventory_sha256=pin,
        dem_manifest=dem, dem_cache_dir=tmp_path / 'cache', radius_m=1000, output_dir=output,
        geographic_config=tmp_path / 'config', tiles_only=True)
    assert sorted(p.name for p in (output / 'tiles').iterdir()) == ['selected.json']
    actual = json.loads((output / 'tiles' / 'selected.json').read_text())
    assert {k: v for k, v in actual.items() if k != 'spots'} == {k: v for k, v in envelopes['selected.json'].items() if k != 'spots'}
    assert [{k: v for k, v in spot.items() if k != 'accessibility'} for spot in actual['spots']] == envelopes['selected.json']['spots']
    for spot in actual['spots']:
        audit.validate_accessibility_payload(spot['accessibility'])
    assert not (output / 'clusters').exists()
    assert not list(tmp_path.glob('.output-*'))
    assert result['assignment']['enriched'] == 2
    assert result['assignment']['by_scope']['great_britain']['enriched'] == 2
    assert result['preservation']['baseline_clusters_authenticated'] is True
    assert result['acceptance']['cluster_structure_preserved'] is None
    assert json.loads((output / 'audit.json').read_text())['assignment'] == result['assignment']
