import json
import hashlib
import sqlite3
import subprocess
import sys

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from src.accessibility import (
    classify_feature,
    resolve_access_evidence,
    validate_accessibility_payload,
)
from src.accessibility_index import (
    SUPPORTED_POLICY_REVISIONS,
    AccessibilityIndex,
    TO_UTM,
    deterministic_nearest,
)


def test_feature_classes_keep_track_and_parking_aisle_distinct():
    assert classify_feature({"highway": "track"}, "way", "LineString") == ("track", "active")
    assert classify_feature(
        {"highway": "service", "service": "parking_aisle"}, "way", "LineString"
    ) == ("road", "active")
    assert classify_feature({"highway": "motorway"}, "way", "LineString") == ("road", "active")


@pytest.mark.parametrize(
    ("tags", "mode", "physical_class", "expected"),
    [
        ({"access": "no", "foot": "designated"}, "foot", "path",
         {"state": "tagged", "key": "foot", "value": "designated"}),
        ({"access": "yes", "vehicle": "no", "motor_vehicle": "yes", "motorcar": "private"},
         "motorcar", "road", {"state": "tagged", "key": "motorcar", "value": "private"}),
        ({"access": "yes", "motor_vehicle": "yes", "motorcar": "unknown-value"},
         "motorcar", "road", {"state": "unknown", "reason": "unsupported_tag"}),
        ({"motorcar": "yes;no"}, "motorcar", "road", {"state": "conflicting"}),
        ({"access": "yes"}, "motorcar", "path", {"state": "unknown", "reason": "ambiguous_mode"}),
        ({}, "foot", "path", {"state": "unknown", "reason": "missing_tag"}),
        ({"access": "no", "foot:conditional": "yes @ (sunrise-sunset)"}, "foot", "path",
         {"state": "conditional", "base": {"state": "tagged", "key": "access", "value": "no"}}),
        ({"foot:forward": "yes", "foot": "designated"}, "foot", "path",
         {"state": "unknown", "reason": "unsupported_tag"}),
    ],
)
def test_mode_specific_access_precedence_and_uncertainty(tags, mode, physical_class, expected):
    assert resolve_access_evidence(tags, mode, physical_class) == expected


@pytest.mark.parametrize(
    "scoped_key",
    ["foot:forward:conditional", "foot:lanes:conditional"],
)
def test_scoped_conditional_access_remains_uncertain_and_auditable(scoped_key):
    from src.accessibility import resolve_access_with_audit

    tags = {"foot": "designated", scoped_key: "no @ (sunset-sunrise)"}
    evidence, audit = resolve_access_with_audit(tags, "foot", "path")
    assert evidence == {
        "state": "conditional",
        "base": {"state": "unknown", "reason": "unsupported_tag"},
    }
    assert audit["conditional"] == {scoped_key: "no @ (sunset-sunrise)"}
    assert audit["unsupportedScoped"] == {scoped_key: "no @ (sunset-sunrise)"}


def test_supported_unconditional_mode_override_remains_affirmative():
    from src.accessibility import resolve_access_with_audit

    evidence, audit = resolve_access_with_audit(
        {"access": "no", "foot": "designated"}, "foot", "path"
    )
    assert evidence == {"state": "tagged", "key": "foot", "value": "designated"}
    assert audit["unsupportedScoped"] == {}


def test_inactive_highways_and_parking_details_are_not_active_candidates():
    assert classify_feature({"highway": "construction"}, "way", "LineString")[1] == "inactive"
    assert classify_feature({"highway": "path", "disused": "yes"}, "way", "LineString")[1] == "inactive"
    assert classify_feature({"amenity": "parking_space"}, "node", "Point") == ("parking_detail", "audit_only")
    assert classify_feature({"type": "restriction", "restriction": "no_left_turn"}, "relation", "Point") == (
        "restriction", "audit_only"
    )


def test_nearest_point_uses_segment_interiors_and_parking_area_holes():
    distance, nearest = deterministic_nearest(Point(5, 2), LineString([(0, 0), (10, 0)]))
    assert distance == pytest.approx(2)
    assert (nearest.x, nearest.y) == pytest.approx((5, 0))

    parking = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        holes=[[(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)]],
    )
    inside_distance, inside_point = deterministic_nearest(Point(2, 2), parking)
    assert inside_distance == 0
    assert (inside_point.x, inside_point.y) == (2, 2)
    hole_distance, hole_point = deterministic_nearest(Point(5, 5), parking)
    assert hole_distance == pytest.approx(1)
    assert (hole_point.x, hole_point.y) == (4, 5)


def test_polygon_nearest_boundary_is_canonical_across_ring_representations():
    point = Point(5, 5)
    rings = [
        [(0, 0), (10, 0), (10, 10), (7, 10), (7, 3), (3, 3), (3, 10), (0, 10), (0, 0)],
        [(7, 10), (7, 3), (3, 3), (3, 10), (0, 10), (0, 0), (10, 0), (10, 10), (7, 10)],
        [(0, 0), (0, 10), (3, 10), (3, 3), (7, 3), (7, 10), (10, 10), (10, 0), (0, 0)],
        [(10, 0), (0, 0), (0, 10), (3, 10), (3, 3), (7, 3), (7, 10), (10, 10), (10, 0)],
    ]
    results = [deterministic_nearest(point, Polygon(ring)) for ring in rings]
    assert all(distance == pytest.approx(2) for distance, _ in results)
    assert {(nearest.x, nearest.y) for _, nearest in results} == {(3.0, 5.0)}


def test_polygon_holes_and_multipolygon_ties_are_deterministic():
    hole_rings = [
        [(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)],
        [(6, 6), (4, 6), (4, 4), (6, 4), (6, 6)],
    ]
    outer = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    hole_points = [
        deterministic_nearest(Point(5, 5), Polygon(outer, [ring]))[1]
        for ring in hole_rings
    ]
    assert {(point.x, point.y) for point in hole_points} == {(4.0, 5.0)}

    left = Polygon([(-3, 0), (-2, 0), (-2, 2), (-3, 2), (-3, 0)])
    right = Polygon([(2, 0), (3, 0), (3, 2), (2, 2), (2, 0)])
    for parts in ([left, right], [right, left]):
        distance, nearest = deterministic_nearest(Point(0, 1), MultiPolygon(parts))
        assert distance == pytest.approx(2)
        assert (nearest.x, nearest.y) == (-2.0, 1.0)


def _synthetic_osm() -> str:
    nodes = [
        (1, 28.2500, -16.6500), (2, 28.2500, -16.6490),
        (3, 28.2510, -16.6500), (4, 28.2510, -16.6490),
        (5, 28.2520, -16.6500), (6, 28.2520, -16.6490),
        (7, 28.2530, -16.6490), (8, 28.2530, -16.6500),
        (9, 28.2540, -16.6500),
    ]
    node_xml = "".join(
        f'<node id="{i}" lat="{lat}" lon="{lon}" version="1" timestamp="2024-05-06T07:08:09Z" />'
        for i, lat, lon in nodes
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <osm version="0.6" generator="synthetic-accessibility-test">
      {node_xml}
      <way id="100" version="1" timestamp="2024-05-06T07:08:09Z"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="motorcar" v="yes"/></way>
      <way id="101" version="1"><nd ref="3"/><nd ref="4"/><tag k="highway" v="path"/><tag k="access" v="no"/><tag k="foot" v="designated"/></way>
      <way id="102" version="1"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="amenity" v="parking"/></way>
      <way id="103" version="1"><nd ref="1"/><nd ref="9"/></way>
      <way id="104" version="1"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
      <relation id="200" version="1">
        <member type="way" ref="103" role="from"/><member type="node" ref="9" role="via"/>
        <member type="way" ref="100" role="to"/><tag k="type" v="restriction"/>
        <tag k="restriction" v="no_left_turn"/>
      </relation>
      <relation id="300" version="1">
        <member type="way" ref="104" role="outer"/><tag k="type" v="multipolygon"/>
        <tag k="amenity" v="parking"/>
      </relation>
    </osm>'''


def test_synthetic_osm_streams_lines_areas_access_and_restrictions(tmp_path, monkeypatch):
    pytest.importorskip("osmium")
    from src.accessibility_prepare import _domain_geometry, _extract_osm

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    source = tmp_path / "synthetic.osm"
    source.write_text(_synthetic_osm(), encoding="utf-8")
    database = tmp_path / "index.sqlite"
    node_cache = tmp_path / "nodes.cache"
    node_cache.write_bytes(b"")
    stats = _extract_osm(source, database, node_cache, _domain_geometry())

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT osm_type,osm_id,feature_class,inventory_state,tags_json,access_json,quality_json,timestamp "
            "FROM features ORDER BY feature_class,osm_type,osm_id"
        ).fetchall()
        lookup = {(row[0], row[1]): row for row in rows}
        assert ("way", "100") in lookup
        assert ("way", "101") in lookup
        assert ("way", "102") in lookup
        assert ("relation", "200") in lookup
        assert ("relation", "300") in lookup
        assert lookup[("way", "100")][7] == "2024-05-06T07:08:09Z"
        path_access = json.loads(lookup[("way", "101")][5])
        assert path_access["foot"]["value"] == "designated"
        assert path_access["motorcar"] == {"state": "tagged", "key": "access", "value": "no"}
        assert stats["class_completeness"]["road"] is True
        assert stats["class_completeness"]["path"] is True
        assert stats["class_completeness"]["parking"] is True
        assert all("user" not in column.lower() and "changeset" not in column.lower()
                   for column in (row[1] for row in connection.execute("PRAGMA table_info(features)")))
        assert connection.execute("SELECT count(*) FROM feature_rtree").fetchone()[0] == len(rows)
    finally:
        connection.close()


def test_missing_way_nodes_and_unassembled_parking_relation_mark_search_incomplete(tmp_path, monkeypatch):
    pytest.importorskip("osmium")
    from src.accessibility_prepare import _domain_geometry, _extract_osm

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    source = tmp_path / "incomplete.osm"
    broken = _synthetic_osm().replace(
        "<member type=\"way\" ref=\"104\" role=\"outer\"/>",
        "<member type=\"way\" ref=\"999\" role=\"outer\"/>",
    ).replace(
        "</osm>",
        "<way id=\"400\" version=\"1\"><nd ref=\"9999\"/><nd ref=\"1\"/>"
        "<tag k=\"highway\" v=\"track\"/></way></osm>",
    )
    source.write_text(broken, encoding="utf-8")
    node_cache = tmp_path / "nodes.cache"
    node_cache.write_bytes(b"")
    stats = _extract_osm(source, tmp_path / "index.sqlite", node_cache, _domain_geometry())

    assert stats["class_completeness"]["track"] is False
    assert stats["class_completeness"]["parking"] is False
    assert stats["errors_by_class"]["track:missing_node_or_geometry"] >= 1
    assert stats["errors_by_class"]["parking:incomplete_multipolygon"] == 1


def test_object_timestamp_preserves_fractional_utc_precision():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.accessibility_prepare import _object_timestamp

    timestamp = datetime(2024, 5, 6, 8, 8, 9, 123456, tzinfo=timezone.utc)
    assert _object_timestamp(SimpleNamespace(timestamp=timestamp)) == "2024-05-06T08:08:09.123456Z"


def test_runtime_index_import_does_not_import_preparation_dependency():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import src.accessibility_index; import src.accessibility_prepare; assert 'osmium' not in sys.modules"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def _payload():
    return {
        "schema": 1,
        "source": "tfe-f021a-example",
        "radiusM": 1000,
        "road": {"status": "not_found_within_radius"},
        "track": {"status": "unknown", "reason": "incomplete_extract"},
        "path": {
            "status": "found", "osm": "way/123", "distanceM": 10,
            "elevationDeltaM": None, "elevationUnknownReason": "water_ocean",
            "access": {
                "foot": {"state": "tagged", "key": "foot", "value": "designated"},
                "motorcar": {"state": "unknown", "reason": "missing_tag"},
            },
        },
        "parking": {"status": "not_found_within_radius"},
    }


def test_public_accessibility_contract_and_no_raw_audit_leak():
    payload = _payload()
    assert validate_accessibility_payload(payload) is payload
    payload["nearbyRestrictions"] = []
    with pytest.raises(ValueError, match="unsupported or missing fields"):
        validate_accessibility_payload(payload)


def test_public_accessibility_rejects_null_and_unsupported_schema():
    with pytest.raises(ValueError, match="must be an object"):
        validate_accessibility_payload(None)
    payload = _payload()
    payload["schema"] = 2
    with pytest.raises(ValueError, match="unsupported accessibility schema"):
        validate_accessibility_payload(payload)


def test_public_accessibility_enforces_the_four_kib_ceiling():
    payload = _payload()
    payload["source"] = "x" * 5000
    with pytest.raises(ValueError, match="exceeds 4 KiB"):
        validate_accessibility_payload(payload)


def test_public_accessibility_rejects_non_contract_access_keys():
    payload = _payload()
    payload["path"]["access"]["foot"]["key"] = "user:email"
    with pytest.raises(ValueError, match="invalid tagged path access value"):
        validate_accessibility_payload(payload)


def test_source_record_is_written_under_the_local_package(tmp_path):
    from src.accessibility_prepare import write_accessibility_source_record

    manifest = {
        "dataset_id": "tfe-f021a-source",
        "pbf_provenance": {
            "provider": "Geofabrik", "source_url": "https://example.test/source.pbf",
            "pbf_size": 10, "pbf_sha256": "a" * 64,
            "retrieved_at_utc": "2026-11-08T00:00:00Z", "http_metadata": {},
            "pbf_header": {}, "coverage_polygon_url": "https://example.test/scope.poly",
            "coverage_polygon_size": 1, "coverage_polygon_sha256": "b" * 64,
            "coverage_polygon_http_metadata": {},
        },
        "dem": {}, "software": {}, "policies": {}, "index": {},
        "attribution": "© OpenStreetMap contributors", "license": {}, "coverage_note": "",
    }
    path = write_accessibility_source_record(tmp_path, manifest)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "accessibility" / "sources" / "tfe-f021a-source.json"
    assert record["osm_sha256"] == "a" * 64
    assert record["coverage_polygon_sha256"] == "b" * 64


def _unchecked_elevation_index():
    index = object.__new__(AccessibilityIndex)
    index.validate_dem_manifest = lambda _path: None
    return index


def test_elevation_delta_uses_preserved_spot_altitude_after_reference_check():
    class Sampler:
        manifest_path = "test-dem-manifest.json"

        def sample_detailed(self, lat, lon):
            elevation = 100.5 if lon == 1 else 90.6
            return {"elevationM": elevation, "reason": None}

    index = _unchecked_elevation_index()
    delta, reason, audit = index.elevation_delta_for_candidate(
        {"tags": {}, "nearestPoint": [2, 1]},
        reference_lat=1, reference_lon=1, reference_altitude=100,
        elevation_sampler=Sampler(),
    )
    assert reason is None
    assert delta == 9  # round(100 - 90.6), not round(100.4 - 90.6)
    assert audit["elevationDeltaMExact"] == pytest.approx(9.4)


@pytest.mark.parametrize("tags", [
    {"bridge": "yes"},
    {"tunnel": "yes"},
    {"layer": "1"},
    {"level": "-1"},
    {"amenity": "parking", "parking": "underground"},  # Present in the pinned Tenerife extract.
    {"amenity": "parking", "parking": "rooftop"},
    {"amenity": "parking", "parking": "multi-storey"},
    {"location": "underground"},
])
def test_elevation_delta_suppresses_non_ground_features(tags):
    class UnusedSampler:
        manifest_path = "test-dem-manifest.json"

        def sample_detailed(self, _lat, _lon):
            raise AssertionError("non-ground features must not be sampled")

    index = _unchecked_elevation_index()
    delta, reason, audit = index.elevation_delta_for_candidate(
        {"tags": tags, "nearestPoint": [2, 1]},
        reference_lat=1, reference_lon=1, reference_altitude=100,
        elevation_sampler=UnusedSampler(),
    )
    assert delta is None
    assert reason == "surface_not_ground"
    assert audit["featurePoint"] == [2, 1]
    assert audit["surfaceIndicators"]


@pytest.mark.parametrize("tags", [{"layer": "0"}, {"level": "0"}, {"layer": "0", "level": "0"}])
def test_zero_layer_and_level_do_not_suppress_ground_elevation(tags):
    class GroundSampler:
        manifest_path = "test-dem-manifest.json"

        def sample_detailed(self, lat, lon):
            return {"elevationM": 100.1 if lon == 1 else 95.2, "reason": None}

    delta, reason, _audit = _unchecked_elevation_index().elevation_delta_for_candidate(
        {"tags": tags, "nearestPoint": [2, 1]}, reference_lat=1, reference_lon=1,
        reference_altitude=100, elevation_sampler=GroundSampler(),
    )
    assert reason is None
    assert delta == 5


@pytest.mark.parametrize(
    ("reference_dem", "feature_dem", "expected_delta"),
    [
        (3095.48, 3085.00, 10),
        (3095.52, 3085.00, 11),
        (3095.838134765625, 3000.0, 96),
    ],
)
def test_fractional_dem_reference_values_preserve_precision(reference_dem, feature_dem, expected_delta):
    class FractionalSampler:
        manifest_path = "test-dem-manifest.json"

        def sample_detailed(self, _lat, lon):
            return {"elevationM": reference_dem if lon == 1 else feature_dem, "reason": None}

    delta, reason, audit = _unchecked_elevation_index().elevation_delta_for_candidate(
        {"tags": {}, "nearestPoint": [2, 1]}, reference_lat=1, reference_lon=1,
        reference_altitude=reference_dem, elevation_sampler=FractionalSampler(),
    )
    assert reason is None
    assert delta == expected_delta
    assert audit["elevationDeltaMExact"] == pytest.approx(reference_dem - feature_dem)
    assert audit["referenceComparison"]["policy"] == "native_fractional_vs_dem"


def test_changed_dem_manifest_rejected_before_reference_or_feature_sampling(tmp_path):
    from src.accessibility_prepare import sha256_file
    from unittest.mock import MagicMock

    manifest_path = tmp_path / "canaries.json"
    manifest_path.write_text('{"revision": "pinned"}\n', encoding="utf-8")
    expected_hash = sha256_file(manifest_path)
    index = object.__new__(AccessibilityIndex)
    index.manifest = {"policies": {"dem_manifest_sha256": expected_hash}}

    class DifferentEndpointSampler:
        def __init__(self):
            self.manifest_path = manifest_path
            self.samples = []

        def sample_detailed(self, lat, lon):
            self.samples.append((lat, lon))
            return {"elevationM": 100.0 if lon == 1 else 80.25, "reason": None}

    sampler = DifferentEndpointSampler()
    manifest_path.write_text('{"revision": "changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="DEM manifest identity mismatch"):
        index.elevation_delta_for_candidate(
            {"tags": {}, "nearestPoint": [2, 1]}, reference_lat=1, reference_lon=1,
            reference_altitude=100, elevation_sampler=sampler,
        )
    assert sampler.samples == []
    index.query_detailed = MagicMock(side_effect=AssertionError("query must not run"))
    with pytest.raises(ValueError, match="DEM manifest identity mismatch"):
        index.query_public(1, 2, 100, reference_altitude=100, elevation_sampler=sampler)
    index.query_detailed.assert_not_called()


def test_frozen_baseline_fetch_verifies_hash_and_is_repeatable(tmp_path, monkeypatch):
    from tools import fetch_pilot_baseline

    content = b'{"tile":"N028W017"}\n'
    expected = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(fetch_pilot_baseline, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(fetch_pilot_baseline, "EXPECTED_SHA256", expected)
    requested = []

    class Response:
        def __init__(self):
            self.blocks = [content[:8], content[8:], b""]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return self.blocks.pop(0)

    def open_url(request, timeout):
        requested.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(fetch_pilot_baseline, "urlopen", open_url)
    output = tmp_path / "input/pilot/N028W017.json"
    first = fetch_pilot_baseline.fetch_baseline(output)
    second = fetch_pilot_baseline.fetch_baseline(output)
    assert output.read_bytes() == content
    assert first["sha256"] == second["sha256"] == expected
    assert first["commit"] == fetch_pilot_baseline.COMMIT
    assert len(requested) == 1
    assert second["already_present"] is True


def _write_test_index(tmp_path, *, add_road=False, road_ids=(40,), path_complete=True,
                      restriction_error=False, policy_overrides=None):
    from shapely import to_wkb
    from src.accessibility_prepare import _create_index, canonical_json_bytes, sha256_file

    tmp_path.mkdir(parents=True, exist_ok=True)
    lat, lon = 28.25, -16.65
    x, y = TO_UTM.transform(lon, lat)
    reference = Point(x, y)
    domain = reference.buffer(10_000)
    coverage = reference.buffer(20_000)
    index_path = tmp_path / "index.sqlite"
    connection = _create_index(index_path)
    if add_road:
        from src.accessibility_prepare import _insert_feature
        from shapely.geometry import LineString
        for road_id in road_ids:
            _insert_feature(
                connection, osm_type="way", osm_id=road_id, version=1, timestamp=None,
                feature_class="road", inventory_state="active",
                tags={"highway": "primary"},
                geometry=LineString([(x - 50, y + 100), (x + 50, y + 100)]),
            )
    connection.commit()
    connection.close()
    identity = {
        "pbf_sha256": "1" * 64,
        "coverage_polygon_sha256": "2" * 64,
        "dem_manifest_sha256": "3" * 64,
        "scope_bbox": [-17.0, 27.95, -16.1, 28.65],
        "preparation_buffer_m": 6000,
        "radii_m": [500, 1000, 3000, 5000],
        "output_radius_m": 1000,
        "crs": "EPSG:32628",
        "software": {
            "python": "3.14.4", "osmium": "4.3.2", "shapely": "2.2.0",
            "pyproj": "test", "proj": "test", "rasterio": "test", "sqlite": "test",
        },
        **SUPPORTED_POLICY_REVISIONS,
    }
    identity.update(policy_overrides or {})
    source_id = "tfe-f021a-" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:16]
    manifest = {
        "schema": 1, "status": "complete", "dataset_id": source_id,
        "scope_bbox": identity["scope_bbox"], "policies": identity,
        "index": {"path": "index.sqlite", "size": index_path.stat().st_size,
                  "sha256": sha256_file(index_path)},
        "files": {}, "domain_wkb_hex": to_wkb(domain).hex(),
        "coverage_wkb_hex": to_wkb(coverage).hex(),
        "class_completeness": {"road": True, "track": True, "path": path_complete, "parking": True},
        "errors_by_class": ({"restriction:unlocated_restriction": 1} if restriction_error else {}),
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    return manifest_path, lat, lon


def _assert_manifest_source_id_is_self_consistent(manifest):
    from src.accessibility_prepare import canonical_json_bytes

    identity_keys = (
        "pbf_sha256", "coverage_polygon_sha256", "dem_manifest_sha256",
        "scope_bbox", "preparation_buffer_m", "radii_m", "output_radius_m",
        "software", "crs", "preparation_revision", "access_policy_revision",
        "distance_policy_revision", "elevation_policy_revision",
    )
    identity = {key: manifest["policies"][key] for key in identity_keys}
    expected = "tfe-f021a-" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:16]
    assert manifest["dataset_id"] == expected


def test_current_runtime_policy_manifest_loads_and_queries(tmp_path):
    manifest_path, lat, lon = _write_test_index(tmp_path, add_road=True)
    with AccessibilityIndex(manifest_path) as index:
        assert {key: index.manifest["policies"][key]
                for key in SUPPORTED_POLICY_REVISIONS} == SUPPORTED_POLICY_REVISIONS
        assert index.query_detailed(lat, lon, 100)["classes"]["road"]["status"] == "found"


@pytest.mark.parametrize(
    ("policy_name", "incompatible_revision"),
    [
        ("preparation_revision", "f02-1a-prep-3"),
        ("access_policy_revision", "f02-1a-access-2"),
        ("distance_policy_revision", "f02-1a-distance-2"),
        ("elevation_policy_revision", "f02-1a-elevation-2"),
    ],
)
def test_incompatible_runtime_policy_rejected_before_index_or_query(
    tmp_path, policy_name, incompatible_revision
):
    from unittest.mock import patch

    manifest_path, _lat, _lon = _write_test_index(
        tmp_path,
        policy_overrides={policy_name: incompatible_revision},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert_manifest_source_id_is_self_consistent(manifest)
    with patch("src.accessibility_index.sqlite3.connect") as connect, \
         patch("src.accessibility_index.from_wkb") as from_wkb:
        with pytest.raises(ValueError, match=policy_name):
            AccessibilityIndex(manifest_path)
    connect.assert_not_called()
    from_wkb.assert_not_called()


def test_retained_revision2_policy_set_is_rejected_even_with_consistent_identity(tmp_path):
    old_revisions = {
        "preparation_revision": "f02-1a-prep-3",
        "access_policy_revision": "f02-1a-access-2",
        "distance_policy_revision": "f02-1a-distance-2",
        "elevation_policy_revision": "f02-1a-elevation-2",
    }
    manifest_path, _lat, _lon = _write_test_index(tmp_path, policy_overrides=old_revisions)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert_manifest_source_id_is_self_consistent(manifest)
    with pytest.raises(ValueError, match="preparation_revision.*f02-1a-prep-4"):
        AccessibilityIndex(manifest_path)


def test_rtree_exact_radius_and_complete_empty_vs_incomplete(tmp_path):
    manifest_path, lat, lon = _write_test_index(tmp_path, add_road=True)
    with AccessibilityIndex(manifest_path) as index:
        at_radius = index.query_detailed(lat, lon + 0.0, 100)
        road = at_radius["classes"]["road"]
        assert road["status"] == "found"
        assert road["nearestCandidate"]["osm"] == "way/40"
        assert road["nearestCandidate"]["distanceMExact"] == pytest.approx(100)
        assert index.query_detailed(lat, lon, 99)["classes"]["road"]["status"] == "not_found_within_radius"
        assert at_radius["classes"]["track"]["status"] == "not_found_within_radius"

    manifest_path, lat, lon = _write_test_index(tmp_path / "incomplete", path_complete=False)
    with AccessibilityIndex(manifest_path) as index:
        assert index.query_detailed(lat, lon, 500)["classes"]["path"] == {
            "status": "unknown", "reason": "incomplete_geometry", "selected": None,
            "nearestCandidate": None, "candidateCount": 0, "rtreeCandidates": 0,
            "geometryReads": 0, "candidates": [],
        }


def test_equal_distance_uses_numeric_osm_id_independent_of_insertion_order(tmp_path):
    manifest_path, lat, lon = _write_test_index(
        tmp_path / "reverse", add_road=True, road_ids=(50, 40)
    )
    with AccessibilityIndex(manifest_path) as index:
        assert index.query_detailed(lat, lon, 100)["classes"]["road"]["nearestCandidate"]["osm"] == "way/40"

    manifest_path, lat, lon = _write_test_index(
        tmp_path / "forward", add_road=True, road_ids=(40, 50)
    )
    with AccessibilityIndex(manifest_path) as index:
        assert index.query_detailed(lat, lon, 100)["classes"]["road"]["nearestCandidate"]["osm"] == "way/40"


def test_unlocated_restriction_error_marks_audit_list_incomplete(tmp_path):
    manifest_path, lat, lon = _write_test_index(tmp_path, restriction_error=True)
    with AccessibilityIndex(manifest_path) as index:
        result = index.query_detailed(lat, lon, 500)
    assert result["nearbyRestrictions"] == []
    assert result["nearbyRestrictionsComplete"] is False
    assert result["classes"]["path"]["status"] == "not_found_within_radius"
