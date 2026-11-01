"""Steps 2-3: mesh grid darkest pixels + redundancy filter."""
import numpy as np
from rasterio.transform import xy

from .config import MESH_KM, REDUNDANCY_KM
from .utils import haversine_km


def mesh_darkest(
    darkness: np.ndarray,
    transform,
    mesh_km: int = MESH_KM,
    *,
    qualify=None,
    crs='EPSG:4326',
    batch_size: int = 4096,
    stats: dict | None = None,
) -> list[dict]:
    """
    Scan the darkness array in mesh_km cells, find the darkest pixel per cell.

    Returns list of dicts: {lat, lon, darkness, row, col}.
    Skips cells where all values are NaN (NaN halo).
    Tie-breaker: ``np.nanargmax`` returns the first maximum in row-major
    order (D6), so equal darkness values are deterministic.

    With ``qualify``, examine alternatives in the same order until a batch
    qualifier accepts a centre. Normal generation always supplies the WBM
    qualifier; omission preserves the historical helper for comparisons.
    """
    if qualify is not None:
        return _qualified_mesh(darkness, transform, mesh_km, qualify, crs, batch_size, stats)
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
            # np.nanargmax row-major (D6): first occurrence of max wins.
            flat_idx = np.nanargmax(cell)
            dr = flat_idx // cell.shape[1]
            dc = flat_idx % cell.shape[1]
            darkest_row = r + dr
            darkest_col = c + dc
            val = float(cell[dr, dc])
            lon, lat = xy(transform, darkest_row, darkest_col, offset="center")
            points.append(
                {
                    "lat": float(lat),
                    "lon": float(lon),
                    "darkness": val,
                    "row": int(darkest_row),
                    "col": int(darkest_col),
                }
            )
    return points


def _qualified_mesh(darkness, transform, mesh_km, qualify, crs, batch_size, stats):
    """Rank a rejected cell once; qualify batches without rescanning maxima.

    The existing mesh is angular. Reject unsupported grids explicitly instead
    of interpreting projected coordinates as longitude/latitude. Qualification
    returns a subset of the original candidate objects, in any order.
    """
    from rasterio.crs import CRS
    if CRS.from_user_input(crs) != CRS.from_epsg(4326) or transform.b or transform.d:
        raise ValueError('Mesh selection requires an unrotated EPSG:4326 VIIRS grid')
    if batch_size < 1:
        raise ValueError('Mesh batch size must be positive')
    stats = stats if stats is not None else {}
    stats.update(cells=0, empty_cells=0, selected_cells=0, candidates_examined=0, ranked_cells=0)
    cy = max(1, int(round(mesh_km / 111.32 / abs(transform.e))))
    cx = max(1, int(round(mesh_km / 111.32 / abs(transform.a))))
    points = []

    def consume(states):
        chosen = {}
        active = states
        if hasattr(qualify, 'possible_cells'):
            # Whole-pixel envelopes are a conservative superset of centres.
            # Compute these affine bounds in one batch, not two xy() calls
            # per cell. Actual candidate centres still use rasterio.xy.
            rr = np.array([s['r'] for s in states])
            cc = np.array([s['c'] for s in states])
            hh = np.array([s['cell'].shape[0] for s in states])
            ww = np.array([s['width'] for s in states])
            x0, x1 = transform.c + transform.a * cc, transform.c + transform.a * (cc + ww)
            y0, y1 = transform.f + transform.e * rr, transform.f + transform.e * (rr + hh)
            bounds = np.column_stack((np.minimum(x0, x1), np.minimum(y0, y1),
                                      np.maximum(x0, x1), np.maximum(y0, y1)))
            active = [s for s, possible in zip(states, qualify.possible_cells(bounds)) if possible]
        while active:
            rows = [s['r'] + s['index'] // s['width'] for s in active]
            cols = [s['c'] + s['index'] % s['width'] for s in active]
            lons, lats = xy(transform, rows, cols, offset='center')
            candidates = [dict(lat=float(lat), lon=float(lon), row=int(row), col=int(col),
                               darkness=float(darkness[row, col]))
                          for lat, lon, row, col in zip(lats, lons, rows, cols)]
            accepted = {id(p) for p in qualify(candidates)}
            if not accepted.issubset({id(p) for p in candidates}):
                raise ValueError('Mesh qualifier must return original candidate objects')
            stats['candidates_examined'] += len(candidates)
            following = []
            for state, candidate in zip(active, candidates):
                if id(candidate) in accepted:
                    chosen[state['ordinal']] = candidate
                    continue
                if state['order'] is None:
                    flat = state['cell'].ravel()
                    valid = np.flatnonzero(~np.isnan(flat))
                    state['order'] = valid[np.argsort(-flat[valid], kind='stable')]
                    state['position'] = 0
                    stats['ranked_cells'] += 1
                state['position'] += 1
                if state['position'] < len(state['order']):
                    state['index'] = int(state['order'][state['position']])
                    following.append(state)
            active = following
        points.extend(chosen[i] for i in sorted(chosen))
        stats['selected_cells'] += len(chosen)
        stats['empty_cells'] += len(states) - len(chosen)

    states = []
    for r in range(0, darkness.shape[0], cy):
        for c in range(0, darkness.shape[1], cx):
            cell = darkness[r:r + cy, c:c + cx]
            stats['cells'] += 1
            if np.all(np.isnan(cell)):
                stats['empty_cells'] += 1
                continue
            states.append(dict(r=r, c=c, width=cell.shape[1], cell=cell,
                               index=int(np.nanargmax(cell)), order=None,
                               ordinal=stats['cells']))
            if len(states) == batch_size:
                consume(states)
                states = []
    if states:
        consume(states)
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
