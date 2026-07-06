"""Step 5: OSM place lookup, near-town (preserved), altitude stub, spot ID generation."""
import time

import requests

from .config import PLACE_QUERY_TIMEOUT, PLACE_SEARCH_RADIUS_KM
from .utils import haversine_km


def _fetch_places(region: dict, overpass_url: str = "https://overpass-api.de/api/interpreter") -> list[dict]:
    """
    Fetch all OSM place nodes/ways within the region bbox.

    Returns a list of dicts with keys: name, lat, lon.
    Retries up to 3 times with exponential backoff on HTTP errors.
    """
    lon_min, lat_min, lon_max, lat_max = region["bbox"]
    query = (
        f'[out:json][timeout:{PLACE_QUERY_TIMEOUT}];'
        f'(node["place"]["name"]({lat_min},{lon_min},{lat_max},{lon_max});'
        f'way["place"]["name"]({lat_min},{lon_min},{lat_max},{lon_max}););'
        f'out center;'
    )
    headers = {"User-Agent": "darkskyspots-pipeline/1.0"}

    retries = [2, 4]
    for attempt in range(3):
        try:
            resp = requests.get(
                overpass_url,
                params={"data": query},
                headers=headers,
                timeout=PLACE_QUERY_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            places = []
            for el in data.get("elements", []):
                tags = el.get("tags", {})
                name = tags.get("name")
                if not name:
                    continue
                if el["type"] == "node":
                    lat, lon = el["lat"], el["lon"]
                else:
                    center = el.get("center")
                    if not center:
                        continue
                    lat, lon = center["lat"], center["lon"]
                places.append({"name": name, "lat": lat, "lon": lon})
            return places
        except requests.RequestException as e:
            if attempt < len(retries):
                time.sleep(retries[attempt])
            else:
                raise RuntimeError(f"Overpass query failed after {attempt + 1} attempts") from e


def _nearest_place(spot: dict, places: list[dict], max_radius_km: float = PLACE_SEARCH_RADIUS_KM) -> str | None:
    """
    Return the name of the nearest place within max_radius_km, or None.
    """
    best_name: str | None = None
    best_km = float("inf")
    for p in places:
        d = haversine_km(spot["lat"], spot["lon"], p["lat"], p["lon"])
        if d < best_km:
            best_km = d
            best_name = p["name"]
    if best_km <= max_radius_km:
        return best_name
    return None


def enrich_spot(spot: dict, places: list[dict], max_radius_km: float = PLACE_SEARCH_RADIUS_KM) -> dict:
    """
    Enrich a single spot: add id, nearest place name, preserve near, set altitude.
    Strips transient row/col fields before returning.

    Returns the spot dict augmented with id, name, near, altitude (or None).
    """
    enriched = dict(spot)
    enriched["id"] = spot_id(enriched["lat"], enriched["lon"])
    enriched["name"] = _nearest_place(spot, places, max_radius_km)
    enriched["near"] = spot.get("near")
    enriched["altitude"] = None
    # Strip transient grid indices (used up to Step 2, not part of output contract)
    enriched.pop("row", None)
    enriched.pop("col", None)
    return enriched


def enrich_all(spots: list[dict], region: dict) -> list[dict]:
    """Enrich a batch of spots using a single batched OSM fetch."""
    places = _fetch_places(region)
    return [enrich_spot(s, places, PLACE_SEARCH_RADIUS_KM) for s in spots]


def spot_id(lat: float, lon: float) -> str:
    """
    Generate a unique, deterministic spot ID from coordinates.

    Format: "lat_lon" with 4 decimal places.
    Normalizes -0.0 to 0.0 via adding 0.0 to avoid "-0.0000".

    Idempotent: same (lat, lon) -> same output.
    """
    return f"{lat + 0.0:.4f}_{lon + 0.0:.4f}"
