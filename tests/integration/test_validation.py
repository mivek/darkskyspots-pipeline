"""Integration tests for the validation procedure with synthetic data."""
import numpy as np
import pytest

from src.alr import compute_alr
from src.convert import alr_to_darkness, alr_to_bortle


def test_validation_with_synthetic_fixture(tmp_geotiff, mock_region):
    """Run step 0+1 on a synthetic GeoTIFF and verify darkness/bortle arrays.

    For a synthetic 10x10 image, the fork returns a mix of NaN (halo) and
    computed values. We verify the arrays have the expected shape and dtype
    and that the functions complete without error.
    """
    alr = compute_alr(str(tmp_geotiff), mock_region["equal_area_epsg"])
    darkness = alr_to_darkness(alr.data)
    bortle = alr_to_bortle(alr.data)
    assert darkness.shape == (10, 10)
    assert bortle.shape == (10, 10)
    assert darkness.dtype == np.float64
    assert bortle.dtype == np.int8
    # At least some NaN values from the halo, some non-NaN from the core
    assert np.any(np.isnan(darkness)), "Expected at least some NaN values from the halo"
