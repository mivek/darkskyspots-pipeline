"""Tests for the region registry and country configuration."""

import pytest


def write_regions(tmp_path, entries):
    lines = []
    for name, (bbox, codes) in entries.items():
        rendered = "[" + ", ".join(codes) + "]" if isinstance(codes, list) else codes
        lines.append(
            f"{name}:\n"
            f"  bbox: {bbox}\n"
            "  equal_area_epsg: 3035\n"
            "  admin_level: 8\n"
            f"  osm_country_code: {rendered}\n"
        )
    path = tmp_path / "regions.yaml"
    path.write_text("".join(lines))
    return path


def test_load_regions_returns_dict():
    from src.regions import load_regions
    regions = load_regions("regions.yaml")
    assert isinstance(regions, dict)
    assert "france" in regions


def test_france_has_required_fields_and_normalised_country_list():
    from src.regions import load_regions
    france = load_regions("regions.yaml")["france"]
    assert all(key in france for key in ("bbox", "equal_area_epsg", "admin_level", "osm_country_code"))
    assert france["osm_country_code"] == ["FR"]


def test_get_region_valid_and_invalid():
    from src.regions import get_region
    assert get_region("france")["name"] == "France"
    with pytest.raises(KeyError):
        get_region("atlantis")


def test_load_regions_invalid_yaml(tmp_path):
    from src.regions import load_regions
    path = tmp_path / "bad.yaml"
    path.write_text("- this is\n- a list, not a mapping\n")
    with pytest.raises(ValueError):
        load_regions(str(path))

def test_load_regions_accepts_multiple_codes_for_one_region(tmp_path):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"benelux": ([0, 0, 3, 3], ["BE", "NL", "LU"])})
    assert load_regions(str(path))["benelux"]["osm_country_code"] == ["BE", "NL", "LU"]


def test_load_regions_rejects_duplicate_codes_across_regions(tmp_path):
    from src.regions import load_regions
    path = write_regions(
        tmp_path,
        {"one": ([0, 0, 1, 1], "AA"), "two": ([1, 0, 2, 1], "AA")},
    )
    with pytest.raises(ValueError, match="AA.*both regions"):
        load_regions(str(path))


def test_load_regions_rejects_duplicate_codes_inside_region(tmp_path):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"one": ([0, 0, 1, 1], ["AA", "AA"])})
    with pytest.raises(ValueError, match="duplicates"):
        load_regions(str(path))


@pytest.mark.parametrize(
    "bbox",
    [[-181, 0, 0, 1], [0, -91, 1, 0], [0, 0, 181, 1], [0, 0, 1, 91]],
)
def test_load_regions_rejects_bbox_outside_tile_domain(tmp_path, bbox):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"alpha": (bbox, "AA")})
    with pytest.raises(ValueError, match="domain|longitude|latitude"):
        load_regions(str(path))


def test_load_regions_accepts_inclusive_tile_domain_boundaries(tmp_path):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"world": ([-180, -90, 180, 90], "AA")})
    assert list(load_regions(str(path))) == ["world"]


def test_load_regions_rejects_non_integer_bbox(tmp_path):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"alpha": ([1.2, 2, 3, 4], "AA")})
    with pytest.raises(ValueError, match="integer"):
        load_regions(str(path))


def test_load_regions_rejects_bad_country_code_shape(tmp_path):
    from src.regions import load_regions
    path = write_regions(tmp_path, {"alpha": ([0, 0, 1, 1], ["FRA"])})
    with pytest.raises(ValueError, match="ISO alpha-2"):
        load_regions(str(path))


def test_load_regions_rejects_non_mapping(tmp_path):
    from src.regions import load_regions
    path = tmp_path / "regions.yaml"
    path.write_text("alpha: nope\n")
    with pytest.raises(ValueError, match="mapping"):
        load_regions(str(path))
