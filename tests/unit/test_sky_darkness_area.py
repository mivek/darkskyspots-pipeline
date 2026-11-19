from types import SimpleNamespace

import numpy as np
import pytest
from shapely import segmentize
from affine import Affine
from pyproj import Geod, Transformer
from rasterio.io import MemoryFile
from shapely import transform as transform_geometry
from shapely.geometry import Point, Polygon, box

from src.sky_darkness_area import (
    halo_coverage_complete,
    halo_shape_eligible,
    halo_water_eligible,
    label_support_eligible,
    normalize_sky_darkness_area,
    measure_sky_darkness_area,
    sky_darkness_area_for_measurement,
    water_cell_fraction,
)


@pytest.mark.parametrize("radius", [2000, 2250, 6000, 10000])
def test_radius_boundaries_and_steps(radius):
    assert normalize_sky_darkness_area({"v": 1, "radiusM": radius}) == {"v": 1, "radiusM": radius}


@pytest.mark.parametrize("radius", [1999, 10250, 2251, 2000.0, True, "6000", None])
def test_invalid_radius_degrades_to_label(radius):
    assert normalize_sky_darkness_area({"v": 1, "radiusM": radius}) == {"v": 1}


def test_only_three_public_states_are_constructed():
    assert sky_darkness_area_for_measurement(False) is None
    assert sky_darkness_area_for_measurement(True) == {"v": 1}
    assert sky_darkness_area_for_measurement(True, 6000) == {"v": 1, "radiusM": 6000}
    assert normalize_sky_darkness_area({"v": 2}) is None
    assert normalize_sky_darkness_area({"v": True}) is None


def test_documented_support_and_shape_boundaries_are_inclusive():
    assert label_support_eligible(10, 10)
    assert not label_support_eligible(9.999, 10)
    assert halo_shape_eligible(.90, 2 / 3)
    assert not halo_shape_eligible(.899999, 1)
    assert not halo_shape_eligible(1, 2 / 3 - 1e-9)


def test_water_veto_thresholds_are_inclusive_and_coverage_is_required():
    assert halo_water_eligible(True, .009999, .249999)
    assert not halo_water_eligible(True, .01, .1)
    assert not halo_water_eligible(True, .001, .25)
    assert not halo_water_eligible(False, 0, 0)


def test_halo_coverage_requires_wbm_and_both_e3_coverage_sources():
    disk = box(0, 0, 10, 10)
    complete = [disk]
    missing = [box(0, 0, 9, 10)]
    assert halo_coverage_complete(disk, complete, complete, complete, False)
    assert not halo_coverage_complete(disk, missing, complete, complete, False)
    assert not halo_coverage_complete(disk, complete, missing, complete, False)
    assert not halo_coverage_complete(disk, complete, complete, missing, False)
    assert not halo_coverage_complete(disk, complete, complete, complete, True)


def test_rim_water_uses_full_cell_denominator_and_disk_clipped_numerator():
    assert water_cell_fraction(30_000, 100_000) == pytest.approx(.30)
    # A 100 m² sliver across the rim is divided by the 100,000 m² full cell.
    assert water_cell_fraction(100, 100_000) == pytest.approx(.001)


GEOD = Geod(ellps="WGS84")


class RealWBMSampler:
    """Small real WBM GeoTIFFs used by both point and footprint sampling."""

    def __init__(self, *, owners, overrides=None, column_offsets=None, row_offsets=None, pixel_size=.01):
        self.tiles = {}
        self.memory_files = []
        overrides = overrides or {}
        column_offsets = column_offsets or {}
        row_offsets = row_offsets or {}
        for south, west in owners:
            width = int(round(1 / pixel_size)) + (1 if (south, west) in column_offsets else 0)
            height = width + (1 if (south, west) in row_offsets else 0)
            values = np.zeros((height, width), dtype=np.uint8)
            if (south, west) in overrides:
                values[:] = overrides[(south, west)]
            memory_file = MemoryFile()
            dataset = memory_file.open(
                driver="GTiff", height=values.shape[0], width=values.shape[1], count=1, dtype="uint8",
                crs="EPSG:4326", transform=Affine(
                    pixel_size, 0, west + column_offsets.get((south, west), 0),
                    0, -pixel_size, south + 1 + row_offsets.get((south, west), 0),
                ),
                nodata=255,
            )
            dataset.write(values, 1)
            self.memory_files.append(memory_file)
            self.tiles[(south, west)] = (None, dataset)

    def wbm_classes(self, lats, lons):
        lats, lons = np.asarray(lats), np.asarray(lons)
        output = np.full(lats.shape, -2, dtype=np.int16)
        for owner, (_, dataset) in self.tiles.items():
            selected = (np.floor(lats) == owner[0]) & (np.floor(lons) == owner[1])
            if not selected.any():
                continue
            cols, rows = (~dataset.transform) @ (lons[selected], lats[selected])
            cols, rows = np.floor(cols).astype(int), np.floor(rows).astype(int)
            in_bounds = ((cols >= 0) & (rows >= 0) & (cols < dataset.width)
                         & (rows < dataset.height))
            values = np.full(cols.shape, -1, dtype=np.int16)
            if in_bounds.any():
                data = dataset.read(1)
                values[in_bounds] = data[rows[in_bounds], cols[in_bounds]]
                values[values == 255] = -1
            output[selected] = values
        return output


class SyntheticIndex:
    def __init__(self, records=()):
        self.records = list(records)

    def query(self, frames):
        frame = frames[0]
        return [(None, i) for i, record in enumerate(self.records)
                if record["geometry"].intersects(frame)]

    def clipped(self, identifier, frame):
        record = self.records[identifier]
        return record["geometry"].intersection(frame), {k: v for k, v in record.items() if k != "geometry"}


def e3_coverage_records(center, *, water=(), include_marine=True, include_inland=True):
    lon, lat = center
    extent = box(lon - .5, lat - .5, lon + .5, lat + .5)
    records = []
    if include_marine:
        records.append({"kind": "marine_land", "geometry": extent})
    if include_inland:
        records.append({"kind": "inland_coverage", "geometry": extent})
    records.extend({"kind": "water", "geometry": geometry, **tags} for geometry, tags in water)
    return records


def synthetic_measurement(monkeypatch, *, center=(2.5, 46.67), size=101,
                          step_lon=.004, step_lat=.004, sampler=None, index=None,
                          owners=None, e3_water=()):
    from src import geography
    monkeypatch.setattr(geography, "load_geography",
                        lambda: SimpleNamespace(land=box(-180, -90, 180, 90)))
    spot = {"id": "synthetic-exported-id", "lon": center[0], "lat": center[1]}
    half = size // 2
    grid = Affine(step_lon, 0, center[0] - (half + .5) * step_lon,
                  0, -step_lat, center[1] + (half + .5) * step_lat)
    cols, rows = np.meshgrid(np.arange(size), np.arange(size))
    x, y = grid @ (cols + .5, rows + .5)
    _, _, distance = GEOD.inv(np.full(x.shape, center[0]), np.full(y.shape, center[1]), x, y)
    alr = np.where(distance <= 4500, .1, .8).astype(np.float64)
    if sampler is None:
        tile_owners = owners or {(46, 2), (46, 3), (47, 2), (47, 3)}
        sampler = RealWBMSampler(owners=tile_owners)
    if index is None:
        index = SyntheticIndex(e3_coverage_records(center, water=e3_water))
    return spot, alr, grid, sampler, index


def set_wbm_cells(sampler, owner, predicate, value):
    dataset = sampler.tiles[owner][1]
    rows, cols = np.meshgrid(np.arange(dataset.height), np.arange(dataset.width), indexing="ij")
    x, y = dataset.transform @ (cols + .5, rows + .5)
    values = dataset.read(1)
    values[predicate(x, y)] = value
    dataset.write(values, 1)


def test_production_measurement_runs_warning_strict_and_returns_label(monkeypatch):
    # The suite is also run with -W error::PendingDeprecationWarning; this
    # exercises the real Affine coordinate path rather than only helpers.
    spot, alr, grid, sampler, index = synthetic_measurement(monkeypatch)
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1, "radiusM": 3750}
    assert audit["id"] == spot["id"]
    assert audit["label"] is True


def test_production_measurement_keeps_dry_two_tile_seam_halo(monkeypatch):
    owners = {(46, 2), (47, 2)}
    sampler = RealWBMSampler(owners=owners, column_offsets={(46, 2): -.005, (47, 2): .005})
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=(2.5, 47.015), owners=owners, sampler=sampler
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1, "radiusM": 3750}
    check = next(row for row in audit["haloRadiusChecks"] if row["radiusM"] == 3750)
    assert check["waterCoverage"] is True
    assert check["waterFraction"] == pytest.approx(0)


def test_fully_covered_aligned_dry_two_tile_fixture_uses_smallest_radius(monkeypatch):
    owners = {(46, 2), (47, 2)}
    sampler = RealWBMSampler(owners=owners, pixel_size=1)
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=(2.5, 47.015), owners=owners, sampler=sampler,
    )
    from src import sky_darkness_area
    projected_wbm = []
    original_coverage_check = sky_darkness_area.halo_coverage_complete

    def record_wbm_coverage(disk, wbm, marine, inland, undeclared):
        projected_wbm.append(wbm[0])
        return original_coverage_check(disk, wbm, marine, inland, undeclared)

    monkeypatch.setattr(sky_darkness_area, "halo_coverage_complete", record_wbm_coverage)
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1, "radiusM": 3750}
    check = next(row for row in audit["haloRadiusChecks"] if row["radiusM"] == 3750)
    assert check["waterCoverage"] is True
    assert check["waterFraction"] == pytest.approx(0)

    # The two real, aligned WBM rasters union to one 1° × 2° native rectangle.
    # Compare the builder's projected edge with the densely projected native
    # edge. Projecting that long edge as a single chord changes its area by
    # millions of square metres, so this assertion catches removal of only
    # the native-edge densification even though the fixture remains dry.
    _, project, _ = _local_projection((spot["lon"], spot["lat"]))
    expected = project(segmentize(box(2, 46, 3, 48), max_segment_length=.0005))
    assert projected_wbm
    assert projected_wbm[-1].symmetric_difference(expected).area <= 1


def test_production_measurement_supports_rectangular_wgs84_cells(monkeypatch):
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, step_lon=.004, step_lat=.005
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field is not None
    assert audit["label"] is True
    assert audit["localSupportKm2"][0.02] >= 10


def test_unverified_anchor_returns_no_indication_and_keeps_audit_id(monkeypatch):
    sampler = RealWBMSampler(owners={(46, 2)})
    set_wbm_cells(sampler, (46, 2), lambda x, y: np.ones(x.shape, dtype=bool), 1)
    spot, alr, grid, sampler, index = synthetic_measurement(monkeypatch, sampler=sampler)
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field is None
    assert audit["id"] == spot["id"]
    assert audit["openBoundaryReasons"] == {"anchor": {"unverified": 1}}
    from src.sky_darkness_area import _native_window
    _, _, distance, _, _, _, _, _, masks = _native_window(alr, grid, 2.5, 46.67, sampler)
    assert np.count_nonzero(masks["undeclared_wbm_tile"] & (distance > 10_000)) > 0
    assert audit["unknownLandCellsWithin10km"]["undeclared_wbm_tile"] == 0


@pytest.mark.parametrize("case", ["undeclared-tile", "internal-nodata", "alr-edge"])
def test_production_measurement_returns_label_only_at_unresolved_boundaries(monkeypatch, case):
    center = (2.998, 46.67) if case == "undeclared-tile" else (2.5, 46.67)
    owners = {(46, 2), (47, 2)} if case == "undeclared-tile" else None
    sampler = RealWBMSampler(owners=owners or {(46, 2), (46, 3), (47, 2), (47, 3)})
    if case == "internal-nodata":
        set_wbm_cells(sampler, (46, 2),
                      lambda x, y: (GEOD.inv(np.full(x.shape, center[0]),
                                            np.full(y.shape, center[1]), x, y)[2] >= 2500)
                      & (GEOD.inv(np.full(x.shape, center[0]),
                                  np.full(y.shape, center[1]), x, y)[2] <= 3000), 255)
    size = 29 if case == "alr-edge" else 101
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=center, size=size, sampler=sampler
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1}
    assert audit["label"] is True
    assert audit["unresolvedBoundary"] is True
    if case == "undeclared-tile":
        assert audit["unknownLandCellsWithin10km"]["undeclared_wbm_tile"] > 0
    elif case == "internal-nodata":
        assert audit["unknownLandCellsWithin10km"]["wbm_nodata"] > 0
    else:
        assert audit["unknownLandCellsWithin10km"]["alr_nodata_or_margin"] > 0


@pytest.mark.parametrize("missing_coverage", [
    "e3-coverage", "e3-marine", "e3-inland", "undeclared-adjacent-wbm",
])
def test_production_measurement_requires_both_real_wbm_and_e3_coverage(monkeypatch, missing_coverage):
    center = (2.5, 47.015)
    owners = {(47, 2)} if missing_coverage == "undeclared-adjacent-wbm" else {(46, 2), (47, 2)}
    records = e3_coverage_records(
        center,
        include_marine=missing_coverage not in {"e3-coverage", "e3-marine"},
        include_inland=missing_coverage not in {"e3-coverage", "e3-inland"},
    )
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=center, owners=owners, index=SyntheticIndex(records)
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1}
    assert audit["label"] is True
    if missing_coverage.startswith("e3-"):
        assert any(check["shapePass"] and check["waterCoverage"] is False
                   for check in audit["haloRadiusChecks"])
    else:
        assert audit["unresolvedBoundary"] is True
        assert audit["unknownLandCellsWithin10km"]["undeclared_wbm_tile"] > 0


def _local_projection(center):
    tr = Transformer.from_crs(4326,
        f"+proj=laea +lat_0={center[1]} +lon_0={center[0]} +ellps=WGS84 +units=m",
        always_xy=True)
    project = lambda geometry: transform_geometry(geometry, tr.transform, interleaved=False)
    inverse = lambda geometry: transform_geometry(
        geometry, lambda x, y: tr.transform(x, y, direction="INVERSE"), interleaved=False
    )
    return tr, project, inverse


def _footprint(sampler, index, center, radius=5000):
    from src.sky_darkness_area import _halo_footprint
    _, project, inverse = _local_projection(center)
    disk = Point(0, 0).buffer(radius, quad_segs=96)
    return _halo_footprint(index, sampler, project, inverse, disk)


def _water_cell(center, distance_m, sampler, grid=None):
    """Return an exact native pixel footprint and its owning tile location."""
    lon, lat, _ = GEOD.fwd(center[0], center[1], 90, distance_m)
    owner = (int(np.floor(lat)), int(np.floor(lon)))
    dataset = sampler.tiles[owner][1]
    col, row = (int(v) for v in (~dataset.transform) @ (lon, lat))
    bounds = dataset.window_transform(__import__("rasterio.windows", fromlist=["Window"]).Window(col, row, 1, 1))
    water = box(bounds.c, bounds.f + bounds.e, bounds.c + bounds.a, bounds.f)
    if grid is not None:
        _, project, _ = _local_projection(center)
        water = project(water)
    return owner, row, col, water


def test_real_footprint_builder_uses_either_water_source_and_unions_overlap(monkeypatch):
    center = (2.5, 46.67)
    owner_set = {(46, 2), (46, 3), (47, 2), (47, 3)}
    dry = RealWBMSampler(owners=owner_set)
    owner, row, col, pixel = _water_cell(center, 3000, dry)
    _, project, _ = _local_projection(center)
    expected = project(pixel).area

    for source in ("e3-only", "wbm-only", "both"):
        sampler = RealWBMSampler(owners=owner_set)
        owner, row, col, pixel = _water_cell(center, 3000, sampler)
        if source in {"wbm-only", "both"}:
            dataset = sampler.tiles[owner][1]
            values = dataset.read(1)
            values[row, col] = 2
            dataset.write(values, 1)
        water_features = [(pixel, {})] if source in {"e3-only", "both"} else []
        index = SyntheticIndex(e3_coverage_records(center, water=water_features))
        covered, water = _footprint(sampler, index, center)
        assert covered is True
        assert water.area == pytest.approx(expected, rel=1e-6)


def test_real_builder_wbm_nodata_gap_blocks_halo_but_complete_dry_wbm_passes(monkeypatch):
    center = (2.5, 46.67)
    owners = {(46, 2), (46, 3), (47, 2), (47, 3)}
    for has_gap, expected in ((False, {"v": 1, "radiusM": 3750}),
        (True, None)):
        sampler = RealWBMSampler(owners=owners)
        if has_gap:
            set_wbm_cells(sampler, (46, 2), lambda x, y: (
                GEOD.inv(np.full(x.shape, center[0]), np.full(y.shape, center[1]), x, y)[2] > 1000
            ) & (
                GEOD.inv(np.full(x.shape, center[0]), np.full(y.shape, center[1]), x, y)[2] < 3000
            ), 255)
        spot, alr, grid, sampler, index = synthetic_measurement(
            monkeypatch, center=center, sampler=sampler,
        )
        field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
        assert field == expected
        if has_gap:
            assert audit["unknownLandCellsWithin10km"]["wbm_nodata"] > 0
            covered, _ = _footprint(sampler, index, center, radius=3750)
            assert covered is False
        else:
            check = next(row for row in audit["haloRadiusChecks"] if row["radiusM"] == 3750)
            assert check["waterCoverage"] is True
            assert check["shapePass"] is True
            covered, _ = _footprint(sampler, index, center, radius=3750)
            assert covered is True


def test_real_builder_clips_wbm_water_at_disk_rim_with_outside_cell_centre(monkeypatch):
    center = (2.5, 46.67)
    sampler = RealWBMSampler(owners={(46, 2), (46, 3), (47, 2), (47, 3)})
    owner, row, col, pixel = _water_cell(center, 5010, sampler)
    dataset = sampler.tiles[owner][1]
    values = dataset.read(1)
    values[row, col] = 2
    dataset.write(values, 1)
    index = SyntheticIndex(e3_coverage_records(center))
    _, project, _ = _local_projection(center)
    disk = Point(0, 0).buffer(5000, quad_segs=96)
    projected_pixel = project(segmentize(pixel, max_segment_length=0.0005))
    center_lon, center_lat = dataset.transform @ (col + .5, row + .5)
    _, _, center_distance = GEOD.inv(center[0], center[1], center_lon, center_lat)
    assert center_distance > 5000
    expected_clipped = projected_pixel.intersection(disk).area
    assert 0 < expected_clipped < projected_pixel.area
    covered, water = _footprint(sampler, index, center, radius=5000)
    assert covered is True
    assert water.area == pytest.approx(expected_clipped, rel=1e-6)
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=center, sampler=sampler, index=index,
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert water_cell_fraction(expected_clipped, projected_pixel.area) == pytest.approx(
        expected_clipped / projected_pixel.area, rel=1e-6
    )
    assert field == {"v": 1, "radiusM": 3750}


@pytest.mark.parametrize(("tag", "expected_water"), [
    ({"intermittent": "yes"}, False),
    ({"intermittent": "true"}, False),
    ({"intermittent": "1"}, False),
    ({"intermittent": "no"}, True),
    ({"intermittent": "false"}, True),
    ({"tidal": "no"}, True),
    ({"tidal": "yes"}, False),
])
def test_real_footprint_builder_uses_affirmative_e3_water_tags(monkeypatch, tag, expected_water):
    center = (2.5, 46.67)
    sampler = RealWBMSampler(owners={(46, 2), (46, 3), (47, 2), (47, 3)})
    _, _, _, pixel = _water_cell(center, 3000, sampler)
    index = SyntheticIndex(e3_coverage_records(center, water=[(pixel, tag)]))
    covered, water = _footprint(sampler, index, center)
    assert covered is True
    assert (water.area > 0) is expected_water


@pytest.mark.parametrize("missing", ["wbm-adjacent", "e3-marine", "e3-inland"])
def test_real_footprint_builder_requires_each_coverage_source(missing):
    center = (2.5, 47.015)
    owners = {(47, 2)} if missing == "wbm-adjacent" else {(46, 2), (47, 2)}
    sampler = RealWBMSampler(owners=owners)
    index = SyntheticIndex(e3_coverage_records(
        center,
        include_marine=missing != "e3-marine",
        include_inland=missing != "e3-inland",
    ))
    covered, _ = _footprint(sampler, index, center, radius=3750)
    assert covered is False


def _split_e3_coverage(center, *, gap=0):
    lon, lat = center
    west, east = lon - .1, lon + .1
    south, north = lat - .1, lat + .1
    cut = lat + gap / 2
    lower = Polygon([
        (west, south), (east, south), (east, lat), (lon, lat), (west, lat), (west, south),
    ])
    upper = Polygon([
        (west, cut), (east, cut), (east, north), (west, north), (west, cut),
    ])
    return lower, upper


@pytest.mark.parametrize("layer", ["marine_land", "inland_coverage"])
def test_real_builder_unions_adjoining_e3_coverage_before_projection(layer):
    center = (2.5, 46.67)
    sampler = RealWBMSampler(owners={(46, 2), (46, 3), (47, 2), (47, 3)})
    left, right = _split_e3_coverage(center)
    records = [
        {"kind": kind, "geometry": geometry}
        for kind in ("marine_land", "inland_coverage")
        for geometry in ((left, right) if kind == layer else (box(center[0]-.1, center[1]-.1,
                                                                   center[0]+.1, center[1]+.1),))
    ]
    index = SyntheticIndex(records)
    covered, _ = _footprint(sampler, index, center, radius=6000)
    assert covered is True

    # A 0.0001° native gap is many square metres across the disk and remains
    # ineligible under the approved 1 m² numerical-sliver tolerance.
    gap_left, gap_right = _split_e3_coverage(center, gap=.0001)
    gap_records = [
        {"kind": kind, "geometry": geometry}
        for kind in ("marine_land", "inland_coverage")
        for geometry in ((gap_left, gap_right) if kind == layer else (box(
            center[0]-.1, center[1]-.1, center[0]+.1, center[1]+.1
        ),))
    ]
    incomplete, _ = _footprint(sampler, SyntheticIndex(gap_records), center, radius=6000)
    assert incomplete is False


def test_production_measurement_tolerates_e3_disk_round_trip_sliver(monkeypatch):
    center = (2.5, 46.67)
    sampler = RealWBMSampler(owners={(46, 2), (46, 3), (47, 2), (47, 3)})
    # Source coverage extends beyond the measured disk. On pyproj 3.7.2 /
    # PROJ 9.5.1, clipping it to the exact inverse-projected disk and
    # projecting it back leaves a 3.397 m² apparent gap. The production
    # measurement must use a conservative native query frame and retain the
    # original metric disk as its final decision domain.
    index = SyntheticIndex(e3_coverage_records(center))
    spot, alr, grid, sampler, index = synthetic_measurement(
        monkeypatch, center=center, sampler=sampler, index=index
    )
    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    assert field == {"v": 1, "radiusM": 3750}
    check = next(row for row in audit["haloRadiusChecks"] if row["radiusM"] == 3750)
    assert check["waterCoverage"] is True

    # A real coverage gap remains larger than the approved tolerance and
    # rejects the halo; the expanded E3 query frame must not fill it.
    gap = box(center[0] - .0001, center[1] - .1,
              center[0] + .1, center[1] + .1)
    gap_records = [
        {"kind": kind, "geometry": gap if kind == "marine_land" else box(
            center[0] - .5, center[1] - .5, center[0] + .5, center[1] + .5
        )}
        for kind in ("marine_land", "inland_coverage")
    ]
    gap_spot, gap_alr, gap_grid, _, gap_index = synthetic_measurement(
        monkeypatch, center=center, sampler=sampler, index=SyntheticIndex(gap_records)
    )
    gap_field, gap_audit = measure_sky_darkness_area(
        gap_spot, gap_alr, gap_grid, sampler, gap_index
    )
    assert gap_field == {"v": 1}
    assert any(row["shapePass"] and row["waterCoverage"] is False
               for row in gap_audit["haloRadiusChecks"])

    # Permanent E3 water still vetoes a halo even when both coverage layers
    # are complete beyond the disk.
    wet = box(center[0] - .5, center[1] - .5,
              center[0] + .5, center[1] + .5)
    wet_spot, wet_alr, wet_grid, _, wet_index = synthetic_measurement(
        monkeypatch, center=center, sampler=sampler,
        index=SyntheticIndex(e3_coverage_records(center, water=[(wet, {})])),
    )
    wet_field, wet_audit = measure_sky_darkness_area(
        wet_spot, wet_alr, wet_grid, sampler, wet_index
    )
    assert wet_field == {"v": 1}
    assert any(row["shapePass"] and row["waterCoverage"] is True
               and row["waterFraction"] >= .01
               for row in wet_audit["haloRadiusChecks"])


def test_rim_cell_water_uses_clipped_numerator_and_full_cell_in_production(monkeypatch):
    spot, alr, grid, sampler, _ = synthetic_measurement(monkeypatch)
    center = (spot["lon"], spot["lat"])
    tr, project, inverse = _local_projection(center)
    rows, cols = np.meshgrid(np.arange(alr.shape[0]), np.arange(alr.shape[1]), indexing="ij")
    xx, yy = grid @ (cols + .5, rows + .5)
    px, py = tr.transform(xx, yy)
    distance = np.hypot(px, py)
    outside = np.where(distance > 3750, distance, np.inf)
    row, col = np.unravel_index(np.argmin(outside), distance.shape)
    dx, dy = grid.a / 2, -grid.e / 2
    native_cell = box(xx[row, col] - dx, yy[row, col] - dy,
                      xx[row, col] + dx, yy[row, col] + dy)
    full_cell = project(native_cell)
    disk = Point(0, 0).buffer(3750, quad_segs=96)
    clipped_water = full_cell.intersection(disk)
    assert distance[row, col] > 3750
    water_geometry = inverse(clipped_water)
    index = SyntheticIndex(e3_coverage_records(center, water=[(water_geometry, {})]))

    field, audit = measure_sky_darkness_area(spot, alr, grid, sampler, index)
    check = next(row for row in audit["haloRadiusChecks"] if row["radiusM"] == 3750)
    expected_full_area = full_cell.area
    expected_clipped_area = clipped_water.area
    assert check["waterFraction"] < .01
    assert check["maxCellWaterAreaM2"] == pytest.approx(expected_clipped_area, rel=.03)
    assert check["maxCellAreaM2"] == pytest.approx(expected_full_area, rel=.03)
    assert check["maxCellWaterFraction"] == pytest.approx(
        expected_clipped_area / expected_full_area, rel=.03
    )
    assert field == {"v": 1}
