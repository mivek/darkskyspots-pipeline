"""regions.yaml loader and region resolver."""
import math
import re
from collections.abc import Mapping

import yaml

from .tile_id import tile_bounds, tile_id

REQUIRED_FIELDS = {"bbox", "equal_area_epsg", "admin_level", "osm_country_code"}


def load_regions(
    path: str = "regions.yaml",
    *,
    allow_legacy_geometry: bool = False,
    validate_partition: bool = False,
) -> dict[str, dict]:
    """Load regions.yaml and validate its geometry.

    Args:
        path: YAML registry path.
        allow_legacy_geometry: Allow non-integer numeric coordinates for preview
            workflows only; strict production loading must leave this false.
        validate_partition: Check that declared bboxes do not overlap in area.
            Set false only when inspecting legacy or intentionally overlapping
            preview data.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"regions.yaml must be a dict, got {type(data).__name__}")
    for name, region in data.items():
        _validate_region(name, region, allow_legacy_geometry=allow_legacy_geometry)
        # Accept the former scalar spelling while exposing one canonical list
        # to all callers.  This permits a gradual migration of local registries
        # without allowing ownership semantics to leak back into the pipeline.
        codes = region["osm_country_code"]
        if isinstance(codes, str):
            codes = [codes]
        if not isinstance(codes, (list, tuple)) or not codes:
            raise ValueError(f"Region {name!r}: osm_country_code must be a non-empty list")
        normalised = [str(code).upper() for code in codes]
        if any(not re.fullmatch(r"[A-Z]{2}", code) for code in normalised):
            raise ValueError(f"Region {name!r}: osm_country_code entries must be ISO alpha-2 codes")
        if len(set(normalised)) != len(normalised):
            raise ValueError(f"Region {name!r}: osm_country_code contains duplicates")
        region["osm_country_code"] = normalised
    if validate_partition:
        validate_bbox_partition(data)
    return data


def _validate_region(
    name: str, region: dict, *, allow_legacy_geometry: bool = False
) -> None:
    if not isinstance(region, dict):
        raise ValueError(f"Region {name!r} must be a mapping")
    missing = REQUIRED_FIELDS - set(region.keys())
    if missing:
        raise ValueError(f"Region {name!r} missing fields: {missing}")
    bbox = region["bbox"]
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"Region {name!r}: bbox must have 4 elements")
    values = []
    for index, value in enumerate(bbox):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"Region {name!r}: bbox coordinate {index} must be numeric"
            )
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(
                f"Region {name!r}: bbox coordinate {index} must be finite"
            )
        axis = "longitude" if index in (0, 2) else "latitude"
        lower, upper = (-180, 180) if axis == "longitude" else (-90, 90)
        if not lower <= numeric <= upper:
            raise ValueError(
                f"Region {name!r}: {axis} bbox coordinate {index} is outside "
                f"the tile domain [{lower}, {upper}]"
            )
        if not allow_legacy_geometry and not numeric.is_integer():
            raise ValueError(
                f"Region {name!r}: bbox coordinate {index} must be integer"
            )
        values.append(numeric)
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError(f"Region {name!r}: bbox coordinates must be ordered")


def validate_bbox_partition(regions: dict[str, dict]) -> None:
    """Reject positive-area intersections between declared region bboxes."""
    items = list(regions.items())
    for index, (left_name, left) in enumerate(items):
        left_bbox = left["bbox"]
        for right_name, right in items[index + 1 :]:
            right_bbox = right["bbox"]
            overlap_width = min(left_bbox[2], right_bbox[2]) - max(left_bbox[0], right_bbox[0])
            overlap_height = min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1])
            if overlap_width > 0 and overlap_height > 0:
                raise ValueError(f"Regions {left_name!r} and {right_name!r} overlap")


def tile_intersects_bbox(
    tile_id_str: str,
    bbox: tuple[float, float, float, float],
) -> bool:
    """Return whether a one-degree tile has strictly positive overlap with bbox."""
    lat_min, lon_min, lat_max, lon_max = tile_bounds(tile_id_str)
    bbox_lon_min, bbox_lat_min, bbox_lon_max, bbox_lat_max = bbox
    overlap_width = min(lon_max, bbox_lon_max) - max(lon_min, bbox_lon_min)
    overlap_height = min(lat_max, bbox_lat_max) - max(lat_min, bbox_lat_min)
    return overlap_width > 0 and overlap_height > 0


def owner_for_tile(tile_id_str: str, regions: dict[str, dict]) -> str | None:
    """Return the first declaration-order region intersecting a tile."""
    for name, region in regions.items():
        if tile_intersects_bbox(tile_id_str, tuple(region["bbox"])):
            return name
    return None


def owned_tile_ids(
    region_name: str,
    regions: dict[str, dict],
    tile_size_deg: float = 1.0,
) -> set[str]:
    """Return all one-degree tiles fully covered by a strict integer bbox."""
    if tile_size_deg != 1.0:
        raise ValueError("owned_tile_ids only supports a 1-degree tile size")
    bbox = regions[region_name]["bbox"]
    values = [float(value) for value in bbox]
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"Region {region_name!r}: bbox coordinates must be finite")
    if any(not value.is_integer() for value in values):
        raise ValueError(f"Region {region_name!r}: bbox coordinates must be integer")
    if values[0] >= values[2] or values[1] >= values[3]:
        raise ValueError(f"Region {region_name!r}: bbox coordinates must be ordered")
    lon_min, lat_min, lon_max, lat_max = (int(value) for value in values)
    return {
        tile_id(lat + 0.5, lon + 0.5)
        for lat in range(lat_min, lat_max)
        for lon in range(lon_min, lon_max)
    }


def _candidate_bbox(bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Return the integer tile envelope proposed for a legacy bbox."""
    lon_min, lat_min, lon_max, lat_max = bbox
    return (
        math.floor(lon_min),
        math.floor(lat_min),
        math.ceil(lon_max),
        math.ceil(lat_max),
    )


def _tiles_covered_by_bbox(bbox: tuple[float, float, float, float]) -> list[str]:
    """Return the complete, sorted one-degree tile set intersecting ``bbox``."""
    lon_min, lat_min, lon_max, lat_max = bbox
    return sorted(
        tile_id(latitude + 0.5, longitude + 0.5)
        for latitude in range(math.floor(lat_min), math.ceil(lat_max))
        for longitude in range(math.floor(lon_min), math.ceil(lon_max))
    )


def _bbox_overlap_pairs(regions: dict[str, dict]) -> list[tuple[str, str]]:
    """Return declaration-order pairs with a positive-area bbox intersection."""
    overlaps = []
    items = list(regions.items())
    for index, (left_name, left) in enumerate(items):
        left_bbox = left["bbox"]
        for right_name, right in items[index + 1 :]:
            right_bbox = right["bbox"]
            overlap_width = min(left_bbox[2], right_bbox[2]) - max(left_bbox[0], right_bbox[0])
            overlap_height = min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1])
            if overlap_width > 0 and overlap_height > 0:
                overlaps.append((left_name, right_name))
    return overlaps


def build_bbox_migration_preview(
    regions: dict[str, dict],
    published_tile_counts: Mapping[str, int],
    candidates: Mapping[str, tuple[float, float, float, float]] | None = None,
) -> dict:
    """Describe published ownership changes without mutating any input or file.

    The preview permits current legacy decimal and overlapping bboxes.  Omitted
    candidates are expanded to an integer tile envelope using floor/ceil bounds;
    explicit candidates must already satisfy strict publishable bbox geometry.
    Proposed overlaps remain visible in the report instead of being hidden.
    """
    candidates = candidates or {}
    for name, region in regions.items():
        _validate_region(name, region, allow_legacy_geometry=True)
    unknown_candidates = set(candidates) - set(regions)
    if unknown_candidates:
        unknown = ", ".join(sorted(unknown_candidates))
        raise ValueError(f"Unknown bbox candidate region(s): {unknown}")

    proposed_regions: dict[str, dict] = {}
    for name, region in regions.items():
        bbox = tuple(float(value) for value in region["bbox"])
        proposed_bbox = tuple(candidates.get(name, _candidate_bbox(bbox)))
        proposed_region = dict(region)
        proposed_region["bbox"] = list(proposed_bbox)
        _validate_region(name, proposed_region)
        proposed_regions[name] = proposed_region

    proposed: dict[str, dict] = {
        name: {
            "bbox": proposed_regions[name]["bbox"],
            "tiles": _tiles_covered_by_bbox(tuple(proposed_regions[name]["bbox"])),
            "gained": [],
            "lost": [],
        }
        for name in regions
    }
    transitions: dict[tuple[str | None, str | None], list[str]] = {}
    orphans: dict[str, int] = {}
    for tile_id_str in sorted(published_tile_counts):
        old_owner = owner_for_tile(tile_id_str, regions)
        new_owner = owner_for_tile(tile_id_str, proposed_regions)
        if new_owner is None:
            orphans[tile_id_str] = published_tile_counts[tile_id_str]
        if old_owner == new_owner:
            continue
        transitions.setdefault((old_owner, new_owner), []).append(tile_id_str)
        if old_owner is not None:
            proposed[old_owner]["lost"].append(tile_id_str)
        if new_owner is not None:
            proposed[new_owner]["gained"].append(tile_id_str)

    proposed["orphans"] = orphans
    return {
        "current": {
            name: {"bbox": list(region["bbox"])} for name, region in regions.items()
        },
        "proposed": proposed,
        "overlaps": _bbox_overlap_pairs(proposed_regions),
        "transitions": transitions,
    }


def format_bbox_migration_preview(preview: dict) -> str:
    """Format a migration preview for the read-only CLI report."""
    lines = ["BBox migration preview"]
    for name, current in preview["current"].items():
        proposed = preview["proposed"][name]
        lines.extend(
            [
                f"{name}:",
                f"  current bbox: {current['bbox']}",
                f"  proposed bbox: {proposed['bbox']}",
                f"  tiles: {', '.join(proposed['tiles']) or '(none)'}",
                f"  newly assigned: {', '.join(proposed['gained']) or '(none)'}",
                f"  ceases to own: {', '.join(proposed['lost']) or '(none)'}",
            ]
        )

    lines.append("Proposed overlaps:")
    lines.extend(
        f"  {left} <-> {right}" for left, right in preview["overlaps"]
    )
    if not preview["overlaps"]:
        lines.append("  (none)")

    lines.append("Published ownership transitions:")
    for (old_owner, new_owner), tile_ids in preview["transitions"].items():
        old_label = old_owner or "unowned"
        new_label = new_owner or "unowned"
        lines.append(f"  {old_label} -> {new_label}: {', '.join(tile_ids)}")
    if not preview["transitions"]:
        lines.append("  (none)")

    lines.append("Proposed orphans:")
    for tile_id_str, spot_count in preview["proposed"]["orphans"].items():
        lines.append(f"  {tile_id_str}: {spot_count} spots")
    if not preview["proposed"]["orphans"]:
        lines.append("  (none)")
    return "\n".join(lines)


def get_region(name: str, regions_path: str = "regions.yaml") -> dict:
    """Resolve a region name; raises KeyError with a helpful message if not found."""
    regions = load_regions(regions_path)
    if name not in regions:
        known = ", ".join(sorted(regions.keys()))
        raise KeyError(f"Unknown region {name!r}. Known: {known}")
    return regions[name]
