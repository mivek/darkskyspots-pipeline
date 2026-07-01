"""Tests for src/regions.py (regions.yaml loader + resolver)."""
import pytest


def test_load_regions_returns_dict():
    """Loads project regions.yaml, asserts key 'france'."""
    from src.regions import load_regions
    regions = load_regions("regions.yaml")
    assert isinstance(regions, dict)
    assert "france" in regions


def test_france_has_required_fields():
    """france entry has bbox, equal_area_epsg, admin_level, osm_country_code."""
    from src.regions import load_regions
    france = load_regions("regions.yaml")["france"]
    for key in ("bbox", "equal_area_epsg", "admin_level", "osm_country_code"):
        assert key in france, f"Missing required field: {key}"


def test_get_region_valid():
    """get_region('france') returns the france dict with the correct name."""
    from src.regions import get_region
    france = get_region("france")
    assert france["name"] == "France"
    assert france["equal_area_epsg"] == 3035


def test_get_region_invalid_keyerror():
    """get_region('atlantis') raises KeyError."""
    from src.regions import get_region
    with pytest.raises(KeyError):
        get_region("atlantis")


def test_region_bbox_is_list_of_numbers():
    """All 4 bbox elements are numbers (int or float)."""
    from src.regions import load_regions
    france = load_regions("regions.yaml")["france"]
    bbox = france["bbox"]
    assert len(bbox) == 4
    for v in bbox:
        assert isinstance(v, (int, float))


def test_load_regions_invalid_yaml(tmp_path):
    """Loading a malformed yaml raises ValueError."""
    from src.regions import load_regions
    bad = tmp_path / "bad.yaml"
    bad.write_text("- this is\n- a list, not a mapping\n")
    with pytest.raises(ValueError):
        load_regions(str(bad))
