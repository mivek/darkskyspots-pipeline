"""Steps 2-3: mesh grid local minima + redundancy filter."""
import numpy as np
from rasterio.transform import xy

from .config import MESH_KM, REDUNDANCY_KM
from .utils import haversine_km


def mesh_minima(
    darkness: np.ndarray,
    transform,
    mesh_km: int = MESH_KM,
) -> list[dict]:
    """
    Scan the darkness array in mesh_km cells, find the darkest pixel per cell.

    Returns list of dicts: {lat, lon, darkness, row, col}.
    Skips cells where all values are NaN (NaN halo).
    Tie-breaker: np.argmin row-major (D6).
    """
    res_deg_x = abs(transform.a)  # degrees per pixel in x
    res_deg_y = abs(transform.e)  # degrees per pixel in y

    mesh_deg = mesh_km / 111.32  # rough conversion km -> degrees
    cell_px_x = max(1, int(round(mesh_deg / res_deg_x)))
    cell_px_y = max(1, int(round(mesh_deg / res_deg_y)))

    points = []
    rows, cols = darkness.shape
    for r in range(0, rows, cell_px_y):
        for c in range(0, cols, cell_px_x):
            cell = darkness[r:r + cell_px_y, c:c + cell_px_x]
            if cell.size == 0 or np.all(np.isnan(cell)):
                continue
            # np.argmin row-major (D6): flat index of first occurrence of min
            flat_idx = np.nanargmin(cell)
            dr = flat_idx // cell.shape[1]
            dc = flat_idx % cell.shape[1]
            min_row = r + dr
            min_col = c + dc
            val = float(cell[dr, dc])
            lon, lat = xy(transform, min_row, min_col, offset="center")
            points.append(
                {
                    "lat": float(lat),
                    "lon": float(lon),
                    "darkness": val,
                    "row": int(min_row),
                    "col": int(min_col),
                }
            )
    return points


def redundancy_filter(
    candidates: list[dict],
    min_distance_km: float = REDUNDANCY_KM,
) -> list[dict]:
    """
    Filter candidates: keep a candidate if no already-kept candidate is within
    min_distance_km AND has the same Bortle class.

    Candidates with different Bortle classes are always kept regardless of distance.
    Sorted by darkness (darkest first) before filtering.
    """
    sorted_cands = sorted(candidates, key=lambda c: c["darkness"], reverse=True)
    kept: list[dict] = []
    for cand in sorted_cands:
        redundant = False
        for kept_cand in kept:
            if kept_cand.get("bortle") == cand.get("bortle"):
                dist = haversine_km(
                    kept_cand["lat"],
                    kept_cand["lon"],
                    cand["lat"],
                    cand["lon"],
                )
                if dist < min_distance_km:
                    redundant = True
                    break
        if not redundant:
            kept.append(cand)
    return kept
