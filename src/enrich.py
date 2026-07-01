"""Step 5: OSM place lookup, near-town (preserved), altitude stub, spot ID generation."""
import re

import requests


def enrich_spot(
    spot: dict,
    region: dict,
    overpass_url: str = "https://overpass-api.de/api/interpreter",
) -> dict:
    """
    Look up the nearest OSM place and populate name/near/altitude.

    Returns the spot dict augmented with name, near, altitude (or None).
    The `near` field is the fallback display for rural spots that have no named
    place; it is set upstream by `attach_near_town` and preserved here.
    """
    enriched = dict(spot)
    query = f"""
    [out:json][timeout:10];
    is_in({spot["lat"]},{spot["lon"]})->.a;
    area[admin_level={region["admin_level"]}]["ISO3166-1"="{region["osm_country_code"]}"](area.a)->.country;
    (
      node["place"](around:10000,{spot["lat"]},{spot["lon"]});
      way["place"](around:10000,{spot["lat"]},{spot["lon"]});
    );
    out center 5;
    """
    try:
        resp = requests.get(overpass_url, params={"data": query}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        if elements:
            first = elements[0]
            enriched["name"] = first.get("tags", {}).get("name", None)
        else:
            enriched["name"] = None
    except requests.RequestException:
        enriched["name"] = None

    enriched["near"] = spot.get("near")
    enriched["altitude"] = None
    return enriched


def enrich_all(spots: list[dict], region: dict) -> list[dict]:
    """Enrich a batch of spots. For MVP, sequential (can be parallelized later)."""
    return [enrich_spot(s, region) for s in spots]


def _slugify(name: str) -> str:
    """Lowercase, replace spaces with hyphens, remove non-alphanumeric except hyphens."""
    s = name.lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def spot_id(
    lat: float,
    lon: float,
    region_code: str,
    name: str | None = None,
    dept: int | None = None,
) -> str:
    """
    Generate a unique, human-readable spot ID.

    Format: {region_code}-{dept:02d}-{slug}
    Where slug is the sanitized place name, or "{abs(lat_int):02d}_{abs(lon_int):03d}" fallback.

    Spot ID scheme: <iso2>-<dept_or_admin>-<slug>
    - For French regions: dept is the 2-digit department code (e.g. 09 for Ariège).
    - For non-French regions, pass dept=None; the default "00" is used. The
      caller is responsible for adding a more specific admin code if needed.
    - For unnamed spots, the slug falls back to "{abs(int(lat)):02d}_{abs(int(lon)):03d}".

    Idempotent: same inputs -> same output.
    """
    dept_str = f"{dept:02d}" if dept is not None else "00"
    if name:
        slug = _slugify(name)
        if slug:
            return f"{region_code}-{dept_str}-{slug}"
    return f"{region_code}-{dept_str}-{abs(int(lat)):02d}_{abs(int(lon)):03d}"
