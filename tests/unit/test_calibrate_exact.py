import json

import pytest

from calibrate_exact import (
    alr_to_sqm,
    darkness_to_sqm,
    ideal_score,
    load_control_points,
    load_elevation_overrides,
    load_sample_points,
    load_spot_points,
    ranking_summary,
    correlation_summary,
    score_distribution,
    sqm_to_alr,
    sqm_to_darkness,
    sky_brightness_to_sqm,
    write_altitude_scatter_svg,
)


def test_control_points_are_loaded_from_json():
    points = load_control_points("validation/calibration_points.json")
    assert points[0]["country"] == "FR"
    assert points[4]["country"] == "ES"
    assert points[4]["sqm"] is None


def test_sqm_alr_conversion_is_explicit_and_reversible():
    sqm = 21.51
    alr = sqm_to_alr(sqm)
    assert alr > 0
    assert alr_to_sqm(alr) == pytest.approx(sqm)


def test_darkness_to_sqm_matches_pipeline_scale():
    darkness = sqm_to_darkness(21.51)
    assert darkness_to_sqm(darkness) == pytest.approx(21.51)


def test_ideal_score_uses_display_rounding():
    assert ideal_score(0.45) == 5
    assert ideal_score(0.44) == 4
    assert ideal_score(1.0) == 10


def test_score_distribution_reports_magnitude_not_only_boundary_crossing():
    assert score_distribution([0, -1, 1, -2, 3, -4]) == {"0": 1, "1": 2, "2": 1, "3+": 2}


def test_ranking_summary_uses_continuous_values():
    rows = [
        {"sqm_reference": 22.0, "sqm_pipeline": 21.9},
        {"sqm_reference": 21.0, "sqm_pipeline": 20.8},
        {"sqm_reference": 20.0, "sqm_pipeline": 20.1},
    ]
    result = ranking_summary(rows)
    assert result["spearman"] == pytest.approx(1.0)
    assert result["discordant_pairs"] == 0


def test_correlation_summary_reports_altitude_slope():
    rows = [
        {"elevation_m": 0, "delta_sqm": -0.1},
        {"elevation_m": 1000, "delta_sqm": -0.3},
        {"elevation_m": 2000, "delta_sqm": -0.5},
    ]
    result = correlation_summary(rows, "elevation_m")
    assert result["pearson_r"] == pytest.approx(-1.0)
    assert result["slope_per_km"] == pytest.approx(-0.2)


def test_elevation_override_loader(tmp_path):
    path = tmp_path / "elevations.json"
    path.write_text(json.dumps({"elevations_m": {"spot-1": 123}}), encoding="utf-8")
    assert load_elevation_overrides(path) == {"spot-1": 123.0}


def test_altitude_scatter_svg_is_written(tmp_path):
    output = tmp_path / "scatter.svg"
    write_altitude_scatter_svg([
        {"country": "FR", "elevation_m": 100, "delta_sqm": -0.1},
        {"country": "FR", "elevation_m": 500, "delta_sqm": -0.2},
    ], output)
    assert output.read_text(encoding="utf-8").startswith("<svg ")


def test_sky_brightness_uses_lightpollutionmap_faq31_anchor():
    assert sky_brightness_to_sqm(0.0) == pytest.approx(22.0)


def test_spot_loader_uses_exact_spot_coordinates(tmp_path):
    tile = tmp_path / "N045E003.json"
    tile.write_text(json.dumps({"spots": [{"id": "one", "lat": 45.5, "lon": 3.25}]}), encoding="utf-8")
    points = load_spot_points(tmp_path)
    assert points == [{
        "label": "one",
        "country": "FR",
        "region": "published spots",
        "lat": 45.5,
        "lon": 3.25,
        "sqm": None,
        "bortle_ref": None,
        "elevation_m": None,
        "source": "published_spot",
    }]


def test_sample_loader_marks_foreign_samples(tmp_path):
    path = tmp_path / "samples.json"
    path.write_text(json.dumps([{
        "label": "es-1", "country": "ES", "region": "sample",
        "lat": 42.8, "lon": -1.0, "sqm": None, "bortle_ref": None,
    }]), encoding="utf-8")
    assert load_sample_points(path)[0]["source"] == "foreign_sample"


def test_loader_rejects_missing_required_fields(tmp_path):
    path = tmp_path / "points.json"
    path.write_text(json.dumps([{"label": "missing coordinates"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_control_points(path)
