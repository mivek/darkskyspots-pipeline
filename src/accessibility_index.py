"""Immutable SQLite/RTree index and exact projected nearest-feature queries."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import Point
from shapely.ops import nearest_points

from .accessibility import (
    OSM_TYPE_ORDER,
    PUBLIC_CLASSES,
    SAC_SCALES,
    classify_feature,
    resolve_access_with_audit,
)

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32628", always_xy=True)
FROM_UTM = Transformer.from_crs("EPSG:32628", "EPSG:4326", always_xy=True)

SUPPORTED_POLICY_REVISIONS = {
    "preparation_revision": "f02-1a-prep-4",
    "access_policy_revision": "f02-1a-access-3",
    "distance_policy_revision": "f02-1a-distance-3",
    "elevation_policy_revision": "f02-1a-elevation-3",
}
PREPARATION_REVISION = SUPPORTED_POLICY_REVISIONS["preparation_revision"]
ACCESS_POLICY_REVISION = SUPPORTED_POLICY_REVISIONS["access_policy_revision"]
DISTANCE_POLICY_REVISION = SUPPORTED_POLICY_REVISIONS["distance_policy_revision"]
ELEVATION_POLICY_REVISION = SUPPORTED_POLICY_REVISIONS["elevation_policy_revision"]


def validate_policy_compatibility(policies: object) -> None:
    """Reject prepared datasets whose processing policies differ from runtime."""
    if not isinstance(policies, Mapping):
        raise ValueError("Accessibility manifest has no processing policy revisions")
    for name, expected in SUPPORTED_POLICY_REVISIONS.items():
        actual = policies.get(name)
        if actual != expected:
            raise ValueError(
                f"Incompatible accessibility policy {name}: expected {expected!r}, got {actual!r}"
            )


def _project(lon: float, lat: float) -> Point:
    x, y = TO_UTM.transform(lon, lat)
    return Point(x, y)


def _identifier_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        OSM_TYPE_ORDER.get(record["osm_type"], 99),
        int(record["osm_id"]),
        record["nearestPointM"][0],
        record["nearestPointM"][1],
    )


def _non_ground_surface_reason(tags: dict[str, Any]) -> str | None:
    """Return a reason when the mapped feature point is not DEM ground level."""
    false_values = {"", "no", "false", "0"}
    if any(str(tags.get(key, "")).strip().lower() not in false_values
           for key in ("bridge", "tunnel")):
        return "surface_not_ground"

    for key in ("layer", "level"):
        if key not in tags:
            continue
        values = [part.strip() for part in str(tags[key]).split(";")]
        try:
            levels = [float(value) for value in values if value]
        except ValueError:
            return "surface_not_ground"
        if not levels or any(not math.isfinite(value) or value != 0 for value in levels):
            return "surface_not_ground"

    parking = str(tags.get("parking", "")).strip().lower().replace("_", "-")
    location = str(tags.get("location", "")).strip().lower()
    if parking == "underground" or location == "underground":
        return "surface_not_ground"
    if parking in {"rooftop", "multi-storey", "multistorey", "multi-level"}:
        return "surface_not_ground"
    return None


def _iter_components(geometry):
    """Yield simple components in a stable geometric order."""
    if geometry.geom_type == "GeometryCollection" or geometry.geom_type.startswith("Multi"):
        parts = list(geometry.geoms)
        for part in sorted(parts, key=lambda item: item.wkb_hex):
            yield from _iter_components(part)
    elif geometry.geom_type == "LineString":
        coords = list(geometry.coords)
        segments = []
        for left, right in zip(coords, coords[1:]):
            a, b = tuple(left[:2]), tuple(right[:2])
            key = (a, b) if a <= b else (b, a)
            segments.append((key, a, b))
        for _, a, b in sorted(segments):
            from shapely.geometry import LineString
            yield LineString([a, b])
    else:
        yield geometry


def _line_segments(line):
    """Yield non-degenerate segments without depending on ring direction/order."""
    coordinates = [tuple(coordinate[:2]) for coordinate in line.coords]
    segments = []
    for left, right in zip(coordinates, coordinates[1:]):
        if left == right:
            continue
        endpoints = (left, right) if left <= right else (right, left)
        segments.append(endpoints)
    for left, right in sorted(set(segments)):
        from shapely.geometry import LineString
        yield LineString([left, right])


def _geometry_segments(geometry):
    if geometry.geom_type == "Polygon":
        for ring in (geometry.exterior, *geometry.interiors):
            yield from _line_segments(ring)
    elif geometry.geom_type in {"LineString", "LinearRing"}:
        yield from _line_segments(geometry)
    elif geometry.geom_type == "Point":
        yield geometry
    elif geometry.geom_type == "GeometryCollection" or geometry.geom_type.startswith("Multi"):
        for component in geometry.geoms:
            yield from _geometry_segments(component)


def deterministic_nearest(reference: Point, geometry) -> tuple[float, Point]:
    """Return exact distance and canonical nearest point for lines and areas.

    Equal-distance boundary choices use projected ``(x, y)`` ascending order.
    Polygon interiors return the query point itself; polygon holes are treated
    as boundaries and remain eligible nearest points.
    """
    candidates: list[tuple[float, float, float]] = []
    if geometry.geom_type == "Point":
        return reference.distance(geometry), geometry
    if geometry.geom_type in {"Polygon", "MultiPolygon"} and geometry.covers(reference):
        return 0.0, Point(reference.x, reference.y)
    for segment in _geometry_segments(geometry):
        point_on_reference, point_on_feature = nearest_points(reference, segment)
        candidates.append(
            (
                point_on_reference.distance(point_on_feature),
                float(point_on_feature.x),
                float(point_on_feature.y),
            )
        )
    if not candidates:
        return math.inf, reference
    distance, x, y = min(candidates)
    return distance, Point(x, y)


class AccessibilityIndex:
    """Read-only view over a checksummed, immutable accessibility dataset."""

    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path).resolve()
        self.root = self.manifest_path.parent
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema") != 1 or self.manifest.get("status") != "complete":
            raise ValueError("Invalid or incomplete accessibility dataset manifest")
        validate_policy_compatibility(self.manifest.get("policies"))
        self.source_id = self.manifest.get("dataset_id")
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("Accessibility manifest has no dataset id")
        index_info = self.manifest.get("index")
        if not isinstance(index_info, dict) or not isinstance(index_info.get("path"), str):
            raise ValueError("Accessibility manifest has no index file")
        self.index_path = (self.root / index_info["path"]).resolve()
        if not self.index_path.is_relative_to(self.root) or not self.index_path.is_file():
            raise ValueError("Accessibility index path is invalid or missing")
        if self.index_path.stat().st_size != index_info.get("size"):
            raise ValueError("Accessibility index size mismatch")
        if self._sha256(self.index_path) != index_info.get("sha256"):
            raise ValueError("Accessibility index checksum mismatch")
        for name, info in self.manifest.get("files", {}).items():
            packaged = (self.root / info["path"]).resolve()
            if not packaged.is_relative_to(self.root) or not packaged.is_file():
                raise ValueError(f"Accessibility package asset missing: {name}")
            if packaged.stat().st_size != info["size"] or self._sha256(packaged) != info["sha256"]:
                raise ValueError(f"Accessibility package asset checksum mismatch: {name}")
        identity = {
            key: self.manifest["policies"][key]
            for key in (
                "pbf_sha256", "coverage_polygon_sha256", "dem_manifest_sha256",
                "scope_bbox", "preparation_buffer_m", "radii_m", "output_radius_m", "software",
                "crs", "preparation_revision",
                "access_policy_revision", "distance_policy_revision", "elevation_policy_revision",
            )
        }
        expected_source = "tfe-f021a-" + hashlib.sha256(
            (json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest()[:16]
        if expected_source != self.source_id:
            raise ValueError("Accessibility dataset identity does not match its source and policy manifest")
        self._coverage = from_wkb(bytes.fromhex(self.manifest["coverage_wkb_hex"]))
        self._domain = from_wkb(bytes.fromhex(self.manifest["domain_wkb_hex"]))
        uri = f"file:{quote(str(self.index_path))}?mode=ro&immutable=1"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        result = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            self.connection.close()
            raise ValueError(f"Accessibility SQLite integrity check failed: {result}")

    def close(self) -> None:
        self.connection.close()

    def validate_dem_manifest(self, manifest_path: str | Path) -> str:
        """Require the runtime DEM manifest to match the immutable source ID."""
        path = Path(manifest_path).resolve()
        if not path.is_file():
            raise ValueError(f"DEM manifest is missing: {path}")
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        cache = getattr(self, "_validated_dem_manifests", None)
        if cache is None:
            cache = self._validated_dem_manifests = {}
        cached = cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        actual = self._sha256(path)
        expected = self.manifest.get("policies", {}).get("dem_manifest_sha256")
        if not isinstance(expected, str) or actual != expected:
            raise ValueError(
                f"DEM manifest identity mismatch: expected {expected!r}, got {actual}"
            )
        cache[path] = (signature, actual)
        return actual

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def supports_scope_point(self, lat: float, lon: float) -> bool:
        """Whether the reference coordinate belongs to the Tenerife pilot scope."""
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        return bool(self.manifest["scope_bbox"][0] <= lon <= self.manifest["scope_bbox"][2]
                    and self.manifest["scope_bbox"][1] <= lat <= self.manifest["scope_bbox"][3])

    def supports_point(self, lat: float, lon: float, radius_m: int) -> bool:
        """Compatibility alias for the runtime scope test (not a completeness test)."""
        return self.supports_scope_point(lat, lon)

    def _coverage_reason(self, point: Point, radius_m: int) -> str | None:
        # Shapely's regular buffer is inscribed. Slightly enlarge its radius so
        # this polygon conservatively contains the true search disk.
        circumscribed_radius = float(radius_m) / math.cos(math.pi / (4 * 64))
        search_disk = point.buffer(circumscribed_radius, quad_segs=64)
        if not self._domain.covers(search_disk):
            return "incomplete_extract"
        if not self._coverage.covers(search_disk):
            return "incomplete_extract"
        return None

    def _features_in_square(self, point: Point, radius_m: int, *, feature_class: str | None = None,
                            audit_only: bool | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT f.* FROM feature_rtree r JOIN features f ON f.rowid=r.id "
            "WHERE r.minx<=? AND r.maxx>=? AND r.miny<=? AND r.maxy>=?"
        )
        args: list[Any] = [point.x + radius_m, point.x - radius_m, point.y + radius_m, point.y - radius_m]
        if feature_class is not None:
            sql += " AND f.feature_class=?"
            args.append(feature_class)
        if audit_only is not None:
            sql += " AND f.inventory_state=?"
            args.append("audit_only" if audit_only else "active")
        sql += " ORDER BY f.osm_type, CAST(f.osm_id AS INTEGER)"
        return self.connection.execute(sql, args).fetchall()

    @staticmethod
    def _public_candidate(row: sqlite3.Row, distance: float, nearest: Point) -> dict[str, Any]:
        tags = json.loads(row["tags_json"])
        physical_class = row["feature_class"]
        foot, foot_audit = resolve_access_with_audit(tags, "foot", physical_class)
        motorcar, car_audit = resolve_access_with_audit(tags, "motorcar", physical_class)
        lon, lat = FROM_UTM.transform(nearest.x, nearest.y)
        result = {
            "osm_type": row["osm_type"],
            "osm_id": row["osm_id"],
            "osm": f"{row['osm_type']}/{row['osm_id']}",
            "distanceMExact": distance,
            "distanceM": math.floor(distance + 0.5),
            "nearestPointM": [float(nearest.x), float(nearest.y)],
            "nearestPoint": [float(lon), float(lat)],
            "access": {"foot": foot, "motorcar": motorcar},
            "accessAudit": {"foot": foot_audit, "motorcar": car_audit},
            "tags": tags,
            "version": row["version"],
            "timestamp": row["timestamp"],
            "quality": json.loads(row["quality_json"]),
        }
        for source_key, target_key in (("highway", "highway"), ("service", "service"),
                                       ("motorroad", "motorroad")):
            if source_key in tags:
                result[target_key] = tags[source_key]
        if tags.get("sac_scale") in SAC_SCALES:
            result["sacScale"] = tags["sac_scale"]
        return result

    def _candidate_records(self, lat: float, lon: float, radius_m: int,
                           feature_class: str | None = None, *, audit_only: bool | None = False):
        point = _project(lon, lat)
        rows = self._features_in_square(point, radius_m, feature_class=feature_class, audit_only=audit_only)
        records = []
        geometry_reads = 0
        for row in rows:
            quality = json.loads(row["quality_json"])
            if not quality.get("geometry_complete", False) or quality.get("geometry_valid") is False:
                continue
            geometry = from_wkb(zlib.decompress(row["geometry_wkb"]))
            geometry_reads += 1
            distance, nearest = deterministic_nearest(point, geometry)
            if distance <= radius_m:
                records.append(self._public_candidate(row, distance, nearest))
        records.sort(key=lambda record: (record["distanceMExact"], *_identifier_key(record)))
        return records, len(rows), geometry_reads

    def query_detailed(self, lat: float, lon: float, radius_m: int) -> dict[str, Any]:
        """Query all classes and return machine-auditable candidates and counters."""
        if isinstance(radius_m, bool) or not isinstance(radius_m, int) or radius_m <= 0:
            raise ValueError("radius_m must be a positive integer")
        started = time.perf_counter()
        point = _project(lon, lat)
        coverage_reason = self._coverage_reason(point, radius_m)
        by_class: dict[str, Any] = {}
        total_rtree = total_geometry_reads = 0
        for feature_class in PUBLIC_CLASSES:
            candidates, rtree_count, geometry_reads = self._candidate_records(
                lat, lon, radius_m, feature_class, audit_only=False
            )
            total_rtree += rtree_count
            total_geometry_reads += geometry_reads
            is_complete = bool(self.manifest.get("class_completeness", {}).get(feature_class, False))
            if coverage_reason:
                status, reason = "unknown", coverage_reason
            elif not is_complete:
                status, reason = "unknown", "incomplete_geometry"
            elif candidates:
                status, reason = "found", None
            else:
                status, reason = "not_found_within_radius", None
            selected = candidates[0] if status == "found" else None
            by_class[feature_class] = {
                "status": status,
                **({"reason": reason} if reason else {}),
                "selected": selected,
                "nearestCandidate": candidates[0] if candidates else None,
                "candidateCount": len(candidates),
                "rtreeCandidates": rtree_count,
                "geometryReads": geometry_reads,
                "candidates": candidates,
            }

        restrictions, restriction_rows, restriction_reads = self._candidate_records(
            lat, lon, radius_m, "restriction", audit_only=True
        )
        total_rtree += restriction_rows
        total_geometry_reads += restriction_reads
        return {
            "location": {"lat": lat, "lon": lon},
            "radiusM": radius_m,
            "coverageStatus": "complete" if coverage_reason is None else "unknown",
            "coverageReason": coverage_reason,
            "classes": by_class,
            "nearbyRestrictions": restrictions,
            "nearbyRestrictionsComplete": bool(
                self.manifest.get("audit_completeness", {}).get(
                    "restriction",
                    "restriction:unlocated_restriction" not in self.manifest.get("errors_by_class", {}),
                )
            ),
            "metrics": {
                "rtreeCandidates": total_rtree,
                "geometryReads": total_geometry_reads,
                "querySeconds": time.perf_counter() - started,
            },
        }

    def _elevation_delta(self, candidate: dict[str, Any], reference_altitude: Any,
                         elevation_sampler: Any) -> tuple[int | None, str | None, dict[str, Any]]:
        tags = candidate["tags"]
        if elevation_sampler is None:
            return None, "dem_unavailable", {"featurePoint": candidate["nearestPoint"], "reason": "dem_unavailable"}
        manifest_path = getattr(elevation_sampler, "manifest_path", None)
        if manifest_path is None:
            raise ValueError("Elevation sampler does not expose its DEM manifest path")
        self.validate_dem_manifest(manifest_path)
        non_ground_reason = _non_ground_surface_reason(tags)
        if non_ground_reason:
            return None, non_ground_reason, {
                "featurePoint": candidate["nearestPoint"],
                "reason": non_ground_reason,
                "surfaceIndicators": {
                    key: tags[key]
                    for key in ("bridge", "tunnel", "layer", "level", "amenity", "parking", "location")
                    if key in tags
                },
            }
        feature_lat, feature_lon = candidate["nearestPoint"][1], candidate["nearestPoint"][0]
        sample = elevation_sampler.sample_detailed(feature_lat, feature_lon)
        audit: dict[str, Any] = {
            "featurePoint": candidate["nearestPoint"],
            "featureSample": sample,
        }
        reason = sample["reason"]
        if reason:
            return None, reason, audit
        if reference_altitude is None:
            return None, "unknown_reference_altitude", audit
        reference_lat, reference_lon = candidate.get("referencePoint", (None, None))
        if reference_lat is None:
            # The public candidate is independent of its query origin; the caller
            # adds the reference sample below when constructing a query.
            return None, "unknown_reference_altitude", audit
        reference_sample = elevation_sampler.sample_detailed(reference_lat, reference_lon)
        audit["referenceSample"] = reference_sample
        if reference_sample["reason"]:
            return None, reference_sample["reason"], audit
        sampled_reference = float(reference_sample["elevationM"])
        if isinstance(reference_altitude, int) and not isinstance(reference_altitude, bool):
            reference_matches = reference_altitude == int(round(sampled_reference))
            comparison = "published_integer_vs_rounded_dem"
        else:
            try:
                preserved_reference = float(reference_altitude)
            except (TypeError, ValueError, OverflowError):
                reference_matches = False
            else:
                reference_matches = math.isfinite(preserved_reference) and math.isclose(
                    preserved_reference, sampled_reference, rel_tol=0.0, abs_tol=1e-7
                )
            comparison = "native_fractional_vs_dem"
        audit["referenceComparison"] = {
            "policy": comparison,
            "preservedAltitudeM": reference_altitude,
            "sampledElevationM": sampled_reference,
        }
        if not reference_matches:
            return None, "spot_dem_mismatch", audit
        # Keep the published spot altitude as the reference term after using
        # the sampled value only to verify that the frozen reference is sound.
        delta = float(reference_altitude) - sample["elevationM"]
        audit["elevationDeltaMExact"] = delta
        return math.floor(delta + 0.5) if delta >= 0 else math.ceil(delta - 0.5), None, audit

    def elevation_delta_for_candidate(self, candidate: dict[str, Any], *,
                                      reference_lat: float, reference_lon: float,
                                      reference_altitude: Any, elevation_sampler: Any):
        candidate = dict(candidate)
        candidate["referencePoint"] = (reference_lat, reference_lon)
        return self._elevation_delta(candidate, reference_altitude, elevation_sampler)

    def query_public(self, lat: float, lon: float, radius_m: int, *,
                     reference_altitude: Any = None, elevation_sampler: Any = None) -> dict[str, Any]:
        if elevation_sampler is not None:
            self.validate_dem_manifest(elevation_sampler.manifest_path)
        detailed = self.query_detailed(lat, lon, radius_m)
        return self.public_from_detailed(
            detailed, reference_altitude=reference_altitude,
            elevation_sampler=elevation_sampler,
        )

    def public_from_detailed(self, detailed: dict[str, Any], *,
                             reference_altitude: Any = None,
                             elevation_sampler: Any = None) -> dict[str, Any]:
        """Project a detailed audit query to the compact public schema."""
        if elevation_sampler is not None:
            self.validate_dem_manifest(elevation_sampler.manifest_path)
        lat = float(detailed["location"]["lat"])
        lon = float(detailed["location"]["lon"])
        result: dict[str, Any] = {}
        for feature_class, query in detailed["classes"].items():
            if query["status"] != "found":
                result[feature_class] = {"status": query["status"], **({"reason": query["reason"]} if "reason" in query else {})}
                continue
            candidate = dict(query["selected"])
            candidate["referencePoint"] = (lat, lon)
            delta, unknown_reason, _audit = self._elevation_delta(candidate, reference_altitude, elevation_sampler)
            public: dict[str, Any] = {
                "status": "found",
                "osm": candidate["osm"],
                "distanceM": candidate["distanceM"],
                "elevationDeltaM": delta,
                "access": candidate["access"],
            }
            if unknown_reason:
                public["elevationUnknownReason"] = unknown_reason
            for key in ("highway", "service", "motorroad", "sacScale"):
                if key in candidate:
                    public[key] = candidate[key]
            result[feature_class] = public
        return result


__all__ = ["AccessibilityIndex", "deterministic_nearest"]
