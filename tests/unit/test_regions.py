"""Tests for src/regions.py (regions.yaml loader + resolver)."""
import pytest


def write_regions(tmp_path, bboxes):
    entries = []
    for name, bbox in bboxes.items():
        entries.append(
            f"{name}:\n"
            f"  bbox: {bbox}\n"
            "  equal_area_epsg: 3035\n"
            "  admin_level: 8\n"
            "  osm_country_code: AA\n"
        )
    path = tmp_path / "regions.yaml"
    path.write_text("".join(entries))
    return path


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


def test_load_regions_rejects_non_integer_bbox(tmp_path):
    from src.regions import load_regions

    path = tmp_path / "regions.yaml"
    path.write_text(
        "alpha:\n"
        "  bbox: [1.2, 2, 3, 4]\n"
        "  equal_area_epsg: 3035\n"
        "  admin_level: 8\n"
        "  osm_country_code: AA\n"
    )
    with pytest.raises(ValueError, match="alpha.*integer"):
        load_regions(str(path))


def test_load_regions_rejects_positive_area_overlap_with_names(tmp_path):
    from src.regions import load_regions

    path = write_regions(
        tmp_path,
        {
            "france": [-6, 41, 8, 51],
            "italy": [7, 40, 12, 47],
        },
    )
    with pytest.raises(ValueError, match="france.*italy|italy.*france"):
        load_regions(str(path))


def test_integer_bbox_contains_every_intersecting_tile(tmp_path):
    from src.regions import load_regions, owned_tile_ids

    path = write_regions(tmp_path, {"france": [-6, 41, 8, 51]})
    regions = load_regions(str(path))
    tiles = owned_tile_ids("france", regions)
    assert "N041W006" in tiles
    assert "N050E007" in tiles
    assert "N051E000" not in tiles
    assert "N040E000" not in tiles


def test_legacy_owner_uses_declaration_order_without_strict_validation(tmp_path):
    from src.regions import load_regions, owner_for_tile

    path = write_regions(
        tmp_path,
        {
            "first": [0, 0, 2, 2],
            "second": [1, 1, 3, 3],
        },
    )
    regions = load_regions(
        str(path),
        allow_legacy_geometry=True,
        validate_partition=False,
    )
    assert owner_for_tile("N001E001", regions) == "first"


def test_edge_touching_bboxes_are_allowed(tmp_path):
    from src.regions import load_regions

    path = write_regions(
        tmp_path,
        {
            "west": [0, 0, 2, 2],
            "east": [2, 0, 4, 2],
        },
    )
    assert list(load_regions(str(path))) == ["west", "east"]


def test_tile_intersection_requires_positive_area():
    from src.regions import tile_intersects_bbox

    assert tile_intersects_bbox("N001E001", (1, 1, 2, 2))
    assert not tile_intersects_bbox("N001E001", (2, 1, 3, 2))
    assert not tile_intersects_bbox("N001E001", (1, 2, 2, 3))


def test_owned_tile_ids_rejects_non_one_degree_size(tmp_path):
    from src.regions import load_regions, owned_tile_ids

    path = write_regions(tmp_path, {"alpha": [0, 0, 2, 2]})
    regions = load_regions(str(path))
    with pytest.raises(ValueError, match="1"):
        owned_tile_ids("alpha", regions, tile_size_deg=0.5)


@pytest.mark.parametrize(
    "bbox",
    [
        [-181, 0, 0, 1],
        [0, -91, 1, 0],
        [0, 0, 181, 1],
        [0, 0, 1, 91],
    ],
)
def test_load_regions_rejects_bbox_outside_tile_domain(tmp_path, bbox):
    from src.regions import load_regions

    path = write_regions(tmp_path, {"alpha": bbox})
    with pytest.raises(ValueError, match="domain|longitude|latitude"):
        load_regions(str(path))


def test_load_regions_accepts_inclusive_tile_domain_boundaries(tmp_path):
    from src.regions import load_regions

    path = write_regions(tmp_path, {"world": [-180, -90, 180, 90]})
    assert list(load_regions(str(path))) == ["world"]


def test_load_regions_rejects_textual_bbox_scalar(tmp_path):
    from src.regions import load_regions

    path = tmp_path / "regions.yaml"
    path.write_text(
        "alpha:\n"
        "  bbox: [\"0\", 0, 1, 1]\n"
        "  equal_area_epsg: 3035\n"
        "  admin_level: 8\n"
        "  osm_country_code: AA\n"
    )
    with pytest.raises(ValueError, match="numeric"):
        load_regions(str(path))


def test_load_regions_rejects_boolean_bbox_scalar(tmp_path):
    from src.regions import load_regions

    path = write_regions(tmp_path, {"alpha": [False, 0, 1, 1]})
    with pytest.raises(ValueError, match="numeric"):
        load_regions(str(path))


def test_owner_for_tile_returns_none_outside_all_regions(tmp_path):
    from src.regions import load_regions, owner_for_tile

    path = write_regions(tmp_path, {"alpha": [0, 0, 2, 2]})
    regions = load_regions(str(path))
    assert owner_for_tile("N003E003", regions) is None


def legacy_regions_with_calibration_overlap(tmp_path):
    from src.regions import load_regions

    return load_regions(
        str(
            write_regions(
                tmp_path,
                {
                    "france": [-5.5, 41.2, 8.4, 50.8],
                    "massif_central": [7.2, 43.1, 10.2, 45.3],
                },
            )
        ),
        allow_legacy_geometry=True,
        validate_partition=False,
    )


def test_preview_accepts_current_decimal_bbox_in_legacy_mode(tmp_path):
    """A current decimal bbox remains inspectable when no candidate is supplied."""
    from src.regions import build_bbox_migration_preview, load_regions

    regions = load_regions(
        str(write_regions(tmp_path, {"france": [-5.5, 41.2, 8.4, 50.8]})),
        allow_legacy_geometry=True,
        validate_partition=False,
    )

    preview = build_bbox_migration_preview(regions, {})

    assert preview["current"]["france"]["bbox"] == [-5.5, 41.2, 8.4, 50.8]
    assert preview["proposed"]["france"]["bbox"] == [-6, 41, 9, 51]


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ((-5.5, 41, 8, 51), "integer"),
        ((-181, 41, 8, 51), "domain|longitude"),
        ((-6, 51, 8, 41), "ordered"),
    ],
)
def test_preview_rejects_unpublishable_explicit_candidate(
    tmp_path, candidate, message
):
    """Explicit candidates are strict even when current geometry is legacy."""
    from src.regions import build_bbox_migration_preview, load_regions

    regions = load_regions(
        str(write_regions(tmp_path, {"france": [-5.5, 41.2, 8.4, 50.8]})),
        allow_legacy_geometry=True,
        validate_partition=False,
    )

    with pytest.raises(ValueError, match=message):
        build_bbox_migration_preview(
            regions,
            {},
            {"france": candidate},
        )


def test_preview_reports_candidate_and_ownership_transition(tmp_path):
    """Preview exposes rounded geometry, tiles, overlaps, and ownership changes."""
    from src.regions import build_bbox_migration_preview

    regions = legacy_regions_with_calibration_overlap(tmp_path)
    published = {"N051E000": 30, "N050E008": 0, "N050E007": 12}

    preview = build_bbox_migration_preview(
        regions,
        published,
        {"france": (-6, 41, 8, 51)},
    )

    assert preview["proposed"]["france"]["bbox"] == [-6, 41, 8, 51]
    assert list(preview["proposed"]["orphans"]) == ["N050E008", "N051E000"]
    assert preview["transitions"][("france", None)] == ["N050E008"]
    assert ("france", "massif_central") in preview["overlaps"]
    assert preview["proposed"]["france"]["tiles"] == sorted(
        preview["proposed"]["france"]["tiles"]
    )


def test_preview_formats_current_proposed_tiles_and_transitions(tmp_path):
    """Formatted output is a human-readable read-only migration report."""
    from src.regions import build_bbox_migration_preview, format_bbox_migration_preview

    preview = build_bbox_migration_preview(
        legacy_regions_with_calibration_overlap(tmp_path),
        {"N050E008": 0},
        {"france": (-6, 41, 8, 51)},
    )

    rendered = format_bbox_migration_preview(preview)

    assert "france" in rendered
    assert "current bbox" in rendered
    assert "proposed bbox" in rendered
    assert "N050E008" in rendered
    assert "france -> unowned" in rendered


def test_preview_does_not_write_regions_yaml_or_spot_files(tmp_path):
    """Preview calculation only inspects supplied region and publication data."""
    from src.regions import build_bbox_migration_preview, load_regions

    regions_path = write_regions(tmp_path, {"france": [-6, 41, 8, 51]})
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    spot_file = spots_dir / "N050E007.json"
    spot_file.write_text('{"spots": []}')
    before = (regions_path.read_text(), spot_file.read_text())

    regions = load_regions(str(regions_path))
    build_bbox_migration_preview(regions, {"N050E007": 0})

    assert (regions_path.read_text(), spot_file.read_text()) == before


@pytest.mark.parametrize(
    ("region", "message"),
    [
        ({"bbox": [0, 0, 1, 1]}, "missing fields"),
        (
            {
                "bbox": [0, 0, float("nan"), 1],
                "equal_area_epsg": 3035,
                "admin_level": 8,
                "osm_country_code": "AA",
            },
            "finite",
        ),
        (
            {
                "bbox": [1, 0, 0, 1],
                "equal_area_epsg": 3035,
                "admin_level": 8,
                "osm_country_code": "AA",
            },
            "ordered",
        ),
    ],
)
def test_preview_keeps_non_legacy_geometry_validation(region, message):
    """Preview only relaxes integer and overlap validation."""
    from src.regions import build_bbox_migration_preview

    with pytest.raises(ValueError, match=message):
        build_bbox_migration_preview({"alpha": region}, {})
