"""Step 0: thin wrapper around the nightskyquality fork's radiance_to_alr."""
import math
import os
import tempfile
from typing import NamedTuple

import numpy as np
import rasterio
from rasterio.windows import Window

from nightskyquality import ALRResult, radiance_to_alr

from .config import (
    ALR_ALPHA_BASE,
    ALR_ALPHA_EXP,
    ALR_CALIB_C,
    ALR_MAX_KM,
    ALR_NOISE_FLOOR,
    ALR_RINGS,
    ALR_WORK_RESOLUTION_M,
)


class SliceResult(NamedTuple):
    """The data array has the same shape as the input raster.
    transform and crs are the input raster's (projected) transform and CRS."""

    data: np.ndarray
    transform: rasterio.Affine
    crs: rasterio.crs.CRS


def compute_alr(
    input_path: str,
    equal_area_epsg: int,
    *,
    work_resolution_m: int = ALR_WORK_RESOLUTION_M,
    rings: int = ALR_RINGS,
    max_km: int = ALR_MAX_KM,
    alpha_base: float = ALR_ALPHA_BASE,
    alpha_exp: float = ALR_ALPHA_EXP,
    calib_c: float = ALR_CALIB_C,
    noise_floor: float = ALR_NOISE_FLOOR,
) -> ALRResult:
    """Compute ALR from a radiance GeoTIFF using the nightskyquality fork.

    Args:
        input_path: Path to input radiance GeoTIFF.
        equal_area_epsg: EPSG code for the equal-area projection.
        overrides: Fork parameter overrides (for testing or calibration).
    """
    return radiance_to_alr(
        radiance_path=input_path,
        equal_area_epsg=equal_area_epsg,
        work_resolution_m=work_resolution_m,
        rings=rings,
        max_km=max_km,
        alpha_base=alpha_base,
        alpha_exp=alpha_exp,
        calib_c=calib_c,
        noise_floor=noise_floor,
    )


def estimate_input_ram(path: str) -> float:
    """Estimate memory needed to load the full GeoTIFF in MB (float64)."""
    with rasterio.open(path) as src:
        rows, cols = src.height, src.width
        bands = src.count
    bytes_total = rows * cols * bands * 8  # float64
    return bytes_total / (1024 * 1024)


def check_ram_budget(path: str, budget_mb: float = 500.0) -> bool:
    """True if the input file fits within budget_mb when fully loaded."""
    return estimate_input_ram(path) <= budget_mb


def _slice_windows(
    width: int,
    height: int,
    max_pixels: int = 4_000_000,
    *,
    overlap_px: int = 0,
):
    """Yield (read_window, x_out, y_out, w_out, h_out, x_in_read, y_in_read)."""
    step_y = max(1, max_pixels // max(width, 1))
    step_x = max(1, max_pixels // max(height, 1))
    for y in range(0, height, step_y):
        h = min(step_y, height - y)
        for x in range(0, width, step_x):
            w = min(step_x, width - x)
            read_x = max(0, x - overlap_px)
            read_y = max(0, y - overlap_px)
            read_w = min(width - read_x, w + 2 * overlap_px)
            read_h = min(height - read_y, h + 2 * overlap_px)
            read_window = Window(read_x, read_y, read_w, read_h)
            x_in_read = x - read_x
            y_in_read = y - read_y
            yield read_window, x, y, w, h, x_in_read, y_in_read


def slice_and_compute(
    input_path: str,
    equal_area_epsg: int,
    *,
    max_window_pixels: int = 4_000_000,
    budget_mb: float = 500.0,
    **alr_kwargs,
) -> SliceResult:
    """Compute ALR for the full input, slicing if needed.

    If the input fits in budget_mb, calls compute_alr directly.
    Otherwise slices the input into windows, computes ALR per slice,
    and stitches the results.

    Returns the full ALR data as 2D float64 ndarray with original geo metadata.
    """
    with rasterio.open(input_path) as src:
        full_width, full_height = src.width, src.height
        full_crs = src.crs
        full_transform = src.transform

    if check_ram_budget(input_path, budget_mb):
        alr_result = compute_alr(input_path, equal_area_epsg, **alr_kwargs)
        return SliceResult(
            data=alr_result.data,
            transform=alr_result.profile["transform"],
            crs=alr_result.profile["crs"],
        )

    # The ALR kernel NaN halo is R_px = int(ALR_MAX_KM * 1000 / work_resolution_m)
    # at the *work* resolution. When the input CRS matches equal_area_epsg,
    # reproject_raster is a no-op and the computation runs at input resolution,
    # so the halo is R_px pixels regardless of input pixel size. We compute
    # overlap_px from the work resolution to guarantee full coverage.
    overlap_px = int(math.ceil(ALR_MAX_KM * 1000 / ALR_WORK_RESOLUTION_M))

    result = np.full((full_height, full_width), np.nan, dtype=np.float64)
    for read_window, xoff, yoff, w_out, h_out, x_in_read, y_in_read in _slice_windows(
        full_width, full_height, max_window_pixels, overlap_px=overlap_px
    ):
        with rasterio.open(input_path) as src:
            data = src.read(1, window=read_window)
            win_transform = rasterio.windows.transform(read_window, full_transform)
            profile = src.profile.copy()
            profile.update(
                {
                    "height": read_window.height,
                    "width": read_window.width,
                    "transform": win_transform,
                }
            )
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with rasterio.open(tmp_path, "w", **profile) as dst:
                dst.write(data, 1)
            slice_alr = compute_alr(tmp_path, equal_area_epsg, **alr_kwargs)
            inner = slice_alr.data[y_in_read : y_in_read + h_out, x_in_read : x_in_read + w_out]
            result[yoff : yoff + h_out, xoff : xoff + w_out] = inner
        finally:
            os.unlink(tmp_path)
    return SliceResult(data=result, transform=full_transform, crs=full_crs)
