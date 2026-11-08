import hashlib
import json
from types import SimpleNamespace

import pytest
from pyproj import Transformer
from shapely import to_wkb
from shapely.geometry import Point, box

from src.accessibility import AccessibilityEnricher
from src.accessibility_index import (
    GENERALIZED_POLICIES,
    GENERALIZED_PROFILE,
    AccessibilityIndex,
)
from src.accessibility_prepare import _create_index, sha256_file
from src.accessibility_scope import GeographicSpecification


def _write_generalized_index(root, *, profile=GENERALIZED_PROFILE, mutate=None, coverage_hole=False,
                             add_road=False, road_distance_m=100,
                             dem_manifest_sha256="3" * 64):
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.sqlite"
    forward = Transformer.from_crs("EPSG:4326", "EPSG:32628", always_xy=True)
    x, y = forward.transform(-16.5, 28.3)
    connection = _create_index(index_path)
    if add_road:
        from shapely.geometry import LineString
        from src.accessibility_prepare import _insert_feature
        _insert_feature(
            connection, osm_type="way", osm_id=42, version=1, timestamp=None,
            feature_class="road", inventory_state="active", tags={"highway": "primary"},
            geometry=LineString([(x - 50, y + road_distance_m), (x + 50, y + road_distance_m)]),
        )
    connection.commit()
    connection.close()

    core = box(x - 1000, y - 1000, x + 1000, y + 1000)
    domain = core.buffer(6000)
    coverage = box(x - 20000, y - 20000, x + 20000, y + 20000)
    if coverage_hole:
        coverage = coverage.difference(box(x - 500, y - 500, x + 500, y + 500))
    hashes = {
        "assignment_core_sha256": hashlib.sha256(to_wkb(core)).hexdigest(),
        "prepared_domain_sha256": hashlib.sha256(to_wkb(domain)).hexdigest(),
        "provider_coverage_sha256": hashlib.sha256(to_wkb(coverage)).hexdigest(),
    }
    software = {"python": "test", "osmium": "test", "shapely": "test",
                "pyproj": "test", "proj": "test", "rasterio": "test", "sqlite": "test"}
    identity = {
        "scope": "canaries", "profile": profile,
        "pbf_sha256": "1" * 64, "coverage_polygon_sha256": "2" * 64,
        "dem_manifest_sha256": dem_manifest_sha256, "scope_bbox": [-19, 27, -13, 30],
        **hashes, "preparation_buffer_m": 6000, "radii_m": [500, 1000, 3000, 5000],
        "output_radius_m": 1000, "software": software, "crs": "EPSG:32628",
        "assignment_exclusions": [], **GENERALIZED_POLICIES,
    }
    policies = dict(identity)
    manifest = {
        "schema": 2, "status": "complete", "profile": profile,
        "dataset_id": "can-g1-" + hashlib.sha256(
            (json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        ).hexdigest()[:16],
        "scope": "canaries", "scope_bbox": identity["scope_bbox"],
        "preparation_buffer_m": 6000, "crs": "EPSG:32628", "identity": identity,
        "policies": policies, "software": software,
        "assignment_exclusions": [],
        "assignment_core_wkb_hex": to_wkb(core).hex(),
        "domain_wkb_hex": to_wkb(domain).hex(), "coverage_wkb_hex": to_wkb(coverage).hex(),
        "pbf_provenance": {"pbf_sha256": "1" * 64, "coverage_polygon_sha256": "2" * 64},
        "dem": {"manifest_sha256": dem_manifest_sha256},
        "index": {"path": "index.sqlite", "size": index_path.stat().st_size,
                  "sha256": sha256_file(index_path)},
        "files": {},
        "class_completeness": {"road": True, "track": True, "path": True, "parking": True},
        "errors_by_class": {}, "audit_completeness": {"restriction": True},
    }
    if mutate:
        mutate(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, (-16.5, 28.3), core


def _write_valid_dem_manifest(root, version):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from src.elevation import ElevationSampler

    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    assets = {}
    for name, dtype, values, nodata in (
        ("dem.tif", "int16", [[100, 101], [102, 103]], -9999),
        ("wbm.tif", "uint8", [[0, 0], [0, 0]], 255),
    ):
        path = cache / name
        with rasterio.open(
            path, "w", driver="GTiff", width=2, height=2, count=1,
            dtype=dtype, crs="EPSG:4326", transform=from_origin(-17.02, 28.02, 0.01, 0.01),
            nodata=nodata,
        ) as dataset:
            dataset.write(np.asarray(values, dtype=dtype), 1)
        payload = path.read_bytes()
        assets[name.split(".")[0]] = {
            "path": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        }
    manifest = {
        "schema": 1, "region": "canaries", "bbox": [-17.02, 28.0, -17.0, 28.02],
        "tiles": [{
            "lat": 28, "lon": -17, "product": "GLO-30", "version": version,
            "dem": assets["dem"], "wbm": assets["wbm"],
        }],
    }
    path = root / f"dem-{version}.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with ElevationSampler(path, cache) as sampler:
        assert (28, -17) in sampler.tiles
    return path, cache


def test_generalized_manifest_uses_per_index_crs_and_assignment_core(tmp_path):
    manifest_path, (lon, lat), _core = _write_generalized_index(tmp_path)
    with AccessibilityIndex(manifest_path) as index:
        x, y = index.to_metric.transform(lon, lat)
        inverse_lon, inverse_lat = index.from_metric.transform(x, y)
        assert inverse_lon == pytest.approx(lon, abs=1e-10)
        assert inverse_lat == pytest.approx(lat, abs=1e-10)
        assert index.supports_scope_point(lat, lon)
        assert not index.supports_scope_point(lat, lon - 0.1)
        query = index.query_detailed(lat, lon, 1000)
        assert query["coverageStatus"] == "complete"
        assert query["classes"]["road"]["status"] == "not_found_within_radius"
        edge_lon, edge_lat = index.from_metric.transform(_core.bounds[0], _core.bounds[1])
        assert index.supports_scope_point(edge_lat, edge_lon)
        outside_lon, outside_lat = index.from_metric.transform(_core.bounds[0] - 0.01, _core.bounds[1])
        assert not index.supports_scope_point(outside_lat, outside_lon)


def test_generalized_manifest_binds_policy_and_provenance_dem_hashes_to_identity(tmp_path):
    import hashlib
    from unittest.mock import patch

    alternate_hash = hashlib.sha256(b"different valid DEM manifest").hexdigest()
    mutations = [
        lambda manifest: manifest["policies"].update(dem_manifest_sha256=alternate_hash),
        lambda manifest: manifest["dem"].update(manifest_sha256=alternate_hash),
        lambda manifest: (manifest["policies"].update(dem_manifest_sha256=alternate_hash),
                          manifest["dem"].update(manifest_sha256=alternate_hash)),
    ]
    for case, mutate in enumerate(mutations):
        case_dir = tmp_path / str(case)
        manifest_path, _point, _core = _write_generalized_index(case_dir, mutate=mutate)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["dataset_id"] == "can-g1-" + hashlib.sha256(
            (json.dumps(manifest["identity"], sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        ).hexdigest()[:16]
        with patch("src.accessibility_index._transformer_for_crs") as transformer, \
                patch("src.accessibility_index.sqlite3.connect") as connect:
            with pytest.raises(ValueError, match="source provenance contradicts canonical identity"):
                AccessibilityIndex(manifest_path)
        transformer.assert_not_called()
        connect.assert_not_called()


def test_generalized_runtime_rejects_different_valid_dem_under_unchanged_source_id(tmp_path):
    import hashlib

    expected_dem, cache = _write_valid_dem_manifest(tmp_path / "dems", "2024_1")
    alternate_dem, _cache = _write_valid_dem_manifest(tmp_path / "dems", "2025_1")
    expected_hash = hashlib.sha256(expected_dem.read_bytes()).hexdigest()
    manifest_path, _point, _core = _write_generalized_index(
        tmp_path / "dataset", dem_manifest_sha256=expected_hash,
    )
    original_id = json.loads(manifest_path.read_text(encoding="utf-8"))["dataset_id"]
    from src.elevation import ElevationSampler
    with ElevationSampler(expected_dem, cache):
        pass
    with ElevationSampler(alternate_dem, cache):
        pass
    with AccessibilityIndex(manifest_path) as index:
        assert index.validate_dem_manifest(expected_dem) == expected_hash
        with pytest.raises(ValueError, match="DEM manifest identity mismatch"):
            index.validate_dem_manifest(alternate_dem)
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["dataset_id"] == original_id

    alternate_hash = hashlib.sha256(alternate_dem.read_bytes()).hexdigest()
    tampered_path, _point, _core = _write_generalized_index(
        tmp_path / "tampered",
        dem_manifest_sha256=expected_hash,
        mutate=lambda manifest: manifest["policies"].update(dem_manifest_sha256=alternate_hash),
    )
    assert json.loads(tampered_path.read_text(encoding="utf-8"))["dataset_id"] == original_id
    with pytest.raises(ValueError, match="source provenance contradicts canonical identity"):
        AccessibilityIndex(tampered_path)


def test_incomplete_geographic_coverage_never_claims_absence_even_with_candidate(tmp_path):
    manifest_path, (lon, lat), _core = _write_generalized_index(
        tmp_path, coverage_hole=True, add_road=True,
    )
    with AccessibilityIndex(manifest_path) as index:
        road = index.query_detailed(lat, lon, 1000)["classes"]["road"]
        assert road["status"] == "unknown"
        assert road["reason"] == "incomplete_extract"
        assert road["nearestCandidate"]["osm"] == "way/42"


def test_generalized_query_rejects_unsupported_radius(tmp_path):
    manifest_path, (lon, lat), _core = _write_generalized_index(tmp_path)
    with AccessibilityIndex(manifest_path) as index:
        with pytest.raises(ValueError, match="Unsupported generalized accessibility radius"):
            index.query_detailed(lat, lon, 750)


@pytest.mark.parametrize(
    ("distance_m", "expected_status"),
    [(499.999999, "found"), (500.0, "found"), (500.000001, "not_found_within_radius")],
)
def test_generalized_radius_inclusion_uses_unrounded_distance(tmp_path, distance_m, expected_status):
    manifest_path, (lon, lat), _core = _write_generalized_index(
        tmp_path, add_road=True, road_distance_m=distance_m,
    )
    with AccessibilityIndex(manifest_path) as index:
        road = index.query_detailed(lat, lon, 500)["classes"]["road"]
        assert road["status"] == expected_status
        if expected_status == "found":
            assert road["nearestCandidate"]["distanceMExact"] == pytest.approx(distance_m, abs=1e-9)


@pytest.mark.parametrize("mutate, message", [
    (lambda manifest: manifest.update(profile="unknown-profile"), "Unknown generalized"),
    (lambda manifest: manifest.update(crs="EPSG:32629"), "contradict canonical identity"),
    (lambda manifest: manifest["policies"].update(preparation_revision="old"), "Incompatible generalized"),
])
def test_generalized_manifest_rejects_unknown_or_contradictory_profile(tmp_path, mutate, message):
    manifest_path, _point, _core = _write_generalized_index(tmp_path, mutate=mutate)
    with pytest.raises(ValueError, match=message):
        AccessibilityIndex(manifest_path)


def test_assignment_audit_fails_on_undeclared_gap_and_counts_declared_exclusion():
    index = SimpleNamespace(
        manifest={"assignment_exclusions": ["excluded"]},
        supports_scope_point=lambda lat, lon: lon >= 0,
    )
    enricher = AccessibilityEnricher(index, SimpleNamespace())
    with pytest.raises(ValueError, match="undeclared gaps"):
        enricher.audit_assignments([{"id": "missing", "lat": 1, "lon": -1}])
    assert enricher.audit_assignments([
        {"id": "assigned", "lat": 1, "lon": 1, "accessibility": {}},
        {"id": "excluded", "lat": 1, "lon": -1},
    ], enriched=True) == {"assigned": 1, "enriched": 1, "excluded": 1, "missing": 0}


def test_configured_canary_scope_uses_distortion_checked_assignment_geometry():
    specification = GeographicSpecification.load("config/accessibility.yaml", "canaries")
    core = specification.assignment_core()
    assert core.is_valid and not core.is_empty
    forward = Transformer.from_crs("EPSG:4326", specification.metric_crs, always_xy=True)
    assert specification.validate_distortion(core, forward) <= 0.005
