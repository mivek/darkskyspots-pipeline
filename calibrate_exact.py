#!/usr/bin/env python3
"""Compare pipeline darkness with continuous SQM control points.

The pipeline value is read at the exact raster pixel containing each control
point. No nearest-spot lookup or spatial interpolation is performed.

The reference model used here is explicit:

* ALR is artificial sky luminance divided by natural sky luminance;
* ``SQM = natural_sqm - 2.5 * log10(1 + ALR)``;
* the default natural-sky anchor is 22.0 mag/arcsec² and can be set with
  ``--natural-sqm``.

This produces an SQM-equivalent value for the pipeline's darkness raster.
Bortle remains a secondary, discrete diagnostic. The script does not
calculate or suggest a new ALR calibration constant.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import rasterio
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform

from src.config import ALR_BRIGHT, ALR_DARK, ALR_EPS


DEFAULT_POINTS_PATH = Path(__file__).resolve().parent / "validation" / "calibration_points.json"


def load_control_points(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate control points from a JSON list.

    ``sqm`` may be null for a point proposed but not yet read from
    lightpollutionmap. ``bortle_ref`` is optional and secondary.
    """
    point_path = Path(path)
    with point_path.open(encoding="utf-8") as handle:
        points = json.load(handle)
    if not isinstance(points, list):
        raise ValueError(f"{point_path}: expected a JSON list of control points")

    required = {"label", "country", "region", "lat", "lon", "sqm"}
    validated: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError(f"{point_path}: point {index} must be an object")
        missing = required - point.keys()
        if missing:
            raise ValueError(f"{point_path}: point {index} missing {sorted(missing)}")

        lat = point["lat"]
        lon = point["lon"]
        if isinstance(lat, bool) or not isinstance(lat, (int, float)) or not -90 <= lat <= 90:
            raise ValueError(f"{point_path}: point {index} has invalid latitude")
        if isinstance(lon, bool) or not isinstance(lon, (int, float)) or not -180 <= lon <= 180:
            raise ValueError(f"{point_path}: point {index} has invalid longitude")

        sqm = point["sqm"]
        if sqm is not None and (
            isinstance(sqm, bool) or not isinstance(sqm, (int, float)) or not math.isfinite(sqm)
        ):
            raise ValueError(f"{point_path}: point {index} has invalid SQM")
        bortle = point.get("bortle_ref")
        if bortle is not None and (
            isinstance(bortle, bool) or not isinstance(bortle, int) or not 1 <= bortle <= 9
        ):
            raise ValueError(f"{point_path}: point {index} has invalid reference Bortle")

        validated.append(dict(point))
    return validated


def read_at(src, lon: float, lat: float) -> float | None:
    """Read one exact pixel at WGS84 lon/lat; NaN/nodata is returned as None."""
    if src.crs is None:
        raise ValueError("Raster has no CRS; cannot locate a WGS84 control point")
    xs, ys = warp_transform("EPSG:4326", src.crs, [lon], [lat])
    x, y = xs[0], ys[0]
    row, col = rowcol(src.transform, x, y)
    if not (0 <= row < src.height and 0 <= col < src.width):
        return None
    # ``sample`` uses the same nearest-pixel transform as rowcol above while
    # avoiding a full-band read for every control point.
    value = float(next(src.sample([(x, y)]))[0])
    if not math.isfinite(value):
        return None
    if src.nodata is not None and math.isclose(value, float(src.nodata), rel_tol=0, abs_tol=1e-12):
        return None
    return value


def sqm_to_alr(sqm: float, natural_sqm: float = 22.0) -> float:
    """Convert reference SQM to ALR using the documented luminance model."""
    return max(0.0, 10 ** ((natural_sqm - sqm) / 2.5) - 1.0)


def alr_to_sqm(alr: float, natural_sqm: float = 22.0) -> float:
    """Convert ALR to the corresponding SQM-equivalent value."""
    return natural_sqm - 2.5 * math.log10(1.0 + max(0.0, alr))


def sqm_to_darkness(sqm: float, natural_sqm: float = 22.0) -> float:
    """Convert reference SQM to the pipeline's continuous darkness scale."""
    alr = sqm_to_alr(sqm, natural_sqm)
    x = (math.log10(alr + ALR_EPS) - math.log10(ALR_DARK)) / (
        math.log10(ALR_BRIGHT) - math.log10(ALR_DARK)
    )
    return max(0.0, min(1.0, 1.0 - x))


def darkness_to_sqm(darkness: float, natural_sqm: float = 22.0) -> float:
    """Convert a pipeline darkness pixel to SQM-equivalent."""
    darkness = max(0.0, min(1.0, darkness))
    log_alr = math.log10(ALR_DARK) + (1.0 - darkness) * (
        math.log10(ALR_BRIGHT) - math.log10(ALR_DARK)
    )
    alr = max(0.0, 10**log_alr - ALR_EPS)
    return alr_to_sqm(alr, natural_sqm)


def js_round(value: float) -> int:
    """Round like JavaScript Math.round for the non-negative score domain."""
    return math.floor(value + 0.5)


def ideal_score(darkness: float) -> int:
    """Score under clear, new-moon, fully astronomical-night conditions."""
    return js_round(max(0.0, min(1.0, darkness)) * 10.0)


def _fmt(value: float | int | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--points", default=str(DEFAULT_POINTS_PATH), help="JSON file containing control points"
    )
    parser.add_argument("--darkness", required=True, help="debug_darkness_*.tif")
    parser.add_argument("--bortle", help="debug_bortle_*.tif (optional secondary diagnostic)")
    parser.add_argument(
        "--natural-sqm",
        type=float,
        default=22.0,
        help="natural-sky SQM anchor used by the ALR↔SQM conversion (default: 22.0)",
    )
    args = parser.parse_args()
    if not math.isfinite(args.natural_sqm):
        parser.error("--natural-sqm must be finite")

    points = load_control_points(args.points)
    with rasterio.open(args.darkness) as darkness_src:
        bortle_context = rasterio.open(args.bortle) if args.bortle else None
        try:
            print("=== CALIBRATION EXACTE — SQM continu, valeur au pixel exact ===\n")
            print(f"Points : {args.points}")
            print(
                "Conversion : ALR = 10^((SQM naturel − SQM) / 2,5) − 1 ; "
                f"SQM naturel = {args.natural_sqm:.2f}"
            )
            print(
                "Sensibilité : SQM_naturel est une hypothèse de zéro-point, pas une constante du pipeline. "
                "Une erreur de ±0,20 mag décale uniformément les Δ SQM absolus de ±0,20 mag ; les écarts "
                "entre pays restent comparables, mais une valeur absolue ou un score près d'un seuil peut changer."
            )
            print(
                "Score idéal : round(darkness × 10), ciel dégagé + nouvelle lune + "
                "nuit astronomique complète\n"
            )
            header = (
                f"{'Pays/région':20s} {'Point':25s} {'SQM réf.':>8s} {'SQM pipe':>9s} "
                f"{'Δ SQM':>7s} {'dark réf.':>9s} {'dark pipe':>10s} "
                f"{'score réf.':>10s} {'score pipe':>11s} {'Δ score':>8s} {'B réf/pipe':>10s}"
            )
            print(header)
            print("-" * len(header))

            rows = []
            for point in points:
                dark_pipe = read_at(darkness_src, point["lon"], point["lat"])
                bortle_pipe = (
                    read_at(bortle_context, point["lon"], point["lat"])
                    if bortle_context is not None
                    else None
                )
                sqm = point["sqm"]
                if sqm is None:
                    print(
                        f"{point['country'] + '/' + point['region'][:14]:20s} "
                        f"{point['label'][:25]:25s} SQM MANQUANT"
                    )
                    continue

                dark_ref = sqm_to_darkness(sqm, args.natural_sqm)
                sqm_pipe = darkness_to_sqm(dark_pipe, args.natural_sqm) if dark_pipe is not None else None
                delta_sqm = sqm_pipe - sqm if sqm_pipe is not None else None
                score_ref = ideal_score(dark_ref)
                score_pipe = ideal_score(dark_pipe) if dark_pipe is not None else None
                delta_score = score_pipe - score_ref if score_pipe is not None else None
                b_ref = point.get("bortle_ref")
                if b_ref is not None and bortle_pipe is not None:
                    b_pair = f"{b_ref:d}/{int(js_round(bortle_pipe)):d}"
                elif b_ref is not None:
                    b_pair = f"{b_ref:d}/—"
                elif bortle_pipe is not None:
                    b_pair = f"—/{int(js_round(bortle_pipe)):d}"
                else:
                    b_pair = "—"
                print(
                    f"{point['country'] + '/' + point['region'][:14]:20s} "
                    f"{point['label'][:25]:25s} {_fmt(sqm):>8s} {_fmt(sqm_pipe):>9s} "
                    f"{_fmt(delta_sqm):>7s} {_fmt(dark_ref, 3):>9s} {_fmt(dark_pipe, 3):>10s} "
                    f"{score_ref:10d} {_fmt(score_pipe, 0):>11s} {_fmt(delta_score, 0):>8s} {b_pair:>10s}"
                )
                rows.append((delta_sqm, delta_score))

            if rows:
                sqm_deltas = [row[0] for row in rows if row[0] is not None]
                score_deltas = [row[1] for row in rows if row[1] is not None]
                print("\nRésumé des points avec SQM :")
                if sqm_deltas:
                    print(f"  Δ SQM moyen (pipeline − référence) : {sum(sqm_deltas) / len(sqm_deltas):+.3f}")
                if score_deltas:
                    changed = sum(delta != 0 for delta in score_deltas)
                    print(f"  Score entier différent : {changed}/{len(score_deltas)} point(s)")
                    print(f"  Δ score entier moyen : {sum(score_deltas) / len(score_deltas):+.2f}")

            print("\nLimite d'interprétation : le SQM de lightpollutionmap est un modèle, pas une mesure.")
            print("Cette comparaison établit une divergence entre deux modèles ; elle ne dit pas lequel a raison.")
            print("En Europe occidentale le modèle de référence est bien contraint ; aux hautes latitudes, les deux")
            print("peuvent être également incertains. Aucun ajustement de ALR_CALIB_C n'est calculé ici.")
            print("Attention : darkness est borné à [0,1] ; un pixel saturé ne permet pas de retrouver son SQM exact.")
        finally:
            if bortle_context is not None:
                bortle_context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
