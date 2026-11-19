"""F01 v1 comparable-darkness measurement and wire contract.

The measurement deliberately reads pre-clip ALR, not clipped darkness. The
public payload is only a label and optional approximate radius; diagnostics are
returned separately for the local generation audit.
"""

from math import ceil, cos, floor, radians

import numpy as np
import rasterio
from pyproj import Geod, Transformer
from rasterio.features import shapes
from rasterio.windows import Window
from scipy.ndimage import binary_dilation, label
from shapely import area, box, intersection, intersects_xy, segmentize, transform, union_all
from shapely.geometry import Point, shape

GEOD = Geod(ellps="WGS84")
TOLERANCES = (0.03, 0.04, 0.05)
HALO_TOLERANCES = (0.03, 0.04, 0.05)
STRICT_LABEL_TOLERANCE = 0.02
NOMINAL_LABEL_TOLERANCE = 0.04
STUDY_RADIUS_M = 10_000
SUPPORT_RADIUS_M = 5_000
MIN_SUPPORT_KM2 = 10.0
RADIUS_CANDIDATES_M = range(2_000, 10_001, 250)
MAX_NATIVE_EDGE_SEGMENT_DEG = 0.0005


def _projected_union(native_geometries, project):
    """Union in WGS84, retain edge detail, then project the continuous shape."""
    if not native_geometries:
        return []
    native_union = union_all(native_geometries)
    if native_union.is_empty:
        return []
    # Projection curves a long WGS84 edge. Union can dissolve its intermediate
    # collinear raster/vector vertices, so restore them before transforming;
    # otherwise the projected chord can leave a >1 m² false coverage slit.
    detailed = segmentize(native_union, max_segment_length=MAX_NATIVE_EDGE_SEGMENT_DEG)
    return [project(detailed)]


def label_support_eligible(strict_km2, nominal_km2):
    return min(strict_km2, nominal_km2) >= MIN_SUPPORT_KM2


def halo_shape_eligible(coverage, capture):
    return coverage >= .90 and capture >= 2 / 3


def halo_water_eligible(covered, disk_fraction, max_cell_fraction):
    return bool(covered and disk_fraction < .01 and max_cell_fraction < .25)


def halo_coverage_complete(disk, wbm_coverage, e3_marine_land, e3_inland_coverage, undeclared):
    """Require every declared coverage source to cover the full disk."""
    if undeclared:
        return False
    return all(disk.difference(union_all(parts)).area <= 1 for parts in
               (wbm_coverage, e3_marine_land, e3_inland_coverage))


def water_cell_fraction(water_inside_disk_area, full_cell_area):
    """Keep the full native cell as denominator, including rim cells."""
    numerator = np.asarray(water_inside_disk_area, dtype=np.float64)
    denominator = np.asarray(full_cell_area, dtype=np.float64)
    return np.divide(numerator, denominator, out=np.zeros_like(denominator), where=denominator > 0)


def _affirmative_tag(value):
    return str(value or "").lower() in {"yes", "true", "1"}


def normalize_sky_darkness_area(value):
    """Return a valid v1 label/radius object, or None for unknown payloads.

    A malformed radius deliberately degrades to label-only; an unknown version
    or malformed object does not enable the indication.
    """
    if (not isinstance(value, dict) or isinstance(value.get("v"), bool) or value.get("v") != 1
            or any(key not in {"v", "radiusM"} for key in value)):
        return None
    radius = value.get("radiusM")
    if radius is None:
        return {"v": 1}
    if (isinstance(radius, bool) or not isinstance(radius, int)
            or radius < 2000 or radius > 10000 or radius % 250):
        return {"v": 1}
    return {"v": 1, "radiusM": radius}


def sky_darkness_area_for_measurement(label_eligible, radius_m=None):
    """Build one of the three approved public output states."""
    if not label_eligible:
        return None
    normalized = normalize_sky_darkness_area({"v": 1, "radiusM": radius_m} if radius_m is not None else {"v": 1})
    if normalized is None:
        raise ValueError("Invalid F01 measurement result")
    return normalized


def _native_window(alr, grid, lon, lat, sampler):
    """Read the bounded native ALR neighbourhood and verified land predicate."""
    col, row = (int(v) for v in (~grid) @ (lon, lat))
    step_lon, step_lat = grid.a, -grid.e
    if step_lon <= 0 or step_lat <= 0 or grid.b or grid.d:
        raise ValueError("F01 requires an unrotated native WGS84 ALR grid")
    reach_x = ceil(22_000 / (111_000 * step_lon * cos(radians(lat)))) + 2
    reach_y = ceil(22_000 / (111_000 * step_lat)) + 2
    cols, rows = np.meshgrid(np.arange(col-reach_x, col+reach_x+1),
                             np.arange(row-reach_y, row+reach_y+1))
    x, y = grid @ (cols + 0.5, rows + 0.5)
    _, _, distance = GEOD.inv(np.full_like(x, lon), np.full_like(y, lat), x, y)
    data = np.full(x.shape, np.nan, dtype=np.float64)
    inside = (rows >= 0) & (cols >= 0) & (rows < alr.shape[0]) & (cols < alr.shape[1])
    data[inside] = alr[rows[inside], cols[inside]]

    from .geography import load_geography
    land = load_geography().land
    ne_land = intersects_xy(land, x, y)
    classes = np.full(x.shape, -2, dtype=np.int16)  # -2 means undeclared owner
    owners = {(floor(float(yy)), floor(float(xx))) for yy, xx in zip(y.flat, x.flat)}
    for south, west in owners & sampler.tiles.keys():
        selected = ne_land & (distance <= 20_600) & (np.floor(y) == south) & (np.floor(x) == west)
        if selected.any():
            classes[selected] = sampler.wbm_classes(y[selected], x[selected])
    verified = ne_land & (classes == 0) & np.isfinite(data) & (data >= 0)
    # Unknown cells cannot bridge components or contribute support. Known
    # water/out-of-band ALR are exclusions and do not leave an open boundary.
    undeclared = ne_land & (classes == -2)
    wbm_nodata = ne_land & (classes == -1)
    alr_unknown = ne_land & (classes == 0) & (~np.isfinite(data) | (data < 0))
    unknown = undeclared | wbm_nodata | alr_unknown
    anchor = (reach_y, reach_x)
    cell_area_km2 = (GEOD.inv(lon, lat, lon+step_lon, lat)[2]
                     * GEOD.inv(lon, lat, lon, lat+step_lat)[2] / 1e6)
    return x, y, distance, data, verified, unknown, anchor, cell_area_km2, {
        "undeclared_wbm_tile": undeclared,
        "wbm_nodata": wbm_nodata,
        "alr_nodata_or_margin": alr_unknown,
    }


def _halo_footprint(index, sampler, project, inverse, disk):
    """Check WBM/E3 coverage, ocean, and permanent-water footprints for disk."""
    # Densify the projected disk before taking its native WGS84 frame. These
    # vertices preserve the exact disk chord boundary through native clipping;
    # densifying after inverse projection would curve each chord away from disk.
    frame = inverse(segmentize(disk, max_segment_length=20))
    left, bottom, right, top = frame.bounds
    evidence = {k: [] for k in ("marine_land", "inland_coverage", "water")}
    for _, feature_id in index.query([frame]):
        geometry, props = index.clipped(feature_id, frame)
        kind = props["kind"]
        if kind in evidence and not geometry.is_empty:
            if kind != "water" or not (
                _affirmative_tag(props.get("tidal"))
                or _affirmative_tag(props.get("intermittent"))
            ):
                evidence[kind].append(geometry)
    # Adjoining polygons within each declared E3 coverage layer are one native
    # WGS84 footprint. Projecting them individually curves shared edges by
    # different chords and creates artificial gaps at the 1 m² limit.
    e3_marine_land = _projected_union(evidence["marine_land"], project)
    e3_inland_coverage = _projected_union(evidence["inland_coverage"], project)
    e3_water = union_all(_projected_union(evidence["water"], project))
    native_parts = {"coverage": [], "ocean": [], "inland": []}
    undeclared = False
    for south in range(floor(bottom), floor(top) + 1):
        for west in range(floor(left), floor(right) + 1):
            owner = box(west, south, west + 1, south + 1)
            if frame.intersection(owner).area <= 0:
                continue
            if (south, west) not in sampler.tiles:
                undeclared = True
                continue
            ds = sampler.tiles[south, west][1]
            if ds.crs.to_epsg() != 4326:
                raise ValueError("F01 WBM footprint requires native WGS84 tiles")
            bounds = owner.intersection(frame).bounds
            win = ds.window(*bounds)
            c, r = floor(win.col_off), floor(win.row_off)
            end_c, end_r = ceil(win.col_off + win.width), ceil(win.row_off + win.height)
            win = Window(c, r, end_c-c, end_r-r).intersection(Window(0, 0, ds.width, ds.height))
            native = ds.read(1, window=win, masked=True)
            valid = ~np.ma.getmaskarray(native)
            values = native.filled(255)
            if not np.isin(values[valid], [0, 1, 2, 3]).all():
                raise ValueError(f"Invalid WBM footprint class in {ds.name}")
            for mask, group in ((valid, native_parts["coverage"]),
                                (valid & (values == 1), native_parts["ocean"]),
                                (valid & np.isin(values, [2, 3]), native_parts["inland"])):
                for geometry, _ in shapes(mask.astype("uint8"), mask=mask,
                                          transform=ds.window_transform(win)):
                    clipped = shape(geometry).intersection(owner)
                    if not clipped.is_empty:
                        group.append(clipped)
    # Join class footprints at ownership seams in WGS84 and restore native edge
    # vertices before projection. Keep coverage, ocean, and inland water
    # distinct because each has a different decision below.
    coverage = _projected_union(native_parts["coverage"], project)
    ocean = _projected_union(native_parts["ocean"], project)
    inland = _projected_union(native_parts["inland"], project)
    full_coverage = halo_coverage_complete(
        disk, coverage, e3_marine_land, e3_inland_coverage, undeclared
    )
    sea_area = disk.intersection(union_all(ocean)).area
    water = union_all([e3_water, *inland]).intersection(disk)
    return full_coverage and sea_area <= 1, water


def measure_sky_darkness_area(spot, alr, grid, sampler, index):
    """Measure and return ``(wire_value, audit)`` for one unchanged anchor.

    ``index`` must be the manifest-verified regional E3 GeographicIndex.
    Declared input corruption propagates; absent undeclared WBM ownership is
    recorded as incomplete evidence and can only suppress a halo.
    """
    lon, lat = float(spot["lon"]), float(spot["lat"])
    x, y, distance, values, verified, unknown, anchor, cell_area_km2, unknown_masks = _native_window(
        alr, grid, lon, lat, sampler
    )
    if not verified[anchor]:
        return None, {
            "id": spot.get("id"), "label": False, "radiusM": None,
            "localSupportKm2": {"0.02": 0.0, "0.04": 0.0},
            "connectedCells": {"0.03": 0, "0.04": 0, "0.05": 0},
            "studyContinuation": False, "unresolvedBoundary": True,
            "studyContinuationByTolerance": {"0.03": False, "0.04": False, "0.05": False},
            "unknownLandCellsWithin10km": {
                name: int((mask & (distance <= STUDY_RADIUS_M)).sum())
                for name, mask in unknown_masks.items()
            },
            "strictAndNominalLabelPass": False, "haloRadiusChecks": [],
            "openBoundaryReasons": {"anchor": {"unverified": 1}},
            "maskProvenance": {"land": "Natural Earth 1:10m v5.1.1",
                               "water": "manifest-verified Copernicus WBM + E3"},
        }
    delta = np.abs(np.log10(values + 0.001) - np.log10(values[anchor] + 0.001))
    tr = Transformer.from_crs(4326,
        f"+proj=laea +lat_0={lat} +lon_0={lon} +ellps=WGS84 +units=m", always_xy=True)
    project = lambda geom: transform(geom, tr.transform, interleaved=False)
    inverse = lambda geom: transform(geom,
        lambda a, b: tr.transform(a, b, direction="INVERSE"), interleaved=False)
    px, py = tr.transform(x, y)
    planar = np.hypot(px, py)
    near_study = distance <= 10_700
    half_lon, half_lat = grid.a / 2, -grid.e / 2
    footprints = project(box(x[near_study]-half_lon, y[near_study]-half_lat,
                             x[near_study]+half_lon, y[near_study]+half_lat))
    unknown_footprints = footprints[unknown[near_study]]
    typed_unknown_footprints = {
        name: footprints[mask[near_study]] for name, mask in unknown_masks.items()
    }

    def component(tolerance, window=STUDY_RADIUS_M):
        eligible = verified & (delta <= tolerance)
        candidates = eligible & (distance <= window)
        labels, _ = label(candidates)  # scipy's default structure is 4-way
        result = candidates & (labels == labels[anchor])
        cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
        outline = binary_dilation(result, structure=cross) & ~result
        continuation = bool((outline & eligible & (distance > window)).any())
        open_boundary = bool((outline & unknown).any())
        return result, continuation, open_boundary

    components = {t: component(t) for t in TOLERANCES}
    local_areas = {
        t: float((component(t)[0] & (distance <= SUPPORT_RADIUS_M)).sum() * cell_area_km2)
        for t in (STRICT_LABEL_TOLERANCE, NOMINAL_LABEL_TOLERANCE)
    }
    label_ok = label_support_eligible(local_areas[STRICT_LABEL_TOLERANCE],
                                      local_areas[NOMINAL_LABEL_TOLERANCE])
    unresolved = any(v[2] for v in components.values())
    continued = any(v[1] for v in components.values())
    boundary_reasons = {}
    for tolerance in TOLERANCES:
        comp = components[tolerance][0]
        cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
        boundary = binary_dilation(comp, structure=cross) & ~comp
        reasons = {name: int((boundary & mask).sum()) for name, mask in unknown_masks.items()
                   if (boundary & mask).any()}
        if reasons:
            boundary_reasons[str(tolerance)] = reasons
    origin = Point(0, 0)

    def circle(radius):
        return origin.buffer(radius, quad_segs=96)

    def shape_stats(radius, comp):
        inside = intersects_xy(circle(radius), px, py)
        n = int((inside & comp).sum())
        return (n / max(1, int(inside.sum())), n / max(1, int(comp.sum())))

    def shape_passes(radius, comp):
        coverage, capture = shape_stats(radius, comp)
        if not halo_shape_eligible(coverage, capture):
            return False
        disk = circle(radius)
        if np.any(area(intersection(unknown_footprints, disk)) > 0):
            return False
        inside = intersects_xy(disk, px, py)
        lobes, _ = label(comp & ~inside)
        sizes = np.bincount(lobes.ravel())
        remote = np.unique(lobes[comp & (planar >= radius + max(1000, radius/4))])
        return not any(k and sizes[k] >= .10 * comp.sum() for k in remote)

    def water_stats(radius):
        disk = circle(radius)
        covered, water = _halo_footprint(index, sampler, project, inverse, disk)
        water_areas = area(intersection(footprints, water))
        full_areas = area(footprints)
        fractions = water_cell_fraction(water_areas, full_areas)
        max_cell = int(np.argmax(fractions)) if fractions.size else 0
        wet_cell = float(fractions.flat[max_cell]) if fractions.size else 0.0
        wet_cell_area = float(water_areas.flat[max_cell]) if water_areas.size else 0.0
        full_cell_area = float(full_areas.flat[max_cell]) if full_areas.size else 0.0
        return (covered, float(water.area / disk.area), wet_cell, float(water.area / 1e6),
                wet_cell_area, full_cell_area)

    radius = None
    radius_diagnostics = []
    if label_ok and not unresolved:
        for candidate in RADIUS_CANDIDATES_M:
            stats = [shape_stats(candidate, components[t][0]) for t in HALO_TOLERANCES]
            shape_ok = all(shape_passes(candidate, components[t][0]) for t in HALO_TOLERANCES)
            disk = circle(candidate)
            unknown_overlaps = {
                name: int(np.count_nonzero(area(intersection(cells, disk)) > 0))
                for name, cells in typed_unknown_footprints.items()
            }
            unknown_overlaps = {name: count for name, count in unknown_overlaps.items() if count}
            covered = wet_disk = wet_cell = None
            wet_cell_area = full_cell_area = None
            if shape_ok:
                (covered, wet_disk, wet_cell, water_km2,
                 wet_cell_area, full_cell_area) = water_stats(candidate)
                water_ok = halo_water_eligible(covered, wet_disk, wet_cell)
            else:
                water_km2, water_ok = None, False
            radius_diagnostics.append({"radiusM": candidate, "coverageCapture": stats,
                "shapePass": shape_ok, "waterCoverage": covered, "waterFraction": wet_disk,
                "maxCellWaterFraction": wet_cell, "waterKm2": water_km2,
                "maxCellWaterAreaM2": wet_cell_area, "maxCellAreaM2": full_cell_area,
                "unknownFootprintOverlaps": unknown_overlaps})
            if shape_ok and water_ok:
                radius = candidate
                break
    if continued:
        radius = None
    wire = sky_darkness_area_for_measurement(label_ok, radius)
    audit = {
        "id": spot.get("id"), "label": label_ok, "radiusM": radius,
        "localSupportKm2": local_areas,
        "connectedCells": {str(t): int(components[t][0].sum()) for t in components},
        "studyContinuation": continued, "unresolvedBoundary": unresolved,
        "studyContinuationByTolerance": {str(t): components[t][1] for t in components},
        "unknownLandCellsWithin10km": {name: int((mask & (distance <= STUDY_RADIUS_M)).sum())
                                       for name, mask in unknown_masks.items()},
        "strictAndNominalLabelPass": label_ok,
        "haloRadiusChecks": radius_diagnostics,
        "openBoundaryReasons": boundary_reasons,
        "maskProvenance": {"land": "Natural Earth 1:10m v5.1.1",
                           "water": "manifest-verified Copernicus WBM + E3"},
    }
    return wire, audit
