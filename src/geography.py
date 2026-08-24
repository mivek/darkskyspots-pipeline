"""Versioned Natural Earth land mask and country attribution.

The pipeline deliberately keeps the raster margin for radiance calculations,
but this module is the first consumer of mesh candidates.  Consequently sea
and foreign-country candidates never participate in redundancy decisions.
"""
from __future__ import annotations

import logging
from numbers import Integral
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "natural_earth"
ADMIN_BASENAME = "ne_10m_admin_0_countries"
LAND_BASENAME = "ne_10m_land"


@dataclass
class Geography:
    """Prepared Natural Earth geometries and a spatial index."""

    land: object
    countries: dict[str, object]
    country_tree: STRtree
    country_codes: tuple[str, ...]

    def country_candidates(self, point: Point) -> list[str]:
        """Return country codes whose geometry covers ``point``."""
        matches = self.country_tree.query(point)
        # Shapely 2 returns integer indexes; supporting geometry results keeps
        # the class usable with the older STRtree API in downstream tools.
        result: list[str] = []
        for match in matches:
            if isinstance(match, Integral):
                code = self.country_codes[match]
                geometry = self.countries[code]
            else:
                geometry = match
                code = next(
                    (candidate for candidate, value in self.countries.items() if value is geometry),
                    "",
                )
            if code and geometry.covers(point):
                result.append(code)
        return sorted(set(result))


def _read_shapes(path: Path):
    """Read a shapefile from a versioned local component set."""
    try:
        import shapefile  # pyshp, intentionally a small runtime dependency
    except ImportError as exc:  # pragma: no cover - exercised at install time
        raise RuntimeError("Natural Earth support requires the pyshp dependency") from exc
    reader = shapefile.Reader(str(path.with_suffix("")))
    fields = [field[0] for field in reader.fields[1:]]
    for record, shp in zip(reader.iterRecords(), reader.iterShapes()):
        yield dict(zip(fields, record)), shape(shp.__geo_interface__)


@lru_cache(maxsize=4)
def load_geography(data_dir: str | Path = DEFAULT_DATA_DIR) -> Geography:
    """Load and index Natural Earth 1:10m data, without network access."""
    directory = Path(data_dir)
    admin_path = directory / ADMIN_BASENAME / (ADMIN_BASENAME + ".shp")
    if not admin_path.exists():
        admin_path = directory / (ADMIN_BASENAME + ".shp")
    land_path = directory / LAND_BASENAME / (LAND_BASENAME + ".shp")
    if not land_path.exists():
        land_path = directory / (LAND_BASENAME + ".shp")
    if not admin_path.exists() or not land_path.exists():
        raise FileNotFoundError(
            "Natural Earth 1:10m files are missing; expected "
            f"{admin_path} and {land_path}"
        )

    countries: dict[str, object] = {}
    for record, geometry in _read_shapes(admin_path):
        code = str(record.get("ISO_A2_EH") or record.get("ISO_A2") or "").upper()
        if len(code) != 2 or code == "-9":
            continue
        countries[code] = unary_union([countries[code], geometry]) if code in countries else geometry
    land = unary_union([geometry for _record, geometry in _read_shapes(land_path)])
    codes = tuple(sorted(countries))
    tree = STRtree([countries[code] for code in codes])
    logger.info("Loaded Natural Earth 1:10m: %d country geometries", len(codes))
    return Geography(land=land, countries=countries, country_tree=tree, country_codes=codes)


def classify_candidates(
    candidates: list[dict],
    country_codes: list[str] | tuple[str, ...] | set[str],
    *,
    geography: Geography | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    transform=None,
    crs=None,
    reject_ambiguous: bool = False,
) -> tuple[list[dict], dict[str, int]]:
    """Apply land mask and national clip before redundancy.

    ``ambiguous_country_candidates`` measures points covered by more than one
    country polygon.  When mesh row/column and raster transform are available,
    the country occupying the largest portion of that pixel wins. Lexical ISO
    is only the final fallback for legacy point-only records or exact ties.
    The count is always emitted for verification.
    """
    allowed = {str(code).upper() for code in country_codes}
    geo = geography or load_geography(data_dir)
    kept: list[dict] = []
    stats = {
        "mesh_candidates": len(candidates),
        "sea_rejected": 0,
        "invalid_candidates": 0,
        "ambiguous_country_candidates": 0,
        "other_country_rejected": 0,
        "country_candidates": 0,
    }
    for candidate in candidates:
        try:
            point = Point(float(candidate["lon"]), float(candidate["lat"]))
        except (KeyError, TypeError, ValueError):
            stats["invalid_candidates"] += 1
            continue
        if not geo.land.covers(point):
            stats["sea_rejected"] += 1
            continue
        matches = geo.country_candidates(point)
        is_ambiguous = len(matches) > 1
        if is_ambiguous:
            stats["ambiguous_country_candidates"] += 1
            # Historical spots have no trustworthy source-pixel footprint.
            # Do not invent a country for an exact boundary point in that
            # mode; the caller can explicitly prune it after reviewing the
            # audit. Raster candidates normally provide a pixel footprint and
            # may use the geographic area rule below.
            if reject_ambiguous and _candidate_pixel_polygon(candidate, transform, crs) is None:
                continue
        eligible = [code for code in matches if code in allowed]
        if not eligible:
            stats["other_country_rejected"] += 1
            continue
        if len(eligible) == 1:
            country = eligible[0]
        else:
            # A mesh point exactly on a political boundary is assigned to the
            # country occupying the largest part of its source raster pixel.
            # This is geographic rather than lexical; lexical order remains
            # only the final tie-breaker for a missing pixel footprint or an
            # exact equal-area tie.
            pixel = _candidate_pixel_polygon(candidate, transform, crs)
            if pixel is None:
                country = min(eligible)
            else:
                areas = {
                    code: pixel.intersection(geo.countries[code]).area
                    for code in eligible
                }
                largest = max(areas.values())
                winners = [code for code, area in areas.items() if abs(area - largest) <= 1e-12]
                country = min(winners)
        candidate["country"] = country
        kept.append(candidate)
        stats["country_candidates"] += 1
    return kept, stats


def _candidate_pixel_polygon(candidate: dict, transform, crs):
    if transform is None or crs is None or "row" not in candidate or "col" not in candidate:
        return None
    try:
        from rasterio.warp import transform as reproject_coords
        row, col = int(candidate["row"]), int(candidate["col"])
        corners = [
            transform * (col, row),
            transform * (col + 1, row),
            transform * (col + 1, row + 1),
            transform * (col, row + 1),
        ]
        xs, ys = zip(*corners)
        lon, lat = reproject_coords(crs, "EPSG:4326", list(xs), list(ys))
        return Polygon(zip(lon, lat))
    except Exception:
        logger.debug("Unable to derive source raster pixel for candidate", exc_info=True)
        return None
