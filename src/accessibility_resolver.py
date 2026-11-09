"""Validated multi-index assignment and enrichment for geographic accessibility scopes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from pyproj import Transformer
from shapely import to_wkb, transform as shapely_transform
from shapely.geometry import Point

from .accessibility import AccessibilityEnricher
from .accessibility_index import AccessibilityIndex, GENERALIZED_PROFILE_REGISTRY
from .accessibility_prepare import parse_geofabrik_poly, sha256_file
from .accessibility_scope import GeographicSpecification


_G2_SCOPES = {"great_britain", "ireland_northern_ireland"}
_PRIORITY = {"ireland_northern_ireland": 0, "great_britain": 1, "canaries": 0}


class AccessibilityResolver:
    """Open and validate every configured immutable index before resolving any spot."""

    def __init__(self, datasets: Mapping[str, str | Path], elevation_sampler: Any, *,
                 radius_m: int | None = None,
                 geographic_config: str | Path = "config/accessibility.yaml"):
        if not isinstance(datasets, Mapping) or not datasets:
            raise ValueError("At least one accessibility scope dataset is required")
        self.datasets = dict(datasets)
        if any(not isinstance(key, str) or not key for key in self.datasets):
            raise ValueError("Accessibility dataset scopes must be non-empty strings")
        if set(self.datasets) & _G2_SCOPES and set(self.datasets) != _G2_SCOPES:
            raise ValueError("Both G2 scopes are required together for uk_ireland accessibility")
        unknown = set(self.datasets) - {"canaries", *_G2_SCOPES}
        if unknown:
            raise ValueError(f"Unknown accessibility dataset scope(s): {sorted(unknown)}")
        self.elevation_sampler = elevation_sampler
        self.geographic_config = Path(geographic_config)
        self.radius_m = radius_m
        self.indices: dict[str, AccessibilityIndex] = {}
        self.enrichers: dict[str, AccessibilityEnricher] = {}
        self.specifications: dict[str, GeographicSpecification] = {}
        self._cores_geographic = {}
        self._closed = False
        try:
            # Open and validate all indexes before a caller can create outputs or
            # begin raster processing. A failure closes every earlier handle.
            for scope in sorted(self.datasets, key=lambda value: (_PRIORITY.get(value, 99), value)):
                index = AccessibilityIndex(self.datasets[scope])
                self.indices[scope] = index
                spec = GeographicSpecification.load(self.geographic_config, scope)
                self.specifications[scope] = spec
                self._validate_index(scope, index, spec)
                selected_radius = self.radius_m if self.radius_m is not None else spec.public_search_radius_m
                if selected_radius != spec.public_search_radius_m:
                    raise ValueError(f"Accessibility scope {scope} requires radius {spec.public_search_radius_m} m")
                index.validate_dem_manifest(self.elevation_sampler.manifest_path)
                if Path(spec.dem_manifest).resolve() != Path(self.elevation_sampler.manifest_path).resolve():
                    raise ValueError(f"Accessibility scope {scope} requires its configured DEM/WBM manifest")
                self.enrichers[scope] = AccessibilityEnricher(index, self.elevation_sampler, selected_radius)
                self._cores_geographic[scope] = shapely_transform(
                    index._assignment_core,
                    Transformer.from_crs(index.crs, "EPSG:4326", always_xy=True).transform,
                    interleaved=False,
                )
            self._validate_partition()
        except BaseException:
            self.close()
            raise

    def _validate_index(self, scope: str, index: AccessibilityIndex,
                        spec: GeographicSpecification) -> None:
        manifest = index.manifest
        if manifest.get("schema") != 2:
            raise ValueError(f"Accessibility scope {scope} requires immutable schema 2")
        profile_spec = GENERALIZED_PROFILE_REGISTRY.get(spec.profile)
        if profile_spec is None or manifest.get("profile") != spec.profile:
            raise ValueError(f"Accessibility scope {scope} has an incompatible profile")
        if manifest.get("scope") != scope or index.crs != spec.metric_crs:
            raise ValueError(f"Accessibility scope {scope} manifest scope or CRS does not match configuration")
        policies = manifest.get("policies", {})
        expected_fields = {
            "scope_bbox": list(spec.bbox),
            "preparation_buffer_m": spec.preparation_buffer_m,
            "output_radius_m": spec.public_search_radius_m,
            "radii_m": list(spec.supported_radii_m),
            "assignment_exclusions": list(spec.assignment_exclusions),
        }
        for name, expected in expected_fields.items():
            actual = policies.get(name) if name in {"radii_m", "output_radius_m"} else manifest.get(name)
            if actual != expected:
                raise ValueError(f"Accessibility scope {scope} {name} does not match configuration")
        if manifest.get("assignment_exclusions", []) != list(spec.assignment_exclusions):
            raise ValueError(f"Accessibility scope {scope} has unexpected assignment exclusions")
        coverage_file = manifest.get("files", {}).get("coverage_polygon", {})
        coverage_path = (index.root / coverage_file.get("path", "")).resolve()
        if not coverage_path.is_relative_to(index.root) or not coverage_path.is_file():
            raise ValueError(f"Accessibility scope {scope} retained provider polygon is missing")
        provider_coverage = parse_geofabrik_poly(coverage_path)
        core, domain, coverage, forward, _inverse, _distortion = spec.projected_geometries(provider_coverage)
        expected_hashes = {
            "assignment_core_sha256": index.manifest.get("assignment_core_wkb_hex"),
            "prepared_domain_sha256": index.manifest.get("domain_wkb_hex"),
            "provider_coverage_sha256": index.manifest.get("coverage_wkb_hex"),
        }
        computed = {
            "assignment_core_sha256": core,
            "prepared_domain_sha256": domain,
            "provider_coverage_sha256": coverage,
        }
        for key, geometry in computed.items():
            expected = hashlib.sha256(bytes.fromhex(expected_hashes[key])).hexdigest()
            actual = hashlib.sha256(to_wkb(geometry)).hexdigest()
            if actual != expected:
                raise ValueError(f"Accessibility scope {scope} {key} does not match configured geography")
        if not coverage.covers(domain):
            raise ValueError(f"Accessibility scope {scope} provider polygon does not cover its preparation halo")
        if index.manifest.get("identity", {}).get("crs") != spec.metric_crs:
            raise ValueError(f"Accessibility scope {scope} identity CRS does not match configuration")
        if scope in _G2_SCOPES:
            source_lock = manifest.get("files", {}).get("source_lock", {})
            lock_path = (index.root / source_lock.get("path", "")).resolve()
            if not lock_path.is_relative_to(index.root) or not lock_path.is_file():
                raise ValueError(f"Accessibility scope {scope} source lock is missing")
            lock_bytes = lock_path.read_bytes()
            if hashlib.sha256(lock_bytes).hexdigest() != manifest["identity"].get("source_lock_sha256"):
                raise ValueError(f"Accessibility scope {scope} source lock hash does not match identity")
            lock = json.loads(lock_bytes)
            if lock.get("scope") != scope or lock.get("polygon_historical_authentication") is not False:
                raise ValueError(f"Accessibility scope {scope} source lock has invalid provenance")
            configured_lock = Path(spec.source["source_lock_path"]).resolve()
            if not configured_lock.is_file() or configured_lock.read_bytes() != lock_bytes:
                raise ValueError(f"Accessibility scope {scope} lock differs from configured source lock")
            sidecar_path = Path(spec.source["checksum_sidecar_path"]).resolve()
            sidecar = lock.get("checksum_sidecar", {})
            if (not sidecar_path.is_file() or sidecar_path.stat().st_size != sidecar.get("size")
                    or sha256_file(sidecar_path) != sidecar.get("sha256")
                    or sidecar_path.read_text(encoding="ascii") != sidecar.get("text")):
                raise ValueError(f"Accessibility scope {scope} provider checksum sidecar is missing or changed")
            provenance = manifest.get("pbf_provenance", {})
            if (lock.get("pbf", {}).get("url") != spec.source["source_url"]
                    or lock.get("pbf", {}).get("sha256") != provenance.get("pbf_sha256")
                    or lock.get("coverage_polygon", {}).get("url") != spec.source["coverage_polygon_url"]
                    or lock.get("coverage_polygon", {}).get("sha256") != provenance.get("coverage_polygon_sha256")):
                raise ValueError(f"Accessibility scope {scope} lock sources differ from the configured profile")

    def _validate_partition(self) -> None:
        scopes = list(self._cores_geographic)
        if len(scopes) < 2:
            return
        to_equal_area = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
        projected = {
            scope: shapely_transform(geometry, to_equal_area.transform, interleaved=False)
            for scope, geometry in self._cores_geographic.items()
        }
        for pos, left in enumerate(scopes):
            for right in scopes[pos + 1:]:
                overlap = projected[left].intersection(projected[right]).area
                if overlap > 1e-6:
                    raise ValueError(
                        f"Accessibility assignment cores {left} and {right} overlap by {overlap:.6f} m²"
                    )

    def _assignment(self, spot: Mapping[str, Any]) -> tuple[str | None, bool]:
        spot_id = str(spot.get("id", ""))
        try:
            lat, lon = float(spot["lat"]), float(spot["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Spot {spot_id!r} has invalid accessibility coordinates") from exc
        matches = [scope for scope, index in self.indices.items()
                   if index.supports_scope_point(lat, lon)]
        matches.sort(key=lambda scope: (_PRIORITY.get(scope, 99), scope))
        if len(matches) > 1:
            # Positive-area overlap is rejected during setup. Multiple covers
            # here can therefore only be a shared boundary or numerical tie.
            return matches[0], True
        if matches:
            return matches[0], False
        declared = any(spot_id in set(index.manifest.get("assignment_exclusions", []))
                       for index in self.indices.values())
        return None, declared

    def resolve_scope(self, spot: Mapping[str, Any]) -> str | None:
        """Return the single validated index scope selected for a spot."""
        scope, excluded = self._assignment(spot)
        if scope is None and not excluded:
            raise ValueError(f"Spot {spot.get('id')!r} has no accessibility assignment")
        if scope is not None and str(spot.get("id", "")) in set(
            self.indices[scope].manifest.get("assignment_exclusions", [])
        ):
            return None
        return scope

    def audit_assignments(self, spots: list[Mapping[str, Any]], *, enriched: bool = False) -> dict[str, Any]:
        counts = Counter()
        missing_ids = []
        for spot in spots:
            scope, boundary_tie = self._assignment(spot)
            if scope is None:
                if boundary_tie:
                    counts["excluded"] += 1
                    continue
                missing_ids.append(str(spot.get("id", "")))
                continue
            exclusions = set(self.indices[scope].manifest.get("assignment_exclusions", []))
            if str(spot.get("id", "")) in exclusions:
                counts["excluded"] += 1
                continue
            counts[f"assigned:{scope}"] += 1
            if enriched and isinstance(spot.get("accessibility"), dict):
                if spot["accessibility"].get("source") != self.indices[scope].source_id:
                    raise ValueError(f"Spot {spot.get('id')!r} accessibility source does not match assigned scope")
                counts[f"enriched:{scope}"] += 1
        if missing_ids:
            raise ValueError("Accessibility resolver has undeclared assignment gaps: " + ", ".join(missing_ids[:20]))
        result = {"assigned": sum(value for key, value in counts.items() if key.startswith("assigned:")),
                  "enriched": sum(value for key, value in counts.items() if key.startswith("enriched:")),
                  "excluded": counts["excluded"], "missing": 0,
                  "by_scope": {scope: {"assigned": counts[f"assigned:{scope}"],
                                       "enriched": counts[f"enriched:{scope}"]}
                               for scope in self.indices}}
        return result

    def enrich_spot(self, spot: Mapping[str, Any]) -> dict[str, Any]:
        scope, _boundary_tie = self._assignment(spot)
        if scope is None:
            spot_id = str(spot.get("id", ""))
            declared = any(spot_id in set(index.manifest.get("assignment_exclusions", []))
                           for index in self.indices.values())
            if declared:
                return dict(spot)
            raise ValueError(f"Spot {spot_id!r} has no accessibility assignment")
        if str(spot.get("id", "")) in set(self.indices[scope].manifest.get("assignment_exclusions", [])):
            return dict(spot)
        # The single chosen index owns both its spatial search and source ID.
        return self.enrichers[scope].enrich_spot(spot)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for index in self.indices.values():
            try:
                index.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


__all__ = ["AccessibilityResolver"]
