#!/usr/bin/env python3
"""Measure the GeoNames naming cascade before wiring it into the pipeline.

This read-only audit tool chooses GeoNames feature *codes* (rather than whole
feature classes) and produces a human-reviewable report of the names they
would give to clipped spot tiles.  It streams national archives directly from
``FR.zip``, ``ES.zip`` or ``GB.zip`` and indexes only records in the region
envelope expanded by 40 km.

Example::

    python measure_landmarks.py --spots-dir output/crosscheck/spots \
      --bbox -6 41 8 51 --country-code FR --geonames-dir data/geonames \
      --out-json validation/naming_cascade_france_2025.json \
      --out-md validation/naming_cascade_france_2025.md

The candidate code list below is a conservative starting point and must be
reviewed alongside the generated 100-name sample before becoming runtime
configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from src.geonames import (
    FILTERED_FEATURE_CODES,
    GeoNamesIndex,
    NAMING_FEATURE_CODES,
    validate_geonames_manifest,
)

try:
    from scipy.spatial import cKDTree
except ImportError:  # pragma: no cover - project environment includes SciPy
    cKDTree = None

EARTH_RADIUS_KM = 6371.0088
ORDINARY_MAX_KM = 40.0
EXPECTED_FR_SPOTS = 2139
DEFAULT_BBOX = (-6.0, 41.0, 8.0, 51.0)

NAMED_ISLANDS: tuple[dict[str, object], ...] = (
    {"name": "Isle of Wight", "lat": 50.68, "lon": -1.30, "country": "GB"},
    {"name": "Anglesey", "lat": 53.27, "lon": -4.35, "country": "GB"},
    {"name": "Isles of Scilly", "lat": 49.92, "lon": -6.30, "country": "GB"},
    {"name": "Lundy", "lat": 51.18, "lon": -4.67, "country": "GB"},
    {"name": "Isle of Sheppey", "lat": 51.40, "lon": 0.75, "country": "GB"},
    {"name": "Achill Island", "lat": 53.96, "lon": -10.00, "country": "IE"},
    {"name": "Valentia Island", "lat": 51.93, "lon": -10.35, "country": "IE"},
    {"name": "Arranmore", "lat": 55.00, "lon": -8.30, "country": "IE"},
)
CROWN_DEPENDENCIES: tuple[dict[str, object], ...] = (
    {"name": "Isle of Man", "lat": 54.23, "lon": -4.50, "country": "IM"},
    {"name": "Jersey", "lat": 49.19, "lon": -2.10, "country": "JE"},
    {"name": "Guernsey", "lat": 49.45, "lon": -2.58, "country": "GG"},
)

# A code list, deliberately not ``feature_class in {T,V,L}``.  It is shared
# with the runtime and the extraction utility from ``src.geonames``.
CANDIDATE_CODES = NAMING_FEATURE_CODES

CODE_REASONS: dict[str, str] = {
    "PPL": "Localité peuplée; repère lisible et comparable à near.",
    "PPLA": "Siège administratif; toponyme local identifiable.",
    "PPLA2": "Siège administratif; toponyme local identifiable.",
    "PPLA3": "Siège administratif; toponyme local identifiable.",
    "PPLA4": "Siège administratif; toponyme local identifiable.",
    "PPLA5": "Siège administratif; toponyme local identifiable.",
    "PPLC": "Capitale; repère nommé stable.",
    "PPLF": "Ancien site de peuplement; toponyme encore cartographié.",
    "PPLG": "Quartier/section de peuplement nommé; repère local.",
    "PPLL": "Lieu de peuplement abandonné; toponyme conservé.",
    "PPLR": "Lieu de peuplement rural; repère local explicite.",
    "PPLS": "Lieu de peuplement; repère local explicite.",
    "CAPE": "Cap nommé et ponctuel; repère géographique lisible.",
    "CLDA": "Caldeira nommée; relief singulier.",
    "CNYN": "Canyon nommé; relief singulier.",
    "GRGE": "Gorge nommée; relief singulier.",
    "HDLD": "Pointe terrestre importante; repère ponctuel.",
    "ISL": "Île nommée; repère ponctuel.",
    "ISLS": "Groupe d'îles nommé; repère ponctuel.",
    "MT": "Montagne nommée; relief significatif.",
    "MTS": "Chaîne ou groupe de montagnes nommé; relief significatif.",
    "PASS": "Col nommé; repère routier et géographique significatif.",
    "PK": "Sommet nommé; relief significatif.",
    "PKS": "Groupe de sommets nommé; relief significatif.",
    "PLAT": "Plateau nommé; relief étendu et identifiable.",
    "PROM": "Promontoire nommé; relief singulier.",
    "SDL": "Zone saline nommée; zone naturelle identifiable.",
    "UPLD": "Haut-plateau nommé; relief étendu identifiable.",
    "VLC": "Vallée nommée; repère naturel étendu.",
    "FRST": "Forêt nommée; zone naturelle étendue.",
    "HTH": "Lande nommée; zone naturelle étendue.",
    "TUND": "Toundra nommée; zone naturelle étendue.",
    "LCTY": "Lieu-dit nommé; toponyme cartographique explicite.",
    "PRK": "Parc nommé; zone étendue identifiable.",
    "RESF": "Réserve forestière nommée; zone étendue.",
    "RESN": "Réserve naturelle nommée; zone étendue.",
    "RESW": "Réserve de faune nommée; zone étendue.",
    "RGN": "Région géographique nommée; repère étendu.",
    "RGNL": "Région naturelle nommée; repère étendu.",
    "LK": "Lac nommé; excellent repère ponctuel.",
    "LKC": "Bras/partie de lac nommé; retenu avec les lacs ponctuels.",
    "LKN": "Lac nommé; excellent repère ponctuel.",
    "LKS": "Groupe de lacs nommé; excellent repère ponctuel.",
    "RSV": "Réservoir nommé; excellent repère ponctuel.",
}


@dataclass(frozen=True)
class GeoName:
    geonameid: int
    name: str
    lat: float
    lon: float
    feature_class: str
    feature_code: str
    country_code: str


@dataclass(frozen=True)
class Match:
    name: str
    code: str
    distance_km: float | None
    tier: str
    geonameid: int


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def _unit_vector(lat: float, lon: float) -> tuple[float, float, float]:
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    c = math.cos(lat_r)
    return c * math.cos(lon_r), c * math.sin(lon_r), math.sin(lat_r)


def expanded_bbox(
    bbox: Sequence[float], margin_km: float = ORDINARY_MAX_KM
) -> tuple[float, float, float, float]:
    """Expand a WGS84 bbox conservatively by a distance in kilometres."""
    lon_min, lat_min, lon_max, lat_max = map(float, bbox)
    lat_margin = margin_km / EARTH_RADIUS_KM * 180.0 / math.pi
    max_abs_lat = min(89.9, max(abs(lat_min), abs(lat_max)) + lat_margin)
    lon_margin = margin_km / (EARTH_RADIUS_KM * math.cos(math.radians(max_abs_lat)))
    lon_margin = lon_margin * 180.0 / math.pi
    return lon_min - lon_margin, lat_min - lat_margin, lon_max + lon_margin, lat_max + lat_margin


def _parse_geoname(parts: list[str], expected_country: str) -> GeoName | None:
    if len(parts) < 19 or parts[8].upper() != expected_country.upper():
        return None
    try:
        geonameid = int(parts[0])
        lat, lon = float(parts[4]), float(parts[5])
    except (TypeError, ValueError):
        return None
    if not parts[1].strip() or not math.isfinite(lat) or not math.isfinite(lon):
        return None
    return GeoName(geonameid, parts[1], lat, lon, parts[6], parts[7], parts[8].upper())


def iter_geonames(
    zip_path: str | Path,
    country_code: str,
    bbox: Sequence[float],
    codes: set[str] | frozenset[str] | None = None,
) -> Iterator[GeoName]:
    """Stream country records matching country, bbox and optional codes."""
    lon_min, lat_min, lon_max, lat_max = map(float, bbox)
    member = f"{country_code.upper()}.txt"
    with zipfile.ZipFile(zip_path) as archive, archive.open(member) as handle:
        for raw in handle:
            parts = raw.decode("utf-8").rstrip("\n").split("\t")
            if len(parts) < 19:
                continue
            if codes is not None and parts[7] not in codes:
                continue
            try:
                lat, lon = float(parts[4]), float(parts[5])
            except (TypeError, ValueError):
                continue
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
            record = _parse_geoname(parts, country_code)
            if record is not None:
                yield record


def load_country_records(
    zip_path: str | Path,
    country_code: str,
    bbox: Sequence[float],
    candidate_codes: Iterable[str] = CANDIDATE_CODES,
) -> tuple[list[GeoName], list[GeoName], Counter[str], int]:
    """Load candidates/admins and count every observed code in one pass."""
    candidate_set = set(candidate_codes)
    ordinary: list[GeoName] = []
    admins: list[GeoName] = []
    observed: Counter[str] = Counter()
    total = 0
    lon_min, lat_min, lon_max, lat_max = map(float, bbox)
    member = f"{country_code.upper()}.txt"
    with zipfile.ZipFile(zip_path) as archive, archive.open(member) as handle:
        for raw in handle:
            parts = raw.decode("utf-8").rstrip("\n").split("\t")
            if len(parts) < 19 or parts[8].upper() != country_code.upper():
                continue
            try:
                lat, lon = float(parts[4]), float(parts[5])
            except (TypeError, ValueError):
                continue
            in_bbox = lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
            code = parts[7]
            record = _parse_geoname(parts, country_code)
            if record is None:
                continue
            # Administrative centroids are a fallback and must remain
            # available for the whole country.  Only ordinary candidates are
            # constrained by the region's +40 km import envelope.
            if parts[6] == "A" and code in {"ADM1", "ADM2"}:
                admins.append(record)
            if not in_bbox:
                continue
            observed[code] += 1
            total += 1
            if parts[6] != "A" and code in candidate_set:
                ordinary.append(record)
    return ordinary, admins, observed, total


class NearestIndex:
    """Nearest point index with exact-distance and ID tie-breaking."""

    def __init__(self, records: Sequence[GeoName]):
        self.records = tuple(records)
        self._tree = None
        if self.records and cKDTree is not None:
            self._tree = cKDTree([_unit_vector(r.lat, r.lon) for r in self.records])

    def nearest(self, lat: float, lon: float) -> tuple[GeoName, float] | None:
        if not self.records:
            return None
        if self._tree is None:
            candidates = self.records
        else:
            # Chord and great-circle distances have the same ordering.  Query
            # the nearest chord distance, then inspect every point at that
            # distance (including all coincident points) so the geonameid
            # tie-break is not truncated by an arbitrary k value.
            vector = _unit_vector(lat, lon)
            chord, _ = self._tree.query(vector, k=1)
            indices = self._tree.query_ball_point(vector, r=float(chord) + 1e-12)
            candidates = [self.records[i] for i in indices]
        return min(
            ((r, haversine_km(lat, lon, r.lat, r.lon)) for r in candidates),
            key=lambda item: (item[1], item[0].geonameid),
        )


def choose_match(
    lat: float,
    lon: float,
    ordinary: NearestIndex,
    admins: NearestIndex,
    admin1: NearestIndex | None = None,
) -> Match:
    nearest = ordinary.nearest(lat, lon)
    if nearest is not None:
        record, distance = nearest
        if distance <= ORDINARY_MAX_KM:
            tier = "under_5" if distance < 5 else "5_to_25" if distance < 25 else "25_to_40"
            return Match(record.name, record.feature_code, distance, tier, record.geonameid)
    # Administrative fallback has an intentional level order: ADM2 is tried
    # first even when an ADM1 centroid happens to be geographically closer.
    # ``admin1`` is optional for backwards-compatible direct callers; in that
    # case derive two small indexes from the combined input.
    if admin1 is None:
        admin2_records = [r for r in admins.records if r.feature_code == "ADM2"]
        admin1_records = [r for r in admins.records if r.feature_code == "ADM1"]
        admin2 = NearestIndex(admin2_records)
        admin1 = NearestIndex(admin1_records)
    else:
        admin2 = admins
    admin = admin2.nearest(lat, lon)
    if admin is None:
        admin = admin1.nearest(lat, lon)
    if admin is None:
        raise ValueError("No ADM2/ADM1 fallback is available; name cannot be guaranteed")
    record, _ = admin
    return Match(record.name, record.feature_code, None,
                 "ADM2" if record.feature_code == "ADM2" else "ADM1", record.geonameid)


def load_spots(spots_dir: str | Path) -> list[dict]:
    """Load and deterministically sort non-empty spot records from tile JSON."""
    spots: list[dict] = []
    for path in sorted(Path(spots_dir).rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("spots"), list):
            continue
        for index, spot in enumerate(payload["spots"]):
            if not isinstance(spot, dict):
                continue
            try:
                lat, lon = float(spot["lat"]), float(spot["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon):
                item = dict(spot)
                item.setdefault("id", f"{lat:.8f}_{lon:.8f}")
                item["_source_file"], item["_source_index"] = str(path), index
                spots.append(item)
    return sorted(spots, key=lambda s: (str(s.get("id", "")), s["lat"], s["lon"]))


def spot_extent(spots: Sequence[dict]) -> list[float] | None:
    """Return the WGS84 extent of spots with finite coordinates."""
    coordinates: list[tuple[float, float]] = []
    for spot in spots:
        try:
            lat, lon = float(spot["lat"]), float(spot["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(lat) and math.isfinite(lon):
            coordinates.append((lat, lon))
    if not coordinates:
        return None
    lats, lons = zip(*coordinates)
    return [min(lons), min(lats), max(lons), max(lats)]


def _sample_matches(rows: list[dict], count: int = 100) -> list[dict]:
    """Cover every winning code, then take 20 examples per tier.

    Code coverage comes first so a rare lake, pass or natural area cannot be
    hidden by the overwhelmingly common PPL winners.  The tier pass then
    ensures the long-distance cases remain visible; both passes use the stable
    ID ordering produced by :func:`load_spots`.
    """
    tiers = ("under_5", "5_to_25", "25_to_40", "ADM2", "ADM1")
    by_tier: dict[str, list[dict]] = {tier: [] for tier in tiers}
    for row in rows:
        by_tier.setdefault(row["tier"], []).append(row)
    sample: list[dict] = []
    used: set[tuple[str, str, str]] = set()
    by_code: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_code.setdefault((str(row.get("country", "")), row["code"]), []).append(row)
    for country, code in sorted(by_code):
        row = by_code[(country, code)][0]
        key = (country, row["tier"], str(row.get("id", "")))
        used.add(key)
        sample.append(row)
    countries = sorted({str(row.get("country", "")) for row in rows})
    for country in countries:
        for tier in tiers:
            country_rows = [row for row in by_tier.get(tier, [])
                            if str(row.get("country", "")) == country]
            for row in country_rows[:20]:
                key = (country, tier, str(row.get("id", "")))
                if key not in used:
                    used.add(key)
                    sample.append(row)
    if len(sample) < count:
        for row in rows:
            key = (str(row.get("country", "")), row["tier"], str(row.get("id", "")))
            if key not in used:
                used.add(key)
                sample.append(row)
                if len(sample) == count:
                    break
    return sample[:count]


def analyse(
    spots: Sequence[dict],
    ordinary_records: Sequence[GeoName],
    admin_records: Sequence[GeoName],
    observed_by_code: Counter[str] | None = None,
    expected_spots: int = EXPECTED_FR_SPOTS,
) -> dict:
    """Measure the cascade and return JSON-serialisable report data."""
    ordinary_index = NearestIndex(ordinary_records)
    admin2_index = NearestIndex([r for r in admin_records if r.feature_code == "ADM2"])
    admin1_index = NearestIndex([r for r in admin_records if r.feature_code == "ADM1"])
    rows: list[dict] = []
    for spot in spots:
        match = choose_match(float(spot["lat"]), float(spot["lon"]), ordinary_index,
                             admin2_index, admin1_index)
        runtime_distance = spot.get("nameDistanceKm")
        try:
            runtime_distance = None if runtime_distance is None else round(float(runtime_distance), 3)
        except (TypeError, ValueError):
            pass
        audit_distance = None if match.distance_km is None else round(match.distance_km, 3)
        rows.append({
            "id": str(spot.get("id", "")), "lat": float(spot["lat"]), "lon": float(spot["lon"]),
            "near": spot.get("near", ""), "darkness": spot.get("darkness"),
            "name": match.name, "code": match.code, "tier": match.tier,
            "distance_km": audit_distance,
            "displayed_name": spot.get("name"),
            "audit_name": match.name, "audit_code": match.code,
            "audit_distance_km": audit_distance,
            "runtime_name": spot.get("name"), "runtime_code": spot.get("nameFeatureCode"),
            "runtime_distance_km": runtime_distance,
        })
    tier_counts = Counter(row["tier"] for row in rows)
    code_counts = Counter(row["code"] for row in rows)
    distances = [row["distance_km"] for row in rows if row["distance_km"] is not None]
    warning = None
    if len(spots) != expected_spots:
        warning = (f"Corpus contient {len(spots)} spots FR, référence indicative {expected_spots} "
                   f"(écart {len(spots) - expected_spots:+d}); mesure poursuivie.")
        print(f"WARNING: {warning}", file=sys.stderr)
    return {
        "schema_version": 1, "expected_spots": expected_spots, "spot_count": len(spots),
        "spot_count_warning": warning, "ordinary_max_km": ORDINARY_MAX_KM,
        "distance_bins": {"under_5_km": tier_counts.get("under_5", 0),
                          "5_to_25_km": tier_counts.get("5_to_25", 0),
                          "25_to_40_km": tier_counts.get("25_to_40", 0),
                          "fallback_adm2": tier_counts.get("ADM2", 0),
                          "fallback_adm1": tier_counts.get("ADM1", 0)},
        "winner_by_code": dict(sorted(code_counts.items())),
        "ordinary_distance_km": {"count": len(distances),
                                 "min": round(min(distances), 3) if distances else None,
                                 "median": round(statistics.median(distances), 3) if distances else None,
                                 "max": round(max(distances), 3) if distances else None},
        "observed_by_code": dict(sorted((observed_by_code or Counter()).items())),
        "excluded_observed_by_code": dict(sorted(
            (code, count) for code, count in (observed_by_code or Counter()).items()
            if code not in CANDIDATE_CODES and code not in {"ADM1", "ADM2"}
        )),
        "candidate_codes": list(CANDIDATE_CODES), "samples": _sample_matches(rows),
    }


def _round_number(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 6)


def darkness_statistics(values: Iterable[object]) -> dict[str, object]:
    """Return stable summary statistics for spot darkness values."""
    valid: list[float] = []
    invalid = 0
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if math.isfinite(number):
            valid.append(number)
        else:
            invalid += 1
    valid.sort()
    quartiles = statistics.quantiles(valid, n=4, method="inclusive") if len(valid) >= 2 else []
    bins = {f"{index / 10:.1f}-{(index + 1) / 10:.1f}": 0 for index in range(10)}
    outside = 0
    for value in valid:
        if 0.0 <= value <= 1.0:
            index = min(9, int(value * 10))
            bins[f"{index / 10:.1f}-{(index + 1) / 10:.1f}"] += 1
        else:
            outside += 1
    return {
        "count": len(valid),
        "invalid": invalid,
        "min": _round_number(min(valid) if valid else None),
        "p25": _round_number(quartiles[0] if quartiles else (valid[0] if valid else None)),
        "median": _round_number(statistics.median(valid) if valid else None),
        "p75": _round_number(quartiles[2] if quartiles else (valid[-1] if valid else None)),
        "max": _round_number(max(valid) if valid else None),
        "bins_0_1": bins,
        "outside_0_1": outside,
    }


def _tier_for_distance(distance: object) -> str:
    if distance is None:
        return "ADM1"
    value = float(distance)
    if value < 5:
        return "under_5"
    if value < 25:
        return "5_to_25"
    return "25_to_40"


def _distance_summary(rows: Sequence[dict]) -> dict[str, object]:
    counts = Counter(str(row["tier"]) for row in rows)
    return {
        "under_5": counts.get("under_5", 0),
        "5_to_25": counts.get("5_to_25", 0),
        "25_to_40": counts.get("25_to_40", 0),
        "ADM2": counts.get("ADM2", 0),
        "ADM1": counts.get("ADM1", 0),
    }


def _country_rows_report(rows: Sequence[dict]) -> dict[str, object]:
    winners = Counter(str(row["audit_code"]) for row in rows)
    return {
        "spot_count": len(rows),
        "darkness": darkness_statistics(row.get("darkness") for row in rows),
        "distance_tiers": _distance_summary(rows),
        "winner_by_code": dict(sorted(winners.items())),
        "samples": _sample_matches(list(rows)),
    }


def _runtime_fields(spot: dict) -> dict[str, object]:
    distance = spot.get("nameDistanceKm")
    try:
        distance = None if distance is None else round(float(distance), 3)
    except (TypeError, ValueError):
        distance = distance
    return {
        "name": spot.get("name"),
        "code": spot.get("nameFeatureCode"),
        "distance_km": distance,
    }


def audit_named_islands(
    spots: Sequence[dict],
    country_codes: Iterable[str],
    *,
    geography=None,
    spot_radius_km: float = 15.0,
) -> dict[str, object]:
    """Audit named islands against Natural Earth and the generated spots.

    The anchor points are intentionally versioned in this source file.  A
    Natural Earth miss is reported as a resolution loss, while a configured
    country with no nearby spot is reported separately as a generation gap.
    Crown dependencies are always reported as out of scope, even when their
    Natural Earth geometry exists.  ``forbidden_spatial_spots`` contains spot
    identifiers only; the corresponding ``*_count`` fields are the counts.
    """
    from shapely.geometry import Point
    from src.geography import load_geography

    configured = {str(code).strip().upper() for code in country_codes}
    geo = geography or load_geography()
    all_spots = []
    for spot in spots:
        try:
            spot_id = str(spot.get("id", ""))
            all_spots.append((float(spot["lat"]), float(spot["lon"]),
                              str(spot.get("country", "")).upper(), spot_id))
        except (KeyError, TypeError, ValueError):
            continue

    def one_island(item: dict[str, object], *, crown: bool = False) -> dict[str, object]:
        lat, lon = float(item["lat"]), float(item["lon"])
        point = Point(lon, lat)
        natural_earth_covered = bool(geo.land.covers(point))
        country = str(item["country"])
        attributed = country in geo.country_candidates(point)
        nearby_spot_ids = [
            spot_id for spot_lat, spot_lon, spot_country, spot_id in all_spots
            if (crown or spot_country == country)
            and haversine_km(lat, lon, spot_lat, spot_lon) <= spot_radius_km
        ]
        spot_count = len(nearby_spot_ids)
        forbidden_spatial_spots = nearby_spot_ids if crown else []
        if crown:
            status = "out_of_scope_crown_dependency"
        elif not natural_earth_covered or not attributed:
            status = "resolution_loss"
        elif spot_count:
            status = "covered_with_spots"
        else:
            status = "covered_by_natural_earth_no_spot"
        return {
            "name": item["name"], "country": country,
            "anchor": [lat, lon], "natural_earth_covered": natural_earth_covered,
            "natural_earth_country_match": attributed,
            "spot_count_within_km": spot_count, "spot_radius_km": spot_radius_km,
            "configured": country in configured, "status": status,
            "forbidden_spatial_spots": forbidden_spatial_spots,
            "forbidden_spatial_spot_count": len(forbidden_spatial_spots),
        }

    crown_dependencies = [one_island(item, crown=True) for item in CROWN_DEPENDENCIES]
    return {
        "named_islands": [one_island(item) for item in NAMED_ISLANDS],
        "crown_dependencies": crown_dependencies,
        "forbidden_spatial_spots": {
            item["country"]: item["forbidden_spatial_spots"] for item in crown_dependencies
        },
        "forbidden_spatial_spot_count": {
            item["country"]: item["forbidden_spatial_spot_count"] for item in crown_dependencies
        },
        "resolution_loss_count": sum(
            1 for item in NAMED_ISLANDS
            if not geo.land.covers(Point(float(item["lon"]), float(item["lat"])))
        ),
        "crown_dependencies_out_of_scope": ["IM", "JE", "GG"],
    }


def _divergence(runtime: dict[str, object], audit: dict[str, object]) -> dict[str, object] | None:
    fields = {
        "name": (runtime.get("name"), audit.get("name")),
        "code": (runtime.get("code"), audit.get("code")),
        "distance_km": (runtime.get("distance_km"), audit.get("distance_km")),
    }
    different = [key for key, (actual, expected) in fields.items()
                 if actual != expected]
    if not different:
        return None
    return {"fields": different, "runtime": runtime, "audit": audit}


def analyse_multi_country(
    spots: Sequence[dict],
    naming_index: GeoNamesIndex,
    country_codes: Iterable[str],
    *,
    manifest: dict | None = None,
    bbox: Sequence[float] | None = None,
    raster_path: str | Path | None = None,
    expected_spots: int | None = None,
    islands: dict[str, object] | None = None,
) -> dict[str, object]:
    """Audit runtime names and recomputed names for a multi-country corpus."""
    configured = tuple(dict.fromkeys(str(code).strip().upper() for code in country_codes))
    configured_set = set(configured)
    rows_by_country: dict[str, list[dict]] = {code: [] for code in configured}
    unexpected: Counter[str] = Counter()
    divergences: list[dict[str, object]] = []
    all_rows: list[dict] = []
    for spot in spots:
        country = str(spot.get("country", "")).strip().upper()
        if country not in configured_set:
            unexpected[country or "<missing>"] += 1
            continue
        result = naming_index.resolve(spot, country=country)
        audit = {
            "name": result.name,
            "code": result.feature_code,
            "distance_km": result.name_distance_km,
            "tier": result.feature_code if result.administrative_fallback else _tier_for_distance(result.name_distance_km),
        }
        runtime = _runtime_fields(spot)
        row = {
            "id": str(spot.get("id", "")),
            "country": country,
            "lat": float(spot["lat"]),
            "lon": float(spot["lon"]),
            "near": spot.get("near", ""),
            "darkness": spot.get("darkness"),
            "name": result.name,
            "code": result.feature_code,
            "tier": audit["tier"],
            "distance_km": result.name_distance_km,
            "displayed_name": runtime["name"],
            "audit_name": result.name,
            "audit_code": result.feature_code,
            "audit_distance_km": result.name_distance_km,
            "runtime_name": runtime["name"],
            "runtime_code": runtime["code"],
            "runtime_distance_km": runtime["distance_km"],
        }
        mismatch = _divergence(runtime, {
            "name": result.name, "code": result.feature_code,
            "distance_km": result.name_distance_km,
        })
        if mismatch is not None:
            divergences.append({"id": row["id"], "country": country, **mismatch})
        rows_by_country[country].append(row)
        all_rows.append(row)

    countries_report = {
        country: _country_rows_report(rows_by_country[country])
        for country in configured
    }
    report: dict[str, object] = {
        "schema_version": 2,
        "country_codes": list(configured),
        "spot_count": len(all_rows),
        "spot_count_input": len(spots),
        "spot_bbox": spot_extent(spots),
        "spot_count_warning": (
            None if expected_spots is None or len(all_rows) == expected_spots else
            f"Corpus contient {len(all_rows)} spots, référence indicative {expected_spots} "
            f"(écart {len(all_rows) - expected_spots:+d})."
        ),
        "countries": countries_report,
        "darkness": darkness_statistics(row.get("darkness") for row in all_rows),
        "distance_tiers": _distance_summary(all_rows),
        "winner_by_code": dict(sorted(Counter(row["audit_code"] for row in all_rows).items())),
        "winner_by_country": {
            country: countries_report[country]["winner_by_code"] for country in configured
        },
        "candidate_codes": list(NAMING_FEATURE_CODES),
        "unexpected_country_codes": dict(sorted(unexpected.items())),
        "unexpected_spot_count": sum(unexpected.values()),
        "forbidden_crown_codes": {code: unexpected.get(code, 0) for code in ("IM", "JE", "GG")},
        "naming_divergences": {"count": len(divergences), "details": divergences},
        "samples": _sample_matches(all_rows, 100),
        "manifest": (manifest or {}).get("countries", {}) if isinstance(manifest, dict) else {},
        "islands": islands or {},
    }
    if bbox is not None:
        region_bbox = list(map(float, bbox))
        report["region_bbox"] = region_bbox
        lon_min, lat_min, lon_max, lat_max = region_bbox
        valid_coordinates = []
        for spot in spots:
            try:
                lat, lon = float(spot["lat"]), float(spot["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon):
                valid_coordinates.append((lat, lon))
        report["spots_outside_bbox"] = sum(
            not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max)
            for lat, lon in valid_coordinates
        )
        report["spots_bbox_ok"] = all(
            lon_min <= lon <= lon_max and lat_min <= lat <= lat_max
            for lat, lon in valid_coordinates
        )
    if raster_path is not None:
        report["raster"] = raster_finite_extent(raster_path, bbox)
    return report


def raster_finite_extent(raster_path: str | Path, bbox: Sequence[float] | None = None) -> dict[str, object]:
    """Measure the WGS84 extent of finite pixels in a debug raster."""
    import numpy as np
    import rasterio
    from rasterio.warp import transform

    with rasterio.open(raster_path) as dataset:
        data = dataset.read(1, masked=False)
        finite = np.isfinite(data)
        if not finite.any():
            result: dict[str, object] = {
                "path": str(raster_path), "finite_pixels": 0, "total_pixels": int(data.size),
                "valid_bounds": None, "edge_coverage": None,
            }
            return result
        rows, cols = np.where(finite)
        row_min, row_max = int(rows.min()), int(rows.max())
        col_min, col_max = int(cols.min()), int(cols.max())
        corners = []
        for row in (row_min, row_max + 1):
            for col in (col_min, col_max + 1):
                x, y = dataset.transform * (col, row)
                corners.append((x, y))
        xs, ys = zip(*corners)
        if dataset.crs is not None and str(dataset.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
            lon, lat = transform(dataset.crs, "EPSG:4326", list(xs), list(ys))
        else:
            lon, lat = list(xs), list(ys)
        valid_bounds = [min(lon), min(lat), max(lon), max(lat)]
        edge_coverage = None
        if bbox is not None:
            requested = list(map(float, bbox))
            tolerance = 1e-8
            edge_coverage = {
                "west": valid_bounds[0] <= requested[0] + tolerance,
                "south": valid_bounds[1] <= requested[1] + tolerance,
                "east": valid_bounds[2] >= requested[2] - tolerance,
                "north": valid_bounds[3] >= requested[3] - tolerance,
                "all": (
                    valid_bounds[0] <= requested[0] + tolerance and
                    valid_bounds[1] <= requested[1] + tolerance and
                    valid_bounds[2] >= requested[2] - tolerance and
                    valid_bounds[3] >= requested[3] - tolerance
                ),
            }
        return {
            "path": str(raster_path), "crs": str(dataset.crs),
            "raster_bounds": [float(value) for value in dataset.bounds],
            "finite_pixels": int(finite.sum()), "total_pixels": int(data.size),
            "valid_bounds": [float(value) for value in valid_bounds],
            "edge_coverage": edge_coverage,
        }


def compare_context_rasters(
    raster_300: str | Path,
    raster_350: str | Path,
    *,
    bortle_300: str | Path | None = None,
    bortle_350: str | Path | None = None,
    threshold: float = 0.1,
) -> dict[str, object]:
    """Compare two supplied context runs over their common finite pixels."""
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.warp import reproject, transform_bounds
    from rasterio.enums import Resampling

    with rasterio.open(raster_300) as first, rasterio.open(raster_350) as second:
        if first.crs is None or second.crs is None:
            raise ValueError("Both context rasters must declare a CRS")
        second_bounds = transform_bounds(second.crs, first.crs, *second.bounds)
        overlap = (
            max(first.bounds.left, second_bounds[0]), max(first.bounds.bottom, second_bounds[1]),
            min(first.bounds.right, second_bounds[2]), min(first.bounds.top, second_bounds[3]),
        )
        if overlap[0] >= overlap[2] or overlap[1] >= overlap[3]:
            raise ValueError("Context rasters have no spatial overlap")
        window = from_bounds(*overlap, transform=first.transform).round_offsets().round_lengths()
        first_data = first.read(1, window=window, masked=False).astype(float)
        destination = np.full(first_data.shape, np.nan, dtype=float)
        reproject(
            source=rasterio.band(second, 1), destination=destination,
            src_transform=second.transform, src_crs=second.crs,
            dst_transform=first.window_transform(window), dst_crs=first.crs,
            src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest,
        )
        common = np.isfinite(first_data) & np.isfinite(destination)
        if not common.any():
            raise ValueError("Context rasters have no common finite pixels")
        deltas = np.abs(first_data[common] - destination[common])
        result: dict[str, object] = {
            "raster_300": str(raster_300), "raster_350": str(raster_350),
            "intersection_bounds_crs": [float(value) for value in overlap],
            "common_finite_pixels": int(common.sum()),
            "mean_absolute_delta_darkness": _round_number(float(np.mean(deltas))),
            "median_delta_darkness": _round_number(float(np.median(deltas))),
            "p95_delta_darkness": _round_number(float(np.percentile(deltas, 95))),
            "max_delta_darkness": _round_number(float(np.max(deltas))),
            "threshold": float(threshold),
            "rate_above_threshold": float(np.mean(deltas > threshold)),
            "bortle_changes": None,
        }
        if (bortle_300 is None) != (bortle_350 is None):
            raise ValueError("Provide both Bortle rasters or neither")
        if bortle_300 is not None and bortle_350 is not None:
            with rasterio.open(bortle_300) as b_first, rasterio.open(bortle_350) as b_second:
                b_second_bounds = transform_bounds(b_second.crs, b_first.crs, *b_second.bounds)
                b_overlap = (max(b_first.bounds.left, b_second_bounds[0]), max(b_first.bounds.bottom, b_second_bounds[1]),
                              min(b_first.bounds.right, b_second_bounds[2]), min(b_first.bounds.top, b_second_bounds[3]))
                b_window = from_bounds(*b_overlap, transform=b_first.transform).round_offsets().round_lengths()
                b_data = b_first.read(1, window=b_window, masked=False).astype(float)
                b_other = np.full(b_data.shape, np.nan, dtype=float)
                reproject(source=rasterio.band(b_second, 1), destination=b_other,
                           src_transform=b_second.transform, src_crs=b_second.crs,
                           dst_transform=b_first.window_transform(b_window), dst_crs=b_first.crs,
                           src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest)
                b_common = np.isfinite(b_data) & np.isfinite(b_other)
                result["bortle_changes"] = {
                    "common_finite_pixels": int(b_common.sum()),
                    "changed_pixels": int(np.count_nonzero(b_data[b_common] != b_other[b_common])),
                    "rate": float(np.mean(b_data[b_common] != b_other[b_common])) if b_common.any() else None,
                }
        return result


def _archive_metadata(geonames_dir: Path, country_codes: Sequence[str]) -> dict:
    archives = {}
    for code in country_codes:
        path = geonames_dir / f"{code.upper()}.zip"
        if not path.is_file():
            raise FileNotFoundError(f"GeoNames archive missing: {path}")
        archives[code.upper()] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "bytes": path.stat().st_size}
    return archives


def _legacy_markdown_report(report: dict, *, country_code: str, bbox: Sequence[float], archives: dict) -> str:
    lines = ["# Mesure de la cascade de nommage GeoNames", "",
             f"- Pays : `{country_code}`",
             f"- Bbox région : `{list(map(float, bbox))}` ; import élargi à 40 km",
             f"- Spots mesurés : **{report['spot_count']}** (référence indicative : {report['expected_spots']})",
             f"- Archives : {', '.join(f'`{c}` {v['sha256'][:12]}' for c, v in sorted(archives.items()))}", ""]
    if report.get("spot_count_warning"):
        lines += [f"> **Avertissement :** {report['spot_count_warning']}", ""]
    lines += ["## Distribution", "", "| Tier | Spots |", "|---|---:|"]
    for key, label in (("under_5_km", "< 5 km"), ("5_to_25_km", "5–25 km"),
                       ("25_to_40_km", "25–40 km"), ("fallback_adm2", "repli ADM2"),
                       ("fallback_adm1", "repli ADM1")):
        lines.append(f"| {label} | {report['distance_bins'][key]} |")
    lines += ["", "## Codes retenus comme candidats", "",
              "La liste est une hypothèse à valider à la lecture des 100 exemples. "
              "L'arbitrage est uniquement la distance au point GeoNames; les classes ne sont pas utilisées comme priorité.", "",
              "| Code | Décision | Spots nommés | Définition / raison |", "|---|---|---:|---|"]
    observed = report.get("observed_by_code", {})
    all_codes = sorted(set(observed) | set(CANDIDATE_CODES))
    for code in all_codes:
        decision = "retenu" if code in CANDIDATE_CODES else "écarté"
        reason = CODE_REASONS.get(code, {"H": "Hydrographie non ponctuelle; exclu pour éviter les noms linéaires anonymes.",
            "T": "Relief non retenu : micro-relief ou point trop hétérogène.",
            "V": "Végétation non retenue : zone trop hétérogène ou peu distinctive.",
            "L": "Zone générique/historique non retenue sans preuve de repère utile.",
            "P": "Type de localité non retenu dans cette hypothèse; near reste la commune cities500.",
            "A": "Administration réservée au repli ADM2 puis ADM1."}.get(code[:1],
            "Code observé hors hypothèse candidate; à examiner dans l'échantillon."))
        lines.append(f"| `{code}` | {decision} | {report['winner_by_code'].get(code, 0)} | {reason} |")
    lines += ["", "## Gagnants par code", "", "| Code | Spots |", "|---|---:|"]
    lines += [f"| `{code}` | {count} |" for code, count in sorted(report["winner_by_code"].items())]
    excluded = sorted(report.get("excluded_observed_by_code", {}).items(),
                      key=lambda item: (-item[1], item[0]))
    lines += ["", "## Codes observés mais écartés", "",
              "Les volumes ci-dessous sont ceux des entités importables dans la bbox élargie; "
              "ils ne signifient pas qu'elles auraient gagné un spot.", "",
              "| Code | Entités observées |", "|---|---:|"]
    lines += [f"| `{code}` | {count} |" for code, count in excluded]
    lines += ["", "## Échantillon déterministe de libellés", "",
              "20 exemples par tier sont pris dans l'ordre stable des identifiants, puis complétés si un tier est court.", "",
              "| Tier | ID | Libellé | Code | Distance km | near | darkness |", "|---|---|---|---|---:|---|---:|"]
    for row in report["samples"]:
        distance = "—" if row["distance_km"] is None else f"{row['distance_km']:.3f}"
        darkness = "—" if row["darkness"] is None else str(row["darkness"])
        near = str(row.get("near", "")).replace("|", "\\|")
        name = str(row["name"]).replace("|", "\\|")
        lines.append(f"| {row['tier']} | `{row['id']}` | {name} | `{row['code']}` | {distance} | {near} | {darkness} |")
    lines += ["", "## Provenance", "", "Données GeoNames sous CC BY 4.0; archives nationales téléchargées depuis "
              "`https://download.geonames.org/export/dump/`. Le readme de chaque archive décrit le format des 19 colonnes et les codes administratifs.", ""]
    return "\n".join(lines)


def markdown_report(
    report: dict,
    *,
    country_codes: Sequence[str] | None = None,
    bbox: Sequence[float] | None = None,
    archives: dict | None = None,
    country_code: str | None = None,
) -> str:
    """Render the combined report as UTF-8 Markdown."""
    codes = list(country_codes or report.get("country_codes", []))
    if not codes and country_code:
        codes = [country_code]
    lines = ["# Audit prépublication — cascade GeoNames", "",
             f"- Pays : {', '.join(f'`{code}`' for code in codes)}",
             f"- Bbox : `{list(map(float, bbox or report.get('region_bbox', [])))}`",
             f"- Spots analysés : **{report.get('spot_count', 0)}**",
             f"- Divergences runtime/audit : **{report.get('naming_divergences', {}).get('count', 0)}**",
             ""]
    if report.get("spot_count_warning"):
        lines += [f"> **Avertissement :** {report['spot_count_warning']}", ""]
    lines += ["## Darkness", "", "| Corpus | Valides | Invalides | Min | P25 | Médiane | P75 | Max |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    stats = [("Global", report.get("darkness", {}))]
    stats += [(country, details.get("darkness", {}))
              for country, details in sorted(report.get("countries", {}).items())]
    for label, values in stats:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            label, values.get("count", 0), values.get("invalid", 0),
            values.get("min", "—"), values.get("p25", "—"), values.get("median", "—"),
            values.get("p75", "—"), values.get("max", "—")))
    lines += ["", "### Bins fixes de darkness (0,1)", "", "| Corpus | " + " | ".join(report.get("darkness", {}).get("bins_0_1", {}).keys()) + " |", "|---|" + "---:|" * len(report.get("darkness", {}).get("bins_0_1", {}))]
    for label, values in stats:
        bins = values.get("bins_0_1", {})
        lines.append("| {} | {} |".format(label, " | ".join(str(bins.get(key, 0)) for key in bins)))
    raster = report.get("raster")
    if raster:
        lines += ["", "## Emprise ALR valide", "", f"- Raster : `{raster.get('path')}`",
                  f"- Pixels finis : **{raster.get('finite_pixels', 0)} / {raster.get('total_pixels', 0)}**",
                  f"- Emprise WGS84 : `{raster.get('valid_bounds')}`",
                  f"- Couverture des bords : `{raster.get('edge_coverage')}`", ""]
    lines += ["## Distances de la cascade", "", "| Pays | <5 km | 5–25 km | 25–40 km | ADM2 | ADM1 |", "|---|---:|---:|---:|---:|---:|"]
    for country, details in sorted(report.get("countries", {}).items()):
        tiers = details["distance_tiers"]
        lines.append(f"| `{country}` | {tiers['under_5']} | {tiers['5_to_25']} | {tiers['25_to_40']} | {tiers['ADM2']} | {tiers['ADM1']} |")
    tiers = report["distance_tiers"]
    lines.append(f"| **Global** | {tiers['under_5']} | {tiers['5_to_25']} | {tiers['25_to_40']} | {tiers['ADM2']} | {tiers['ADM1']} |")
    lines += ["", "## Gagnants par code", "", "| Pays | Code | Spots |", "|---|---|---:|"]
    for country, details in sorted(report.get("countries", {}).items()):
        for code, count in sorted(details["winner_by_code"].items()):
            lines.append(f"| `{country}` | `{code}` | {count} |")
    lines += ["", "## Contrôle des pays", "", f"- Codes inattendus : `{report.get('unexpected_country_codes', {})}`",
              f"- IM/JE/GG : `{report.get('forbidden_crown_codes', {})}`", ""]
    island_report = report.get("islands", {})
    if island_report:
        lines += ["## Îles", "", "| Île | Pays | Natural Earth 1:10m | Spots proches | Statut |", "|---|---|---|---:|---|"]
        for island in island_report.get("named_islands", []):
            lines.append(f"| {island['name']} | `{island['country']}` | {'oui' if island['natural_earth_covered'] else 'non'} | {island['spot_count_within_km']} | `{island['status']}` |")
        lines += ["", "Dépendances de la Couronne volontairement hors périmètre : `IM`, `JE`, `GG`.", ""]
        for island in island_report.get("crown_dependencies", []):
            lines.append(f"- `{island['country']}` {island['name']} : Natural Earth={'oui' if island['natural_earth_covered'] else 'non'}, statut=`{island['status']}`.")
        lines += ["", "Spots situés spatialement dans une dépendance interdite (identifiants uniquement) :", ""]
        for country, spot_ids in sorted(island_report.get("forbidden_spatial_spots", {}).items()):
            ids = ", ".join(f"`{spot_id}`" for spot_id in spot_ids) or "aucun"
            lines.append(f"- `{country}` : {ids}")
    lines += ["## Divergences runtime/audit", ""]
    if report.get("naming_divergences", {}).get("details"):
        lines += ["| Pays | ID | Champs | Runtime | Audit |", "|---|---|---|---|---|"]
        for item in report["naming_divergences"]["details"]:
            lines.append(f"| `{item['country']}` | `{item['id']}` | {', '.join(item['fields'])} | `{item['runtime']}` | `{item['audit']}` |")
    else:
        lines.append("Aucune divergence.")
    lines += ["", "## Échantillon déterministe", "", "| Pays | Tier | ID | Runtime name | Audit name | Runtime code | Runtime distance km | Audit code | Audit distance km | near | darkness |", "|---|---|---|---|---|---|---:|---|---:|---|---:|"]
    for row in report.get("samples", []):
        runtime_distance = ("—" if row.get("runtime_distance_km") is None else
                            f"{float(row['runtime_distance_km']):.3f}")
        audit_distance = ("—" if row.get("audit_distance_km") is None else
                          f"{float(row['audit_distance_km']):.3f}")
        darkness = "—" if row.get("darkness") is None else str(row["darkness"])
        values = [str(row.get(key, "")).replace("|", "\\|") for key in (
            "country", "tier", "id", "runtime_name", "audit_name", "runtime_code",
        )]
        values += [runtime_distance,
                   str(row.get("audit_code", "")).replace("|", "\\|"), audit_distance,
                   str(row.get("near", "")).replace("|", "\\|"),
                   darkness.replace("|", "\\|")]
        lines.append(f"| {' | '.join(values)} |")
    lines += ["", "## Provenance GeoNames", ""]
    for country, entry in sorted(report.get("manifest", {}).items()):
        lines.append(f"- `{country}` : source `{entry.get('source_url', '')}`, archive SHA-256 `{entry.get('source_sha256', '')}`, extract `{entry.get('extract_path', '')}`, codes `{entry.get('codes_applied', [])}`.")
    return "\n".join(lines) + "\n"


def run_measurement(args: argparse.Namespace) -> dict:
    bbox = tuple(args.bbox)
    raw_codes = getattr(args, "country_code", None) or ["FR"]
    country_codes = [raw_codes] if isinstance(raw_codes, str) else list(raw_codes)
    country_codes = list(dict.fromkeys(str(code).strip().upper() for code in country_codes if str(code).strip()))
    geonames_dir = Path(args.geonames_dir)
    # This is deliberately the same guard and loader used by run.py.  The
    # audit must never silently fall back to national ZIP archives.
    manifest = validate_geonames_manifest(
        data_dir=geonames_dir,
        countries=country_codes,
        feature_codes=FILTERED_FEATURE_CODES,
        manifest_path=getattr(args, "manifest_path", None),
    )
    naming_index = GeoNamesIndex.from_filtered_extracts(
        data_dir=geonames_dir,
        countries=country_codes,
        feature_codes=FILTERED_FEATURE_CODES,
        bbox=bbox,
        margin_km=ORDINARY_MAX_KM,
        manifest_path=getattr(args, "manifest_path", None),
    )
    spots = load_spots(args.spots_dir)
    raster_path = getattr(args, "raster_path", None)
    report = analyse_multi_country(
        spots, naming_index, country_codes, manifest=manifest, bbox=bbox,
        raster_path=raster_path,
        expected_spots=(EXPECTED_FR_SPOTS if country_codes == ["FR"] else None),
        islands=audit_named_islands(spots, country_codes),
    )
    report["expanded_bbox"] = list(expanded_bbox(bbox, ORDINARY_MAX_KM))
    report["geonames_dir"] = str(geonames_dir)
    report["geonames_feature_codes"] = list(FILTERED_FEATURE_CODES)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spots-dir", required=True, help="Dossier des tuiles JSON après clip")
    parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX,
                        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    parser.add_argument("--country-code", action="append", default=None,
                        help="Code ISO du corpus; répétable pour un audit multi-pays")
    parser.add_argument("--geonames-dir", default="data/geonames")
    parser.add_argument("--manifest-path", default=None,
                        help="Manifeste filtré explicite (défaut: <geonames-dir>/manifest.yaml)")
    parser.add_argument("--raster-path", default=None,
                        help="Raster darkness debug à mesurer (emprise des pixels finis)")
    parser.add_argument("--out-json", default="validation/naming_cascade_france_2025.json")
    parser.add_argument("--out-md", default="validation/naming_cascade_france_2025.md")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_measurement(args)
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    codes = report["country_codes"]
    Path(args.out_md).write_text(markdown_report(report, country_codes=codes, bbox=args.bbox), encoding="utf-8")
    print(f"Wrote {args.out_json} and {args.out_md}: {report['spot_count']} spots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
