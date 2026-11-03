"""Canonical WGS84 representations, including the antimeridian.

Splitting at ±180 changes representation, not the geographic feature. Source
geometries remain authoritative; index envelopes never become masks.
"""

import math
from shapely.geometry import Polygon, MultiPolygon, LineString, box
from shapely.ops import split, unary_union
from shapely.affinity import translate


def longitude(value):
    return value if -180 <= value < 180 else (value + 180) % 360 - 180


def domain(bounds):
    west, south, east, north = bounds
    if west <= east:
        return box(west, south, east, north)
    return unary_union([box(west, south, 180, north), box(-180, south, east, north)])


def inside(lon, lat, bounds):
    w, s, e, n = bounds
    return s <= lat <= n and (w <= lon <= e if w <= e else lon >= w or lon <= e)


def contains(geometry, point):
    """Strict interior without making the representation seam a shoreline."""
    if geometry.contains(point):
        return True
    if abs(point.x) != 180:
        return False
    pieces = (
        list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    )
    joined = unary_union(
        [translate(p, xoff=360) if p.bounds[2] <= 0 else p for p in pieces]
    )
    from shapely.geometry import Point

    return joined.contains(Point(180, point.y))


def canonical(geometry):
    """Unwrap crossing rings together, then represent them in [-180,180]."""
    if geometry.geom_type == "MultiPolygon":
        return unary_union([canonical(g) for g in geometry.geoms])
    if geometry.geom_type != "Polygon":
        raise ValueError("Expected polygon geographic geometry")

    def ring(coords, anchor=None):
        result = []
        for x, y, *_ in coords:
            if result:
                while x - result[-1][0] > 180:
                    x -= 360
                while x - result[-1][0] < -180:
                    x += 360
            result.append((x, y))
        if anchor is not None:
            centre = sum(x for x, y in result) / len(result)
            offset = round((anchor - centre) / 360) * 360
            result = [(x + offset, y) for x, y in result]
        return result

    xs = [p[0] for p in geometry.exterior.coords]
    # A whole-world rectangular coverage domain is not a narrow crossing.
    if min(xs) == -180 and max(xs) == 180 and set(xs) <= {-180, 180}:
        return geometry
    crossing = any(
        abs(a[0] - b[0]) > 180
        for a, b in zip(geometry.exterior.coords, list(geometry.exterior.coords)[1:])
    )
    if not crossing and -180 <= geometry.bounds[0] <= geometry.bounds[2] <= 180:
        return geometry
    exterior = ring(geometry.exterior.coords)
    anchor = sum(x for x, y in exterior) / len(exterior)
    unwrapped = Polygon(exterior, [ring(r.coords, anchor) for r in geometry.interiors])
    parts = [unwrapped]
    for meridian in [-180, 180]:
        following = []
        for p in parts:
            following.extend(
                split(p, LineString([(meridian, -90), (meridian, 90)])).geoms
            )
        parts = following
    result = []
    for p in parts:
        x = p.representative_point().x
        result.append(translate(p, xoff=-360 if x > 180 else 360 if x < -180 else 0))
    return unary_union(result)
