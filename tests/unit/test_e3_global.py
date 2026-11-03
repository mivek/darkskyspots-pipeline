"""Global geographic semantics without additional regional downloads."""

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, Polygon, Point

from src.geographic_confidence import confidence, priority
from src.geographic_geometry import canonical, domain
from src.geographic_index import build_index, GeographicIndex
from src.geographic_data import file_digest, valid_geometry
from src.worldcover import WorldCover
from src.extract import mesh_darkest, redundancy_filter
from src.tile_export import enumerate_tiles_in_bbox
from tests.geographic_helpers import feature


def cache(root, tiles):
    root.mkdir()
    entries = {}
    for name, (west, south, value) in tiles.items():
        path = root / (name + ".tif")
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=4,
            height=4,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_origin(west, south + 3, 0.75, 0.75),
            nodata=0,
        ) as out:
            out.write(np.full((4, 4), value, dtype="uint8"), 1)
        entries[name] = dict(
            path=path.name, size=path.stat().st_size, sha256=file_digest(path)
        )
    (root / "manifest.json").write_text(
        json.dumps(dict(product="ESA WorldCover 2021 v200", tiles=entries))
    )
    return WorldCover(root)


def test_worldcover_native_parallel_boundary_and_antimeridian(tmp_path):
    wc = cache(
        tmp_path / "wc",
        {
            "N00E177": (177, 0, 30),
            "N00W180": (-180, 0, 80),
            "S03E000": (0, -3, 30),
            "N00E000": (0, 0, 80),
        },
    )
    # Exact latitude zero samples the north edge of the southern tile. Its
    # positive-area WBM support can lie wholly in the northern tile.
    sample = wc.sample(
        [dict(point={"lat": 0, "lon": 0.1}, wbm_support=[0, 0, 0.2, 0.2])]
    )[0]
    assert sample["centre"] == 30 and set(sample["support_classes"]) == {"30", "80"}
    assert sample["windows"][-1]["role"] == "exact_VIIRS_centre"
    # A native support crossing 180 retains evidence from BOTH source tiles.
    sample = wc.sample(
        [dict(point={"lat": 1, "lon": -180}, wbm_support=[179.9, 0.9, 180.1, 1.1])]
    )[0]
    assert sample["centre"] == 80 and set(sample["support_classes"]) == {"30", "80"}
    assert {w["tile"] for w in sample["windows"]} == {"N00E177", "N00W180"}
    wc.close()


def test_high_latitude_unavailability_is_explicit_not_missing_expected_file(tmp_path):
    wc = cache(tmp_path / "wc", {})
    sample = wc.sample(
        [dict(point={"lat": 85, "lon": 10}, wbm_support=[10, 84.9, 10.1, 85.1])]
    )[0]
    assert (
        sample["available"] is False
        and sample["unavailable_reason"] == "outside_product_support"
    )
    ev = dict(
        wbm_class=0,
        marine_support_land=True,
        inland_support_coverage=True,
        water_features=[],
        wetland_features=[],
        reasons=[],
    )
    assert confidence(ev, sample)["level"] == "B1"
    with pytest.raises(ValueError, match="cache incomplete"):
        wc.sample(
            [dict(point={"lat": 50, "lon": 10}, wbm_support=[10, 49.9, 10.1, 50.1])]
        )
    wc.close()


def test_missing_corrupt_and_unexpected_worldcover_are_technical_failures(tmp_path):
    wc = cache(tmp_path / "wc", {"N00E000": (0, 0, 17)})
    with pytest.raises(ValueError, match="Unexpected WorldCover class"):
        wc.sample([dict(point={"lat": 1, "lon": 1}, wbm_support=[0.9, 0.9, 1.1, 1.1])])
    wc.close()
    (tmp_path / "wc/N00E000.tif").write_bytes(b"broken")
    with pytest.raises(ValueError, match="integrity error"):
        WorldCover(tmp_path / "wc")


def test_complete_feature_survives_regional_index_boundaries_and_holes(tmp_path):
    water = Polygon(
        [(-2, -1), (2, -1), (2, 1), (-2, 1)],
        holes=[[(-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2)]],
    )
    full = feature(water, "water", id="same-authoritative-river")
    for name, extent in [("west", box(-2, -2, 0, 2)), ("east", box(0, -2, 2, 2))]:
        # Preparation retains the whole feature; its regional index may only
        # change lookup scope, never clip the source geometry to the region.
        assert water.intersects(extent)
        build_index(tmp_path / name, [full])
        index = GeographicIndex(tmp_path / name)
        indexed, _ = index.feature(1)
        assert indexed.equals_exact(water, 0)
        result = index.observe(
            [{"lat": 0, "lon": 0}, {"lat": 0.5, "lon": 0}],
            [box(-0.01, -0.01, 0.01, 0.01), box(-0.01, 0.49, 0.01, 0.51)],
        )
        assert result[0]["water_features"] == [] and result[1]["water_features"]
        index.close()


def test_crossing_holes_are_normalized_before_planar_validation(tmp_path):
    raw = Polygon(
        [(179, -2), (-179, -2), (-179, 2), (179, 2)],
        holes=[[(179.8, -0.2), (-179.8, -0.2), (-179.8, 0.2), (179.8, 0.2)]],
    )
    from shapely.geometry import mapping

    normalized = valid_geometry(mapping(raw), polygon=True)
    assert normalized.is_valid and domain([179, -2, -179, 2]).covers(normalized)
    build_index(tmp_path / "index", [feature(raw, "water")])
    index = GeographicIndex(tmp_path / "index")
    assert index.feature(1)[0].equals(normalized)
    index.close()
    assert set(enumerate_tiles_in_bbox((179, -1, -179, 1))) == {
        "S001E179",
        "S001W180",
        "N000E179",
        "N000W180",
    }


@pytest.mark.parametrize(
    "levels,expected",
    [(["A", "B1", "B2"], "A"), (["B1", "B2", "C"], "B1"), (["B2", "C", "C"], "B2")],
)
def test_fallback_tiers_and_stable_ties(levels, expected):
    darkness = np.array([[0.9, 0.8, 0.7], [0.6, 0.5, 0.4], [0.3, 0.2, 0.1]])

    def qualify(points):
        for p in points:
            p["_geographic_level"] = levels[p["col"]]
        return [p for p in points if p["_geographic_level"] != "C"]

    out = mesh_darkest(
        darkness,
        rasterio.Affine(0.01, 0, 0, 0, -0.01, 1),
        qualify=qualify,
        priority_key=priority,
    )
    assert (
        len(out) == 1 and out[0]["_geographic_level"] == expected and out[0]["row"] == 0
    )


def test_lower_tier_cannot_suppress_higher_tier_and_strict_distance_is_preserved():
    pool = [
        dict(lat=0, lon=0, darkness=0.5, bortle=3, _geographic_level="A"),
        dict(lat=0, lon=0.001, darkness=0.99, bortle=3, _geographic_level="B2"),
    ]
    assert redundancy_filter(pool, priority_key=priority) == pool[:1]
    # Exercise the exact existing comparison, independently of haversine
    # floating point at a mathematically constructed coordinate.
    with patch("src.extract.haversine_km", return_value=15.0):
        assert len(redundancy_filter(pool, priority_key=priority)) == 2
