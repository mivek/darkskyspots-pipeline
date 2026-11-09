"""Declarative geographic scopes for accessibility preparation and lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml
from pyproj import CRS, Geod, Transformer
from shapely import segmentize, transform as shapely_transform
from shapely.geometry import Point

from .regions import build_region_ownership, load_regions


SUPPORTED_SCOPE_PROFILES = {
    "canaries": {"profile": "canary-islands-g1-v1", "crs": "EPSG:32628", "countries": ("ES",),
                 "dem_region": "canaries", "dem_policy": None},
    "great_britain": {"profile": "great-britain-g2-v1", "crs": "EPSG:32630", "countries": ("GB",),
                      "dem_region": "uk_ireland-accessibility-g2",
                      "dem_policy": "uk_ireland_accessibility_g2"},
    "ireland_northern_ireland": {
        "profile": "ireland-northern-ireland-g2-v1",
        "crs": "EPSG:32629",
        "countries": ("GB", "IE"),
        "dem_region": "uk_ireland-accessibility-g2",
        "dem_policy": "uk_ireland_accessibility_g2",
    },
}


@dataclass(frozen=True)
class GeographicSpecification:
    name: str
    profile: str
    assignment_region: str
    metric_crs: str
    preparation_buffer_m: int
    public_search_radius_m: int
    supported_radii_m: tuple[int, ...]
    assignment_exclusions: tuple[str, ...]
    assignment_countries: tuple[str, ...]
    source: dict[str, Any]
    dem_manifest: str
    dem_manifest_policy: dict[str, Any] | None
    bbox: tuple[float, float, float, float]

    @classmethod
    def load(cls, path: str | Path, scope: str) -> "GeographicSpecification":
        config_path = Path(path).resolve()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ValueError("Unsupported accessibility geographic specification")
        scopes = data.get("scopes")
        raw = scopes.get(scope) if isinstance(scopes, dict) else None
        if not isinstance(raw, dict):
            raise ValueError(f"Accessibility scope is not configured: {scope}")
        profile = raw.get("profile")
        supported = SUPPORTED_SCOPE_PROFILES.get(scope)
        if supported is None or profile != supported["profile"]:
            raise ValueError(f"Unknown generalized accessibility profile for {scope}: {profile!r}")
        region_name = raw.get("assignment_region")
        regions = load_regions()
        if region_name not in regions:
            raise ValueError(f"Unknown accessibility assignment region: {region_name!r}")
        crs = str(raw.get("metric_crs", ""))
        try:
            parsed_crs = CRS.from_user_input(crs)
        except Exception as exc:
            raise ValueError(f"Invalid metric CRS: {crs!r}") from exc
        if not parsed_crs.is_projected:
            raise ValueError("Accessibility metric CRS must be projected")
        if parsed_crs.to_string() != supported["crs"]:
            raise ValueError(
                "Accessibility CRS contradicts the profile's canonical identity: "
                f"profile {profile!r} requires {supported['crs']}, got {parsed_crs.to_string()}"
            )
        assignment_countries = raw.get("assignment_countries", list(supported["countries"]))
        if (not isinstance(assignment_countries, list)
                or tuple(str(value).upper() for value in assignment_countries) != supported["countries"]):
            raise ValueError(f"Profile {profile!r} has an invalid assignment country set")
        buffer_m = raw.get("preparation_buffer_m")
        radius_m = raw.get("public_search_radius_m")
        radii = raw.get("supported_radii_m")
        if (isinstance(buffer_m, bool) or not isinstance(buffer_m, int) or buffer_m <= 0
                or isinstance(radius_m, bool) or not isinstance(radius_m, int) or radius_m <= 0
                or not isinstance(radii, list) or not radii
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in radii)
                or radius_m not in radii or len(set(radii)) != len(radii)):
            raise ValueError("Invalid accessibility buffer or supported radii")
        source = raw.get("source")
        if not isinstance(source, dict) or not all(isinstance(source.get(key), str) and source[key]
                                                   for key in ("provider", "pbf_path", "source_url",
                                                               "coverage_polygon_path", "coverage_polygon_url")):
            raise ValueError("Accessibility scope has incomplete pinned source configuration")
        if scope != "canaries" and (
            not isinstance(source.get("source_lock_path"), str) or not source["source_lock_path"]
            or not isinstance(source.get("checksum_sidecar_path"), str)
            or not source["checksum_sidecar_path"]
        ):
            raise ValueError("G2 accessibility scope has no immutable source-lock path")
        dem_manifest = raw.get("dem_manifest")
        if not isinstance(dem_manifest, str) or not dem_manifest:
            raise ValueError("Accessibility scope has no DEM/WBM manifest")
        configured_policy = supported["dem_policy"]
        policy_name = raw.get("dem_manifest_policy")
        if policy_name != configured_policy:
            raise ValueError(
                f"Accessibility scope {scope} has an unknown or contradictory DEM manifest policy: "
                f"{policy_name!r}"
            )
        policies = data.get("dem_manifest_policies", {})
        dem_policy = None
        if configured_policy is not None:
            dem_policy = policies.get(configured_policy) if isinstance(policies, dict) else None
            if not isinstance(dem_policy, dict):
                raise ValueError(f"Accessibility scope {scope} has no configured DEM manifest policy")
            expected_manifest = f"data/elevation/manifests/{supported['dem_region']}.json"
            if (dem_manifest != expected_manifest or dem_policy.get("region") != supported["dem_region"]
                    or dem_policy.get("bbox") != [-11, 49, 2, 56]
                    or dem_policy.get("base_region") != "uk_ireland"
                    or dem_policy.get("base_bbox") != [-11, 49, 2, 55]
                    or dem_policy.get("expected_base_pairs") != 56
                    or not isinstance(dem_policy.get("known_nonterrestrial_cells"), list)):
                raise ValueError(f"Accessibility scope {scope} DEM manifest contradicts its validated profile")
            if (dem_policy.get("manifest") != dem_manifest
                    or dem_policy.get("product") != "GLO-30"
                    or dem_policy.get("version") != "2024_1"
                    or not isinstance(dem_policy.get("base_manifest"), str)
                    or not isinstance(dem_policy.get("base_manifest_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", dem_policy["base_manifest_sha256"])
                    or not isinstance(dem_policy.get("required_additional_cells"), list)):
                raise ValueError(f"Accessibility scope {scope} has an invalid G2 DEM manifest policy")
            cells = dem_policy["required_additional_cells"]
            if (not cells or any(not isinstance(cell, list) or len(cell) != 2
                                 or any(type(value) is not int for value in cell) for cell in cells)
                    or len({tuple(cell) for cell in cells}) != len(cells)):
                raise ValueError(f"Accessibility scope {scope} has invalid additional DEM cells")
            dem_policy = dict(dem_policy)
            base_manifest = Path(dem_policy["base_manifest"])
            if not base_manifest.is_absolute():
                base_manifest = config_path.parent.parent / base_manifest
            dem_policy["base_manifest_path"] = str(base_manifest.resolve())
        bbox = tuple(float(value) for value in regions[region_name]["bbox"])
        exclusions = raw.get("assignment_exclusions", [])
        if not isinstance(exclusions, list) or any(not isinstance(value, str) or not value for value in exclusions):
            raise ValueError("Accessibility assignment exclusions must be a list of spot IDs")
        if len(set(exclusions)) != len(exclusions):
            raise ValueError("Accessibility assignment exclusions contain duplicates")
        return cls(scope, profile, region_name, crs, buffer_m, radius_m,
                   tuple(radii), tuple(exclusions), tuple(assignment_countries),
                   dict(source), dem_manifest, dict(dem_policy) if dem_policy is not None else None, bbox)

    def assignment_core(self, provider_coverage=None):
        """Build canonical profile ownership, clipping G2 cores to provider coverage."""
        regions = load_regions()
        region = regions[self.assignment_region]
        ownership = build_region_ownership(region)
        geometries = [geometry for code, geometry in ownership.countries.items()
                      if code in self.assignment_countries]
        if not geometries:
            raise ValueError(f"No assignment ownership geometry for scope {self.name}")
        from shapely.ops import unary_union
        core = unary_union(geometries)
        # G1's canonical assignment geometry predates provider clipping. Keep
        # its exact WKB construction; clipping is part of the G2 profiles only.
        if provider_coverage is not None and self.profile != "canary-islands-g1-v1":
            core = core.intersection(provider_coverage)
        if core.is_empty or not core.is_valid:
            raise ValueError(f"Invalid assignment core for scope {self.name}")
        return core

    def projected_geometries(self, provider_coverage):
        """Return projected assignment core, buffered domain, and provider coverage."""
        core = self.assignment_core(provider_coverage)
        # Densify geographic edges before projecting so long Natural Earth
        # segments follow their geographic path rather than a chord in metres.
        dense_core = segmentize(core, 0.02)
        forward = Transformer.from_crs("EPSG:4326", self.metric_crs, always_xy=True)
        inverse = Transformer.from_crs(self.metric_crs, "EPSG:4326", always_xy=True)
        projected_core = shapely_transform(dense_core, forward.transform, interleaved=False)
        domain = projected_core.buffer(self.preparation_buffer_m)
        dense_coverage = segmentize(provider_coverage, 0.02)
        coverage = shapely_transform(dense_coverage, forward.transform, interleaved=False)
        max_distortion = self.validate_distortion(core, forward)
        return projected_core, domain, coverage, forward, inverse, max_distortion

    def validate_distortion(self, core, forward: Transformer) -> float:
        """Check projected distances against geodesic controls (max 0.5%)."""
        dense = segmentize(core, 0.05)
        geod = Geod(ellps="WGS84")
        max_distortion = 0.0
        boundaries = [dense.boundary] if not hasattr(dense.boundary, "geoms") else list(dense.boundary.geoms)
        for line in boundaries:
            coords = list(line.coords)
            for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
                geodesic_m = geod.inv(lon1, lat1, lon2, lat2)[2]
                if geodesic_m <= 0:
                    continue
                x1, y1 = forward.transform(lon1, lat1)
                x2, y2 = forward.transform(lon2, lat2)
                projected_m = Point(x1, y1).distance(Point(x2, y2))
                distortion = abs(projected_m / geodesic_m - 1.0)
                max_distortion = max(max_distortion, distortion)
        if max_distortion > 0.005:
            raise ValueError(f"Metric CRS distortion {max_distortion:.4%} exceeds 0.5%")
        return max_distortion


__all__ = ["GeographicSpecification"]
