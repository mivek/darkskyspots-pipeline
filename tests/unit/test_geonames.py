"""Tests for the configurable GeoNames naming cascade."""

from pathlib import Path
import csv
import hashlib
import sys
import zipfile

import yaml

import pytest

from src.geonames import (
    FILTERED_EXTRACT_COLUMNS,
    FILTERED_FEATURE_CODES,
    GeoNamesIndex,
    NAMING_FEATURE_CODES,
    validate_filtered_extracts,
)
from tools.extract_geonames import build_manifest, extract_country, main as extract_main


def _row(
    geonameid: int,
    name: str,
    lat: float,
    lon: float,
    feature_class: str,
    feature_code: str,
    country: str = "FR",
) -> str:
    fields = [
        str(geonameid), name, name, "", str(lat), str(lon), feature_class,
        feature_code, country, "", "A", "B", "", "", "0", "", "", "Europe/Paris", "2026-01-01",
    ]
    return "\t".join(fields)


def _archive(tmp_path: Path, country: str = "FR", rows: list[str] | None = None) -> Path:
    directory = tmp_path / "data" / "geonames"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{country}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{country}.txt", "\n".join(rows or []))
    return path


def _source_archive(root: Path, country: str, rows: list[str]) -> Path:
    directory = root / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{country}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{country}.txt", "\n".join(rows))
    return path


def _index(tmp_path: Path, rows: list[str]) -> GeoNamesIndex:
    _archive(tmp_path, rows=rows)
    return GeoNamesIndex.from_archives(
        data_dir=tmp_path / "data",
        countries=["fr"],
        feature_codes=["LK", "PASS"],
        bbox=[-1, 44, 4, 49],
    )


def _filtered_dataset(
    tmp_path: Path,
    country_rows: dict[str, list[list[str]]],
    codes: tuple[str, ...] = FILTERED_FEATURE_CODES,
) -> Path:
    root = tmp_path / "filtered" / "geonames"
    extracts = root / "extracts"
    extracts.mkdir(parents=True, exist_ok=True)
    countries = {}
    for country, rows in country_rows.items():
        path = extracts / f"{country}.tsv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(FILTERED_EXTRACT_COLUMNS)
            writer.writerows(rows)
        countries[country] = {
            "source_url": f"https://download.geonames.org/export/dump/{country}.zip",
            "source_path": f"sources/{country}.zip",
            "downloaded_at": "2026-08-27",
            "source_sha256": "source-not-needed-by-runtime",
            "extract_path": f"extracts/{country}.tsv",
            "extract_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "codes_applied": list(codes),
        }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "countries": countries}, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path / "filtered"


def _filtered_row(geonameid, name, lat, lon, feature_class, feature_code, country):
    return [str(geonameid), name, str(lat), str(lon), feature_class, feature_code, country]


def test_loads_country_zip_filters_codes_and_expanded_bbox(tmp_path):
    index = _index(tmp_path, [
        _row(1, "Lac", 45, 0, "H", "LK"),
        _row(2, "Pass", 45, 0.4, "T", "PASS"),  # inside the 40 km expansion
        _row(3, "River", 45, 0, "H", "STM"),
        _row(4, "Far lake", 45, 5, "H", "LK"),  # outside bbox+40 km
        _row(5, "Region", 47, 1, "A", "ADM1"),
        _row(6, "Department", 46, 1, "A", "ADM2"),
    ])
    assert [r.name for r in index.ordinary_by_country["FR"]] == ["Lac", "Pass"]
    assert [r.name for r in index.adm1_by_country["FR"]] == ["Region"]
    assert [r.name for r in index.adm2_by_country["FR"]] == ["Department"]


def test_poleward_edge_keeps_38km_ordinary_feature_instead_of_admin_fallback(tmp_path):
    """A feature 38 km east of the north edge must remain an ordinary name.

    The old midpoint-based longitude margin used the bbox latitude midpoint
    (46° here), which was too narrow for the 51° poleward edge and dropped the
    feature before nearest-name resolution could consider it.
    """
    _archive(tmp_path, rows=[
        _row(1, "North-edge village", 51.0, 10.55, "P", "PPL"),
        _row(2, "Department fallback", 51.0, 10.0, "A", "ADM2"),
        _row(3, "Region fallback", 48.0, 2.0, "A", "ADM1"),
    ])
    index = GeoNamesIndex.from_archives(
        data_dir=tmp_path / "data",
        countries=["FR"],
        feature_codes=["PPL"],
        bbox=[-5, 41, 10, 51],
        margin_km=40,
    )

    result = index.resolve({"lat": 51.0, "lon": 10.0, "country": "FR"})

    assert result.name == "North-edge village"
    assert result.administrative_fallback is False
    assert result.name_distance_km is not None
    assert 37.0 < result.name_distance_km < 40.0


def test_ordinary_distance_is_pure_and_rounded(tmp_path):
    index = _index(tmp_path, [
        _row(20, "Village", 45, 0.1, "P", "LK"),
        _row(10, "Pass", 45, 0.2, "T", "PASS"),
        _row(30, "Region", 47, 1, "A", "ADM1"),
    ])
    result = index.resolve({"lat": 45, "lon": 0, "country": "FR"})
    assert result.name == "Village"
    assert result.feature_code == "LK"
    assert result.name_distance_km == round(result.name_distance_km, 3)
    assert result.name_distance_km is not None


def test_adm2_fallback_precedes_adm1_and_has_no_distance(tmp_path):
    index = _index(tmp_path, [
        _row(20, "Far region", 47, 1, "A", "ADM1"),
        _row(10, "Cantal", 45, 0.1, "A", "ADM2"),
    ])
    result = index.resolve({"lat": 45, "lon": 0, "country": "FR"})
    assert result.name == "Cantal"
    assert result.administrative_fallback is True
    assert result.name_distance_km is None
    assert result.as_dict()["nameDistanceKm"] is None


def test_absent_adm2_falls_back_to_adm1(tmp_path):
    index = _index(tmp_path, [_row(20, "Region", 47, 1, "A", "ADM1")])
    result = index.resolve({"lat": 45, "lon": 0, "country": "FR"})
    assert result.name == "Region"
    assert result.feature_code == "ADM1"
    assert result.name_distance_km is None


def test_equal_distance_uses_lowest_geonameid(tmp_path):
    index = _index(tmp_path, [
        _row(20, "Higher id", 45, 0.1, "H", "LK"),
        _row(10, "Lower id", 45, -0.1, "T", "PASS"),
        _row(30, "Region", 47, 1, "A", "ADM1"),
    ])
    result = index.resolve({"lat": 45, "lon": 0, "country": "FR"})
    assert result.name == "Lower id"


def test_missing_archive_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        GeoNamesIndex.from_archives(
            data_dir=tmp_path / "data", countries=["FR"], feature_codes=["LK"], bbox=[-1, 44, 4, 49]
        )


def test_missing_adm1_is_rejected(tmp_path):
    _archive(tmp_path, rows=[_row(1, "Lake", 45, 0, "H", "LK")])
    with pytest.raises(ValueError, match="ADM1"):
        GeoNamesIndex.from_archives(
            data_dir=tmp_path / "data", countries=["FR"], feature_codes=["LK"], bbox=[-1, 44, 4, 49]
        )


def test_enrich_spots_copies_input_and_guarantees_wire_fields(tmp_path):
    index = _index(tmp_path, [
        _row(1, "Lac", 45, 0, "H", "LK"),
        _row(2, "Region", 47, 1, "A", "ADM1"),
    ])
    original = {"lat": 45, "lon": 0, "country": "FR", "near": ""}
    enriched = index.enrich_spot(original)
    assert original == {"lat": 45, "lon": 0, "country": "FR", "near": ""}
    assert enriched["name"] == "Lac"
    assert "nameDistanceKm" in enriched
    assert enriched["near"] == ""


def test_extractor_filters_by_code_only_and_keeps_rows_outside_bbox(tmp_path):
    source = _archive(tmp_path, "GB", [
        _row(1, "In range", 51, -1, "P", "PPL", "GB"),
        _row(2, "Far but retained", 70, 20, "P", "PPL", "GB"),
        _row(3, "Excluded stream", 51, -1, "H", "STM", "GB"),
        _row(4, "Region", 55, 0, "A", "ADM1", "GB"),
    ])
    destination = tmp_path / "extract.tsv"
    assert extract_country(source, destination, "GB", ("PPL", "ADM1")) == 3
    text = destination.read_text(encoding="utf-8")
    assert "Far but retained" in text
    assert "Excluded stream" not in text


def test_filtered_extract_loads_multiple_countries_and_preserves_unicode(tmp_path):
    root = _filtered_dataset(tmp_path, {
        "GB": [
            _filtered_row(1, "England", 51, -1, "P", "PPL", "GB"),
            _filtered_row(2, "United Kingdom", 55, 0, "A", "ADM1", "GB"),
        ],
        "IE": [
            _filtered_row(3, "Dún Chaoin", 52, -10, "P", "PPL", "IE"),
            _filtered_row(4, "Éire", 53, -8, "A", "ADM1", "IE"),
        ],
    }, codes=("ADM1", "ADM2", "PPL"))
    index = GeoNamesIndex.from_filtered_extracts(
        data_dir=root,
        countries=["GB", "IE"],
        feature_codes=["PPL"],
        bbox=[-11, 49, 2, 55],
    )
    assert index.resolve({"lat": 51, "lon": -1, "country": "GB"}).name == "England"
    assert index.resolve({"lat": 52, "lon": -10, "country": "IE"}).name == "Dún Chaoin"
    assert "Dún Chaoin" in (root / "geonames" / "extracts" / "IE.tsv").read_text(encoding="utf-8")


def test_filtered_extract_keeps_administrative_fallback_countrywide(tmp_path):
    root = _filtered_dataset(tmp_path, {
        "IE": [
            _filtered_row(1, "County Cork", 51.9, -8.5, "A", "ADM2", "IE"),
            _filtered_row(2, "Ireland", 53.2, -8, "A", "ADM1", "IE"),
        ],
    }, codes=("ADM1", "ADM2", "PPL"))
    index = GeoNamesIndex.from_filtered_extracts(
        data_dir=root, countries=["IE"], feature_codes=["PPL"], bbox=[-1, 49, 1, 50]
    )
    result = index.resolve({"lat": 49.5, "lon": 0, "country": "IE"})
    assert result.name == "County Cork"
    assert result.feature_code == "ADM2"
    assert result.administrative_fallback is True


def test_stale_manifest_lists_old_and_current_codes_and_reextract_hint(tmp_path):
    root = _filtered_dataset(tmp_path, {
        "GB": [_filtered_row(1, "United Kingdom", 55, 0, "A", "ADM1", "GB")],
    })
    manifest_path = root / "geonames" / "manifest.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["countries"]["GB"]["codes_applied"] = ["ADM1"]
    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match=r"(?i)stale.*GB.*manifest codes.*current codes.*source_path.*source_url.*re-extract"):
        validate_filtered_extracts(data_dir=root, countries=["GB"], feature_codes=NAMING_FEATURE_CODES)


def test_missing_filtered_extract_is_explicit(tmp_path):
    root = _filtered_dataset(tmp_path, {
        "GB": [_filtered_row(1, "United Kingdom", 55, 0, "A", "ADM1", "GB")],
    })
    path = root / "geonames" / "extracts" / "GB.tsv"
    path.unlink()
    with pytest.raises(FileNotFoundError, match=r"filtered extract missing.*GB.*source_path.*source_url"):
        validate_filtered_extracts(data_dir=root, countries=["GB"], feature_codes=NAMING_FEATURE_CODES)


def test_partial_cli_extraction_merges_manifest_and_preserves_other_countries(tmp_path, monkeypatch):
    root = tmp_path / "geonames"
    countries = ("FR", "ES", "GB")
    dates = {country: "2026-08-25" for country in countries}
    for offset, country in enumerate(countries, start=1):
        source = _source_archive(root, country, [
            _row(offset, f"{country} region", 47, 1, "A", "ADM1", country),
            _row(offset + 10, f"{country} village", 47, 1, "P", "PPL", country),
        ])
        extract_country(source, root / "extracts" / f"{country}.tsv", country)

    before = build_manifest(root, countries, dates)
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(before, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    preserved = {
        country: dict(before["countries"][country]) for country in countries
    }
    _source_archive(root, "IE", [
        _row(100, "Ireland", 53, -8, "A", "ADM1", "IE"),
        _row(101, "Dublin", 53.3, -6.3, "P", "PPL", "IE"),
    ])

    monkeypatch.setattr(sys, "argv", [
        "extract_geonames.py",
        "--geonames-dir", str(root),
        "--country", "IE",
        "--downloaded-at", "IE=2026-08-27",
    ])
    assert extract_main() == 0

    after = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    assert set(after["countries"]) == {"FR", "ES", "GB", "IE"}
    for country in countries:
        assert after["countries"][country] == preserved[country]
    ie_entry = after["countries"]["IE"]
    ie_extract = root / "extracts" / "IE.tsv"
    ie_source = root / "sources" / "IE.zip"
    assert ie_entry["downloaded_at"] == "2026-08-27"
    assert ie_entry["source_sha256"] == hashlib.sha256(ie_source.read_bytes()).hexdigest()
    assert ie_entry["extract_sha256"] == hashlib.sha256(ie_extract.read_bytes()).hexdigest()
    assert ie_entry["codes_applied"] == list(FILTERED_FEATURE_CODES)


def test_custom_codes_are_applied_and_recorded_as_the_effective_list(tmp_path, monkeypatch):
    root = tmp_path / "geonames"
    source = _source_archive(root, "IE", [
        _row(1, "Ireland", 53, -8, "A", "ADM1", "IE"),
        _row(2, "County Cork", 52, -8, "A", "ADM2", "IE"),
        _row(3, "Dublin", 53.3, -6.3, "P", "PPL", "IE"),
        _row(4, "Excluded lake", 53.3, -6.3, "H", "LK", "IE"),
    ])
    requested_codes = ("PPL", "ADM1")
    monkeypatch.setattr(sys, "argv", [
        "extract_geonames.py",
        "--geonames-dir", str(root),
        "--country", "IE",
        "--code", "PPL",
        "--code", "ADM1",
        "--downloaded-at", "IE=2026-08-27",
    ])

    assert extract_main() == 0

    extract = root / "extracts" / "IE.tsv"
    text = extract.read_text(encoding="utf-8")
    assert "Dublin" in text
    assert "Ireland" in text
    assert "County Cork" in text
    assert "Excluded lake" not in text
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    expected_codes = sorted(set(requested_codes) | {"ADM2"})
    assert manifest["countries"]["IE"]["codes_applied"] == expected_codes
    assert manifest["countries"]["IE"]["extract_sha256"] == hashlib.sha256(
        extract.read_bytes()
    ).hexdigest()
    assert manifest["countries"]["IE"]["source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()


def test_admin_fallback_is_loaded_countrywide_outside_region_bbox(tmp_path):
    index = _index(tmp_path, [
        # Deliberately outside bbox+40: it must still be available as fallback.
        _row(9, "Distant region", 55, 20, "A", "ADM1"),
    ])
    result = index.resolve({"lat": 45, "lon": 0, "country": "FR"})
    assert result.name == "Distant region"
    assert result.name_distance_km is None


def test_multiple_country_archives_are_independent(tmp_path):
    _archive(tmp_path, "FR", [
        _row(1, "France region", 47, 1, "A", "ADM1", "FR"),
        _row(2, "French lake", 45, 0, "H", "LK", "FR"),
    ])
    _archive(tmp_path, "ES", [
        _row(3, "Spain region", 40, -3, "A", "ADM1", "ES"),
        _row(4, "Spanish lake", 40, -3, "H", "LK", "ES"),
    ])
    index = GeoNamesIndex.from_archives(
        data_dir=tmp_path / "data", countries="FR", feature_codes="LK", bbox=[-5, 41, 4, 49]
    )
    assert index.resolve({"lat": 45, "lon": 0, "country": "FR"}).name == "French lake"
    with pytest.raises(ValueError, match="not loaded"):
        index.resolve({"lat": 40, "lon": -3, "country": "ES"})
