"""Step 1: ALR -> darkness (0-1) + Bortle (1-9)."""
import numpy as np

from .config import ALR_BRIGHT, ALR_DARK, ALR_EPS, bortle_class


def alr_to_darkness(alr_data: np.ndarray) -> np.ndarray:
    """
    Convert ALR values to darkness score [0, 1].

    Formula:
      x = (log10(ALR + EPS) - log10(ALR_DARK)) / (log10(ALR_BRIGHT) - log10(ALR_DARK))
      darkness = clip(1 - x, 0, 1)

    NaN in -> NaN out.
    """
    x = (np.log10(alr_data + ALR_EPS) - np.log10(ALR_DARK)) / (
        np.log10(ALR_BRIGHT) - np.log10(ALR_DARK)
    )
    darkness = np.clip(1.0 - x, 0.0, 1.0)
    return darkness


def alr_to_bortle(alr_data: np.ndarray) -> np.ndarray:
    """
    Convert ALR values to Bortle class (1-9, int8).
    Element-wise application of the Bortle threshold table.
    """
    b = np.vectorize(bortle_class, otypes=[np.int8])(alr_data)
    return b
