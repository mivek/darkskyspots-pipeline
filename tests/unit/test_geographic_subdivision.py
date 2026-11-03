"""Compare subdivided lookup directly with authoritative source predicates."""

import json
import sqlite3
import time
from unittest.mock import patch

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, MultiPolygon, Point, box, mapping

from src.geographic_index import GeographicIndex, build_index, MAX_GEOMETRY_BYTES
from src.geographic_subdivision import require_subdivision_runtime
from src.geographic_data import geometry_digest
from src.geographic_geometry import canonical
from tests.geographic_helpers import feature, make_e3_dataset, SPOT


@pytest.fixture(scope="module")
def oversized_index(tmp_path_factory):
    directory = tmp_path_factory.mktemp("authoritative-oversized")
    angles = np.linspace(0, 2 * np.pi, 530000, endpoint=False)
    polygon = Polygon(
        np.column_stack((np.cos(angles), np.sin(angles))),
        holes=[[(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]],
    )
    assert len(shapely.to_wkb(polygon)) > 8 * 1024 * 1024
    start = time.perf_counter()
    build_index(
        directory / "index.sqlite",
        [feature(polygon, "water", id="large-authoritative-water")],
    )
    print("oversized single polygon preparation seconds", time.perf_counter() - start)
    return directory / "index.sqlite", polygon


def test_actual_oversized_component_uses_bounded_certified_storage(oversized_index):
    path, original = oversized_index
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT geometry FROM features").fetchone() == (None,)
        assert (
            db.execute("SELECT max(length(geometry)) FROM originals").fetchone()[0]
            <= 1024 * 1024
        )
        assert (
            db.execute("SELECT max(length(geometry)) FROM pages").fetchone()[0]
            <= 1024 * 1024
        )
        assert db.execute("SELECT count(*) FROM pages").fetchone()[0] > 1
    reader = GeographicIndex(path)
    assert reader.original_wkb(1) == shapely.to_wkb(original, byte_order=1)
    reader.close()


def test_large_feature_lookup_uses_spatial_index_instead_of_scanning_every_part(
    oversized_index,
):
    path, source = oversized_index
    reader = GeographicIndex(path)
    calls = [0]

    def budget():
        calls[0] += 1
        return int(calls[0] > 100)

    # A narrow spatial probe must finish within 100,000 SQLite VM operations.
    # Scanning this feature's 530,004 parts cannot fit that budget. This checks
    # bounded lookup work without depending on EXPLAIN text or wall-clock speed.
    reader.db.set_progress_handler(budget, 1000)
    try:
        geometry, _ = reader.feature_window(1, box(0.99999, -0.00001, 1.00001, 0.00001))
    finally:
        reader.db.set_progress_handler(None, 0)
    assert geometry.covers(Point(1, 0)) == source.covers(Point(1, 0))
    reader.close()


def test_original_boundary_holes_thin_supports_and_artificial_seams(oversized_index):
    path, original = oversized_index
    reader = GeographicIndex(path, cache_bytes=2 * 1024 * 1024)
    page = shapely.from_wkb(
        reader.db.execute("SELECT geometry FROM pages LIMIT 1").fetchone()[0]
    )
    triangle = page if page.geom_type == "Polygon" else page.geoms[0]
    vertices = list(triangle.exterior.coords)
    seam = None
    for a, b in zip(vertices, vertices[1:]):
        mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if original.contains(mid):
            seam = mid
            break
    assert seam is not None
    probes = [
        Point(1, 0),
        Point(*original.exterior.coords[12345]),
        Point(0.1, 0.1),
        Point(0, 0.1),
        Point(0, 0),
        Point(1.01, 0),
        Point(0.5, 0),
        seam,
        Point(np.nextafter(1.0, -np.inf), 0),
        Point(np.nextafter(1.0, np.inf), 0),
    ]
    for point in probes:
        support = box(point.x - 1e-8, point.y - 1e-8, point.x + 1e-8, point.y + 1e-8)
        local, prop = reader.feature_window(1, support)
        assert prop["id"] == "large-authoritative-water"
        assert local.covers(point) == original.covers(point)
        assert local.contains(point) == original.contains(point)
        assert local.intersects(support) == original.intersects(support)
        assert local.covers(support) == original.covers(support)
        finding = reader.observe([dict(lon=point.x, lat=point.y)], [support])[0]
        assert bool(finding["water_features"]) == original.intersects(support)
        if finding["water_features"]:
            assert len(finding["water_features"]) == 1
            assert finding["water_features"][0]["centre_covered"] == original.covers(
                point
            )
        assert reader.bytes <= reader.limit
    clipped, _ = reader.clipped(1, box(0.099, -0.2, 0.101, 0.2))
    assert geometry_digest(clipped) == geometry_digest(
        original.intersection(box(0.099, -0.2, 0.101, 0.2))
    )
    reader.close()


def test_corrupt_authoritative_chunks_and_missing_pages_never_approximate(tmp_path):
    source = Point(0, 0).buffer(1, quad_segs=40)
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 512):
        build_index(tmp_path / "chunks", [feature(source, "water")])
        build_index(tmp_path / "pages", [feature(source, "water")])
    with sqlite3.connect(tmp_path / "chunks") as db:
        db.execute(
            "UPDATE originals SET geometry=? WHERE feature=1 AND sequence=0",
            (b"corrupt",),
        )
    reader = GeographicIndex(tmp_path / "chunks")
    with pytest.raises(ValueError, match="Corrupt authoritative geometry"):
        reader.original_wkb(1)
    reader.close()
    with sqlite3.connect(tmp_path / "pages") as db:
        db.execute("DELETE FROM pages")
    reader = GeographicIndex(tmp_path / "pages")
    with pytest.raises(ValueError, match="Invalid geographic fragment"):
        reader.feature_window(1, box(-0.1, -0.1, 0.1, 0.1))
    reader.close()


def test_legacy_index_keeps_authoritative_predicates_and_context_bytes(tmp_path):
    source = Polygon([(0, 0), (1, 0.3), (0.8, 1), (0, 0)])
    path = tmp_path / "legacy"
    with sqlite3.connect(path) as db:
        db.executescript(
            """CREATE TABLE features(id INTEGER PRIMARY KEY,properties TEXT,geometry BLOB);
            CREATE TABLE parts(id INTEGER PRIMARY KEY,feature INTEGER);
            CREATE VIRTUAL TABLE bounds USING rtree(id,west,east,south,north);"""
        )
        db.execute(
            "INSERT INTO features VALUES(1,?,?)",
            (json.dumps({"kind": "water"}), shapely.to_wkb(source)),
        )
        db.execute("INSERT INTO parts VALUES(1,1)")
        db.execute("INSERT INTO bounds VALUES(1,0,1,0,1)")
    reader = GeographicIndex(path)
    extent = box(0.1, 0.1, 0.8, 0.8)
    clipped, _ = reader.clipped(1, extent)
    assert geometry_digest(clipped) == geometry_digest(source.intersection(extent))
    assert reader.feature(1)[0].equals_exact(source, 0)
    reader.close()


def test_oversized_multipolygon_queries_only_relevant_authoritative_components(
    tmp_path,
):
    angle = np.linspace(0, 2 * np.pi, 267000, endpoint=False)
    circle = np.column_stack((np.cos(angle), np.sin(angle)))
    source = canonical(MultiPolygon([Polygon(circle), Polygon(circle + [4, 0])]))
    assert len(shapely.to_wkb(source)) > 8 * 1024 * 1024
    build_index(
        tmp_path / "index", [feature(source, "marine_land", id="disjoint-islands")]
    )
    reader = GeographicIndex(tmp_path / "index", cache_bytes=0)
    point = {"lon": 0.0, "lat": 0.0}
    support = box(-0.01, -0.01, 0.01, 0.01)
    finding = reader.observe([point], [support])[0]
    assert finding["marine_land"] and finding["marine_support_land"]
    assert reader.geometry_reads == 1 and reader.bytes == 0
    reader.close()


def test_failed_exact_boundary_certificate_aborts_preparation(tmp_path):
    source = box(0, 0, 1, 1).buffer(0.1, quad_segs=20)
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 100), patch(
        "shapely.coverage_union_all", return_value=box(0, 0, 1, 1)
    ):
        with pytest.raises(ValueError, match="boundary coordinates exactly"):
            build_index(tmp_path / "bad", [feature(source, "water")])
    assert not (tmp_path / "bad").exists()


def test_topological_equality_does_not_waive_changed_boundary_coordinates(tmp_path):
    source = shapely.segmentize(box(0, 0, 1, 1), 1 / 32)
    reconstructed = box(0, 0, 1, 1)
    assert reconstructed.equals(source)
    assert shapely.to_wkb(shapely.normalize(reconstructed)) != shapely.to_wkb(
        shapely.normalize(source)
    )
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 512), patch(
        "shapely.coverage_union_all", return_value=reconstructed
    ):
        with pytest.raises(ValueError, match="boundary coordinates exactly"):
            build_index(tmp_path / "changed", [feature(source, "water")])
    assert not (tmp_path / "changed").exists()


def test_narrow_source_preserves_boundary_and_native_support_predicates(tmp_path):
    west = 0.123456789
    east = west + 2**-28
    source = shapely.segmentize(box(west, 0, east, 1), 1 / 32)
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 512):
        build_index(tmp_path / "narrow", [feature(source, "water")])
    reader = GeographicIndex(tmp_path / "narrow")
    for x in [
        np.nextafter(west, -np.inf),
        west,
        (west + east) / 2,
        east,
        np.nextafter(east, np.inf),
    ]:
        point = Point(x, 0.5)
        support = box(x - 2**-32, 0.49, x + 2**-32, 0.51)
        indexed, _ = reader.feature_window(1, support)
        for predicate, query in [
            ("contains", point),
            ("covers", point),
            ("covers", support),
            ("intersects", support),
        ]:
            assert getattr(indexed, predicate)(query) == getattr(source, predicate)(
                query
            )
    reader.close()


def test_fragmented_antimeridian_support_includes_both_sides_and_island_hole(tmp_path):
    source = shapely.segmentize(
        canonical(
            Polygon(
                [(179, -2), (-179, -2), (-179, 2), (179, 2)],
                holes=[
                    [(179.75, -0.25), (-179.75, -0.25), (-179.75, 0.25), (179.75, 0.25)]
                ],
            )
        ),
        0.125,
    )
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 512):
        build_index(
            tmp_path / "crossing", [feature(source, "water", id="crossing-feature")]
        )
    reader = GeographicIndex(tmp_path / "crossing")
    for lon, lat in [(-180, 1), (-180, 0), (179.875, 1), (-179.875, 1), (179.75, 0.25)]:
        support = canonical(box(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01))
        local, properties = reader.feature_window(1, support)
        point = Point(lon, lat)
        for predicate, query in [
            ("contains", point),
            ("covers", point),
            ("covers", support),
            ("intersects", support),
        ]:
            assert getattr(local, predicate)(query) == getattr(source, predicate)(query)
        finding = reader.observe([dict(lon=lon, lat=lat)], [support])[0]
        assert bool(finding["water_features"]) == source.intersects(support)
    reader.close()


@pytest.mark.parametrize(
    "version,geos,missing",
    [
        ("2.0.7", (3, 14, 1), False),
        ("2.2.0", (3, 9, 9), False),
        ("2.2.0", (3, 14, 1), True),
    ],
)
def test_incompatible_runtime_fails_before_output_creation(
    tmp_path, version, geos, missing
):
    with patch("shapely.__version__", version), patch("shapely.geos_version", geos):
        with patch(
            "shapely.constrained_delaunay_triangles",
            None if missing else shapely.constrained_delaunay_triangles,
        ):
            with pytest.raises(ValueError, match="Shapely >=2.1 and GEOS >=3.10"):
                build_index(tmp_path / "absent", [])
    assert not (tmp_path / "absent").exists()


def test_seams_context_fingerprints_determinism_and_workspace_guards(tmp_path):
    shape = Point(0, 45).buffer(0.2, quad_segs=80)
    evidence = [
        feature(box(-1, 44, 1, 46), "marine_land"),
        feature(box(-1, 44, 1, 46), "inland_coverage"),
        feature(shape, "water", id="stable-water"),
    ]
    with patch("src.geographic_index.MAX_GEOMETRY_BYTES", 512):
        build_index(tmp_path / "a", evidence)
        build_index(tmp_path / "b", evidence)
        assert (tmp_path / "a").read_bytes() == (tmp_path / "b").read_bytes()
        ds = make_e3_dataset(tmp_path / "fragmented", extra=[evidence[-1]])
    unsplit = make_e3_dataset(tmp_path / "unsplit", extra=[evidence[-1]])
    from src.geographic_e3 import local_context

    extent = box(-0.15, 44.85, 0.15, 45.15)
    assert local_context(ds, extent, "france") == local_context(
        unsplit, extent, "france"
    )
    assert (
        ds.audit([SPOT])["findings"][0]["reasons"]
        == unsplit.audit([SPOT])["findings"][0]["reasons"]
    )
    reader = GeographicIndex(tmp_path / "a", query_bytes=64)
    with pytest.raises(ValueError, match="bounded workspace"):
        reader.feature_window(3, extent)
    reader.close()
    ds.close()
    unsplit.close()
