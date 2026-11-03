"""Immutable SQLite/RTree lookup of authoritative geographic geometries."""

from collections import OrderedDict, defaultdict
import json
from pathlib import Path
import sqlite3
import hashlib

import numpy as np
from shapely import from_wkb, to_wkb, points, covers, intersects
from shapely.geometry import box, GeometryCollection
from shapely.ops import unary_union

from .geographic_geometry import canonical, longitude
from .geographic_subdivision import (
    require_subdivision_runtime,
    component_pages,
    normalized_wkb,
)

MAX_GEOMETRY_BYTES = 8 * 1024 * 1024
INDEX_VERSION = 2
QUERY_BYTES = 64 * 1024 * 1024


def index_version(path):
    with sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    ) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def build_index(path, features):
    """RTree boxes/components are lookup-only; full WKB is authoritative."""
    require_subdivision_runtime()
    if Path(path).exists():
        raise ValueError("Index preparation requires a new output path")
    import shapely

    db = sqlite3.connect(path)
    try:
        db.executescript(
            """PRAGMA page_size=4096; PRAGMA journal_mode=OFF;
          PRAGMA temp_store=FILE; PRAGMA cache_size=-32768; PRAGMA user_version=2;
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE features(id INTEGER PRIMARY KEY, properties TEXT NOT NULL, geometry BLOB,
                                byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL);
          CREATE TABLE originals(feature INTEGER, sequence INTEGER, geometry BLOB NOT NULL,
                                 PRIMARY KEY(feature,sequence));
          CREATE TABLE pages(id INTEGER PRIMARY KEY, feature INTEGER NOT NULL, geometry BLOB NOT NULL);
          CREATE TABLE parts(id INTEGER PRIMARY KEY, feature INTEGER NOT NULL, page INTEGER);
          CREATE INDEX part_features ON parts(feature);
          CREATE VIRTUAL TABLE bounds USING rtree(id, west, east, south, north);"""
        )
        db.executemany(
            "INSERT INTO metadata VALUES(?,?)",
            [
                ("subdivision", "source-vertex-constrained-triangulation-v1"),
                ("shapely", shapely.__version__),
                ("geos", shapely.geos_version_string),
            ],
        )
        part = 0
        page = 0
        count = 0
        for count, f in enumerate(features, 1):
            from .geographic_data import valid_geometry

            geometry = canonical(valid_geometry(f["geometry"], polygon=True))
            data = to_wkb(geometry, byte_order=1)
            fragmented = len(data) > MAX_GEOMETRY_BYTES
            db.execute(
                "INSERT INTO features VALUES(?,?,?,?,?)",
                (
                    count,
                    json.dumps(f["properties"], sort_keys=True, separators=(",", ":")),
                    None if fragmented else data,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                ),
            )
            if fragmented:
                from .geographic_subdivision import PAGE_BYTES

                for sequence, offset in enumerate(range(0, len(data), PAGE_BYTES)):
                    db.execute(
                        "INSERT INTO originals VALUES(?,?,?)",
                        (count, sequence, data[offset : offset + PAGE_BYTES]),
                    )
            del data
            # Outward-rounded SQLite float32 envelopes are a conservative
            # superset. Exact predicates below use the complete original WKB.
            for g in (
                geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
            ):
                if not fragmented:
                    part += 1
                    w, s, e, n = g.bounds
                    db.execute("INSERT INTO parts VALUES(?,?,NULL)", (part, count))
                    db.execute(
                        "INSERT INTO bounds VALUES(?,?,?,?,?)", (part, w, e, s, n)
                    )
                else:
                    for blob, envelopes in component_pages(g, db, MAX_GEOMETRY_BYTES):
                        page += 1
                        db.execute(
                            "INSERT INTO pages VALUES(?,?,?)", (page, count, blob)
                        )
                        for w, s, e, n in envelopes:
                            part += 1
                            db.execute(
                                "INSERT INTO parts VALUES(?,?,?)", (part, count, page)
                            )
                            db.execute(
                                "INSERT INTO bounds VALUES(?,?,?,?,?)",
                                (part, w, e, s, n),
                            )
        db.execute("INSERT INTO metadata VALUES('complete','1')")
        db.commit()
        db.execute("VACUUM")
        return count
    except BaseException:
        db.close()
        Path(path).unlink(missing_ok=True)
        raise
    finally:
        db.close()


class GeographicIndex:
    def __init__(self, path, cache_bytes=64 * 1024 * 1024, query_bytes=QUERY_BYTES):
        self.db = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        self.db.execute("PRAGMA cache_size=-32768")
        self.db.execute("PRAGMA temp_store=FILE")
        self.version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if self.version not in {0, INDEX_VERSION}:
            raise ValueError("Unsupported geographic index schema")
        if self.version == INDEX_VERSION and self.db.execute(
            "SELECT value FROM metadata WHERE key='complete'"
        ).fetchone() != ("1",):
            raise ValueError("Incomplete geographic index")
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Corrupt geographic index")
        self.cache = OrderedDict()
        self.bytes = 0
        self.limit = cache_bytes
        self.geometry_reads = 0
        self.query_limit = int(query_bytes)
        if self.limit < 0 or self.query_limit <= 0:
            raise ValueError("Invalid geographic memory budget")

    def close(self):
        self.cache.clear()
        self.db.close()

    def feature(self, identifier):
        if identifier in self.cache:
            value = self.cache.pop(identifier)
            self.cache[identifier] = value
            return value[:2]
        metadata = self.db.execute(
            "SELECT properties,length(geometry) FROM features WHERE id=?", (identifier,)
        ).fetchone()
        if metadata is None or (
            metadata[1] is not None and metadata[1] > MAX_GEOMETRY_BYTES
        ):
            raise ValueError("Invalid geographic index feature")
        blob = (
            self.original_wkb(identifier)
            if metadata[1] is None
            else self.db.execute(
                "SELECT geometry FROM features WHERE id=?", (identifier,)
            ).fetchone()[0]
        )
        if len(blob) * 4 > self.query_limit:
            raise ValueError(
                "Authoritative geometry exceeds query workspace; use a spatial window"
            )
        geometry = from_wkb(blob)
        properties = json.loads(metadata[0])
        cost = len(blob) * 4 + len(metadata[0])
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError("Invalid authoritative indexed geometry")
        self.geometry_reads += 1
        if cost <= self.limit:
            while self.cache and self.bytes + cost > self.limit:
                self.bytes -= self.cache.popitem(last=False)[1][2]
            self.cache[identifier] = (geometry, properties, cost)
            self.bytes += cost
        return geometry, properties

    def original_wkb(self, identifier):
        """Read original bytes only under the explicit query working budget."""
        if self.version == 0:
            return self.db.execute(
                "SELECT geometry FROM features WHERE id=?", (identifier,)
            ).fetchone()[0]
        row = self.db.execute(
            "SELECT geometry,byte_size,sha256 FROM features WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValueError("Missing authoritative feature")
        if row[1] * 4 > self.query_limit:
            raise ValueError("Authoritative geometry exceeds query workspace")
        if row[0] is not None:
            return row[0]
        data = bytearray()
        for expected, (sequence, blob) in enumerate(
            self.db.execute(
                "SELECT sequence,geometry FROM originals WHERE feature=? ORDER BY sequence",
                (identifier,),
            )
        ):
            if sequence != expected or len(data) + len(blob) > row[1]:
                raise ValueError("Corrupt authoritative geometry chunks")
            data.extend(blob)
        if len(data) != row[1] or hashlib.sha256(data).hexdigest() != row[2]:
            raise ValueError("Corrupt authoritative geometry checksum")
        return bytes(data)

    def fragmented(self, identifier):
        return (
            self.version == INDEX_VERSION
            and self.db.execute(
                "SELECT geometry IS NULL FROM features WHERE id=?", (identifier,)
            ).fetchone()[0]
        )

    def feature_window(self, identifier, window):
        """Union whole source-vertex fragments BEFORE applying predicates."""
        if not self.fragmented(identifier):
            return self.feature(identifier)
        ids = set()

        def leaves(geometry):
            if hasattr(geometry, "geoms"):
                for piece in geometry.geoms:
                    yield from leaves(piece)
            else:
                yield geometry

        size = 0
        for piece in leaves(window):
            w, s, e, n = piece.bounds
            # Keep RTree first: starting at parts(feature) would enumerate an
            # entire giant feature for every small spatial probe.
            for page, length in self.db.execute(
                """SELECT DISTINCT p.page,length(g.geometry) FROM bounds b
                CROSS JOIN parts p ON p.id=b.id LEFT JOIN pages g ON g.id=p.page AND g.feature=p.feature
                WHERE p.feature=? AND b.west<=? AND b.east>=? AND b.south<=? AND b.north>=?""",
                (identifier, e, w, n, s),
            ):
                if length is None or length > MAX_GEOMETRY_BYTES:
                    raise ValueError("Invalid geographic fragment")
                if page not in ids:
                    size += length * 4
                    if size > self.query_limit:
                        raise ValueError(
                            "Geographic fragment query exceeds bounded workspace"
                        )
                    ids.add(page)
        pieces = []
        for page in sorted(ids):
            key = ("page", page)
            if key in self.cache:
                value = self.cache.pop(key)
                self.cache[key] = value
                geometry = value[0]
            else:
                blob = self.db.execute(
                    "SELECT geometry FROM pages WHERE id=?", (page,)
                ).fetchone()[0]
                geometry = from_wkb(blob)
                cost = len(blob) * 4
                if not geometry.is_valid or geometry.is_empty:
                    raise ValueError("Invalid geographic fragment geometry")
                self.geometry_reads += 1
                if cost <= self.limit:
                    while self.cache and self.bytes + cost > self.limit:
                        self.bytes -= self.cache.popitem(last=False)[1][2]
                    self.cache[key] = (geometry, None, cost)
                    self.bytes += cost
            pieces.extend(
                geometry.geoms
                if geometry.geom_type == "GeometryCollection"
                else [geometry]
            )
        import shapely

        geometry = shapely.coverage_union_all(pieces)
        if not geometry.is_valid or len(to_wkb(geometry)) * 4 > self.query_limit:
            raise ValueError(
                "Geographic reconstruction exceeds workspace or is invalid"
            )
        properties = json.loads(
            self.db.execute(
                "SELECT properties FROM features WHERE id=?", (identifier,)
            ).fetchone()[0]
        )
        return geometry, properties

    def clipped(self, identifier, extent):
        """Preserve authoritative review fingerprints, not triangulation layout."""
        # Review contexts use original WKB, including otherwise redundant source
        # vertices. Never replace an existing fingerprint by a fragment digest.
        geometry, properties = self.feature(identifier)
        return geometry.intersection(extent), properties

    def query(self, geometries):
        """Return index pairs only; callers must perform exact predicates."""
        self.db.execute(
            "CREATE TEMP TABLE IF NOT EXISTS queries(i INTEGER,w REAL,s REAL,e REAL,n REAL)"
        )
        self.db.execute("DELETE FROM queries")
        rows = []
        for i, g in enumerate(geometries):
            for part in g.geoms if g.geom_type == "MultiPolygon" else [g]:
                rows.append((i, *part.bounds))
        self.db.executemany("INSERT INTO queries VALUES(?,?,?,?,?)", rows)
        return self.db.execute(
            """SELECT DISTINCT q.i,p.feature FROM queries q CROSS JOIN bounds b
          JOIN parts p ON p.id=b.id WHERE b.west<=q.e AND b.east>=q.w AND b.south<=q.n AND b.north>=q.s
          ORDER BY p.feature,q.i"""
        ).fetchall()

    def observe(self, candidates, supports):
        coordinates = points([(longitude(p["lon"]), p["lat"]) for p in candidates])
        supports = [canonical(g) for g in supports]
        result = [
            dict(
                marine_land=False,
                marine_support_land=False,
                inland_coverage=False,
                inland_support_coverage=False,
                water_features=[],
                wetland_features=[],
            )
            for p in candidates
        ]
        groups = defaultdict(list)
        for i, identifier in self.query(supports):
            groups[identifier].append(i)
        partial = defaultdict(list)
        coverage = defaultdict(list)

        def observations():
            for identifier, indexes in groups.items():
                if not self.fragmented(identifier):
                    geometry, prop = self.feature(identifier)
                    yield geometry, prop, indexes
                else:
                    for i in indexes:
                        geometry, prop = self.feature_window(
                            identifier,
                            GeometryCollection([supports[i], coordinates[i]]),
                        )
                        yield geometry, prop, [i]

        for geometry, prop, indexes in observations():
            kind = prop["kind"]
            gs = [supports[i] for i in indexes]
            if kind == "marine_land":
                for i, centre, full, touch in zip(
                    indexes,
                    covers(geometry, coordinates[indexes]),
                    covers(geometry, gs),
                    intersects(geometry, gs),
                ):
                    result[i]["marine_land"] |= bool(centre)
                    result[i]["marine_support_land"] |= bool(full)
                    if touch and not full:
                        partial[i].append(geometry.intersection(supports[i]))
            elif kind == "inland_coverage":
                for i in indexes:
                    result[i]["inland_coverage"] |= geometry.covers(coordinates[i])
                    if geometry.covers(supports[i]):
                        result[i]["inland_support_coverage"] = True
                    elif geometry.intersects(supports[i]):
                        coverage[i].append(geometry.intersection(supports[i]))
            elif kind in {"water", "wetland"}:
                for i, touch in zip(indexes, intersects(geometry, gs)):
                    if touch:
                        result[i][
                            "water_features" if kind == "water" else "wetland_features"
                        ].append(
                            dict(
                                prop,
                                centre_covered=bool(geometry.covers(coordinates[i])),
                            )
                        )
            else:
                raise ValueError(f"Unexpected indexed evidence kind: {kind}")
        for i, parts in partial.items():
            if not result[i]["marine_support_land"]:
                result[i]["marine_support_land"] = unary_union(parts).covers(
                    supports[i]
                )
        for i, parts in coverage.items():
            if not result[i]["inland_support_coverage"]:
                result[i]["inland_support_coverage"] = unary_union(parts).covers(
                    supports[i]
                )
        return result
