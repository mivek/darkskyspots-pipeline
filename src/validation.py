"""Validation (§6 of design): control-point comparison runner."""
import json
from datetime import datetime, timezone
from typing import Callable


def load_checkpoints(path: str) -> list[dict]:
    """Load and validate checkpoints.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("checkpoints.json must be a JSON array")
    for i, cp in enumerate(data):
        for key in ("lat", "lon", "expected_bortle"):
            if key not in cp:
                raise ValueError(
                    f"checkpoints[{i}] missing required key '{key}'"
                )
    return data


def validate_bortle(
    control_points: list[dict],
    darkness_fn: Callable,
    bortle_fn: Callable,
) -> list[dict]:
    """
    For each control point, compute darkness and bortle at (lat, lon)
    using the provided functions, and compare to expected_bortle.

    Returns list of result dicts with match criterion: ±1 Bortle class (D4).
    """
    results = []
    for cp in control_points:
        darkness_val = darkness_fn(cp["lat"], cp["lon"])
        bortle_val = bortle_fn(cp["lat"], cp["lon"])
        match = abs(bortle_val - cp["expected_bortle"]) <= 1
        results.append({
            "lat": cp["lat"],
            "lon": cp["lon"],
            "expected_bortle": cp["expected_bortle"],
            "got_bortle": bortle_val,
            "got_darkness": darkness_val,
            "match": match,
        })
    return results


def append_checkpoint_results(path: str, results: list[dict], run_id: str) -> None:
    """Append validation results with run_id to checkpoints.json."""
    with open(path, encoding="utf-8") as f:
        existing = json.load(f)
    for r in results:
        r["run_id"] = run_id
    existing.extend(results)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=4, ensure_ascii=False)
        f.write("\n")
