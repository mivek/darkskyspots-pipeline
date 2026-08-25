#!/usr/bin/env python3
"""Compare pipeline darkness with continuous SQM control points and rasters.

The pipeline value is read at the exact raster pixel containing each control
point. No nearest-spot lookup or spatial interpolation is performed.

The reference model used here is explicit:

* ALR is artificial sky luminance divided by natural sky luminance;
* ``SQM = natural_sqm - 2.5 * log10(1 + ALR)``;
* the default natural-sky anchor is 22.0 mag/arcsec² and can be set with
  ``--natural-sqm``.

This produces an SQM-equivalent value for the pipeline's darkness raster.
Bortle remains a secondary, discrete diagnostic. With ``--sky-brightness`` and
``--spots-dir``, the same exact-pixel comparison can be applied to every
published French spot and to a deterministic foreign sample.

The comparison with lightpollutionmap is not field validation: both models
start from the same VIIRS input. The script reports model divergence and never
calculates or suggests a new ALR calibration constant.

The optional altitude covariate comes from ASTER30m through OpenTopoData. It
is a modeled elevation, not a terrain measurement, and is used only to test
explanatory variables.
"""

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import rasterio
import numpy as np
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform

from src.config import ALR_BRIGHT, ALR_DARK, ALR_EPS


DEFAULT_POINTS_PATH = Path(__file__).resolve().parent / "validation" / "calibration_points.json"
LPM_NATURAL_LUMINANCE_MCD_M2 = 0.171168465
LPM_REFERENCE_URL = "https://www.lightpollutionmap.info/help.html#FAQ31"


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


def load_elevation_overrides(path: str | Path) -> dict[str, float]:
    """Load modeled elevations keyed by published spot id."""
    override_path = Path(path)
    with override_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    values = payload.get("elevations_m", payload) if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise ValueError(f"{override_path}: expected an elevations_m object")
    validated: dict[str, float] = {}
    for label, elevation in values.items():
        if isinstance(elevation, bool) or not isinstance(elevation, (int, float)):
            raise ValueError(f"{override_path}: invalid elevation for {label}")
        if not math.isfinite(float(elevation)):
            raise ValueError(f"{override_path}: invalid elevation for {label}")
        validated[str(label)] = float(elevation)
    return validated


def load_spot_points(
    path: str | Path,
    country: str = "FR",
    elevation_overrides: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Load published tile spots as exact-pixel comparison points."""
    root = Path(path)
    files = sorted(root.glob("**/*.json"))
    points: list[dict[str, Any]] = []
    for tile_path in files:
        with tile_path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        for index, spot in enumerate(envelope.get("spots", [])):
            try:
                lat = float(spot["lat"])
                lon = float(spot["lon"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{tile_path}: invalid spot {index} coordinates") from exc
            label = str(spot.get("id", f"{tile_path.name}:{index}"))
            elevation = spot.get("elevation_m", spot.get("altitude"))
            if elevation is None and elevation_overrides is not None:
                elevation = elevation_overrides.get(label)
            points.append(
                {
                    "label": label,
                    "country": str(spot.get("country") or country).upper(),
                    "region": "published spots",
                    "lat": lat,
                    "lon": lon,
                    "sqm": None,
                    "bortle_ref": None,
                    "elevation_m": elevation,
                    "source": "published_spot",
                }
            )
    if not points:
        raise ValueError(f"{root}: no spots found")
    return points


def load_sample_points(path: str | Path) -> list[dict[str, Any]]:
    """Load deterministic non-French sample points from a JSON list."""
    points = load_control_points(path)
    for point in points:
        point.setdefault("source", "foreign_sample")
    return points


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


def sky_brightness_to_sqm(
    artificial_brightness_mcd_m2: float,
    natural_luminance_mcd_m2: float = LPM_NATURAL_LUMINANCE_MCD_M2,
) -> float:
    """Apply lightpollutionmap FAQ31 to an SB artificial-brightness pixel."""
    total = max(0.0, artificial_brightness_mcd_m2) + natural_luminance_mcd_m2
    return math.log10(total / 108_000_000.0) / -0.4


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


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: list[float]) -> dict[str, float | int | None]:
    """Return a compact, JSON-serializable distribution summary."""
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0 if values else None,
        "q1": _quantile(values, 0.25),
        "q3": _quantile(values, 0.75),
    }


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    result = {}
    for group, group_rows in sorted(groups.items()):
        sqm = [row["delta_sqm"] for row in group_rows]
        dark = [row["delta_darkness"] for row in group_rows]
        score = [row["delta_score"] for row in group_rows]
        result[group] = {
            "delta_sqm": summary(sqm),
            "delta_darkness": summary(dark),
            "delta_score": summary(score),
            "absolute_delta_score": summary([abs(value) for value in score]),
            "score_distribution": score_distribution(score),
            "score_boundary_crossed": sum(row["score_boundary_crossed"] for row in group_rows),
            "score_significant": sum(row["score_significant"] for row in group_rows),
            "score_total": len(group_rows),
        }
    return result


def score_distribution(deltas: list[int]) -> dict[str, int]:
    """Count exact absolute score differences, grouping 3+ together."""
    counts = Counter(min(abs(delta), 3) for delta in deltas)
    return {str(value): counts.get(value, 0) for value in (0, 1, 2)} | {
        "3+": counts.get(3, 0)
    }


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and math.isclose(ordered[end][1], ordered[index][1], abs_tol=1e-12):
            end += 1
        rank = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[ordered[position][0]] = rank
        index = end
    return ranks


def ranking_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure continuous-order preservation, independent of score rounding."""
    if len(rows) < 2:
        return {"n": len(rows), "spearman": None, "pairs": 0, "discordant_pairs": 0}
    reference = [row["sqm_reference"] for row in rows]
    pipeline = [row["sqm_pipeline"] for row in rows]
    ref_ranks = _average_ranks(reference)
    pipe_ranks = _average_ranks(pipeline)
    ref_mean = statistics.fmean(ref_ranks)
    pipe_mean = statistics.fmean(pipe_ranks)
    numerator = sum((a - ref_mean) * (b - pipe_mean) for a, b in zip(ref_ranks, pipe_ranks))
    ref_norm = math.sqrt(sum((a - ref_mean) ** 2 for a in ref_ranks))
    pipe_norm = math.sqrt(sum((b - pipe_mean) ** 2 for b in pipe_ranks))
    discordant = concordant = tied = 0
    for index, left in enumerate(reference):
        for right, left_pipe in zip(reference[index + 1 :], pipeline[index + 1 :]):
            ref_sign = (left > right) - (left < right)
            pipe_left = pipeline[index]
            pipe_sign = (pipe_left > left_pipe) - (pipe_left < left_pipe)
            if ref_sign == 0 or pipe_sign == 0:
                tied += 1
            elif ref_sign == pipe_sign:
                concordant += 1
            else:
                discordant += 1
    pairs = concordant + discordant + tied
    return {
        "n": len(rows),
        "spearman": numerator / (ref_norm * pipe_norm) if ref_norm and pipe_norm else None,
        "pairs": pairs,
        "concordant_pairs": concordant,
        "discordant_pairs": discordant,
        "tied_pairs": tied,
        "discordance_rate_excluding_ties": discordant / (concordant + discordant)
        if concordant + discordant
        else None,
    }


def geography_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """Summarize sample extent and optional modeled elevations."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    result = {}
    for group, group_rows in sorted(groups.items()):
        latitudes = [row["lat"] for row in group_rows]
        longitudes = [row["lon"] for row in group_rows]
        elevations = [row["elevation_m"] for row in group_rows if row.get("elevation_m") is not None]
        result[group] = {
            "n": len(group_rows),
            "latitude": summary(latitudes),
            "longitude": summary(longitudes),
            "bbox": [min(longitudes), min(latitudes), max(longitudes), max(latitudes)],
            "elevation_m": summary(elevations),
        }
    return result


def _finite_pairs(rows: list[dict[str, Any]], variable: str) -> tuple[list[float], list[float]]:
    pairs = []
    for row in rows:
        x = row.get(variable)
        y = row.get("delta_sqm")
        if x is None or y is None:
            continue
        try:
            x_value = float(x)
            y_value = float(y)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x_value) and math.isfinite(y_value):
            pairs.append((x_value, y_value))
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_norm = math.sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_norm = math.sqrt(sum((y - y_mean) ** 2 for y in ys))
    return numerator / (x_norm * y_norm) if x_norm and y_norm else None


def correlation_summary(rows: list[dict[str, Any]], variable: str) -> dict[str, Any]:
    """Summarize Pearson/Spearman correlation and a simple OLS slope."""
    xs, ys = _finite_pairs(rows, variable)
    if len(xs) < 2:
        return {
            "n": len(xs),
            "pearson_r": None,
            "spearman_r": None,
            "slope": None,
            "intercept": None,
            "r_squared": None,
        }
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sxx if sxx else None
    intercept = y_mean - slope * x_mean if slope is not None else None
    pearson = _correlation(xs, ys)
    spearman = _correlation(_average_ranks(xs), _average_ranks(ys))
    result = {
        "n": len(xs),
        "pearson_r": pearson,
        "spearman_r": spearman,
        "slope": slope,
        "intercept": intercept,
        "r_squared": pearson * pearson if pearson is not None else None,
    }
    if variable == "elevation_m" and slope is not None:
        result["slope_per_km"] = slope * 1000.0
    return result


def multiple_regression(
    rows: list[dict[str, Any]],
    predictors: list[str],
    categorical: str | None = None,
) -> dict[str, Any]:
    """Fit OLS on standardized predictors to compare covariate influence.

    The response is the SQM residual (pipeline minus reference). Coefficients
    are standardized betas, so their magnitudes are comparable across
    altitude, coordinates, darkness, and Bortle. This is descriptive rather
    than causal, especially because darkness and Bortle are related outputs.
    """
    feature_names = list(predictors)
    categories: list[str] = []
    if categorical is not None:
        categories = sorted({str(row.get(categorical)) for row in rows if row.get(categorical) is not None})
        if "FR" in categories:
            categories.remove("FR")
            categories.insert(0, "FR")
        feature_names.extend(f"{categorical}={category}" for category in categories[1:])

    usable = []
    for row in rows:
        values = [row.get(name) for name in predictors]
        if categories:
            values.extend(float(str(row.get(categorical)) == category) for category in categories[1:])
        target = row.get("delta_sqm")
        try:
            numeric = [float(value) for value in values]
            target_value = float(target)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in numeric) and math.isfinite(target_value):
            usable.append((numeric, target_value))
    if len(usable) < len(predictors) + 2:
        return {"n": len(usable), "predictors": feature_names, "error": "insufficient complete rows"}

    matrix = np.asarray([item[0] for item in usable], dtype=float)
    target = np.asarray([item[1] for item in usable], dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    varying = scales > 0
    if not np.all(varying):
        kept_predictors = [name for name, keep in zip(feature_names, varying) if keep]
        kept_indices = [index for index, keep in enumerate(varying) if keep]
        matrix = matrix[:, kept_indices]
        feature_names = kept_predictors
        means = matrix.mean(axis=0)
        scales = matrix.std(axis=0)
    standardized = (matrix - means) / scales
    design = np.column_stack([np.ones(len(target)), standardized])
    coefficients, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
    fitted = design @ coefficients
    residuals = target - fitted
    sse = float(np.sum(residuals**2))
    sst = float(np.sum((target - target.mean()) ** 2))
    r_squared = 1.0 - sse / sst if sst else None
    degrees_of_freedom = len(target) - design.shape[1]
    adjusted = (
        1.0 - (1.0 - r_squared) * (len(target) - 1) / degrees_of_freedom
        if r_squared is not None and degrees_of_freedom > 0
        else None
    )
    return {
        "n": len(target),
        "predictors": feature_names,
        "intercept": float(coefficients[0]),
        "standardized_betas": {
            name: float(value) for name, value in zip(feature_names, coefficients[1:])
        },
        "r_squared": r_squared,
        "adjusted_r_squared": adjusted,
        "rmse": math.sqrt(sse / len(target)),
        "rank": int(rank),
    }


def write_altitude_scatter_svg(rows: list[dict[str, Any]], path: str | Path) -> None:
    """Write a dependency-free altitude/residual scatter plot as SVG."""
    import html

    groups = [("Ensemble", rows), ("FR", [row for row in rows if row["country"] == "FR"])]
    groups.extend(
        (country, [row for row in rows if row["country"] == country])
        for country in ("ES", "GB")
    )
    pairs = [
        (float(row["elevation_m"]), float(row["delta_sqm"]))
        for row in rows
        if row.get("elevation_m") is not None and math.isfinite(float(row["elevation_m"]))
    ]
    if not pairs:
        raise ValueError("cannot draw altitude scatter without elevations")
    x_max = max(500.0, math.ceil(max(pair[0] for pair in pairs) / 500.0) * 500.0)
    y_min = math.floor(min(pair[1] for pair in pairs) * 10.0) / 10.0 - 0.1
    y_max = math.ceil(max(pair[1] for pair in pairs) * 10.0) / 10.0 + 0.1
    width, height = 1120, 820
    panels = [(40, 45), (585, 45), (40, 430), (585, 430)]
    panel_width, panel_height = 495, 330
    colors = {"FR": "#377eb8", "ES": "#e41a1c", "GB": "#4daf4a"}
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text { font-family: sans-serif; fill: #222; } .axis { stroke: #333; stroke-width: 1; } .grid { stroke: #ddd; stroke-width: 1; } .point { opacity: .38; }</style>',
        '<text x="40" y="25" font-size="18">Altitude / résidu SQM (pipeline − référence)</text>',
        '<text x="40" y="805" font-size="12">Altitude ASTER30m modélisée (m), pas une mesure terrain ; résidu en mag/arcsec²</text>',
    ]
    for (title, group_rows), (left, top) in zip(groups, panels):
        plot_left, plot_top = left + 52, top + 30
        plot_width, plot_height = panel_width - 70, panel_height - 62
        svg.append(f'<text x="{left + 4}" y="{top + 18}" font-size="15">{html.escape(title)}</text>')
        for fraction in (0.0, 0.5, 1.0):
            x = plot_left + fraction * plot_width
            y = plot_top + (1.0 - fraction) * plot_height
            altitude = x_max * fraction
            residual = y_min + (y_max - y_min) * fraction
            svg.append(f'<line class="grid" x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{plot_top + plot_height}"/>')
            svg.append(f'<line class="grid" x1="{plot_left}" y1="{y:.1f}" x2="{plot_left + plot_width}" y2="{y:.1f}"/>')
            svg.append(f'<text x="{x:.1f}" y="{plot_top + plot_height + 18}" text-anchor="middle" font-size="10">{altitude:.0f}</text>')
            svg.append(f'<text x="{plot_left - 7}" y="{y + 3:.1f}" text-anchor="end" font-size="10">{residual:.1f}</text>')
        svg.append(f'<line class="axis" x1="{plot_left}" y1="{plot_top + plot_height}" x2="{plot_left + plot_width}" y2="{plot_top + plot_height}"/>')
        svg.append(f'<line class="axis" x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_top + plot_height}"/>')
        for row in group_rows:
            if row.get("elevation_m") is None:
                continue
            altitude = float(row["elevation_m"])
            residual = float(row["delta_sqm"])
            x = plot_left + altitude / x_max * plot_width
            y = plot_top + (y_max - residual) / (y_max - y_min) * plot_height
            color = colors.get(row["country"], "#777")
            svg.append(f'<circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="1.7" fill="{color}"/>')
        stats = correlation_summary(group_rows, "elevation_m")
        if stats.get("slope") is not None:
            x1_value, x2_value = 0.0, x_max
            y1_value = stats["intercept"] + stats["slope"] * x1_value
            y2_value = stats["intercept"] + stats["slope"] * x2_value
            x1 = plot_left + x1_value / x_max * plot_width
            x2 = plot_left + x2_value / x_max * plot_width
            y1 = plot_top + (y_max - y1_value) / (y_max - y_min) * plot_height
            y2 = plot_top + (y_max - y2_value) / (y_max - y_min) * plot_height
            svg.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#111" stroke-width="2"/>')
            svg.append(
                f'<text x="{plot_left + 8}" y="{plot_top + 15}" font-size="10">'
                f'n={stats["n"]}  r={stats["pearson_r"]:.3f}  pente={stats["slope_per_km"]:.3f} mag/km</text>'
            )
    svg.append('</svg>')
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def pollution_band(sqm: float) -> str:
    """Coarse bands used only to stratify the residual report."""
    if sqm >= 21.0:
        return "dark (SQM >= 21)"
    if sqm >= 20.0:
        return "rural (20 <= SQM < 21)"
    return "urban (SQM < 20)"


def _fmt_summary(stats: dict[str, Any]) -> str:
    d = stats["delta_sqm"]
    return (
        f"n={d['n']} moyenne={_fmt(d['mean'], 3)} médiane={_fmt(d['median'], 3)} "
        f"écart-type={_fmt(d['stddev'], 3)} Q1={_fmt(d['q1'], 3)} Q3={_fmt(d['q3'], 3)}"
    )


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
        "--sky-brightness",
        help="versioned lightpollutionmap Sky Brightness GeoTIFF (30 arcsec)",
    )
    parser.add_argument(
        "--spots-dir",
        help="published spot tile directory; every spot becomes an exact-pixel sample",
    )
    parser.add_argument(
        "--elevation-overrides",
        help="JSON sidecar of modeled ASTER30m elevations keyed by published spot id",
    )
    parser.add_argument(
        "--foreign-samples",
        help="JSON list of deterministic Spain/UK samples",
    )
    parser.add_argument(
        "--scatter-out",
        help="optional SVG altitude/residual scatter plot",
    )
    parser.add_argument("--json-out", help="optional JSON report path")
    parser.add_argument(
        "--natural-sqm",
        type=float,
        default=22.0,
        help="natural-sky SQM anchor used by the ALR↔SQM conversion (default: 22.0)",
    )
    args = parser.parse_args()
    if not math.isfinite(args.natural_sqm):
        parser.error("--natural-sqm must be finite")
    if (args.spots_dir or args.foreign_samples) and not args.sky_brightness:
        parser.error("--sky-brightness is required with --spots-dir or --foreign-samples")

    points = load_control_points(args.points)
    elevation_overrides = (
        load_elevation_overrides(args.elevation_overrides)
        if args.elevation_overrides
        else None
    )
    comparison_points: list[dict[str, Any]] = []
    if args.spots_dir:
        comparison_points.extend(load_spot_points(args.spots_dir, elevation_overrides=elevation_overrides))
    if args.foreign_samples:
        comparison_points.extend(load_sample_points(args.foreign_samples))
    sky_context = rasterio.open(args.sky_brightness) if args.sky_brightness else None
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
            if sky_context is not None:
                print(f"Sky Brightness versionnée : {args.sky_brightness}")
                print(
                    "Appariement : chaque coordonnée est transformée dans le CRS de chaque raster "
                    "et lue dans son pixel contenant, sans interpolation. Le résultat ne prétend "
                    "pas à une précision supérieure à la grille Sky Brightness de 30\" ; le raster "
                    "pipeline est à 15\"."
                )
                print(
                    "Conversion Sky Brightness officielle (FAQ31) : SQM = "
                    "log10((brillance artificielle + 0,171168465 mcd/m²) / 108000000) / −0,4."
                )
            if elevation_overrides is not None:
                print(
                    f"Altitude covariable : {args.elevation_overrides} ; ASTER30m modélisé, "
                    "pas une mesure de terrain."
                )
            header = (
                f"{'Pays/région':20s} {'Point':25s} {'SQM réf.':>8s} {'SQM pipe':>9s} "
                f"{'Δ SQM':>7s} {'dark réf.':>9s} {'dark pipe':>10s} "
                f"{'score réf.':>10s} {'score pipe':>11s} {'Δ score':>8s} {'B réf/pipe':>10s}"
            )
            print(header)
            print("-" * len(header))

            rows: list[dict[str, Any]] = []
            all_points = points + comparison_points
            for point in all_points:
                dark_pipe = read_at(darkness_src, point["lon"], point["lat"])
                bortle_pipe = (
                    read_at(bortle_context, point["lon"], point["lat"])
                    if bortle_context is not None
                    else None
                )
                sqm = point["sqm"]
                source = point.get("source", "manual_control")
                lpm_artificial = (
                    read_at(sky_context, point["lon"], point["lat"])
                    if sky_context is not None
                    else None
                )
                if sqm is None and lpm_artificial is not None:
                    sqm = sky_brightness_to_sqm(lpm_artificial)
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
                if delta_sqm is not None and dark_pipe is not None:
                    rows.append(
                        {
                            "label": point["label"],
                            "country": point["country"],
                            "region": point["region"],
                            "lat": point["lat"],
                            "lon": point["lon"],
                            "source": source,
                            "sqm_reference": sqm,
                            "sqm_pipeline": sqm_pipe,
                            "delta_sqm": delta_sqm,
                            "darkness_reference": dark_ref,
                            "darkness_pipeline": dark_pipe,
                            "delta_darkness": dark_pipe - dark_ref,
                            "score_reference": score_ref,
                            "score_pipeline": score_pipe,
                            "delta_score": delta_score,
                            "score_boundary_crossed": delta_score != 0,
                            "score_significant": abs(delta_score) >= 1,
                            "pollution_band": pollution_band(sqm),
                            "latitude_band": f"{math.floor(point['lat'])}–{math.floor(point['lat']) + 1}°",
                            "elevation_m": point.get("elevation_m", point.get("altitude")),
                            "bortle_reference": b_ref,
                            "bortle_pipeline": int(js_round(bortle_pipe)) if bortle_pipe is not None else None,
                            "lpm_artificial_brightness_mcd_m2": lpm_artificial,
                        }
                    )

            if rows:
                print("\nRésumé des résidus (pipeline − Sky Brightness/référence) :")
                print(f"  Global : {_fmt_summary({'delta_sqm': summary([r['delta_sqm'] for r in rows])})}")
                print("\nPar pays :")
                for country, stats in _group_summary(rows, "country").items():
                    print(
                        f"  {country} : {_fmt_summary(stats)} ; "
                        f"Δ note moyenne={_fmt(stats['delta_score']['mean'], 2)}, "
                        f"médiane={_fmt(stats['delta_score']['median'], 2)}, "
                        f"écart ≥1={stats['score_significant']}/{stats['score_total']}"
                    )
                print("\nPar niveau de pollution (bandes SQM de référence) :")
                for band, stats in _group_summary(rows, "pollution_band").items():
                    print(
                        f"  {band} : {_fmt_summary(stats)} ; "
                        f"écart ≥1={stats['score_significant']}/{stats['score_total']}"
                    )
                print("\nPar latitude (bandes de 1 degré) :")
                for band, stats in _group_summary(rows, "latitude_band").items():
                    print(f"  {band} : {_fmt_summary(stats)}")
                score_deltas = [row["delta_score"] for row in rows]
                score_boundary_crossed = sum(row["score_boundary_crossed"] for row in rows)
                score_significant = sum(row["score_significant"] for row in rows)
                score_delta = [row["delta_score"] for row in rows]
                distribution = score_distribution(score_delta)
                print(
                    "\nÉcart de note, ciel dégagé + nouvelle lune + nuit astronomique complète : "
                    f"moyenne={statistics.fmean(score_deltas):+.2f}, "
                    f"médiane={statistics.median(score_deltas):+.2f}, "
                    f"distribution 0/1/2/3+={distribution['0']}/{distribution['1']}/"
                    f"{distribution['2']}/{distribution['3+']}."
                )
                print(
                    f"  Franchissement de frontière d'arrondi : {score_boundary_crossed}/{len(rows)} ; "
                    f"changement significatif (|Δ note| ≥ 1) : {score_significant}/{len(rows)}."
                )
                print(
                    "  Comme la note affichée est entière, ces deux décomptes sont nécessairement "
                    "identiques ; la distribution 0/1/2/3+ indique l'amplitude réelle."
                )
                published_rows = [row for row in rows if row["source"] == "published_spot"]
                ranking = {
                    "all": ranking_summary(rows),
                    "published_spots": ranking_summary(published_rows),
                    "by_country": {
                        country: ranking_summary([row for row in rows if row["country"] == country])
                        for country in sorted({row["country"] for row in rows})
                    },
                }
                print("\nConservation du classement continu (SQM, avant arrondi) :")
                for label, stats in (("spots français", ranking["published_spots"]), ("ensemble", ranking["all"])):
                    print(
                        f"  {label} : Spearman={_fmt(stats['spearman'], 5)}, "
                        f"paires discordantes={stats['discordant_pairs']}/{stats['pairs']} "
                        f"({100 * stats['discordance_rate_excluding_ties']:.2f}% hors égalités)"
                    )
                print("\nGéographie des points :")
                for country, stats in geography_summary(rows, "country").items():
                    elevation = stats["elevation_m"]
                    print(
                        f"  {country} : n={stats['n']}, lat={stats['bbox'][1]:.2f}–{stats['bbox'][3]:.2f}°, "
                        f"lon={stats['bbox'][0]:.2f}–{stats['bbox'][2]:.2f}°, "
                        f"altitude moyenne={_fmt(elevation['mean'], 0)} m, "
                        f"écart-type={_fmt(elevation['stddev'], 0)} m"
                    )
                altitude_analysis = {
                    "all_rows": correlation_summary(rows, "elevation_m"),
                    "by_country": {
                        country: correlation_summary(
                            [row for row in rows if row["country"] == country], "elevation_m"
                        )
                        for country in sorted({row["country"] for row in rows})
                    },
                    "published_france": correlation_summary(published_rows, "elevation_m"),
                }
                print("\nAltitude contre résidu SQM (pipeline − référence) :")
                for label, stats in (
                    ("ensemble", altitude_analysis["all_rows"]),
                    *[(country, value) for country, value in altitude_analysis["by_country"].items()],
                    ("France, spots publiés", altitude_analysis["published_france"]),
                ):
                    print(
                        f"  {label} : n={stats['n']}, Pearson={_fmt(stats['pearson_r'], 4)}, "
                        f"Spearman={_fmt(stats['spearman_r'], 4)}, "
                        f"pente={_fmt(stats.get('slope_per_km'), 4)} mag/km, "
                        f"R²={_fmt(stats['r_squared'], 4)}"
                    )
                covariates = ["elevation_m", "darkness_pipeline", "bortle_pipeline", "lat", "lon"]
                covariate_analysis = {
                    "all_rows": {
                        variable: correlation_summary(rows, variable) for variable in covariates
                    },
                    "published_france": {
                        variable: correlation_summary(published_rows, variable)
                        for variable in covariates
                    },
                }
                regression = {
                    "all_rows": multiple_regression(rows, covariates),
                    "all_rows_country_adjusted": multiple_regression(
                        rows, covariates, categorical="country"
                    ),
                    "published_france": multiple_regression(published_rows, covariates),
                }
                if args.scatter_out:
                    write_altitude_scatter_svg(rows, args.scatter_out)
                    print(f"\nNuage altitude/résidu : {args.scatter_out}")
                print("\nRégression multiple descriptive (résidu SQM ; betas standardisés) :")
                for label, model in regression.items():
                    print(
                        f"  {label} : n={model.get('n')}, R²={_fmt(model.get('r_squared'), 4)}, "
                        f"R² ajusté={_fmt(model.get('adjusted_r_squared'), 4)}, "
                        f"RMSE={_fmt(model.get('rmse'), 4)}"
                    )
                    if "standardized_betas" in model:
                        print(
                            "    "
                            + ", ".join(
                                f"{name}={value:+.4f}"
                                for name, value in model["standardized_betas"].items()
                            )
                        )
                report = {
                    "metadata": {
                        "points_file": str(args.points),
                        "spots_dir": args.spots_dir,
                        "foreign_samples": args.foreign_samples,
                        "elevation_overrides": args.elevation_overrides,
                        "scatter_out": args.scatter_out,
                        "sky_brightness": args.sky_brightness,
                        "sky_brightness_reference": LPM_REFERENCE_URL,
                        "natural_sqm": args.natural_sqm,
                        "score_conditions": "clear sky, new moon, complete astronomical night",
                        "grid_matching": "exact containing pixel at the shared coordinate; no interpolation; SB is 30 arcsec and pipeline is 15 arcsec",
                        "interpretation": "Both models use the same VIIRS input; this measures model divergence, not field truth.",
                        "scope": "western Europe only; this latitude span says nothing about Scandinavia or other high latitudes.",
                        "score_interpretation": "A boundary crossing (delta != 0) is not the decision metric; a significant score change is abs(delta) >= 1.",
                        "elevation_source": "ASTER30m via OpenTopoData for foreign samples and published spots when --elevation-overrides is supplied; elevation is modeled, not measured.",
                        "covariate_interpretation": "Correlations and standardized OLS betas are descriptive associations, not causal effects. Darkness and Bortle are related pipeline outputs; altitude is a modeled ASTER30m covariate.",
                    },
                    "summary": {
                        "global": {
                            "delta_sqm": summary([r["delta_sqm"] for r in rows]),
                            "delta_darkness": summary([r["delta_darkness"] for r in rows]),
                            "delta_score": summary(score_deltas),
                            "absolute_delta_score": summary([abs(value) for value in score_deltas]),
                            "score_distribution": distribution,
                            "score_boundary_crossed": score_boundary_crossed,
                            "score_significant": score_significant,
                            "score_total": len(rows),
                        },
                        "by_country": _group_summary(rows, "country"),
                        "by_pollution_band": _group_summary(rows, "pollution_band"),
                        "by_latitude_band": _group_summary(rows, "latitude_band"),
                        "ranking": ranking,
                        "geography_by_source": geography_summary(rows, "source"),
                        "geography_by_country": geography_summary(rows, "country"),
                        "altitude_vs_residual": altitude_analysis,
                        "covariate_correlations": covariate_analysis,
                        "multiple_regression": regression,
                    },
                    "rows": rows,
                }
                if args.json_out:
                    json_path = Path(args.json_out)
                    json_path.parent.mkdir(parents=True, exist_ok=True)
                    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            print("\nLimite d'interprétation : le SQM de lightpollutionmap est un modèle, pas une mesure.")
            print("Cette comparaison établit une divergence entre deux modèles ; elle ne dit pas lequel a raison.")
            print("En Europe occidentale le modèle de référence est bien contraint ; aux hautes latitudes, les deux")
            print("peuvent être également incertains. Cette emprise ne permet aucune conclusion sur la Scandinavie.")
            print("Aucun ajustement de ALR_CALIB_C n'est calculé ici.")
            print("Attention : darkness est borné à [0,1] ; un pixel saturé ne permet pas de retrouver son SQM exact.")
        finally:
            if bortle_context is not None:
                bortle_context.close()
            if sky_context is not None:
                sky_context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
