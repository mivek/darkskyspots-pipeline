"""Shared utilities (haversine, nearest commune)."""
import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in km between two WGS84 points using the haversine formula.

    Args:
        lat1, lon1: First point in decimal degrees.
        lat2, lon2: Second point in decimal degrees.

    Returns:
        Distance in kilometers.
    """
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_commune(
    spot: dict, communes: list[dict], max_distance_km: float | None = None
) -> str | None:
    """
    Return the name of the commune closest to `spot`, or None if no usable commune.

    Skips communes with missing coordinates (`lat` is None or `lon` is None),
    so a partially-loaded commune set never crashes the orchestrator.

    If `max_distance_km` is set and the closest commune is farther than that,
    returns None (indicating the spot is too far from any known place).
    """
    best_name: str | None = None
    best_km = float("inf")
    for commune in communes:
        lat = commune.get("lat")
        lon = commune.get("lon")
        if lat is None or lon is None:
            continue
        d = haversine_km(spot["lat"], spot["lon"], float(lat), float(lon))
        if d < best_km:
            best_km = d
            best_name = commune.get("name")
    if max_distance_km is not None and best_km > max_distance_km:
        return None
    return best_name
