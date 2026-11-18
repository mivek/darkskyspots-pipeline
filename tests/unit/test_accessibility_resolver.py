import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pyproj import Transformer
from shapely import to_wkb, transform as shapely_transform
from shapely.geometry import box

from src.accessibility_index import G2_POLICIES, AccessibilityIndex
from src.accessibility_prepare import _create_index, sha256_file
from src.accessibility_resolver import AccessibilityResolver


PROFILES = {
    "great_britain": ("great-britain-g2-v1", "EPSG:32630", "g2-gb-"),
    "ireland_northern_ireland": ("ireland-northern-ireland-g2-v1", "EPSG:32629", "g2-ie-ni-"),
}


def _metric_box(crs, bounds):
    project = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return shapely_transform(box(*bounds), project.transform, interleaved=False)


def _write_index(root: Path, scope: str, bounds, *, exclusions=()):
    root.mkdir(parents=True, exist_ok=True)
    profile, crs, prefix = PROFILES[scope]
    index_path = root / "index.sqlite"
    connection = _create_index(index_path)
    connection.commit()
    connection.close()
    core = _metric_box(crs, bounds)
    domain = core.buffer(6000)
    coverage = domain.buffer(20000)
    coverage_path = root / "coverage.poly"
    coverage_path.write_text("retained provider coverage fixture\n", encoding="utf-8")
    lock_path = root / "source-lock.json"
    lock_bytes = json.dumps({
        "schema": 1,
        "scope": scope,
        "polygon_historical_authentication": False,
        "pbf": {"sha256": "1" * 64},
        "coverage_polygon": {"sha256": "2" * 64},
    }, sort_keys=True).encode()
    lock_path.write_bytes(lock_bytes)
    lock_hash = hashlib.sha256(lock_bytes).hexdigest()
    dem_path = root.parent / "dem.json"
    dem_path.write_text("{}", encoding="utf-8")
    dem_hash = sha256_file(dem_path)
    software = {"python": "test", "osmium": "test", "shapely": "test",
                "pyproj": "test", "proj": "test", "rasterio": "test", "sqlite": "test"}
    geometry_hashes = {
        "assignment_core_sha256": hashlib.sha256(to_wkb(core)).hexdigest(),
        "prepared_domain_sha256": hashlib.sha256(to_wkb(domain)).hexdigest(),
        "provider_coverage_sha256": hashlib.sha256(to_wkb(coverage)).hexdigest(),
    }
    identity = {
        "scope": scope, "profile": profile,
        "pbf_sha256": "1" * 64, "coverage_polygon_sha256": "2" * 64,
        "dem_manifest_sha256": dem_hash, "scope_bbox": [-11, 49, 2, 55],
        **geometry_hashes, "preparation_buffer_m": 6000,
        "radii_m": [500, 1000, 3000, 5000], "output_radius_m": 1000,
        "software": software, "crs": crs, "assignment_exclusions": list(exclusions),
        **G2_POLICIES, "source_lock_sha256": lock_hash,
    }
    source_id = prefix + hashlib.sha256(
        (json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    ).hexdigest()[:16]
    files = {
        "coverage_polygon": {"path": coverage_path.name, "size": coverage_path.stat().st_size,
                              "sha256": sha256_file(coverage_path)},
        "source_lock": {"path": lock_path.name, "size": len(lock_bytes), "sha256": lock_hash},
    }
    manifest = {
        "schema": 2, "status": "complete", "scope": scope, "profile": profile,
        "dataset_id": source_id, "scope_bbox": [-11, 49, 2, 55],
        "preparation_buffer_m": 6000, "crs": crs, "identity": identity, "software": software,
        "policies": {**identity}, "assignment_exclusions": list(exclusions),
        "assignment_core_wkb_hex": to_wkb(core).hex(),
        "domain_wkb_hex": to_wkb(domain).hex(), "coverage_wkb_hex": to_wkb(coverage).hex(),
        "pbf_provenance": {"pbf_sha256": "1" * 64, "coverage_polygon_sha256": "2" * 64,
                           "source_lock_sha256": lock_hash},
        "dem": {"manifest_sha256": dem_hash},
        "index": {"path": index_path.name, "size": index_path.stat().st_size,
                  "sha256": sha256_file(index_path)},
        "files": files,
        "class_completeness": {"road": True, "track": True, "path": True, "parking": True},
        "errors_by_class": {}, "audit_completeness": {"restriction": True},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, source_id, dem_path


def _fake_specs(dem_path, exclusions=None):
    exclusions = exclusions or {}

    def load(_path, scope):
        profile, crs, _prefix = PROFILES[scope]
        return SimpleNamespace(
            profile=profile, metric_crs=crs, dem_manifest=str(dem_path),
            public_search_radius_m=1000, preparation_buffer_m=6000,
            supported_radii_m=(500, 1000, 3000, 5000), bbox=(-11, 49, 2, 55),
            assignment_exclusions=tuple(exclusions.get(scope, ())), source={},
        )
    return load


def _make_resolver(tmp_path, *, gb_bounds=(-4.9, 50.0, 2.0, 55.0),
                   ie_bounds=(-11.0, 49.0, -5.1, 55.0), exclusions=None):
    gb_path, gb_id, dem_path = _write_index(tmp_path / "gb", "great_britain", gb_bounds)
    ie_path, ie_id, _ = _write_index(tmp_path / "ie", "ireland_northern_ireland", ie_bounds,
                                     exclusions=(exclusions or {}).get("ireland_northern_ireland", ()))
    sampler = SimpleNamespace(manifest_path=dem_path)
    spec_loader = _fake_specs(dem_path, exclusions)
    patcher = patch("src.accessibility_resolver.GeographicSpecification.load", side_effect=spec_loader)
    patcher.start()
    validation_patcher = patch.object(AccessibilityResolver, "_validate_index", lambda *_args: None)
    validation_patcher.start()
    resolver = AccessibilityResolver(
        {"great_britain": gb_path, "ireland_northern_ireland": ie_path}, sampler,
    )
    return resolver, gb_id, ie_id, (patcher, validation_patcher)


def test_two_indexes_route_by_geography_and_preserve_the_selected_source_id(tmp_path):
    resolver, gb_id, ie_id, patches = _make_resolver(tmp_path)
    try:
        ni = {"id": "ni", "lat": 54.5, "lon": -6.0, "country": "GB"}
        england = {"id": "england", "lat": 52.0, "lon": -2.0, "country": "IE"}
        assert resolver.resolve_scope(ni) == "ireland_northern_ireland"
        assert resolver.resolve_scope(england) == "great_britain"
        assert resolver.enrich_spot(ni)["accessibility"]["source"] == ie_id
        assert resolver.enrich_spot(england)["accessibility"]["source"] == gb_id
        counts = resolver.audit_assignments([ni, england])
        assert counts["assigned"] == 2
        assert counts["by_scope"]["ireland_northern_ireland"]["assigned"] == 1
    finally:
        resolver.close()
        for item in patches:
            item.stop()


def test_resolver_rejects_undeclared_gaps_and_positive_area_overlap(tmp_path):
    resolver, _gb_id, _ie_id, patches = _make_resolver(tmp_path / "gap")
    try:
        with pytest.raises(ValueError, match="undeclared assignment gaps"):
            resolver.audit_assignments([{"id": "outside", "lat": 40.0, "lon": 0.0}])
    finally:
        resolver.close()
        for item in patches:
            item.stop()

    gb_path, _gb_id, dem_path = _write_index(
        tmp_path / "overlap-gb", "great_britain", (-7.0, 50.0, -2.0, 55.0),
    )
    ie_path, _ie_id, _ = _write_index(
        tmp_path / "overlap-ie", "ireland_northern_ireland", (-11.0, 49.0, -5.0, 55.0),
    )
    patcher = patch("src.accessibility_resolver.GeographicSpecification.load",
                    side_effect=_fake_specs(dem_path))
    patcher.start()
    validation_patcher = patch.object(AccessibilityResolver, "_validate_index", lambda *_args: None)
    validation_patcher.start()
    with pytest.raises(ValueError, match="overlap"):
        AccessibilityResolver({"great_britain": gb_path, "ireland_northern_ireland": ie_path},
                              SimpleNamespace(manifest_path=dem_path))
    validation_patcher.stop()
    patcher.stop()


def test_shared_boundary_tie_uses_ireland_first_priority(tmp_path):
    resolver, _gb_id, ie_id, patches = _make_resolver(tmp_path)
    try:
        spot = {"id": "boundary", "lat": 54.5, "lon": -6.0, "country": "GB"}
        with patch.object(resolver.indices["great_britain"], "supports_scope_point", return_value=True), \
                patch.object(resolver.indices["ireland_northern_ireland"], "supports_scope_point", return_value=True):
            assert resolver.resolve_scope(spot) == "ireland_northern_ireland"
            assert resolver.enrich_spot(spot)["accessibility"]["source"] == ie_id
    finally:
        resolver.close()
        for item in patches:
            item.stop()


def test_context_manager_closes_indexes_after_processing_exception(tmp_path):
    resolver, _gb_id, _ie_id, patches = _make_resolver(tmp_path)
    index_connections = [index.connection for index in resolver.indices.values()]
    try:
        with pytest.raises(RuntimeError, match="processing failure"):
            with resolver:
                raise RuntimeError("processing failure")
        assert all(connection is not None for connection in index_connections)
        with pytest.raises(Exception):
            index_connections[0].execute("SELECT 1")
    finally:
        resolver.close()
        for item in patches:
            item.stop()


@pytest.mark.parametrize("scope", list(PROFILES))
def test_g2_profile_registry_binds_scope_and_expected_crs(tmp_path, scope):
    bounds = (-4.9, 50.0, 2.0, 55.0) if scope == "great_britain" else (-11.0, 49.0, -5.1, 55.0)
    path, _source_id, _dem_path = _write_index(tmp_path / scope, scope, bounds)
    with AccessibilityIndex(path) as index:
        assert index.manifest["scope"] == scope
        assert index.crs == PROFILES[scope][1]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["crs"] = "EPSG:32628"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="requires EPSG"):
        AccessibilityIndex(path)


def test_resolver_honors_explicit_exclusions_and_requires_both_scopes(tmp_path):
    exclusions = {"ireland_northern_ireland": ["reviewed-exclusion"]}
    resolver, _gb_id, _ie_id, patches = _make_resolver(tmp_path, exclusions=exclusions)
    try:
        spot = {"id": "reviewed-exclusion", "lat": 54.5, "lon": -6.0, "country": "GB"}
        assert resolver.audit_assignments([spot])["excluded"] == 1
        assert "accessibility" not in resolver.enrich_spot(spot)
    finally:
        resolver.close()
        for item in patches:
            item.stop()

    with patch("src.accessibility_resolver.AccessibilityIndex") as index:
        with pytest.raises(ValueError, match="Both G2 scopes"):
            AccessibilityResolver({"great_britain": "gb.json"}, object())
        index.assert_not_called()


def test_runtime_rejects_an_exclusion_not_declared_in_profile(tmp_path):
    path, _source_id, dem_path = _write_index(
        tmp_path / "unexpected", "great_britain", (-4.9, 50.0, 2.0, 55.0),
        exclusions=("unexpected-id",),
    )
    profile, crs, _prefix = PROFILES["great_britain"]
    specification = SimpleNamespace(
        profile=profile, metric_crs=crs, dem_manifest=str(dem_path),
        public_search_radius_m=1000, preparation_buffer_m=6000,
        supported_radii_m=(500, 1000, 3000, 5000), bbox=(-11, 49, 2, 55),
        assignment_exclusions=(), source={},
    )
    with AccessibilityIndex(path) as index, pytest.raises(ValueError, match="assignment_exclusions"):
        AccessibilityResolver._validate_index(AccessibilityResolver.__new__(AccessibilityResolver),
                                              "great_britain", index, specification)


def test_tampered_second_index_closes_first_index(tmp_path):
    gb_path, _gb_id, dem_path = _write_index(
        tmp_path / "gb", "great_britain", (-4.9, 50.0, 2.0, 55.0),
    )
    ie_path, _ie_id, _ = _write_index(
        tmp_path / "ie", "ireland_northern_ireland", (-11.0, 49.0, -5.1, 55.0),
    )
    with (gb_path.parent / "index.sqlite").open("ab") as stream:
        stream.write(b"tamper")
    closed = []
    original_close = AccessibilityIndex.close

    def record_close(index):
        closed.append(index.source_id)
        original_close(index)

    patcher = patch("src.accessibility_resolver.GeographicSpecification.load",
                    side_effect=_fake_specs(dem_path))
    patcher.start()
    validation_patcher = patch.object(AccessibilityResolver, "_validate_index", lambda *_args: None)
    validation_patcher.start()
    close_patcher = patch.object(AccessibilityIndex, "close", record_close)
    close_patcher.start()
    try:
        with pytest.raises(ValueError, match="index size mismatch"):
            AccessibilityResolver({"great_britain": gb_path,
                                   "ireland_northern_ireland": ie_path},
                                  SimpleNamespace(manifest_path=dem_path))
        assert len(closed) == 1
        assert closed[0].startswith("g2-ie-ni-")
    finally:
        close_patcher.stop()
        validation_patcher.stop()
        patcher.stop()
