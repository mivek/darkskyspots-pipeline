"""Tests for the configurable GeoNames naming cascade."""

from pathlib import Path
import zipfile

import pytest

from src.geonames import GeoNamesIndex


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


def _index(tmp_path: Path, rows: list[str]) -> GeoNamesIndex:
    _archive(tmp_path, rows=rows)
    return GeoNamesIndex.from_archives(
        data_dir=tmp_path / "data",
        countries=["fr"],
        feature_codes=["LK", "PASS"],
        bbox=[-1, 44, 4, 49],
    )


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
