"""Declarative geographic scopes for accessibility preparation and lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pyproj import CRS, Geod, Transformer
from shapely import segmentize, transform as shapely_transform
from shapely.geometry import Point

from .regions import build_region_ownership, load_regions


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
    source: dict[str, Any]
    dem_manifest: str
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
        if profile != "canary-islands-g1-v1":
            raise ValueError(f"Unknown generalized accessibility profile: {profile!r}")
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
        dem_manifest = raw.get("dem_manifest")
        if not isinstance(dem_manifest, str) or not dem_manifest:
            raise ValueError("Accessibility scope has no DEM/WBM manifest")
        bbox = tuple(float(value) for value in regions[region_name]["bbox"])
        exclusions = raw.get("assignment_exclusions", [])
        if not isinstance(exclusions, list) or any(not isinstance(value, str) or not value for value in exclusions):
            raise ValueError("Accessibility assignment exclusions must be a list of spot IDs")
        if len(set(exclusions)) != len(exclusions):
            raise ValueError("Accessibility assignment exclusions contain duplicates")
        return cls(scope, profile, region_name, crs, buffer_m, radius_m,
                   tuple(radii), tuple(exclusions), dict(source), dem_manifest, bbox)

    def assignment_core(self):
        """Build country ownership clipped to its existing publication bbox."""
        regions = load_regions()
        region = regions[self.assignment_region]
        ownership = build_region_ownership(region)
        geometries = [geometry for code, geometry in ownership.countries.items()
                      if code in {str(value).upper() for value in region["osm_country_code"]}]
        if not geometries:
            raise ValueError(f"No assignment ownership geometry for scope {self.name}")
        from shapely.ops import unary_union
        core = unary_union(geometries)
        if core.is_empty or not core.is_valid:
            raise ValueError(f"Invalid assignment core for scope {self.name}")
        return core

    def projected_geometries(self, provider_coverage):
        """Return densified assignment core, buffered domain, and provider coverage."""
        core = self.assignment_core()
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
