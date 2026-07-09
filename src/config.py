"""Pipeline constants + Bortle threshold table."""
import numpy as np

# Mesh and filter
MESH_KM = 5
REDUNDANCY_KM = 15
MIN_SPOTS_PER_AREA = 4
COVERAGE_RADIUS_KM = 100

# Coverage — max distance for nearest commune/place attachment
MAX_NEAR_DISTANCE_KM = 25

# Tile
TILE_SIZE_DEG = 1.0
BORTLE_MIN = 1
BORTLE_MAX = 9

# ALR — fork defaults
ALR_ALPHA_BASE = 2.3
ALR_ALPHA_EXP = 0.28
# ALR calibration constant.
# Divides the summed ring-weighted radiance to yield ALR (All-sky Light
# pollution Ratio). The upstream/US default was 1/562.72 (Duriscoe et al.,
# calibrated on south-west US skies).
#
# Europe calibration (2026-07): the US default over-estimated Bortle by ~1
# class across the rural-to-urban range on European skies. Recalibrated to
# 1/1125.44 (US value x2) against 4 lightpollutionmap.info control points in
# the Massif Central, which brought the mean Bortle delta from +1.25 to +0.25.
# Three of four points became exact; only a dense-urban point retains +1
# (acceptable: the app targets dark/rural skies, and the residual is on the
# conservative side — the pipeline slightly over-estimates urban pollution).
# Control points and expected values are recorded in validation/checkpoints.json.
ALR_CALIB_C = 1.0 / 1125.44
ALR_RINGS = 38
ALR_MAX_KM = 300
ALR_NOISE_FLOOR = 0.5
ALR_WORK_RESOLUTION_M = 450

# Darkness normalization
ALR_DARK = 0.1
ALR_BRIGHT = 10.0
ALR_EPS = 1e-3

# Bortle thresholds (inclusive upper bound)
BORTLE_THRESHOLDS: list[tuple[float, int]] = [
    (0.10, 1),   # ALR <= 0.10 -> Bortle 1
    (0.20, 2),   # ALR <= 0.20 -> Bortle 2
    (0.33, 3),   # ALR <= 0.33 -> Bortle 3
    (1.0,  4),   # ALR <= 1.0  -> Bortle 4
    (2.0,  5),   # ALR <= 2.0  -> Bortle 5
    (4.0,  6),   # ALR <= 4.0  -> Bortle 6
    (10.0, 7),   # ALR <= 10.0 -> Bortle 7
]


def bortle_class(alr_value: float) -> int:
    """Map an ALR value to Bortle class 1-9. NaN -> 9 (urban worst-case)."""
    if np.isnan(alr_value):
        return 9
    for threshold, cls in BORTLE_THRESHOLDS:
        if alr_value <= threshold:
            return cls
    return 8
