"""Step 4: OSM communes loader, coverage guarantee, attach nearest commune."""
import requests

from .config import COVERAGE_RADIUS_KM, MIN_SPOTS_PER_AREA
from .utils import haversine_km, nearest_commune


def load_communes(
    region: dict,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
) -> list[dict]:
    """
    Load OSM communes for the region via Overpass API.

    Returns list of {name, lat, lon, population}.
    For testing, this function can be monkeypatched.
    """
    bbox = region["bbox"]
    overpass_query = f"""
    [out:json][timeout:30];
    area[admin_level={region["admin_level"]}]["ISO3166-1"="{region["osm_country_code"]}"]->.country;
    nwr[admin_level={region["admin_level"]}](area.country);
    out center;
    """
    resp = requests.get(overpass_url, params={"data": overpass_query}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    communes = []
    for element in data.get("elements", []):
        if "tags" not in element:
            continue
        name = element["tags"].get("name", "")
        pop_str = element["tags"].get("population", "")
        population = int(pop_str) if pop_str and pop_str.isdigit() else None
        if "center" in element:
            lat = element["center"]["lat"]
            lon = element["center"]["lon"]
        elif "lat" in element:
            lat = element["lat"]
            lon = element["lon"]
        else:
            continue
        communes.append({"name": name, "lat": lat, "lon": lon, "population": population})
    return communes


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
    For each spot, set `spot["near"]` to the name of the closest commune.

    Defensive: if a spot already has a non-None `near` value, it is preserved
    (this lets `enrich.py` keep an OSM-derived "near" if it ever sets one,
    and it makes the function safe to call twice in a pipeline).
    """
    for spot in spots:
        if spot.get("near") is not None:
            continue
        spot["near"] = nearest_commune(spot, communes)
    return spots
