"""Focused tests for the pre-runtime GeoNames measurement tool."""

import csv
import hashlib
import json
import zipfile
from argparse import Namespace
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
import yaml
from rasterio.transform import from_bounds

from measure_landmarks import (
    FILTERED_FEATURE_CODES,
    GeoName,
    GeoNamesIndex,
    NearestIndex,
    analyse,
    analyse_multi_country,
    audit_named_islands,
    choose_match,
    compare_context_rasters,
    darkness_statistics,
    expanded_bbox,
    iter_geonames,
    load_country_records,
    markdown_report,
    raster_finite_extent,
    run_measurement,
    spot_extent,
)


def _row(geonameid, name, lat, lon, feature_class, feature_code, country="FR"):
    fields = [
        str(geonameid), name, name, "", str(lat), str(lon), feature_class,
        feature_code, country, "", "", "", "", "", "0", "", "0",
        "Europe/Paris", "2026-01-01",
    ]
    return "\t".join(fields) + "\n"


def _archive(tmp_path):
    path = tmp_path / "FR.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("FR.txt", "".join([
            _row(1, "Village", 45, 2, "P", "PPL"),
            _row(2, "Lac", 45.1, 2.1, "H", "LK"),
            _row(3, "Ruisseau", 45.1, 2.1, "H", "STM"),
            _row(4, "Département", 45.2, 2.2, "A", "ADM2"),
            _row(5, "Région", 45.3, 2.3, "A", "ADM1"),
            _row(6, "Other country", 45, 2, "P", "PPL", "ES"),
            _row(7, "Département hors bbox", 48, 5, "A", "ADM2"),
        ]))
    return path


def test_expanded_bbox_is_at_least_40_km_in_latitude():
    lon_min, lat_min, lon_max, lat_max = expanded_bbox((-1, 44, 3, 46))
    assert lon_min < -1 and lon_max > 3
    assert lat_min < 44 and lat_max > 46


def test_country_loader_filters_codes_and_keeps_admin_fallback(tmp_path):
    path = _archive(tmp_path)
    ordinary, admins, observed, total = load_country_records(
        path, "FR", (1, 3, 3, 46), {"PPL", "LK"}
    )
    assert {record.feature_code for record in ordinary} == {"PPL", "LK"}
    assert {record.feature_code for record in admins} == {"ADM1", "ADM2"}
    assert {record.name for record in admins} == {"Département", "Département hors bbox", "Région"}
    assert observed["STM"] == 1
    assert total == 5  # ES record is rejected before bbox/code accounting.
    match = choose_match(48, 5, NearestIndex([]), NearestIndex(admins))
    assert match.name == "Département hors bbox"


def test_iter_geonames_streams_only_requested_code(tmp_path):
    path = _archive(tmp_path)
    records = list(iter_geonames(path, "FR", (1, 3, 3, 46), {"LK"}))
    assert [record.name for record in records] == ["Lac"]


def test_admin2_is_preferred_even_when_adm1_centroid_is_closer():
    ordinary = NearestIndex([])
    admins = NearestIndex([
        GeoName(1, "Département", 45.3, 2.3, "A", "ADM2", "FR"),
        GeoName(2, "Région", 45.01, 2.01, "A", "ADM1", "FR"),
    ])
    match = choose_match(45, 2, ordinary, admins)
    assert match.name == "Département"
    assert match.tier == "ADM2"


def test_nearest_considers_all_coincident_points_for_id_tie_break():
    records = [
        GeoName(100 + i, f"duplicate-{i}", 45, 2, "P", "PPL", "FR")
        for i in range(12, 0, -1)
    ]
    nearest = NearestIndex(records).nearest(45, 2)
    assert nearest is not None
    assert nearest[0].geonameid == 101


def test_analyse_warns_by_data_field_and_returns_100_samples():
    records = [GeoName(1, "Village", 45, 2, "P", "PPL", "FR")]
    spots = [{"id": str(i), "lat": 45, "lon": 2, "darkness": 0.5, "near": ""} for i in range(101)]
    report = analyse(spots, records, [], expected_spots=100)
    assert report["spot_count"] == 101
    assert report["spot_count_warning"]
    assert len(report["samples"]) == 100
    assert report["distance_bins"]["under_5_km"] == 101


def _write_extract(root, country, records):
    extract = root / "extracts" / f"{country}.tsv"
    extract.parent.mkdir(parents=True, exist_ok=True)
    columns = list(FILTERED_FEATURE_CODES)
    # The runtime's public extract schema is deliberately not derived from
    # the order of feature codes.
    columns = ["geonameid", "name", "latitude", "longitude", "feature_class", "feature_code", "country_code"]
    with extract.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(records)
    return extract


def _filtered_fixture(tmp_path, countries=("GB", "IE")):
    root = tmp_path / "geonames"
    entries = {}
    for offset, country in enumerate(countries):
        records = [
            [100 + offset, "Région gaelique" if country == "IE" else "North Region", 53 + offset, -2 + offset, "A", "ADM1", country],
            [200 + offset, "County", 52 + offset, -2 + offset, "A", "ADM2", country],
            [300 + offset, "Árainn" if country == "IE" else "Wales", 52 + offset, -2 + offset, "P", "PPL", country],
        ]
        extract = _write_extract(root, country, records)
        entries[country] = {
            "source_url": f"https://download.geonames.org/export/dump/{country}.zip",
            "source_path": f"sources/{country}.zip",
            "downloaded_at": "2026-08-27",
            "source_sha256": "source-not-needed-in-runtime",
            "extract_path": f"extracts/{country}.tsv",
            "extract_sha256": hashlib.sha256(extract.read_bytes()).hexdigest(),
            "codes_applied": list(FILTERED_FEATURE_CODES),
        }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "countries": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return root


def test_darkness_statistics_has_invalid_quartiles_and_fixed_bins():
    report = darkness_statistics([0.0, 0.1, 0.25, 1.0, float("nan"), None, "bad"])
    assert report["invalid"] == 3
    assert report["min"] == 0.0
    assert report["median"] == 0.175
    assert report["bins_0_1"]["0.0-0.1"] == 1
    assert report["bins_0_1"]["0.1-0.2"] == 1
    assert report["bins_0_1"]["1.0-1.0"] == 1 if "1.0-1.0" in report["bins_0_1"] else report["bins_0_1"]["0.9-1.0"] == 1


def test_multi_country_audit_uses_filtered_extracts_utf8_and_reports_mismatch(tmp_path):
    root = _filtered_fixture(tmp_path)
    index = GeoNamesIndex.from_filtered_extracts(
        data_dir=root, countries=("GB", "IE"), feature_codes=FILTERED_FEATURE_CODES,
        bbox=(-11, 49, 2, 55),
    )
    spots = [
        {"id": "gb-1", "country": "GB", "lat": 52, "lon": -2, "darkness": 0.8,
         "near": "Town", "name": "Wales", "nameFeatureCode": "PPL", "nameDistanceKm": 0.0},
        {"id": "ie-1", "country": "IE", "lat": 53, "lon": -1, "darkness": 0.6,
         "near": "Baile", "name": "mauvais", "nameFeatureCode": "PPL", "nameDistanceKm": 0.0},
        {"id": "crown", "country": "GG", "lat": 49.5, "lon": -2.5, "darkness": 0.4},
    ]
    report = analyse_multi_country(
        spots, index, ("GB", "IE"),
        manifest=yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8")),
        bbox=(-11, 49, 2, 55),
    )
    assert report["spot_count"] == 2
    assert report["countries"]["GB"]["spot_count"] == 1
    assert report["countries"]["IE"]["spot_count"] == 1
    assert report["unexpected_country_codes"] == {"GG": 1}
    assert report["forbidden_crown_codes"]["GG"] == 1
    assert report["naming_divergences"]["count"] == 1
    assert any(row["name"] == "Árainn" for row in report["countries"]["IE"]["samples"])
    assert report["manifest"]["IE"]["extract_path"] == "extracts/IE.tsv"


def test_multi_country_report_keeps_input_extent_and_bbox_status():
    class Index:
        def resolve(self, spot, *, country):
            return SimpleNamespace(name="Audit", feature_code="PPL",
                                   name_distance_km=1.0, administrative_fallback=False)

    spots = [
        {"id": "inside", "country": "GB", "lat": 50, "lon": -2},
        {"id": "outside", "country": "GG", "lat": 56, "lon": 3},
    ]
    report = analyse_multi_country(spots, Index(), ("GB",), bbox=(-11, 49, 2, 55))
    assert spot_extent(spots) == [-2.0, 50.0, 3.0, 56.0]
    assert report["spot_bbox"] == [-2.0, 50.0, 3.0, 56.0]
    assert report["spots_outside_bbox"] == 1
    assert report["spots_bbox_ok"] is False


def test_sample_contains_separate_runtime_and_audit_fields_and_markdown():
    class Index:
        def resolve(self, spot, *, country):
            return SimpleNamespace(name="Audit name", feature_code="PPL",
                                   name_distance_km=2.0, administrative_fallback=False)

    report = analyse_multi_country([{
        "id": "one", "country": "GB", "lat": 50, "lon": -2,
        "name": "Displayed name", "nameFeatureCode": "PPL",
        "nameDistanceKm": 1.0,
    }], Index(), ("GB",))
    row = report["samples"][0]
    assert row["displayed_name"] == "Displayed name"
    assert row["runtime_name"] == "Displayed name"
    assert row["audit_name"] == "Audit name"
    assert row["runtime_code"] == "PPL"
    assert row["audit_distance_km"] == 2.0
    markdown = markdown_report(report)
    assert "Runtime name" in markdown and "Audit name" in markdown


def test_markdown_report_renders_all_v2_samples_with_escaped_pipes_and_utf8():
    samples = [{
        "country": "FR",
        "tier": "under_5",
        "id": f"spot-{index:03d}",
        "runtime_name": "Nom runtime | éclairé" if index == 0 else f"Runtime {index}",
        "runtime_code": "PPL|R" if index == 0 else "PPL",
        "runtime_distance_km": 1.25,
        "audit_name": "Nom audit | vérifié" if index == 0 else f"Audit {index}",
        "audit_code": "PPL|A" if index == 0 else "PPL",
        "audit_distance_km": 2.5,
        "near": "Près | nuit" if index == 0 else "",
        "darkness": "sombre | test" if index == 0 else 0.5,
    } for index in range(100)]
    report = {
        "country_codes": ["FR"],
        "spot_count": 100,
        "naming_divergences": {"count": 0},
        "darkness": {"bins_0_1": {}},
        "distance_tiers": {
            "under_5": 100, "5_to_25": 0, "25_to_40": 0, "ADM2": 0, "ADM1": 0,
        },
        "samples": samples,
    }

    markdown = markdown_report(report, bbox=(-5, 41, 10, 51))
    sample_lines = markdown.split("## Échantillon déterministe", 1)[1].split(
        "## Provenance GeoNames", 1
    )[0].splitlines()
    table_lines = [line for line in sample_lines if line.startswith("|")]

    assert table_lines[0] == (
        "| Pays | Tier | ID | Runtime name | Audit name | Runtime code | "
        "Runtime distance km | Audit code | Audit distance km | near | darkness |"
    )
    assert len(table_lines) == 102
    assert table_lines[2] == (
        "| FR | under_5 | spot-000 | Nom runtime \\| éclairé | Nom audit \\| vérifié | "
        "PPL\\|R | 1.250 | PPL\\|A | 2.500 | Près \\| nuit | sombre \\| test |"
    )
    assert "| FR | under_5 | spot-099 | Runtime 99 | Audit 99 | PPL | 1.250 | PPL | 2.500 |  | 0.5 |" in table_lines


def test_audit_run_measurement_rejects_stale_manifest(tmp_path):
    root = _filtered_fixture(tmp_path, countries=("GB",))
    manifest_path = root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["countries"]["GB"]["codes_applied"] = ["PPL"]
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    spots = tmp_path / "spots"
    spots.mkdir()
    args = Namespace(
        bbox=(-11, 49, 2, 55), country_code=["GB"], geonames_dir=root,
        spots_dir=spots, raster_path=None,
    )
    with pytest.raises(ValueError, match="stale.*GB"):
        run_measurement(args)


def test_finite_extent_reports_all_edges(tmp_path):
    path = tmp_path / "darkness.tif"
    data = np.full((4, 4), np.nan, dtype="float32")
    data[1:3, 1:3] = 0.5
    with rasterio.open(path, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_bounds(-2, 50, 2, 54, 4, 4)) as dst:
        dst.write(data, 1)
    report = raster_finite_extent(path, (-1, 51, 1, 53))
    assert report["finite_pixels"] == 4
    assert report["valid_bounds"] == [-1.0, 51.0, 1.0, 53.0]
    assert report["edge_coverage"]["all"] is True


def test_sample_selection_is_exactly_100_and_country_stratified(tmp_path):
    root = _filtered_fixture(tmp_path)
    index = GeoNamesIndex.from_filtered_extracts(
        data_dir=root, countries=("GB", "IE"), feature_codes=FILTERED_FEATURE_CODES,
        bbox=(-11, 49, 2, 55),
    )
    spots = []
    for i in range(120):
        country = "GB" if i % 2 == 0 else "IE"
        spots.append({"id": f"{country}-{i:03d}", "country": country,
                      "lat": 52, "lon": -2, "darkness": i / 120,
                      "near": "Town", "name": "Wales" if country == "GB" else "Árainn",
                      "nameFeatureCode": "PPL", "nameDistanceKm": 0.0})
    report = analyse_multi_country(spots, index, ("GB", "IE"))
    assert len(report["samples"]) == 100
    assert {row["country"] for row in report["samples"]} == {"GB", "IE"}
    assert all("darkness" in row and "near" in row for row in report["samples"])


def _write_raster(path, values):
    with rasterio.open(path, "w", driver="GTiff", height=2, width=2, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_bounds(0, 0, 2, 2, 2, 2)) as dst:
        dst.write(np.asarray(values, dtype="float32"), 1)


def test_compare_context_uses_common_finite_pixels_and_reports_bortle(tmp_path):
    dark_a, dark_b = tmp_path / "d300.tif", tmp_path / "d350.tif"
    bortle_a, bortle_b = tmp_path / "b300.tif", tmp_path / "b350.tif"
    _write_raster(dark_a, [[0.1, np.nan], [0.3, 0.4]])
    _write_raster(dark_b, [[0.2, 0.9], [0.3, 0.6]])
    _write_raster(bortle_a, [[3, np.nan], [4, 4]])
    _write_raster(bortle_b, [[4, 5], [4, 5]])
    report = compare_context_rasters(dark_a, dark_b, bortle_300=bortle_a, bortle_350=bortle_b)
    assert report["common_finite_pixels"] == 3
    assert report["mean_absolute_delta_darkness"] == pytest.approx(0.1, abs=1e-5)
    assert report["bortle_changes"]["changed_pixels"] == 2
    assert report["bortle_changes"]["rate"] == pytest.approx(2 / 3)


def test_named_island_audit_separates_natural_earth_and_crown_scope():
    report = audit_named_islands([
        {"lat": 50.68, "lon": -1.30, "country": "GB"},
        {"lat": 49.45, "lon": -2.58, "country": "GG"},
    ], ("GB", "IE"))
    isle = next(item for item in report["named_islands"] if item["name"] == "Isle of Wight")
    guernsey = next(item for item in report["crown_dependencies"] if item["country"] == "GG")
    assert isle["natural_earth_covered"] is True
    assert isle["status"] == "covered_with_spots"
    assert guernsey["natural_earth_covered"] is True
    assert guernsey["status"] == "out_of_scope_crown_dependency"


def test_crown_spatial_audit_ignores_spot_country_tag():
    class Land:
        def covers(self, point):
            return True

    class Geography:
        land = Land()

        def country_candidates(self, point):
            return {"GB"}

    report = audit_named_islands([
        {"id": "wrong-country", "lat": 49.45, "lon": -2.58, "country": "GB"},
    ], ("GB",), geography=Geography())
    guernsey = next(item for item in report["crown_dependencies"] if item["country"] == "GG")
    assert guernsey["forbidden_spatial_spots"] == ["wrong-country"]
    assert report["forbidden_spatial_spots"]["GG"] == ["wrong-country"]
    assert guernsey["forbidden_spatial_spot_count"] == 1
    assert report["forbidden_spatial_spot_count"]["GG"] == 1
