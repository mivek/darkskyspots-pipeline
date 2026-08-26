"""Streaming, deterministic aggregation of spot tiles into global clusters."""

from dataclasses import dataclass
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
from typing import Collection


@dataclass(frozen=True)
class LevelSpec:
    level: int
    cell_deg: float
    width_km: tuple[int, int]


LEVELS: tuple[LevelSpec, ...] = (
    LevelSpec(1, 0.3, (100, 200)),
    LevelSpec(2, 0.6, (200, 400)),
    LevelSpec(3, 1.2, (400, 800)),
    LevelSpec(4, 2.4, (800, 1600)),
    LevelSpec(5, 4.8, (1600, 3200)),
    LevelSpec(6, 9.6, (3200, 6400)),
)


# Cluster representatives use the same public spot contract as tile data.
# ``nameDistanceKm`` is nullable for ADM2/ADM1 fallbacks, but remains required
# so consumers can distinguish a missing field from an administrative fallback.
SPOT_FIELDS = (
    "id",
    "lat",
    "lon",
    "darkness",
    "bortle",
    "near",
    "name",
    "nameDistanceKm",
    "altitude",
)


def _normalize_spot(
    source_spot: object, tile_path: Path, spot_index: int
) -> tuple[dict, float, float]:
    if not isinstance(source_spot, dict):
        raise ValueError(
            f"Invalid spot in {tile_path}: spot {spot_index} must be an object"
        )
    missing = [field for field in SPOT_FIELDS if field not in source_spot]
    if missing:
        fields = ", ".join(missing)
        raise ValueError(
            f"Invalid spot in {tile_path}: spot {spot_index} missing field(s): {fields}"
        )
    try:
        lat = float(source_spot["lat"]) + 0.0
        lon = float(source_spot["lon"]) + 0.0
        darkness = float(source_spot["darkness"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid spot in {tile_path}: spot {spot_index} has non-numeric "
            "lat/lon/darkness"
        ) from exc
    name = source_spot["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"Invalid spot in {tile_path}: spot {spot_index} has an empty or non-string name"
        )
    name_distance = source_spot["nameDistanceKm"]
    if name_distance is not None:
        if (
            isinstance(name_distance, bool)
            or not isinstance(name_distance, Real)
            or not math.isfinite(float(name_distance))
            or float(name_distance) < 0
        ):
            raise ValueError(
                f"Invalid spot in {tile_path}: spot {spot_index} has an invalid nameDistanceKm"
            )
    normalized = {field: source_spot[field] for field in SPOT_FIELDS}
    normalized["lat"] = lat
    normalized["lon"] = lon
    normalized["darkness"] = darkness
    # Keep the wire value JSON-compatible even when callers pass a numeric
    # scalar from NumPy or another Real implementation.
    normalized["nameDistanceKm"] = (
        None if name_distance is None else float(name_distance)
    )
    return normalized, lat, lon


def _new_accumulator(spot: dict, lat: float, lon: float) -> dict:
    return {
        "count": 1,
        "sum_lat": lat,
        "sum_lon": lon,
        "min_lat": lat,
        "min_lon": lon,
        "max_lat": lat,
        "max_lon": lon,
        "rep": spot,
    }


def _consider(accumulator: dict, spot: dict, lat: float, lon: float) -> None:
    accumulator["count"] += 1
    accumulator["sum_lat"] += lat
    accumulator["sum_lon"] += lon
    accumulator["min_lat"] = min(accumulator["min_lat"], lat)
    accumulator["min_lon"] = min(accumulator["min_lon"], lon)
    accumulator["max_lat"] = max(accumulator["max_lat"], lat)
    accumulator["max_lon"] = max(accumulator["max_lon"], lon)

    representative = accumulator["rep"]
    if (
        spot["darkness"] > representative["darkness"]
        or (
            spot["darkness"] == representative["darkness"]
            and str(spot["id"]) < str(representative["id"])
        )
    ):
        accumulator["rep"] = spot


def _cluster(cluster_id: str, accumulator: dict) -> dict:
    count = accumulator["count"]
    return {
        "id": cluster_id,
        "lat": accumulator["sum_lat"] / count + 0.0,
        "lon": accumulator["sum_lon"] / count + 0.0,
        "count": count,
        "bbox": [
            accumulator["min_lon"] + 0.0,
            accumulator["min_lat"] + 0.0,
            accumulator["max_lon"] + 0.0,
            accumulator["max_lat"] + 0.0,
        ],
        "rep": accumulator["rep"],
    }


def aggregate_spot_files(
    spots_dir: str | Path,
    allowed_tile_ids: Collection[str] | None = None,
) -> dict[int, list[dict]]:
    """Aggregate all spots, retaining only constant-size state per occupied cell."""
    allowed = set(allowed_tile_ids) if allowed_tile_ids is not None else None
    accumulators: dict[int, dict[tuple[int, int], dict]] = {
        spec.level: {} for spec in LEVELS
    }

    for tile_path in sorted(Path(spots_dir).glob("*.json")):
        if allowed is not None and tile_path.stem not in allowed:
            continue
        with tile_path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        try:
            spots = envelope["spots"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid spot tile {tile_path}: missing spots field") from exc
        if not isinstance(spots, list):
            raise ValueError(f"Invalid spot tile {tile_path}: spots must be an array")
        for spot_index, source_spot in enumerate(spots):
            normalized_spot, lat, lon = _normalize_spot(
                source_spot, tile_path, spot_index
            )
            for spec in LEVELS:
                cell = (
                    math.floor(lon / spec.cell_deg),
                    math.floor(lat / spec.cell_deg),
                )
                level_accumulators = accumulators[spec.level]
                accumulator = level_accumulators.get(cell)
                if accumulator is None:
                    level_accumulators[cell] = _new_accumulator(
                        normalized_spot, lat, lon
                    )
                else:
                    _consider(accumulator, normalized_spot, lat, lon)

    result: dict[int, list[dict]] = {}
    for spec in LEVELS:
        result[spec.level] = sorted(
            (
                _cluster(f"L{spec.level}_{ix}_{iy}", accumulator)
                for (ix, iy), accumulator in accumulators[spec.level].items()
            ),
            key=lambda cluster: cluster["id"],
        )
    return result


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    options = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        options.update({"indent": 4, "separators": (",", ": ")})
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def write_cluster_files(
    spots_dir: str | Path,
    clusters_dir: str | Path,
    *,
    data_year: int,
    generated: str,
    allowed_tile_ids: Collection[str] | None = None,
) -> dict[int, Path]:
    """Write all levels and their deterministic manifest.

    Manifest paths are bare filenames relative to the directory containing
    ``index.json``. Each path points to a level file written beside the
    manifest by this call.
    """
    output_dir = Path(clusters_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clusters = aggregate_spot_files(spots_dir, allowed_tile_ids)
    paths: dict[int, Path] = {}
    manifest_levels = []

    for spec in LEVELS:
        path = output_dir / f"L{spec.level}.json"
        payload = _json_bytes(clusters[spec.level])
        path.write_bytes(payload)
        paths[spec.level] = path
        manifest_levels.append(
            {
                "level": spec.level,
                "cell_deg": spec.cell_deg,
                "width_km": list(spec.width_km),
                "files": [
                    {
                        "path": path.name,
                        "hash": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        )

    (output_dir / "index.json").write_bytes(
        _json_bytes(
            {
                "schema": 1,
                "generated": generated,
                "data_year": data_year,
                "levels": manifest_levels,
            },
            pretty=True,
        )
    )
    return paths
