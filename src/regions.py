"""regions.yaml loader and region resolver."""
import math
import re

import yaml
from shapely.geometry import box
from shapely.ops import unary_union

from .geography import load_geography, validate_country_codes

REQUIRED_FIELDS = {"bbox", "equal_area_epsg", "admin_level", "osm_country_code"}


def load_regions(
    path: str = "regions.yaml",
    *,
    allow_legacy_geometry: bool = False,
    validate_partition: bool = True,
    geography=None,
) -> dict[str, dict]:
    """Load regions.yaml and validate its geometry.

    Args:
        path: YAML registry path.
        allow_legacy_geometry: Allow non-integer numeric coordinates for preview
            workflows only; strict production loading must leave this false.
        validate_partition: Check that publishable Natural Earth country
            geometries do not overlap in area. Set false only when inspecting
            legacy or intentionally overlapping preview data.
        geography: Optional loaded Natural Earth geography, primarily useful
            for tests and callers that already hold the dataset in memory.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"regions.yaml must be a dict, got {type(data).__name__}")
    country_owners: dict[str, str] = {}
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
        for code in normalised:
            previous = country_owners.get(code)
            if previous is not None:
                raise ValueError(
                    f"Country code {code} is configured in both regions "
                    f"{previous!r} and {name!r}"
                )
            country_owners[code] = name
        region["osm_country_code"] = normalised
    if validate_partition:
        validate_publishable_partition(data, geography=geography)
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


def validate_publishable_partition(regions: dict[str, dict], *, geography=None) -> None:
    """Reject positive-area intersections between publishable region extents.

    Region bboxes are working envelopes and may overlap.  Ownership is defined
    by the configured Natural Earth country geometries clipped to each bbox.
    A shared border or point has zero area and is intentionally allowed.
    """
    geo = geography or load_geography()
    validate_country_codes(
        [code for region in regions.values() for code in region["osm_country_code"]],
        geography=geo,
    )
    extents = {}
    for name, region in regions.items():
        codes = region["osm_country_code"]
        country_geometry = unary_union([geo.countries[code] for code in codes])
        extents[name] = country_geometry.intersection(box(*region["bbox"]))

    items = list(regions.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, _right in items[index + 1 :]:
            if extents[left_name].intersection(extents[right_name]).area > 0:
                raise ValueError(
                    f"Publishable extents for regions {left_name!r} and "
                    f"{right_name!r} overlap"
                )


def validate_bbox_partition(regions: dict[str, dict], *, geography=None) -> None:
    """Backward-compatible name for publishable partition validation.

    The old rectangle-only behavior is deliberately no longer available:
    bboxes are working envelopes and do not define ownership.
    """
    validate_publishable_partition(regions, geography=geography)


def get_region(name: str, regions_path: str = "regions.yaml") -> dict:
    """Resolve a region name; raises KeyError with a helpful message if not found."""
    regions = load_regions(regions_path)
    if name not in regions:
        known = ", ".join(sorted(regions.keys()))
        raise KeyError(f"Unknown region {name!r}. Known: {known}")
    return regions[name]
