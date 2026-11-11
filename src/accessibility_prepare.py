"""Offline preparation of a pinned OSM accessibility evidence dataset."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely import from_wkb, to_wkb, transform as shapely_transform
from shapely.geometry import Point, Polygon, box
from shapely.errors import GEOSException
from shapely.ops import unary_union

from .accessibility import PUBLIC_CLASSES, classify_feature, resolve_access_evidence
from .accessibility_dem import validate_accessibility_dem_manifest
from .accessibility_index import (
    ACCESS_POLICY_REVISION,
    DISTANCE_POLICY_REVISION,
    ELEVATION_POLICY_REVISION,
    GENERALIZED_POLICIES,
    GENERALIZED_PROFILE,
    GENERALIZED_PROFILE_REGISTRY,
    G2_POLICIES,
    PREPARATION_REVISION,
    TO_UTM,
    AccessibilityIndex,
)
from .accessibility_scope import GeographicSpecification
from .resource_metrics import PeakRssSampler

SOURCE_URL = "https://download.geofabrik.de/africa/canary-islands-261107.osm.pbf"
COVERAGE_URL = "https://download.geofabrik.de/africa/canary-islands.poly"
SCOPE_BBOX = (-17.00, 27.95, -16.10, 28.65)
SCOPE_CORE = box(*SCOPE_BBOX)
PREPARATION_BUFFER_M = 6000
CRS = "EPSG:32628"
PILOT_RADII_M = (500, 1000, 3000, 5000)
PILOT_OUTPUT_RADIUS_M = 1000
LICENSE_INFO = {
    "source": "© OpenStreetMap contributors",
    "sourceUrl": "https://www.openstreetmap.org/copyright",
    "databaseLicense": "ODbL-1.0",
    "licenseUrl": "https://opendatacommons.org/licenses/odbl/1-0/",
    "copernicus": "Copernicus DEM GLO-30 delivery 2024_1; see its retained supplier terms and notices.",
    "distribution": "Pilot outputs are local review artifacts. Any later distribution requires the applicable ODbL attribution, derivative database/data offer and preparation recipe, plus Copernicus notices.",
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def parse_geofabrik_poly(path: str | Path):
    """Read the simple Geofabrik .poly format, preserving outer rings and holes."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    rings: list[tuple[bool, list[tuple[float, float]]]] = []
    current: list[tuple[float, float]] | None = None
    is_hole = False
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if line == "END":
            if current:
                rings.append((is_hole, current))
            current = None
            continue
        if current is None:
            is_hole = line.startswith("!")
            current = []
            continue
        parts = line.split()
        if len(parts) >= 2:
            current.append((float(parts[0]), float(parts[1])))
    if current:
        rings.append((is_hole, current))
    outers = [ring for hole, ring in rings if not hole]
    holes = [ring for hole, ring in rings if hole]
    if not outers or any(len(ring) < 4 for ring in outers + holes):
        raise ValueError(f"Invalid Geofabrik coverage polygon: {path}")
    polygons = []
    for outer in outers:
        contained_holes = [hole for hole in holes if Polygon(outer).covers(Point(hole[0]))]
        polygons.append(Polygon(outer, contained_holes))
    geometry = unary_union(polygons)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("Geofabrik coverage polygon is empty or invalid")
    return geometry


def _domain_geometry():
    project = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
    return shapely_transform(SCOPE_CORE, project, interleaved=False).buffer(PREPARATION_BUFFER_M)


def validate_accessibility_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Validate source, index hashes, manifest identity and SQLite integrity."""
    path = Path(manifest_path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") not in {1, 2} or data.get("status") != "complete":
        raise ValueError("Accessibility manifest is incomplete or unsupported")
    root = path.parent
    for name, info in data.get("files", {}).items():
        source = (root / info["path"]).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError(f"Accessibility manifest file missing: {name}")
        if source.stat().st_size != info["size"] or sha256_file(source) != info["sha256"]:
            raise ValueError(f"Accessibility manifest checksum mismatch: {name}")
    index = data.get("index", {})
    index_path = (root / index.get("path", "")).resolve()
    if not index_path.is_relative_to(root) or not index_path.is_file():
        raise ValueError("Accessibility SQLite index missing")
    if index_path.stat().st_size != index.get("size") or sha256_file(index_path) != index.get("sha256"):
        raise ValueError("Accessibility SQLite index checksum mismatch")
    connection = sqlite3.connect(f"file:{index_path}?mode=ro&immutable=1", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Accessibility SQLite index failed integrity_check")
    finally:
        connection.close()
    with AccessibilityIndex(path):
        pass
    return data


def accessibility_source_record(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the compact reusable source/provenance record for output packages."""
    provenance = manifest["pbf_provenance"]
    audit_completeness = manifest.get("audit_completeness", {
        "restriction": "restriction:unlocated_restriction" not in manifest.get("errors_by_class", {}),
    })
    record = {
        "schema": 1,
        "source_id": manifest["dataset_id"],
        "provider": provenance["provider"],
        "osm_url": provenance["source_url"],
        "osm_size": provenance["pbf_size"],
        "osm_sha256": provenance["pbf_sha256"],
        "osm_retrieved_at_utc": provenance["retrieved_at_utc"],
        "osm_http_metadata": provenance["http_metadata"],
        "osm_snapshot_metadata": provenance["pbf_header"],
        "coverage_polygon_url": provenance["coverage_polygon_url"],
        "coverage_polygon_size": provenance["coverage_polygon_size"],
        "coverage_polygon_sha256": provenance["coverage_polygon_sha256"],
        "coverage_polygon_http_metadata": provenance["coverage_polygon_http_metadata"],
        "dem": manifest["dem"],
        "software": manifest["software"],
        "policies": manifest["policies"],
        "index": manifest["index"],
        "counts_by_class": manifest.get("counts_by_class", {}),
        "errors_by_class": manifest.get("errors_by_class", {}),
        "audit_completeness": audit_completeness,
        "attribution": manifest["attribution"],
        "license": manifest["license"],
        "coverage_note": manifest["coverage_note"],
    }
    if manifest.get("schema") == 2 and manifest.get("profile") != GENERALIZED_PROFILE:
        record.update({
            "scope": manifest["scope"],
            "profile": manifest["profile"],
            "source_lock_sha256": manifest["identity"]["source_lock_sha256"],
        })
    return record


def write_accessibility_source_record(output_dir: str | Path, manifest: dict[str, Any]) -> Path:
    """Write the source record under a local tile/cluster output package."""
    source_id = manifest.get("dataset_id")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("Accessibility manifest has no dataset id")
    target = Path(output_dir) / "accessibility" / "sources" / f"{source_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(accessibility_source_record(manifest)))
    return target


def _create_index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE features (
            id INTEGER PRIMARY KEY,
            osm_type TEXT NOT NULL,
            osm_id TEXT NOT NULL,
            version INTEGER,
            timestamp TEXT,
            feature_class TEXT NOT NULL,
            inventory_state TEXT NOT NULL,
            geometry_type TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            access_json TEXT NOT NULL,
            geometry_wkb BLOB NOT NULL,
            minx REAL NOT NULL,
            miny REAL NOT NULL,
            maxx REAL NOT NULL,
            maxy REAL NOT NULL,
            quality_json TEXT NOT NULL,
            UNIQUE(osm_type, osm_id, feature_class)
        );
        CREATE VIRTUAL TABLE feature_rtree USING rtree(id, minx, maxx, miny, maxy);
        CREATE INDEX feature_class_state ON features(feature_class, inventory_state);
        CREATE TABLE build_errors (
            id INTEGER PRIMARY KEY,
            feature_class TEXT NOT NULL,
            error_kind TEXT NOT NULL,
            object_type TEXT,
            object_id TEXT,
            detail TEXT NOT NULL,
            UNIQUE(feature_class,error_kind,object_type,object_id)
        );
        """
    )
    return connection


def _insert_feature(connection: sqlite3.Connection, *, osm_type: str, osm_id: int | str,
                    version: int | None, timestamp: str | None, feature_class: str,
                    inventory_state: str, tags: dict[str, str], geometry,
                    geometry_complete: bool = True, geometry_valid: bool | None = None,
                    quality_extra: dict[str, Any] | None = None) -> bool:
    if geometry is None or geometry.is_empty:
        return False
    bounds = geometry.bounds
    quality = {"geometry_complete": geometry_complete,
               "geometry_valid": geometry.is_valid if geometry_valid is None else geometry_valid}
    quality.update(quality_extra or {})
    access = {
        "foot": resolve_access_evidence(tags, "foot", feature_class),
        "motorcar": resolve_access_evidence(tags, "motorcar", feature_class),
    }
    cursor = connection.execute(
        """INSERT OR REPLACE INTO features
        (osm_type,osm_id,version,timestamp,feature_class,inventory_state,geometry_type,
         tags_json,access_json,geometry_wkb,minx,miny,maxx,maxy,quality_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (osm_type, str(osm_id), version, timestamp, feature_class, inventory_state,
         geometry.geom_type, json.dumps(tags, sort_keys=True, ensure_ascii=False),
         json.dumps(access, sort_keys=True, ensure_ascii=False), zlib.compress(to_wkb(geometry), level=6),
         bounds[0], bounds[1], bounds[2], bounds[3], json.dumps(quality, sort_keys=True)),
    )
    row = connection.execute(
        "SELECT id FROM features WHERE osm_type=? AND osm_id=? AND feature_class=?",
        (osm_type, str(osm_id), feature_class),
    ).fetchone()
    connection.execute("DELETE FROM feature_rtree WHERE id=?", (row[0],))
    connection.execute("INSERT INTO feature_rtree VALUES (?,?,?,?,?)",
                       (row[0], bounds[0], bounds[2], bounds[1], bounds[3]))
    return True


def _object_timestamp(obj) -> str | None:
    timestamp = obj.timestamp
    if timestamp is None:
        return None
    if not isinstance(timestamp, datetime):
        raise TypeError(f"Unexpected Pyosmium timestamp type: {type(timestamp).__name__}")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("OSM timestamp is not timezone-aware")
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_osm(pbf: Path, db_path: Path, node_cache: Path, domain, transformer=TO_UTM) -> dict[str, Any]:
    """Stream OSM objects into an index with disk-backed location lookup."""
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - tested via dependency-isolation check
        raise RuntimeError("Install preparation dependencies with requirements-accessibility.txt") from exc

    connection = _create_index(db_path)
    counters = Counter()
    errors = Counter()
    class_complete = {feature_class: True for feature_class in PUBLIC_CLASSES}
    candidate_parking_ways: set[str] = set()
    assembled_parking_ways: set[str] = set()
    candidate_parking_relations: set[str] = set()
    assembled_parking_relations: set[str] = set()
    factory = osmium.geom.WKBFactory()

    class Extractor(osmium.SimpleHandler):
        def _record_error(self, feature_class, error_kind, obj, detail):
            cursor = connection.execute(
                "INSERT OR IGNORE INTO build_errors(feature_class,error_kind,object_type,object_id,detail) VALUES(?,?,?,?,?)",
                (feature_class, error_kind, obj.__class__.__name__.lower(), str(getattr(obj, "id", "")), str(detail)[:500]),
            )
            if cursor.rowcount:
                errors[(feature_class, error_kind)] += 1
                if feature_class in class_complete:
                    class_complete[feature_class] = False

        def _tags(self, obj):
            return {str(tag.k): str(tag.v) for tag in obj.tags}

        def _store(self, obj, osm_type, feature_class, inventory_state, geometry, tags, quality=None):
            if not geometry.intersects(domain):
                return
            valid = bool(geometry.is_valid)
            if not valid and feature_class in class_complete:
                class_complete[feature_class] = False
                self._record_error(feature_class, "invalid_geometry", obj, geometry.geom_type)
            inserted = _insert_feature(
                connection, osm_type=osm_type, osm_id=obj.id, version=getattr(obj, "version", None),
                timestamp=_object_timestamp(obj), feature_class=feature_class,
                inventory_state=inventory_state, tags=tags, geometry=geometry,
                geometry_valid=valid, quality_extra=quality,
            )
            if inserted:
                counters[(feature_class, inventory_state)] += 1

        def node(self, obj):
            if not obj.location.valid():
                return
            lon, lat = float(obj.location.lon), float(obj.location.lat)
            tags = self._tags(obj)
            feature_class, state = classify_feature(tags, "node", "Point")
            if feature_class is not None and state in {"active", "audit_only"}:
                point = Point(*transformer.transform(lon, lat))
                self._store(obj, "node", feature_class, state, point, tags)

        def way(self, obj):
            tags = self._tags(obj)
            feature_class, state = classify_feature(tags, "way", "LineString")
            if feature_class == "parking":
                if len(obj.nodes) < 4 or obj.nodes[0].ref != obj.nodes[-1].ref:
                    self._record_error("parking", "unsupported_parking_area", obj, "parking way is not closed")
                else:
                    candidate_parking_ways.add(str(obj.id))
                return
            # Interpret the physical line class before asking Pyosmium to build it.
            if feature_class is None or state not in {"active", "audit_only"}:
                return
            try:
                # The factory drops repeated coordinates and otherwise raises an
                # untyped RuntimeError for degenerate lines. Check that source
                # condition explicitly instead of catching arbitrary runtime errors.
                first_location = None
                distinct_locations = False
                for node in obj.nodes:
                    location = (node.location.lon, node.location.lat)
                    if first_location is None:
                        first_location = location
                    elif location != first_location:
                        distinct_locations = True
                if not distinct_locations:
                    raise ValueError("way has fewer than two distinct locations")
                geometry = from_wkb(bytes.fromhex(factory.create_linestring(obj)))
                geometry = shapely_transform(geometry, transformer.transform, interleaved=False)
                if geometry.is_empty or len(obj.nodes) < 2:
                    raise ValueError("way has no usable line geometry")
            except (osmium.InvalidLocationError, ValueError, GEOSException) as exc:
                error_class = feature_class if feature_class in class_complete else "unsupported"
                self._record_error(error_class, "missing_node_or_geometry", obj, exc)
                return
            self._store(obj, "way", feature_class, state, geometry, tags)

        def relation(self, obj):
            tags = self._tags(obj)
            relation_class, _relation_state = classify_feature(tags, "relation", "MultiPolygon")
            if tags.get("type") == "multipolygon" and relation_class == "parking":
                candidate_parking_relations.add(str(obj.id))
            feature_class, state = classify_feature(tags, "relation", "Point")
            if feature_class != "restriction" or state != "audit_only":
                return
            geometries = []
            located_outside_domain = False
            for member in obj.members:
                if member.role != "via":
                    continue
                if member.type == "n":
                    location = self.location_storage.get(member.ref)
                    if location.valid():
                        lon, lat = float(location.lon), float(location.lat)
                        point = Point(*transformer.transform(lon, lat))
                        if domain.covers(point):
                            geometries.append(point)
                        else:
                            located_outside_domain = True
                elif member.type == "w":
                    row = connection.execute(
                        "SELECT geometry_wkb FROM features WHERE osm_type='way' AND osm_id=? ORDER BY id LIMIT 1",
                        (str(member.ref),),
                    ).fetchone()
                    if row:
                        geometries.append(from_wkb(zlib.decompress(row[0])))
            if not geometries:
                if located_outside_domain:
                    return
                self._record_error("restriction", "unlocated_restriction", obj, "no indexed via member")
                return
            geometry = unary_union(geometries)
            self._store(obj, "relation", "restriction", "audit_only", geometry, tags,
                        {"relation_proxy": True, "proxy_role": "via member"})

        def area(self, obj):
            tags = self._tags(obj)
            if tags.get("amenity") != "parking":
                return
            try:
                if obj.num_rings()[0] == 0:
                    raise ValueError("parking area has no outer ring")
                geometry = from_wkb(bytes.fromhex(factory.create_multipolygon(obj)))
                geometry = shapely_transform(geometry, transformer.transform, interleaved=False)
                intersects_domain = geometry.intersects(domain)
            except (osmium.InvalidLocationError, ValueError, GEOSException) as exc:
                self._record_error("parking", "area_assembly_error", obj, exc)
                return
            osm_type = "way" if obj.from_way() else "relation"
            area_class, _area_state = classify_feature(tags, osm_type, geometry.geom_type)
            if area_class != "parking":
                return
            osm_id = obj.orig_id()
            inserted = _insert_feature(
                connection, osm_type=osm_type, osm_id=osm_id,
                version=getattr(obj, "version", None), timestamp=_object_timestamp(obj),
                feature_class="parking", inventory_state="active", tags=tags,
                geometry=geometry, geometry_valid=geometry.is_valid,
                quality_extra={"assembly": "area_processor", "geometry_complete": True},
            ) if intersects_domain else False
            if osm_type == "way":
                assembled_parking_ways.add(str(osm_id))
            else:
                assembled_parking_relations.add(str(osm_id))
            if inserted:
                counters[("parking", "active")] += 1
                if not geometry.is_valid:
                    class_complete["parking"] = False
                    self._record_error("parking", "invalid_geometry", obj, "assembled parking area")

    handler = Extractor()
    try:
        area_filter = osmium.filter.TagFilter(("amenity", "parking"))
        area_filter.enable_for(osmium.osm.RELATION)
        processor = osmium.FileProcessor(str(pbf))
        processor.with_locations(f"sparse_file_array,{node_cache}").with_areas(area_filter)
        handler.location_storage = processor.node_location_storage
        for obj in processor:
            if obj.is_node():
                handler.node(obj)
            elif obj.is_way():
                handler.way(obj)
            elif obj.is_relation():
                handler.relation(obj)
            elif obj.is_area():
                handler.area(obj)
        for relation_id in sorted(candidate_parking_relations - assembled_parking_relations):
            class_complete["parking"] = False
            errors[("parking", "incomplete_multipolygon")] += 1
            connection.execute(
                "INSERT OR IGNORE INTO build_errors(feature_class,error_kind,object_type,object_id,detail) VALUES(?,?,?,?,?)",
                ("parking", "incomplete_multipolygon", "relation", relation_id, "parking multipolygon not assembled"),
            )
        for way_id in sorted(candidate_parking_ways - assembled_parking_ways):
            class_complete["parking"] = False
            errors[("parking", "incomplete_area_way")] += 1
            connection.execute(
                "INSERT OR IGNORE INTO build_errors(feature_class,error_kind,object_type,object_id,detail) VALUES(?,?,?,?,?)",
                ("parking", "incomplete_area_way", "way", way_id, "closed parking area not assembled"),
            )
        counts = {
            feature_class: {
                "active": int(connection.execute(
                    "SELECT count(*) FROM features WHERE feature_class=? AND inventory_state='active'",
                    (feature_class,),
                ).fetchone()[0]),
                "audit_only": int(connection.execute(
                    "SELECT count(*) FROM features WHERE feature_class=? AND inventory_state='audit_only'",
                    (feature_class,),
                ).fetchone()[0]),
            }
            for feature_class in sorted({row[0] for row in connection.execute("SELECT DISTINCT feature_class FROM features")})
        }
        error_counts = {
            f"{feature_class}:{kind}": count
            for (feature_class, kind), count in sorted(errors.items())
        }
        connection.commit()
        connection.execute("ANALYZE")
        connection.commit()
        return {
            "counts_by_class": counts,
            "errors_by_class": error_counts,
            "class_completeness": class_complete,
            "indexed_features": int(connection.execute("SELECT count(*) FROM features").fetchone()[0]),
        }
    finally:
        connection.close()


def _read_pbf_header(pbf: Path) -> dict[str, Any]:
    import osmium
    with osmium.io.Reader(str(pbf)) as reader:
        header = reader.header()
        fields = {}
        for key in ("generator", "osmosis_replication_timestamp", "osmosis_replication_sequence_number",
                    "osmosis_replication_base_url", "pbf_dense_nodes", "sorting"):
            try:
                value = header.get(key)
            except (AttributeError, TypeError):
                value = None
            if value not in (None, ""):
                fields[key] = value
        try:
            box_info = header.box()
            fields["bbox"] = list(box_info)
        except (AttributeError, TypeError):
            pass
        return fields


def prepare_accessibility(
    pbf: str | Path,
    source_url: str,
    scope: str,
    dem_manifest: str | Path,
    output_dir: str | Path,
    *,
    coverage_polygon: str | Path | None = None,
    dem_cache_dir: str | Path = "input/elevation",
    geographic_config: str | Path = "config/accessibility.yaml",
) -> dict[str, Any]:
    """Build a validated, immutable index for the legacy pilot or a declared G1 scope."""
    preparation_started = time.perf_counter()
    pbf = Path(pbf).resolve()
    output_dir = Path(output_dir).resolve()
    dem_manifest = Path(dem_manifest).resolve()
    specification = None
    if scope == "tenerife":
        if source_url != SOURCE_URL:
            raise ValueError(f"The pilot requires the exact pinned source URL: {SOURCE_URL}")
        scope_bbox = SCOPE_BBOX
        preparation_buffer_m = PREPARATION_BUFFER_M
        radii_m = PILOT_RADII_M
        output_radius_m = PILOT_OUTPUT_RADIUS_M
        crs = CRS
        profile = "tenerife-legacy-v1"
    else:
        specification = GeographicSpecification.load(geographic_config, scope)
        if source_url != specification.source["source_url"]:
            raise ValueError("Source URL does not match the configured immutable geographic specification")
        configured_pbf = Path(specification.source["pbf_path"]).resolve()
        if pbf != configured_pbf:
            raise ValueError(f"PBF path does not match configured pinned source: {configured_pbf}")
        scope_bbox = specification.bbox
        preparation_buffer_m = specification.preparation_buffer_m
        radii_m = specification.supported_radii_m
        output_radius_m = specification.public_search_radius_m
        crs = specification.metric_crs
        profile = specification.profile
        configured_dem = Path(specification.dem_manifest).resolve()
        if dem_manifest != configured_dem:
            raise ValueError(f"DEM/WBM manifest does not match configured scope: {configured_dem}")
    if not pbf.is_file():
        raise FileNotFoundError(pbf)
    if coverage_polygon is None:
        coverage_polygon = pbf.parent / ("canary-islands.poly" if specification is None else
                                          Path(specification.source["coverage_polygon_path"]).name)
    coverage_polygon = Path(coverage_polygon).resolve()
    if not coverage_polygon.is_file():
        raise FileNotFoundError(f"Retained Geofabrik coverage polygon is required: {coverage_polygon}")
    if output_dir.exists():
        raise FileExistsError(f"Immutable accessibility revision already exists: {output_dir}")

    import pyproj
    import rasterio
    import shapely

    software = {
        "python": sys.version.split()[0],
        "osmium": importlib.metadata.version("osmium"),
        "shapely": shapely.__version__,
        "pyproj": pyproj.__version__,
        "proj": pyproj.proj_version_str,
        "rasterio": rasterio.__version__,
        "sqlite": sqlite3.sqlite_version,
    }
    dem_data = validate_accessibility_dem_manifest(
        dem_manifest, dem_cache_dir, scope, profile, geographic_config=geographic_config,
    )
    expected_dem_region = dem_data["region"]

    polygon = parse_geofabrik_poly(coverage_polygon)
    assignment_core = None
    if specification is None:
        projected = shapely_transform(
            polygon, Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform,
            interleaved=False,
        )
        domain = _domain_geometry()
        transformer = TO_UTM
        if not projected.covers(domain):
            raise ValueError("Geofabrik coverage polygon does not cover the complete Tenerife preparation domain")
    else:
        assignment_core, domain, projected, transformer, _inverse, max_distortion = specification.projected_geometries(polygon)
        if not projected.covers(domain):
            raise ValueError(f"Accessibility scope {scope} provider polygon does not cover its complete preparation domain including buffer")
        configured_coverage = Path(specification.source["coverage_polygon_path"]).resolve()
        if coverage_polygon != configured_coverage:
            raise ValueError(f"Coverage polygon path does not match configured pinned source: {configured_coverage}")
    pbf_header = _read_pbf_header(pbf)
    http_path = pbf.with_name(pbf.name + ".http.json")
    downloaded_metadata = json.loads(http_path.read_text(encoding="utf-8")) if http_path.exists() else {}
    http_metadata = downloaded_metadata.get("pbf", downloaded_metadata)
    coverage_http_metadata = downloaded_metadata.get("coverage_polygon", {})
    source_lock_path = None
    source_lock_sha256 = None
    if specification is not None and scope != "canaries":
        source_lock_path = Path(specification.source["source_lock_path"]).resolve()
        if not source_lock_path.is_file():
            raise ValueError(f"Immutable G2 source lock is missing: {source_lock_path}")
        source_lock_bytes = source_lock_path.read_bytes()
        source_lock = json.loads(source_lock_bytes)
        source_lock_sha256 = hashlib.sha256(source_lock_bytes).hexdigest()
        expected_paths = {
            "pbf": (pbf, specification.source["source_url"]),
            "coverage_polygon": (coverage_polygon, specification.source["coverage_polygon_url"]),
            "checksum_sidecar": (
                Path(specification.source["checksum_sidecar_path"]).resolve(),
                specification.source["source_url"] + ".md5",
            ),
        }
        if source_lock.get("schema") != 1 or source_lock.get("scope") != scope:
            raise ValueError("G2 source lock has an unsupported schema or scope")
        if source_lock.get("polygon_historical_authentication") is not False:
            raise ValueError("G2 source lock must state that retained polygons are not authenticated historical snapshots")
        for name, (path, url) in expected_paths.items():
            item = source_lock.get(name)
            if (not isinstance(item, dict) or item.get("url") != url
                    or item.get("size") != path.stat().st_size
                    or item.get("sha256") != sha256_file(path)):
                raise ValueError(f"G2 source lock does not match retained {name} bytes or URL")
            http = item.get("http_metadata", {})
            if (http.get("url") != url or http.get("size") != item.get("size")
                    or http.get("sha256") != item.get("sha256")
                    or not http.get("retrieved_at_utc")):
                raise ValueError(f"G2 source lock has inconsistent HTTP metadata for {name}")
        pbf_lock = source_lock["pbf"]
        checksum = pbf_lock.get("provider_checksum", {})
        if checksum.get("algorithm") != "md5" or not isinstance(checksum.get("value"), str):
            raise ValueError("G2 source lock has no provider MD5 checksum")
        sidecar = source_lock["checksum_sidecar"]
        if (checksum.get("sidecar_sha256") != sidecar.get("sha256")
                or checksum.get("sidecar_url") != sidecar.get("url")):
            raise ValueError("G2 provider checksum sidecar metadata contradicts the source lock")
        sidecar_text = expected_paths["checksum_sidecar"][0].read_text(encoding="ascii")
        if sidecar_text != sidecar.get("text") or checksum["value"].lower() not in sidecar_text.lower():
            raise ValueError("G2 retained provider checksum sidecar is inconsistent")
        md5 = hashlib.md5()
        with pbf.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(block)
        if md5.hexdigest() != checksum["value"].lower():
            raise ValueError("G2 PBF provider checksum mismatch")
        if pbf_lock.get("header") != pbf_header:
            raise ValueError("G2 PBF replication header differs from the locked header")
        replication_timestamp = pbf_header.get("osmosis_replication_timestamp")
        if hasattr(replication_timestamp, "timestamp"):
            replication_timestamp = int(replication_timestamp.timestamp())
        if isinstance(replication_timestamp, (list, tuple)) and len(replication_timestamp) == 1:
            replication_timestamp = replication_timestamp[0]
        if str(replication_timestamp) not in {"1794086496", "2026-11-07T21:21:36Z"}:
            raise ValueError("G2 PBF is not the approved 2026-11-07 dated snapshot")
        http_metadata = pbf_lock.get("http_metadata", {})
        coverage_http_metadata = source_lock["coverage_polygon"].get("http_metadata", {})
    else:
        if not http_metadata.get("retrieved_at_utc") or not coverage_http_metadata.get("retrieved_at_utc"):
            raise ValueError("PBF and coverage polygon retrieval metadata are required; use tools/fetch_accessibility_source.py")
        if http_metadata.get("sha256") != sha256_file(pbf) or http_metadata.get("size") != pbf.stat().st_size:
            raise ValueError("PBF download metadata does not match the retained source")
        if coverage_http_metadata.get("sha256") != sha256_file(coverage_polygon):
            raise ValueError("Coverage polygon download metadata does not match the retained source")
        if specification is not None and (
            http_metadata.get("url") != source_url
            or coverage_http_metadata.get("url") != specification.source["coverage_polygon_url"]
        ):
            raise ValueError("Source retrieval URLs do not match the pinned geographic specification")
    provenance = {
        "provider": "Geofabrik",
        "source_url": source_url,
        "retrieved_at_utc": http_metadata["retrieved_at_utc"],
        "pbf_size": pbf.stat().st_size,
        "pbf_sha256": sha256_file(pbf),
        "http_metadata": http_metadata,
        "pbf_header": pbf_header,
        "coverage_polygon_url": (COVERAGE_URL if specification is None else specification.source["coverage_polygon_url"]),
        "coverage_polygon_size": coverage_polygon.stat().st_size,
        "coverage_polygon_sha256": sha256_file(coverage_polygon),
        "coverage_polygon_http_metadata": coverage_http_metadata,
        "coverage_polygon_retrieved_at_utc": coverage_http_metadata.get("retrieved_at_utc"),
        "source_license": "OpenStreetMap data: ODbL 1.0; © OpenStreetMap contributors.",
    }
    if source_lock_path is not None:
        provenance["source_lock_sha256"] = source_lock_sha256
    source_identity = {
        "pbf_sha256": provenance["pbf_sha256"],
        "coverage_polygon_sha256": provenance["coverage_polygon_sha256"],
        "dem_manifest_sha256": sha256_file(dem_manifest),
        "scope_bbox": list(scope_bbox),
        "preparation_buffer_m": preparation_buffer_m,
        "radii_m": list(radii_m),
        "output_radius_m": output_radius_m,
        "crs": crs,
        "software": software,
        "preparation_revision": (PREPARATION_REVISION if specification is None
                                  else (GENERALIZED_POLICIES if scope == "canaries" else G2_POLICIES)["preparation_revision"]),
        "access_policy_revision": ACCESS_POLICY_REVISION,
        "distance_policy_revision": DISTANCE_POLICY_REVISION,
        "elevation_policy_revision": ELEVATION_POLICY_REVISION,
    }
    if specification is None:
        dataset_id = "tfe-f021a-" + hashlib.sha256(canonical_json_bytes(source_identity)).hexdigest()[:16]
    else:
        registry = GENERALIZED_PROFILE_REGISTRY[profile]
        source_identity.update({
            "scope": scope,
            "profile": profile,
            "assignment_exclusions": list(specification.assignment_exclusions),
            "assignment_core_sha256": hashlib.sha256(to_wkb(assignment_core)).hexdigest(),
            "prepared_domain_sha256": hashlib.sha256(to_wkb(domain)).hexdigest(),
            "provider_coverage_sha256": hashlib.sha256(to_wkb(projected)).hexdigest(),
        })
        if source_lock_sha256 is not None:
            source_identity["source_lock_sha256"] = source_lock_sha256
        dataset_id = registry["prefix"] + hashlib.sha256(canonical_json_bytes(source_identity)).hexdigest()[:16]
    if os.environ.get("TMPDIR"):
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    node_cache = None
    monitor_stop = threading.Event()
    high_water = {"temporary_bytes": 0, "index_bytes": 0, "node_cache_bytes": 0}
    monitor_thread = None
    rss_sampler = PeakRssSampler().start()
    try:
        index_path = stage / "index.sqlite"
        cache_directory = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
        fd, cache_name = tempfile.mkstemp(prefix="tfe-node-cache-", dir=cache_directory)
        os.close(fd)
        node_cache = Path(cache_name)
        shutil.copyfile(coverage_polygon, stage / "coverage.poly")
        def monitor_disk():
            while not monitor_stop.wait(0.25):
                index_bytes = index_path.stat().st_size if index_path.exists() else 0
                cache_bytes = 0
                if node_cache and node_cache.exists():
                    stat = node_cache.stat()
                    cache_bytes = getattr(stat, "st_blocks", 0) * 512 or stat.st_size
                staged_bytes = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
                high_water["index_bytes"] = max(high_water["index_bytes"], index_bytes)
                high_water["node_cache_bytes"] = max(high_water["node_cache_bytes"], cache_bytes)
                high_water["temporary_bytes"] = max(high_water["temporary_bytes"], staged_bytes + cache_bytes)
        monitor_thread = threading.Thread(target=monitor_disk, daemon=True)
        monitor_thread.start()
        stats = _extract_osm(pbf, index_path, node_cache, domain, transformer=transformer)
        monitor_stop.set()
        monitor_thread.join()
        connection = sqlite3.connect(index_path)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("New SQLite accessibility index failed integrity_check")
            features = connection.execute("SELECT count(*) FROM features").fetchone()[0]
            rtree = connection.execute("SELECT count(*) FROM feature_rtree").fetchone()[0]
            if features != rtree:
                raise ValueError("Spatial index row count does not match features table")
        finally:
            connection.close()
        polygon_bytes = (stage / "coverage.poly").stat().st_size
        polygon_hash = sha256_file(stage / "coverage.poly")
        index_size = index_path.stat().st_size
        index_hash = sha256_file(index_path)
        peak_rss_mib = rss_sampler.stop()
        manifest = {
            "schema": 1 if specification is None else 2,
            "status": "complete",
            "dataset_id": dataset_id,
            "scope": scope,
            "scope_bbox": list(scope_bbox),
            "preparation_buffer_m": preparation_buffer_m,
            "crs": crs,
            "geometry_storage": "zlib-compressed projected WKB",
            "domain_wkb_hex": to_wkb(domain).hex(),
            "coverage_wkb_hex": to_wkb(projected).hex(),
            "pbf_provenance": provenance,
            "dem": {
                "manifest_path": str(dem_manifest),
                "manifest_sha256": source_identity["dem_manifest_sha256"],
                "region": expected_dem_region,
                "product": "GLO-30",
                "version": "2024_1",
            },
            "policies": source_identity,
            "software": software,
            "class_completeness": stats["class_completeness"],
            "audit_completeness": {
                "restriction": "restriction:unlocated_restriction" not in stats["errors_by_class"],
            },
            "counts_by_class": stats["counts_by_class"],
            "errors_by_class": stats["errors_by_class"],
            "indexed_features": stats["indexed_features"],
            "metrics": {
                "preparation_seconds": time.perf_counter() - preparation_started,
                "peak_rss_mib": peak_rss_mib,
                "temporary_disk_high_water_bytes": high_water["temporary_bytes"],
                "staging_high_water_bytes": high_water["index_bytes"],
                "node_cache_high_water_bytes": high_water["node_cache_bytes"],
                "retained_source_bytes": pbf.stat().st_size + coverage_polygon.stat().st_size,
                "index_apparent_bytes": index_size,
                "index_allocated_bytes": getattr(index_path.stat(), "st_blocks", 0) * 512 or index_size,
            },
            "index": {"path": "index.sqlite", "size": index_size, "sha256": index_hash},
            "files": {
                "coverage_polygon": {"path": "coverage.poly", "size": polygon_bytes, "sha256": polygon_hash},
            },
            "license": LICENSE_INFO,
            "attribution": "© OpenStreetMap contributors",
            "coverage_note": "Completeness is relative to supported OSM objects in this extract and the prepared domain; it does not assert that all real-world features are mapped.",
        }
        if specification is not None:
            manifest["identity"] = source_identity
            manifest["profile"] = profile
            manifest["assignment_core_wkb_hex"] = to_wkb(assignment_core).hex()
            manifest["specification"] = {
                "profile": profile,
                "assignment_region": specification.assignment_region,
                "source_provider": specification.source["provider"],
                "metric_crs": specification.metric_crs,
                "public_search_radius_m": specification.public_search_radius_m,
                "supported_radii_m": list(specification.supported_radii_m),
                "max_projection_distortion_fraction": max_distortion,
                "accepted_projection_distortion_fraction": 0.005,
            }
            manifest["assignment_exclusions"] = list(specification.assignment_exclusions)
            if source_lock_path is not None:
                shutil.copyfile(source_lock_path, stage / "source-lock.json")
                manifest["files"]["source_lock"] = {
                    "path": "source-lock.json",
                    "size": (stage / "source-lock.json").stat().st_size,
                    "sha256": sha256_file(stage / "source-lock.json"),
                }
        (stage / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        validate_accessibility_manifest(stage / "manifest.json")
        stage.rename(output_dir)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        rss_sampler.stop()
        monitor_stop.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=2)
        if node_cache is not None:
            node_cache.unlink(missing_ok=True)


__all__ = [
    "prepare_accessibility", "validate_accessibility_manifest", "parse_geofabrik_poly",
    "accessibility_source_record", "write_accessibility_source_record",
    "sha256_file", "SOURCE_URL", "COVERAGE_URL", "SCOPE_BBOX",
    "PILOT_RADII_M", "PILOT_OUTPUT_RADIUS_M",
]
