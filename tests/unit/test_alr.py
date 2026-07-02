"""Tests for src/alr.py (step 0 wrapper + RAM check + slice_and_compute)."""
import numpy as np
import pytest

from src.alr import compute_alr, estimate_input_ram, check_ram_budget, slice_and_compute, _slice_windows


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


def test_slice_windows_overlap():
    """Overlapping windows cover every pixel in a 2000x1000 raster."""
    windows = list(_slice_windows(width=2000, height=1000, max_pixels=1_000_000, overlap_px=50))
    assert len(windows) >= 2
    # Full coverage: every pixel must be in at least one output rect
    coverage = set()
    for _, xoff, yoff, w_out, h_out, x_in_read, y_in_read in windows:
        for y in range(yoff, yoff + h_out):
            for x in range(xoff, xoff + w_out):
                coverage.add((x, y))
        # Interior windows should have overlap_px for both read offsets
        if xoff > 0:
            assert x_in_read == 50, f"x_in_read={x_in_read} at xoff={xoff}"
        if yoff > 0:
            assert y_in_read == 50, f"y_in_read={y_in_read} at yoff={yoff}"
    for x in range(2000):
        for y in range(1000):
            assert (x, y) in coverage, f"Pixel ({x},{y}) not covered"


def test_slice_windows_no_overlap_unchanged():
    """With overlap_px=0, behavior is the same as contiguous non-overlapping."""
    windows = list(_slice_windows(width=1000, height=500, max_pixels=250_000, overlap_px=0))
    covered = 0
    for read_window, xoff, yoff, w_out, h_out, x_in_read, y_in_read in windows:
        assert x_in_read == 0, f"x_in_read={x_in_read} should be 0"
        assert y_in_read == 0, f"y_in_read={y_in_read} should be 0"
        assert w_out == read_window.width, "w_out should match read width when no overlap"
        assert h_out == read_window.height, "h_out should match read height when no overlap"
        covered += w_out * h_out
    assert covered == 1000 * 500, f"Covered {covered} instead of {1000*500}"


def test_slice_and_compute_overlap_happy_path(wide_geotiff, mock_region):
    """Forced-slicing with overlap produces a result with valid interior data.

    ALR kernel has a ~667-pixel NaN halo (computed as `ceil(ALR_MAX_KM * 1000 / ALR_WORK_RESOLUTION_M)`).
    Overlap > halo ensures seam-free stitching.
    """
    result = slice_and_compute(
        str(wide_geotiff),
        mock_region["equal_area_epsg"],
        budget_mb=0.0001,
        max_window_pixels=1_000_000,
    )
    assert result.data.shape == (2000, 2000), f"Shape is {result.data.shape}"
    # The ALR kernel halo is ~647px. Valid interior starts at ~647px from edges.
    # Use a conservative margin of 700px on each side to check the valid core.
    margin = 700
    interior = result.data[margin:2000 - margin, margin:2000 - margin]
    nan_ratio = np.isnan(interior).sum() / interior.size
    assert nan_ratio < 0.05, f"NaN ratio {nan_ratio:.4f} exceeds 5%"
    # Check that valid (non-NaN) horizontal span is wide
    y_mid = result.data.shape[0] // 2
    row = result.data[y_mid, :]
    valid = ~np.isnan(row)
    valid_cols = valid.sum()
    assert valid_cols > 500, f"Only {valid_cols} non-NaN columns at mid-row"
