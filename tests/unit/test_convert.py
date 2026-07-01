"""Tests for src/convert.py (ALR -> darkness + bortle conversion)."""
import numpy as np

from src.convert import alr_to_darkness, alr_to_bortle


def test_darkness_zero_alr():
    """Very low ALR (~0) -> darkness ~1 (very dark)."""
    d = alr_to_darkness(np.array([0.001]))
    assert d.shape == (1,)
    assert 0.9 < float(d[0]) <= 1.0


def test_darkness_mid_alr():
    """ALR=1.0 -> darkness = 0.5 (mid-scale, since ALR_DARK=0.1, ALR_BRIGHT=10)."""
    d = alr_to_darkness(np.array([1.0]))
    assert abs(float(d[0]) - 0.5) < 0.01


def test_darkness_high_alr():
    """ALR=100 -> darkness ~0 (very bright)."""
    d = alr_to_darkness(np.array([100.0]))
    assert float(d[0]) < 0.05


def test_darkness_nan_in_nan_out():
    """NaN input -> NaN output."""
    d = alr_to_darkness(np.array([float("nan")]))
    assert np.isnan(d[0])


def test_darkness_vectorized():
    """2D input, same shape output."""
    arr = np.array([[0.1, 1.0], [10.0, 100.0]])
    d = alr_to_darkness(arr)
    assert d.shape == (2, 2)


def test_bortle_known_values():
    """Specific ALR values map to expected Bortle classes."""
    b = alr_to_bortle(np.array([0.05, 0.5, 5.0, 100.0]))
    assert b.tolist() == [1, 4, 7, 8]


def test_bortle_shape_preserved():
    """2D input, same shape output."""
    arr = np.array([[0.1, 1.0], [10.0, 100.0]])
    b = alr_to_bortle(arr)
    assert b.shape == (2, 2)
