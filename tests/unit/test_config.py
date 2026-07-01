"""Tests for src/config.py (constants + Bortle table)."""
import numpy as np

from src.config import bortle_class, MESH_KM, REDUNDANCY_KM, ALR_DARK, ALR_BRIGHT, BORTLE_THRESHOLDS


def test_bortle_1():
    """Very low ALR → Bortle 1."""
    assert bortle_class(0.05) == 1


def test_bortle_4():
    """ALR 0.5 → Bortle 4."""
    assert bortle_class(0.5) == 4


def test_bortle_7():
    """ALR 5.0 → Bortle 7."""
    assert bortle_class(5.0) == 7


def test_bortle_8():
    """ALR 100 → Bortle 8."""
    assert bortle_class(100.0) == 8


def test_bortle_nan():
    """NaN → Bortle 9 (urban worst-case)."""
    assert bortle_class(float("nan")) == 9


def test_bortle_boundary():
    """Exact boundary (0.10) is inclusive → Bortle 1."""
    assert bortle_class(0.10) == 1


def test_bortle_just_above_boundary():
    """Just above boundary (0.11) → Bortle 2."""
    assert bortle_class(0.11) == 2


def test_constants_are_typed():
    """Each constant is int or float."""
    assert isinstance(MESH_KM, (int, float))
    assert isinstance(REDUNDANCY_KM, (int, float))
    assert isinstance(ALR_DARK, (int, float))
    assert isinstance(ALR_BRIGHT, (int, float))
    assert isinstance(BORTLE_THRESHOLDS, list)
    for entry in BORTLE_THRESHOLDS:
        assert isinstance(entry, tuple) and len(entry) == 2
