"""Step 5: Spot ID generation, near passthrough, altitude stub."""


def enrich_spot(spot: dict) -> dict:
    """Enrich a single spot: add id, preserve near, set altitude, strip row/col.
    Also strips name if present (defensive)."""
    enriched = dict(spot)
    enriched["id"] = spot_id(enriched["lat"], enriched["lon"])
    enriched["near"] = spot.get("near")
    enriched["altitude"] = None
    enriched.pop("row", None)
    enriched.pop("col", None)
    enriched.pop("name", None)
    return enriched


def enrich_all(spots: list[dict]) -> list[dict]:
    """Enrich a batch of spots: generate id, preserve near, set altitude."""
    return [enrich_spot(s) for s in spots]


def spot_id(lat: float, lon: float) -> str:
    """
    Generate a unique, deterministic spot ID from coordinates.

    Format: "lat_lon" with 4 decimal places.
    Normalizes -0.0 to 0.0 via adding 0.0 to avoid "-0.0000".

    Idempotent: same (lat, lon) -> same output.
    """
    return f"{lat + 0.0:.4f}_{lon + 0.0:.4f}"
