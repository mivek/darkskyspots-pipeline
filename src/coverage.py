"""Step 4: GeoNames places loader, coverage guarantee, attach nearest town."""
import logging
import math
import zipfile
from pathlib import Path

from .config import COVERAGE_RADIUS_KM, MAX_NEAR_DISTANCE_KM, MIN_SPOTS_PER_AREA
from .utils import haversine_km, nearest_commune

logger = logging.getLogger(__name__)

# Rough conversion: 1 degree ≈ 111 km at the equator
_DEG_PER_KM = 1.0 / 111.0


def _load_places_from_file(path: str | Path) -> list[dict]:
    """Parse a GeoNames cities500.txt file and return list of {name, lat, lon, population}.

    The file is tab-separated with NO header row. Columns (0-indexed):
      0: geonameid, 1: name, 2: asciiname, 3: alternatenames,
      4: latitude, 5: longitude, 6: feature class, 7: feature code,
      8: country code, 9: cc2, 10: admin1, 11: admin2, 12: admin3,
      13: admin4, 14: population, 15: elevation, 16: dem,
      17: timezone, 18: modification date
    """
    places: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) < 15:
                continue  # malformed line
            try:
                lat = float(cols[4])
                lon = float(cols[5])
            except (ValueError, IndexError):
                continue
            name = cols[1] if cols[1] else ""
            pop_str = cols[14].strip()
            try:
                population = int(pop_str) if pop_str else None
            except (ValueError, TypeError):
                population = None
            places.append({
                "name": name,
                "lat": lat,
                "lon": lon,
                "population": population,
            })
    return places


def load_places(
    region: dict,
    data_dir: str = "data",
) -> list[dict]:
    """
    Load populated places for the region from GeoNames (cities500).

    Extracts ``data/cities500.zip`` → ``data/cities500.txt`` on first run,
    then parses the tab-separated file. Retains only places within the
    region's bounding box expanded by a 100 km margin.

    Returns list of ``{name, lat, lon, population}``.
    """
    data_path = Path(data_dir)
    txt_path = data_path / "cities500.txt"
    zip_path = data_path / "cities500.zip"

    # Extract on first run if .txt doesn't exist
    if not txt_path.exists():
        if not zip_path.exists():
            raise FileNotFoundError(
                f"GeoNames data not found: {zip_path}. "
                "Download from https://download.geonames.org/export/dump/cities500.zip"
            )
        logger.info("Extracting %s → %s", zip_path, txt_path)
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            try:
                zf.extract("cities500.txt", str(data_path))
            except KeyError:
                raise RuntimeError(
                    f"cities500.txt not found in {zip_path}. Is this a valid GeoNames cities500.zip?"
                )

    # Parse the TSV file
    all_places = _load_places_from_file(txt_path)

    # Bbox filtering with margin_km margin
    margin_km = 100
    lon_min, lat_min, lon_max, lat_max = region["bbox"]
    mid_lat = (lat_min + lat_max) / 2.0
    # Degree-based margin (approximate): sufficient for pre-filtering.
    # Not haversine-exact — may be off by ~1 km at high latitudes, but generous margin covers it.
    margin_lat = float(margin_km) * _DEG_PER_KM
    margin_lon = float(margin_km) * _DEG_PER_KM / math.cos(math.radians(mid_lat))

    filtered = [
        p
        for p in all_places
        if (lat_min - margin_lat) <= p["lat"] <= (lat_max + margin_lat)
        and (lon_min - margin_lon) <= p["lon"] <= (lon_max + margin_lon)
    ]

    logger.info("  Loaded %d places (GeoNames), %d within bbox+%dkm margin", len(all_places), len(filtered), margin_km)
    return filtered


def ensure_coverage(
    candidates: list[dict],
    all_mesh_points: list[dict],
    communes: list[dict],
    min_spots: int = MIN_SPOTS_PER_AREA,
    radius_km: float = COVERAGE_RADIUS_KM,
) -> list[dict]:
    """
    For each commune, verify >= min_spots candidates within radius_km.
    If insufficient, add the darkest from all_mesh_points within radius_km
    that aren't already in candidates.

    Returns augmented candidates list.
    """
    kept_ids = {id(c) for c in candidates}
    result = list(candidates)

    for commune in communes:
        within = [
            c
            for c in result
            if haversine_km(commune["lat"], commune["lon"], c["lat"], c["lon"])
            <= radius_km
        ]
        needed = min_spots - len(within)
        if needed <= 0:
            continue

        pool = sorted(
            [
                p
                for p in all_mesh_points
                if id(p) not in kept_ids
                and haversine_km(commune["lat"], commune["lon"], p["lat"], p["lon"])
                <= radius_km
            ],
            key=lambda p: p["darkness"],
            reverse=True,
        )
        for p in pool[:needed]:
            result.append(p)
            kept_ids.add(id(p))
    return result


def attach_near_town(spots: list[dict], communes: list[dict]) -> list[dict]:
    """
    For each spot, set `spot["near"]` to the name of the closest commune
    (or ``""`` if the closest commune is farther than ``MAX_NEAR_DISTANCE_KM``).

    Defensive: if a spot already has a non-None `near` value, it is preserved
    (this makes the function safe to call twice in a pipeline).
    """
    for spot in spots:
        if spot.get("near") is not None:
            continue
        near = nearest_commune(spot, communes, max_distance_km=MAX_NEAR_DISTANCE_KM)
        spot["near"] = near if near is not None else ""
    return spots
