import json
import sqlite3
import zlib

import pytest
from shapely import from_wkb
from shapely.geometry import Point

from src.accessibility import resolve_access_evidence
from src.accessibility_prepare import _create_index, _insert_feature


@pytest.fixture
def connection(tmp_path):
    db = _create_index(tmp_path / "index.sqlite")
    yield db
    db.close()


def insert(db, osm_id, feature_class, x, *, tags=None):
    return _insert_feature(
        db, osm_type="node", osm_id=osm_id, version=1, timestamp=None,
        feature_class=feature_class, inventory_state="active",
        tags=tags or {"access": "yes"}, geometry=Point(x, x),
    )


def assert_spatial_consistency(db):
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT rtreecheck('feature_rtree')").fetchone()[0] == "ok"
    assert db.execute(
        "SELECT count(*) FROM features f LEFT JOIN feature_rtree r ON f.id=r.id WHERE r.id IS NULL"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM feature_rtree r LEFT JOIN features f ON r.id=f.id WHERE f.id IS NULL"
    ).fetchone()[0] == 0


def at(db, coordinate):
    return db.execute(
        "SELECT f.osm_id, f.feature_class FROM feature_rtree r "
        "JOIN features f ON f.id=r.id "
        "WHERE r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=? "
        "ORDER BY f.osm_id, f.feature_class",
        (coordinate,) * 4,
    ).fetchall()


def test_fresh_insert_preserves_geometry_and_access(connection):
    tags = {"highway": "path", "access": "private", "foot": "yes"}
    assert insert(connection, 1, "path", 5, tags=tags)
    row = connection.execute(
        "SELECT geometry_type, geometry_wkb, tags_json, access_json, quality_json FROM features"
    ).fetchone()
    assert row[0] == "Point"
    assert from_wkb(zlib.decompress(row[1])).equals_exact(Point(5, 5), 0)
    assert json.loads(row[2]) == tags
    assert json.loads(row[3]) == {
        mode: resolve_access_evidence(tags, mode, "path") for mode in ("foot", "motorcar")
    }
    assert json.loads(row[4]) == {"geometry_complete": True, "geometry_valid": True}
    assert at(connection, 5) == [("1", "path")]
    assert_spatial_consistency(connection)


@pytest.mark.parametrize("feature_class", ["road", "track", "path", "parking"])
@pytest.mark.parametrize("highest", [True, False])
def test_repeated_replacement_moves_spatial_entry(connection, feature_class, highest):
    insert(connection, 1, feature_class, 10)
    if not highest:
        insert(connection, 2, "road", 20)
    original_id = connection.execute("SELECT id FROM features WHERE osm_id='1'").fetchone()[0]
    for position in (30, 40):
        insert(connection, 1, feature_class, position)
        new_id = connection.execute("SELECT id FROM features WHERE osm_id='1'").fetchone()[0]
        assert new_id != original_id
        assert at(connection, position) == [("1", feature_class)]
        assert at(connection, 10) == []
        if position == 40:
            assert at(connection, 30) == []
        if not highest:
            assert at(connection, 20) == [("2", "road")]
        assert_spatial_consistency(connection)
        original_id = new_id


def test_same_osm_key_in_different_classes_remains_independent(connection):
    insert(connection, 1, "road", 10)
    insert(connection, 1, "path", 20)
    insert(connection, 1, "road", 30)
    assert at(connection, 10) == []
    assert at(connection, 20) == [("1", "path")]
    assert at(connection, 30) == [("1", "road")]
    assert_spatial_consistency(connection)


def test_failed_rtree_insert_rolls_back_feature_and_spatial_changes(connection):
    insert(connection, 1, "road", 10)
    connection.commit()
    original = connection.execute("SELECT * FROM features").fetchall()

    def deny_rtree_insert(action, table, _column, _database, _trigger):
        if action == sqlite3.SQLITE_INSERT and table == "feature_rtree":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_rtree_insert)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            insert(connection, 1, "road", 30)
    finally:
        connection.set_authorizer(None)
        connection.rollback()

    assert connection.execute("SELECT * FROM features").fetchall() == original
    assert at(connection, 10) == [("1", "road")]
    assert at(connection, 30) == []
    assert_spatial_consistency(connection)
