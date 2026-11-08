"""Factual, optional accessibility evidence for mapped OSM features.

This module is runtime-safe: the preparation-only Pyosmium dependency is loaded
only by :mod:`src.accessibility_prepare`.
"""

from __future__ import annotations

import math
import json
from dataclasses import dataclass
from typing import Any, Mapping


ROAD_HIGHWAYS = frozenset(
    {
        "motorway", "trunk", "primary", "secondary", "tertiary",
        "motorway_link", "trunk_link", "primary_link", "secondary_link",
        "tertiary_link", "unclassified", "residential", "living_street",
        "service", "road",
    }
)
PATH_HIGHWAYS = frozenset({"path", "footway", "steps", "pedestrian", "bridleway"})
INACTIVE_VALUES = frozenset({"proposed", "construction", "abandoned", "disused"})
RECOGNIZED_ACCESS = frozenset(
    {
        "yes", "designated", "permissive", "no", "private", "destination",
        "customers", "delivery", "agricultural", "forestry", "permit",
    }
)
AFFIRMATIVE_ACCESS = frozenset(
    {"yes", "designated", "permissive", "destination", "customers", "delivery", "permit"}
)
SAC_SCALES = frozenset(
    {
        "hiking", "mountain_hiking", "demanding_mountain_hiking",
        "alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking",
    }
)
PUBLIC_CLASSES = ("road", "track", "path", "parking")
OSM_TYPE_ORDER = {"node": 0, "way": 1, "relation": 2}


def _clean_tags(tags: Mapping[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in tags.items() if value is not None}


def classify_feature(
    tags: Mapping[str, Any], osm_type: str, geometry_type: str
) -> tuple[str | None, str]:
    """Return ``(physical_class, inventory_state)`` for one mapped object.

    ``inventory_state`` is ``active``, ``audit_only``, ``inactive`` or
    ``unsupported``. Audit-only features never become public query candidates.
    """
    tags = _clean_tags(tags)
    highway = tags.get("highway", "").strip().lower()

    lifecycle = any(
        key in tags and str(tags[key]).strip().lower() not in {"no", "false", "0"}
        for key in ("construction", "proposed", "abandoned", "disused")
    ) or any(f"{name}:highway" in tags for name in INACTIVE_VALUES)
    if lifecycle or highway in INACTIVE_VALUES:
        return "lifecycle", "inactive"

    if highway in ROAD_HIGHWAYS:
        return "road", "active"
    if highway == "track":
        return "track", "active"
    if highway in PATH_HIGHWAYS:
        return "path", "active"
    if tags.get("amenity") == "parking" and osm_type in {"node", "way", "relation"}:
        if osm_type == "node" and geometry_type == "Point":
            return "parking", "active"
        if osm_type in {"way", "relation"} and geometry_type in {"Polygon", "MultiPolygon"}:
            return "parking", "active"
        return "parking", "unsupported"

    if highway == "cycleway":
        return "cycleway", "audit_only"
    if tags.get("barrier"):
        return "barrier", "audit_only"
    if tags.get("amenity") in {"parking_space", "parking_entrance"}:
        return "parking_detail", "audit_only"
    if tags.get("entrance") == "parking":
        return "parking_detail", "audit_only"
    if tags.get("area:highway"):
        return "access_area", "audit_only"
    if tags.get("type") in {"restriction", "route"} and (
        tags.get("type") == "restriction" or tags.get("restriction")
    ):
        return "restriction", "audit_only"
    return None, "unsupported"


def _mode_ancestry(mode: str) -> tuple[str, ...]:
    if mode == "foot":
        return ("foot", "access")
    if mode == "motorcar":
        return ("motorcar", "motor_vehicle", "vehicle", "access")
    raise ValueError(f"Unsupported access mode: {mode}")


def _parse_access_value(value: str) -> dict[str, Any]:
    parts = [part.strip().lower() for part in value.split(";") if part.strip()]
    if not parts:
        return {"state": "unknown", "reason": "unsupported_tag"}
    if any(part not in RECOGNIZED_ACCESS for part in parts):
        return {"state": "unknown", "reason": "unsupported_tag"}
    if len(set(parts)) > 1:
        return {"state": "conflicting"}
    return {"state": "tagged", "key": "", "value": parts[0]}


def resolve_access_with_audit(
    tags: Mapping[str, Any], mode: str, physical_class: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one mode's explicit OSM evidence and retain unevaluated details."""
    tags = _clean_tags(tags)
    ancestry = _mode_ancestry(mode)
    recognized_conditional_keys = {f"{ancestor}:conditional" for ancestor in ancestry}
    conditional = {
        key: value
        for key, value in sorted(tags.items())
        if any(key.startswith(f"{ancestor}:") for ancestor in ancestry)
        and key.endswith(":conditional")
    }
    unsupported_scoped = {
        key: value
        for key, value in sorted(tags.items())
        if any(key.startswith(f"{ancestor}:") for ancestor in ancestry)
        and key not in recognized_conditional_keys
    }

    evidence: dict[str, Any] | None = None
    evidence_key: str | None = None
    for key in ancestry:
        if key not in tags:
            continue
        parsed = _parse_access_value(tags[key])
        if parsed["state"] == "tagged":
            parsed["key"] = key
        evidence, evidence_key = parsed, key
        break

    if unsupported_scoped:
        evidence = {"state": "unknown", "reason": "unsupported_tag"}
    elif evidence is None:
        evidence = {"state": "unknown", "reason": "missing_tag"}

    if (
        mode == "motorcar"
        and physical_class == "path"
        and evidence_key == "access"
        and evidence.get("state") == "tagged"
        and evidence.get("value") in AFFIRMATIVE_ACCESS
    ):
        evidence = {"state": "unknown", "reason": "ambiguous_mode"}

    base = evidence
    if conditional:
        evidence = {"state": "conditional"}
        if base.get("reason") != "missing_tag":
            evidence["base"] = base

    audit = {
        "conditional": conditional,
        "unsupportedScoped": unsupported_scoped,
        "base": base,
    }
    return evidence, audit


def resolve_access_evidence(
    tags: Mapping[str, Any], mode: str, physical_class: str
) -> dict[str, Any]:
    """Return compact public evidence for ``foot`` or passenger ``motorcar``."""
    return resolve_access_with_audit(tags, mode, physical_class)[0]


def validate_accessibility_payload(payload: object) -> dict[str, Any]:
    """Validate and return a compact public accessibility payload.

    The strict key allowlists prevent source tags, geometry and audit candidates
    from accidentally leaking into a spot or cluster representative.
    """
    if not isinstance(payload, dict):
        raise ValueError("accessibility must be an object")
    if set(payload) != {"schema", "source", "radiusM", *PUBLIC_CLASSES}:
        raise ValueError("accessibility has unsupported or missing fields")
    if payload["schema"] != 1 or isinstance(payload["schema"], bool):
        raise ValueError("unsupported accessibility schema")
    if not isinstance(payload["source"], str) or not payload["source"].strip():
        raise ValueError("accessibility source must be a non-empty string")
    radius = payload["radiusM"]
    if isinstance(radius, bool) or not isinstance(radius, int) or radius <= 0:
        raise ValueError("accessibility radiusM must be a positive integer")

    for feature_class in PUBLIC_CLASSES:
        query = payload[feature_class]
        if not isinstance(query, dict) or "status" not in query:
            raise ValueError(f"accessibility {feature_class} must have a status")
        status = query["status"]
        if status == "not_found_within_radius":
            if set(query) != {"status"}:
                raise ValueError(f"empty {feature_class} query has extra fields")
            continue
        if status == "unknown":
            if set(query) != {"status", "reason"} or query["reason"] not in {
                "incomplete_extract", "incomplete_geometry"
            }:
                raise ValueError(f"unknown {feature_class} query requires a reason")
            continue
        if status != "found":
            raise ValueError(f"invalid {feature_class} query status")
        allowed = {
            "status", "osm", "distanceM", "elevationDeltaM", "elevationUnknownReason",
            "access", "highway", "service", "motorroad", "sacScale",
        }
        if not set(query) <= allowed or not {"osm", "distanceM", "elevationDeltaM", "access"} <= set(query):
            raise ValueError(f"invalid found {feature_class} query fields")
        if not isinstance(query["osm"], str) or "/" not in query["osm"]:
            raise ValueError(f"invalid {feature_class} OSM identifier")
        osm_type, osm_id = query["osm"].split("/", 1)
        if osm_type not in OSM_TYPE_ORDER or not osm_id.isdigit():
            raise ValueError(f"invalid {feature_class} OSM identifier")
        distance = query["distanceM"]
        if isinstance(distance, bool) or not isinstance(distance, int) or distance < 0 or distance > radius:
            raise ValueError(f"invalid {feature_class} distance")
        delta = query["elevationDeltaM"]
        if delta is not None and (isinstance(delta, bool) or not isinstance(delta, int)):
            raise ValueError(f"invalid {feature_class} elevation delta")
        if (delta is None) != ("elevationUnknownReason" in query):
            raise ValueError(f"{feature_class} elevation uncertainty is incomplete")
        access = query["access"]
        if not isinstance(access, dict) or set(access) != {"foot", "motorcar"}:
            raise ValueError(f"invalid {feature_class} access evidence")
        allowed_keys = {
            "foot": {"foot", "access"},
            "motorcar": {"motorcar", "motor_vehicle", "vehicle", "access"},
        }
        for mode, mode_evidence in access.items():
            if not isinstance(mode_evidence, dict) or mode_evidence.get("state") not in {
                "tagged", "unknown", "conflicting", "conditional"
            }:
                raise ValueError(f"invalid {feature_class} mode evidence")
            if mode_evidence["state"] == "tagged":
                if (set(mode_evidence) != {"state", "key", "value"}
                        or not isinstance(mode_evidence["key"], str)
                        or mode_evidence["key"] not in allowed_keys[mode]
                        or not isinstance(mode_evidence["value"], str)
                        or mode_evidence["value"] not in RECOGNIZED_ACCESS):
                    raise ValueError(f"invalid tagged {feature_class} access value")
            elif mode_evidence["state"] == "unknown":
                if set(mode_evidence) != {"state", "reason"} or mode_evidence["reason"] not in {
                    "missing_tag", "unsupported_tag", "ambiguous_mode"
                }:
                    raise ValueError(f"invalid unknown {feature_class} access reason")
            elif mode_evidence["state"] == "conflicting" and set(mode_evidence) != {"state"}:
                raise ValueError(f"invalid conflicting {feature_class} access evidence")
            elif mode_evidence["state"] == "conditional":
                if set(mode_evidence) not in ({"state"}, {"state", "base"}):
                    raise ValueError(f"invalid conditional {feature_class} access evidence")
                if "base" in mode_evidence:
                    base = mode_evidence["base"]
                    if not isinstance(base, dict) or base.get("state") not in {"tagged", "unknown", "conflicting"}:
                        raise ValueError(f"invalid conditional base for {feature_class}")
                    if base["state"] == "tagged" and (
                        set(base) != {"state", "key", "value"}
                        or not isinstance(base["key"], str)
                        or base["key"] not in allowed_keys[mode]
                        or not isinstance(base["value"], str)
                        or base["value"] not in RECOGNIZED_ACCESS
                    ):
                        raise ValueError(f"invalid conditional tagged base for {feature_class}")
                    if base["state"] == "unknown" and (
                        set(base) != {"state", "reason"}
                        or base["reason"] not in {"missing_tag", "unsupported_tag", "ambiguous_mode"}
                    ):
                        raise ValueError(f"invalid conditional unknown base for {feature_class}")
                    if base["state"] == "conflicting" and set(base) != {"state"}:
                        raise ValueError(f"invalid conditional conflicting base for {feature_class}")
    encoded_size = len(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8"))
    if encoded_size > 4096:
        raise ValueError(f"accessibility payload exceeds 4 KiB: {encoded_size} bytes")
    return payload


@dataclass
class AccessibilityEnricher:
    """Apply one immutable index and query policy to in-domain pipeline spots."""

    index: Any
    elevation_sampler: Any
    radius_m: int | None = None

    def __post_init__(self) -> None:
        if self.radius_m is None:
            self.radius_m = int(self.index.manifest.get("policies", {}).get("output_radius_m", 1000))
        if isinstance(self.radius_m, bool) or not isinstance(self.radius_m, int) or self.radius_m <= 0:
            raise ValueError("radius_m must be a positive integer")
        supported = self.index.manifest.get("policies", {}).get("radii_m")
        if supported is not None and self.radius_m not in supported:
            raise ValueError(f"Requested accessibility radius is not supported by source: {self.radius_m}")
        validate_dem_manifest = getattr(self.index, "validate_dem_manifest", None)
        manifest_path = getattr(self.elevation_sampler, "manifest_path", None)
        if validate_dem_manifest is not None and manifest_path is not None:
            validate_dem_manifest(manifest_path)

    def audit_assignments(self, spots: list[Mapping[str, Any]], *, enriched: bool = False) -> dict[str, int]:
        """Count assignment outcomes and reject undeclared scope gaps."""
        exclusions = set(self.index.manifest.get("assignment_exclusions", []))
        assigned = excluded = missing = enriched_count = 0
        missing_ids = []
        for spot in spots:
            spot_id = str(spot.get("id", ""))
            try:
                lat, lon = float(spot["lat"]), float(spot["lon"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Spot {spot_id!r} has invalid coordinates for accessibility assignment") from exc
            if self.index.supports_scope_point(lat, lon):
                assigned += 1
                if enriched and "accessibility" in spot:
                    enriched_count += 1
            elif spot_id in exclusions:
                excluded += 1
            else:
                missing += 1
                missing_ids.append(spot_id)
        if missing_ids:
            raise ValueError("Accessibility assignment has undeclared gaps: " + ", ".join(missing_ids[:20]))
        return {
            "assigned": assigned,
            "enriched": enriched_count,
            "excluded": excluded,
            "missing": missing,
        }

    def enrich_spot(self, spot: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(spot)
        if not self.index.supports_scope_point(float(spot["lat"]), float(spot["lon"])):
            return result
        queries = self.index.query_public(
            float(spot["lat"]), float(spot["lon"]), self.radius_m,
            reference_altitude=spot.get("altitude"), elevation_sampler=self.elevation_sampler,
        )
        payload = {
            "schema": 1,
            "source": self.index.source_id,
            "radiusM": self.radius_m,
            **queries,
        }
        validate_accessibility_payload(payload)
        result["accessibility"] = payload
        return result


__all__ = [
    "AccessibilityEnricher", "PUBLIC_CLASSES", "ROAD_HIGHWAYS", "PATH_HIGHWAYS",
    "RECOGNIZED_ACCESS", "SAC_SCALES", "classify_feature",
    "resolve_access_evidence", "resolve_access_with_audit",
    "validate_accessibility_payload",
]
