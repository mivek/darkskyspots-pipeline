"""Focused tests for the pre-runtime GeoNames measurement tool."""

import zipfile

from measure_landmarks import (
    GeoName,
    NearestIndex,
    analyse,
    choose_match,
    expanded_bbox,
    iter_geonames,
    load_country_records,
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
