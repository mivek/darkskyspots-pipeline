"""Step 6: tile I/O, spot diff, classification, empty tile enumeration."""
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable, Iterator

from .tile_id import tile_id


def tile_envelope(
    tile_id_str: str,
    spots: list[dict],
    version: str,
    source: str,
    generated: str,
) -> dict:
    """Build the tile envelope dict with strict key order: version, source, generated, tile, spots."""
    return {
        "version": version,
        "source": source,
        "generated": generated,
        "tile": tile_id_str,
        "spots": spots,
    }


def write_tile_file(
    tile_id_str: str,
    spots: list[dict],
    output_dir: str,
    version: str,
    source: str,
    generated: str,
) -> str:
    """Atomically write a tile JSON file to {output_dir}/spots/{tile_id_str}.json."""
    spots_dir = Path(output_dir) / "spots"
    spots_dir.mkdir(parents=True, exist_ok=True)
    out_path = spots_dir / f"{tile_id_str}.json"

    envelope = tile_envelope(tile_id_str, spots, version, source, generated)
    content = json.dumps(envelope, indent=4, ensure_ascii=False) + "\n"

    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=spots_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, out_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return str(out_path)


def spot_diff(old_spots: list[dict], new_spots: list[dict]) -> dict:
    """
    Compare two spot lists by id.

    Returns {changed, added, removed, modified}.
    """
    old_by_id = {s["id"]: s for s in old_spots}
    new_by_id = {s["id"]: s for s in new_spots}

    old_ids = set(old_by_id.keys())
    new_ids = set(new_by_id.keys())

    added = new_ids - old_ids
    removed = old_ids - new_ids
    modified = set()
    for sid in old_ids & new_ids:
        if old_by_id[sid] != new_by_id[sid]:
            modified.add(sid)

    return {
        "changed": bool(added or removed or modified),
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
    }


def classify_spots_into_tiles(
    spots: list[dict], tile_size_deg: float = 1.0
) -> dict[str, list[dict]]:
    """
    Group spots by tile ID.

    Returns {tile_id: [spots_in_tile]}. Every tile that has at least one spot
    is included. (Empty tiles are written later via write_empty_tiles.)
    """
    tiles: dict[str, list[dict]] = {}
    for spot in spots:
        tid = tile_id(spot["lat"], spot["lon"])
        tiles.setdefault(tid, []).append(spot)
    return tiles


def load_existing_tile(path: str) -> dict | None:
    """Load an existing tile JSON file, return envelope dict or None."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def enumerate_tiles_in_bbox(
    bbox: tuple[float, float, float, float], tile_size_deg: float = 1.0
) -> Iterator[str]:
    """
    Enumerate every tile ID whose SW corner falls within the bounding box.

    bbox: (lon_min, lat_min, lon_max, lat_max) in WGS84 decimal degrees.
    Yields tile_id strings like 'N042E001'.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_start = int(math.floor(lat_min))
    lat_end = int(math.floor(lat_max))
    lon_start = int(math.floor(lon_min))
    lon_end = int(math.floor(lon_max))
    for lat in range(lat_start, lat_end):
        for lon in range(lon_start, lon_end):
            yield tile_id(float(lat) + 0.5, float(lon) + 0.5)


def write_empty_tiles(
    tiles: Iterable[str],
    output_dir: str,
    version: str,
    source: str,
    generated: str,
) -> int:
    """
    Write empty tile JSONs for any tile not already present in output_dir/spots/.

    Returns the number of empty tiles written.
    """
    spots_dir = Path(output_dir) / "spots"
    count = 0
    for tid in tiles:
        target = spots_dir / f"{tid}.json"
        if not target.exists():
            write_tile_file(tid, [], output_dir, version, source, generated)
            count += 1
    return count
