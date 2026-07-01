"""Universal tile naming convention (Decision 3 in the design doc)."""
import math
import re

TILE_PATTERN = re.compile(r"^([NS])(\d{3})([EW])(\d{3})$")


def tile_id(lat: float, lon: float) -> str:
    """
    Universal tile naming convention (Decision 3).
    Returns e.g. 'N042E001' for (42.7283, 1.6492).
    Raises ValueError if lat == 90, lat == -90 (pole points),
    lon == 180 (E180 does not exist), or lat/lon out of range.
    """
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude {lat} out of range [-90, 90]")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude {lon} out of range [-180, 180]")
    if lat == 90.0 or lat == -90.0:
        raise ValueError(
            f"Latitude {lat} is a pole point, no valid 1° tile — "
            "clamp to 89.9999 or use a different function for polar points"
        )
    if lon == 180.0:
        raise ValueError("Longitude 180 is invalid; use -180 for W180 tile")
    lat_part = (
        f"N{int(math.floor(lat)):03d}"
        if lat >= 0
        else f"S{abs(int(math.floor(lat))):03d}"
    )
    lon_part = (
        f"E{int(math.floor(lon)):03d}"
        if lon >= 0
        else f"W{abs(int(math.floor(lon))):03d}"
    )
    return f"{lat_part}{lon_part}"


def tile_bounds(tile_id_str: str) -> tuple[float, float, float, float]:
    """Return (lat_min, lon_min, lat_max, lon_max) for the tile."""
    m = TILE_PATTERN.match(tile_id_str)
    if not m:
        raise ValueError(f"Invalid tile ID: {tile_id_str}")
    lat_base = int(m.group(2))
    lon_base = int(m.group(4))
    lat_min = lat_base if m.group(1) == "N" else -lat_base
    lon_min = lon_base if m.group(3) == "E" else -lon_base
    return (lat_min, lon_min, lat_min + 1.0, lon_min + 1.0)
