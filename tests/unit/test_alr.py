"""Tests for src/alr.py (step 0 wrapper + RAM check + slice_and_compute)."""
import numpy as np
import pytest

from src.alr import compute_alr, estimate_input_ram, check_ram_budget, slice_and_compute


def test_compute_alr_returns_alrresult(tmp_geotiff, mock_region):
    """compute_alr returns an ALRResult with .data and .profile."""
    result = compute_alr(str(tmp_geotiff), mock_region["equal_area_epsg"])
    assert hasattr(result, "data")
    assert hasattr(result, "profile")


def test_compute_alr_data_is_float64(tmp_geotiff, mock_region):
    """ALR data is float64."""
    result = compute_alr(str(tmp_geotiff), mock_region["equal_area_epsg"])
    assert result.data.dtype == np.float64


def test_compute_alr_accepts_overrides(tmp_geotiff, mock_region):
    """Pass overrides without error (small image may give all-NaN, that's fine)."""
    result = compute_alr(
        str(tmp_geotiff),
        mock_region["equal_area_epsg"],
        work_resolution_m=1000,
    )
    assert result.data.shape == (10, 10)


def test_estimate_input_ram(tmp_geotiff):
    """10x10 float64 single-band = 800 bytes ≈ 0.0008 MB."""
    ram = estimate_input_ram(str(tmp_geotiff))
    assert 0.0001 < ram < 0.01


def test_check_ram_budget_under(tmp_geotiff):
    """A 10x10 file fits in a generous budget."""
    assert check_ram_budget(str(tmp_geotiff), budget_mb=1.0) is True


def test_check_ram_budget_over(tmp_geotiff):
    """A 10x10 file does not fit in a tiny budget."""
    assert check_ram_budget(str(tmp_geotiff), budget_mb=0.0001) is False


def test_slice_and_compute_small_file(tmp_geotiff, mock_region):
    """Tiny file with tiny budget forces slicing path; no crash."""
    # budget_mb=0.0001 forces slicing on the 10x10 file
    result = slice_and_compute(
        str(tmp_geotiff),
        mock_region["equal_area_epsg"],
        budget_mb=0.0001,
    )
    assert result.data.shape == (10, 10)
    assert result.crs is not None
    assert result.transform is not None
