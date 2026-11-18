"""Profile-aware integrity and coverage validation for accessibility DEMs."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from typing import Any

import rasterio
from affine import Affine

from .accessibility_scope import GeographicSpecification, SUPPORTED_SCOPE_PROFILES
from .elevation import ElevationSampler


_LEGACY_PROFILE = ("tenerife", "tenerife-legacy-v1")
_DGED_GRID_TOLERANCE_DEGREES = 1e-10
_GLO30_DGED_LATITUDE_SPACING = 1 / 3600
_GLO30_DGED_LONGITUDE_FACTORS = ((50, 1), (60, 1.5), (70, 2), (80, 3), (85, 5), (90, 10))


def _validate_tiles(data: dict[str, Any], *, product: str, version: str, label: str) -> dict[tuple[int, int], dict]:
    tiles = data.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise ValueError(f"{label} must contain a non-empty tile inventory")
    by_cell = {}
    for tile in tiles:
        if (not isinstance(tile, dict) or type(tile.get("lat")) is not int
                or type(tile.get("lon")) is not int):
            raise ValueError(f"{label} tile coordinates must be integers")
        cell = tile["lat"], tile["lon"]
        if cell in by_cell:
            raise ValueError(f"{label} contains duplicate cell {cell}")
        if tile.get("product") != product or tile.get("version") != version:
            raise ValueError(f"{label} has an incompatible DEM product or version for {cell}")
        for layer in ("dem", "wbm"):
            item = tile.get(layer)
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]
                    or type(item.get("size")) is not int or item["size"] <= 0
                    or not isinstance(item.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or not isinstance(item.get("source"), str) or not item["source"]):
                raise ValueError(f"{label} has invalid {layer.upper()} metadata for {cell}")
        by_cell[cell] = tile
    return by_cell


def validate_accessibility_dem_manifest(
    manifest_path: str | Path,
    cache_dir: str | Path,
    scope: str,
    profile: str,
    *,
    geographic_config: str | Path = "config/accessibility.yaml",
) -> dict[str, Any]:
    """Authenticate the manifest against the validated profile before opening its rasters.

    The legacy Tenerife schema-1 and immutable Canary G1 profiles retain their
    historical ``canaries`` manifest requirements. G2 scopes additionally bind
    the extended inventory byte-for-byte to the pinned 56-pair source and
    require every configured G2 halo cell.
    """
    if scope == "tenerife":
        if (scope, profile) != _LEGACY_PROFILE:
            raise ValueError(f"Unknown or contradictory accessibility scope/profile: {scope}/{profile}")
        expected_region = "canaries"
        policy = None
    else:
        specification = GeographicSpecification.load(geographic_config, scope)
        if specification.profile != profile:
            raise ValueError(f"Unknown or contradictory accessibility scope/profile: {scope}/{profile}")
        supported = SUPPORTED_SCOPE_PROFILES.get(scope)
        if supported is None or supported["profile"] != profile:
            raise ValueError(f"Unknown or contradictory accessibility scope/profile: {scope}/{profile}")
        expected_region = supported["dem_region"]
        policy = specification.dem_manifest_policy

    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read accessibility DEM manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError(f"Accessibility profile {profile} requires a schema-1 DEM manifest")
    if manifest.get("region") != expected_region:
        raise ValueError(
            f"Accessibility profile {profile} requires DEM region {expected_region}, "
            f"got {manifest.get('region')!r}"
        )
    if policy is None:
        # Keep the historical Tenerife/G1 contract: region, declared product
        # and delivery, then the existing sampler's integrity checks.
        tiles = manifest.get("tiles")
        if (not isinstance(tiles, list) or not tiles
                or any(not isinstance(tile, dict) or tile.get("product") != "GLO-30"
                       or tile.get("version") != "2024_1" for tile in tiles)):
            raise ValueError(f"Accessibility profile {profile} requires GLO-30 2024_1 DEM/WBM tiles")
    else:
        _validate_g2_inventory(manifest, policy)

    # Performs file, byte-hash, raster, and DEM/WBM grid validation for every
    # declared pair. This runs only after the full profile inventory is known.
    with ElevationSampler(manifest_path, cache_dir):
        pass
    if policy is not None:
        _validate_g2_added_dged_grids(
            manifest, policy, Path(cache_dir).resolve(),
        )
    return manifest


def _glo30_dged_expected_grid(lat: int, lon: int) -> tuple[int, int, Affine]:
    """Return native GLO-30 DGED dimensions and PixelIsPoint affine for a cell.

    DGED stores posts at integer-degree cell boundaries. GeoTIFF's affine
    transform describes the outer raster bounds, so its origin extends half a
    pixel northwest of the first post. Longitude spacing follows the official
    latitude-band reduction factors; latitude spacing is always one arc-second.
    """
    center_latitude = abs(lat + 0.5)
    factor = next(
        factor for upper_bound, factor in _GLO30_DGED_LONGITUDE_FACTORS
        if center_latitude < upper_bound
    )
    longitude_spacing = factor / 3600
    latitude_spacing = _GLO30_DGED_LATITUDE_SPACING
    width = round(3600 / factor) + 1
    height = 3601
    expected_transform = Affine(
        longitude_spacing, 0, lon - longitude_spacing / 2,
        0, -latitude_spacing, lat + 1 + latitude_spacing / 2,
    )
    return width, height, expected_transform


def _glo30_dged_source_identity(tile: dict[str, Any], cell: tuple[int, int]) -> None:
    """Check declared CDSE source locators separately from raster geography.

    The source URL is an acquisition locator and product/cell claim. It is not
    an independently pinned upstream checksum; the locally computed SHA-256
    proves only that the bytes match this manifest.
    """
    lat, lon = cell
    lat_dir, lon_dir = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    cell_id = f"{lat_dir}{abs(lat):02d}_00_{lon_dir}{abs(lon):03d}_00"
    product_dir = "/CCM/COP-DEM_GLO-30-DGED/SAR_DGE_30_A4AD/"
    for layer in ("dem", "wbm"):
        item = tile[layer]
        name = f"Copernicus_DSM_10_{cell_id}_{layer.upper()}.tif"
        source = urlsplit(item["source"])
        expected_directory = "DEM" if layer == "dem" else "AUXFILES"
        source_parts = source.path.split("/")
        if (source.scheme != "s3" or source.netloc != "eodata"
                or source.query or source.fragment or source.username or source.password
                or not source.path.startswith(product_dir)
                or ".." in PurePosixPath(source.path).parts
                or not source.path.endswith(
                    f"/Copernicus_DSM_10_{cell_id}/{expected_directory}/{name}"
                )
                or Path(item["path"]).name != name):
            raise ValueError(
                f"G2 {layer.upper()} source metadata contradicts approved GLO-30 DGED cell {cell}"
            )
        # Keep the native product type explicit; derived COGs use another
        # layout and are not interchangeable with the DGED source contract.
        if "SAR_DGE_30_A4AD" not in source_parts:
            raise ValueError(f"G2 {layer.upper()} source is not native GLO-30 DGED for {cell}")


def _validate_g2_added_dged_grids(
    manifest: dict[str, Any], policy: dict[str, Any], cache_dir: Path,
) -> None:
    """Bind every added G2 raster to its precise native DGED cell grid."""
    additional = {tuple(cell) for cell in policy["required_additional_cells"]}
    tiles = {(tile["lat"], tile["lon"]): tile for tile in manifest["tiles"]}
    for cell in sorted(additional):
        tile = tiles[cell]
        _glo30_dged_source_identity(tile, cell)
        width, height, expected = _glo30_dged_expected_grid(*cell)
        datasets = {}
        with rasterio.Env(), ExitStack() as stack:
            for layer in ("dem", "wbm"):
                path = (cache_dir / tile[layer]["path"]).resolve()
                if not path.is_relative_to(cache_dir) or not path.is_file():
                    raise ValueError(f"Missing G2 {layer.upper()} raster for cell {cell}: {path}")
                try:
                    datasets[layer] = stack.enter_context(rasterio.open(path))
                except rasterio.errors.RasterioError as exc:
                    raise ValueError(f"Cannot open G2 {layer.upper()} raster for cell {cell}: {exc}") from exc
            for layer, ds in datasets.items():
                if ds.driver != "GTiff" or ds.crs != rasterio.crs.CRS.from_epsg(4326):
                    raise ValueError(
                        f"G2 {layer.upper()} geographic-grid mismatch for {cell}: "
                        "expected native EPSG:4326 GeoTIFF"
                    )
                if ds.tags().get("AREA_OR_POINT") != "Point":
                    raise ValueError(
                        f"G2 {layer.upper()} geographic-grid mismatch for {cell}: "
                        "expected RasterPixelIsPoint DGED"
                    )
                if ds.count != 1 or ds.dtypes != (("float32",) if layer == "dem" else ("uint8",)):
                    raise ValueError(
                        f"G2 {layer.upper()} raster has incompatible bands or data type for {cell}"
                    )
                if (ds.width, ds.height) != (width, height):
                    raise ValueError(
                        f"G2 {layer.upper()} geographic-grid mismatch for {cell}: expected DGED "
                        f"dimensions {width}x{height}; got {ds.width}x{ds.height}"
                    )
                actual = ds.transform
                actual_coefficients = tuple(actual)[:6]
                expected_coefficients = tuple(expected)[:6]
                if any(
                    abs(actual_value - expected_value) > _DGED_GRID_TOLERANCE_DEGREES
                    for actual_value, expected_value in zip(actual_coefficients, expected_coefficients)
                ):
                    raise ValueError(
                        f"G2 {layer.upper()} geographic-grid mismatch for declared DGED cell {cell}"
                    )
                if ds.gcps[0] or ds.rpcs:
                    raise ValueError(
                        f"G2 {layer.upper()} raster for {cell} has a non-affine georeferencing override"
                    )
            dem, wbm = datasets["dem"], datasets["wbm"]
            if (dem.crs != wbm.crs or dem.transform != wbm.transform
                    or dem.width != wbm.width or dem.height != wbm.height):
                raise ValueError(f"G2 DEM/WBM grids are not aligned for declared cell {cell}")


def _validate_g2_inventory(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    expected_region = policy["region"]
    product = policy["product"]
    version = policy["version"]
    expected_bbox = policy["bbox"]
    if manifest.get("bbox") != expected_bbox:
        raise ValueError(f"G2 accessibility DEM bbox must match its configured policy: {expected_bbox}")
    base_ref = Path(policy["base_manifest"]).name
    if manifest.get("base_manifest") != base_ref:
        raise ValueError(f"G2 accessibility DEM must declare its original base manifest {base_ref}")
    if manifest.get("base_manifest_sha256") != policy["base_manifest_sha256"]:
        raise ValueError("G2 accessibility DEM base manifest identity does not match the approved source")

    base_path = Path(policy["base_manifest_path"])
    if not base_path.is_file():
        raise ValueError(f"Approved G2 base DEM manifest is missing: {base_path}")
    base_bytes = base_path.read_bytes()
    base_sha = hashlib.sha256(base_bytes).hexdigest()
    if base_sha != policy["base_manifest_sha256"]:
        raise ValueError(f"Approved G2 base DEM manifest hash mismatch: {base_sha}")
    try:
        base = json.loads(base_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("Approved G2 base DEM manifest is invalid JSON") from exc
    if (not isinstance(base, dict) or base.get("schema") != 1
            or base.get("region") != policy["base_region"]
            or base.get("bbox") != policy["base_bbox"]):
        raise ValueError("Approved G2 base DEM manifest has an incompatible region or bbox")
    base_tiles = _validate_tiles(base, product=product, version=version, label="Approved G2 base manifest")
    if len(base_tiles) != policy["expected_base_pairs"]:
        raise ValueError(
            f"Approved G2 base manifest must preserve {policy['expected_base_pairs']} original pairs; "
            f"found {len(base_tiles)}"
        )

    additional = {tuple(cell) for cell in policy["required_additional_cells"]}
    if base_tiles.keys() & additional:
        raise ValueError("Configured G2 halo overlaps the approved historical DEM inventory")
    full_tiles = _validate_tiles(manifest, product=product, version=version,
                                 label="G2 accessibility DEM manifest")
    expected_cells = set(base_tiles) | additional
    if set(full_tiles) != expected_cells:
        missing, extra = expected_cells - set(full_tiles), set(full_tiles) - expected_cells
        raise ValueError(f"G2 DEM cell inventory is incomplete: missing={sorted(missing)}, extra={sorted(extra)}")
    for cell, original in base_tiles.items():
        if full_tiles[cell] != original:
            raise ValueError(f"G2 accessibility DEM replaced or altered historical source pair {cell}")


__all__ = ["validate_accessibility_dem_manifest"]
