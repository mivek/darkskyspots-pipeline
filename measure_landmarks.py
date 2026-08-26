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

try:
    from scipy.spatial import cKDTree
except ImportError:  # pragma: no cover - project environment includes SciPy
    cKDTree = None

EARTH_RADIUS_KM = 6371.0088
ORDINARY_MAX_KM = 40.0
EXPECTED_FR_SPOTS = 2139
DEFAULT_BBOX = (-6.0, 41.0, 8.0, 51.0)

# A code list, deliberately not ``feature_class in {T,V,L}``.
CANDIDATE_CODES: tuple[str, ...] = tuple(sorted({
    # Populated places, including administrative seats.
    "PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC",
    "PPLF", "PPLG", "PPLL", "PPLR", "PPLS",
    # Named, point-like natural features.
    "CAPE", "CLDA", "CNYN", "GRGE", "HDLD", "ISL", "ISLS", "MT",
    "MTS", "PASS", "PK", "PKS", "PLAT", "PROM", "SDL", "UPLD", "VLC",
    # Broad vegetation and named areas/protected areas.
    "FRST", "HTH", "TUND", "LCTY", "PRK", "RESF", "RESN", "RESW",
    "RGN", "RGNL",
    # Standing water and reservoirs; streams remain excluded.
    "LK", "LKC", "LKN", "LKS", "RSV",
}))

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
    used: set[tuple[str, str]] = set()
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        by_code.setdefault(row["code"], []).append(row)
    for code in sorted(by_code):
        row = by_code[code][0]
        key = (row["tier"], str(row.get("id", "")))
        used.add(key)
        sample.append(row)
    for tier in tiers:
        for row in by_tier.get(tier, [])[:20]:
            key = (tier, str(row.get("id", "")))
            if key not in used:
                used.add(key)
                sample.append(row)
    if len(sample) < count:
        for row in rows:
            key = (row["tier"], str(row.get("id", "")))
            if key not in used:
                sample.append(row)
                used.add(key)
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
        rows.append({
            "id": str(spot.get("id", "")), "lat": float(spot["lat"]), "lon": float(spot["lon"]),
            "near": spot.get("near", ""), "darkness": spot.get("darkness"),
            "name": match.name, "code": match.code, "tier": match.tier,
            "distance_km": None if match.distance_km is None else round(match.distance_km, 3),
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


def _archive_metadata(geonames_dir: Path, country_codes: Sequence[str]) -> dict:
    archives = {}
    for code in country_codes:
        path = geonames_dir / f"{code.upper()}.zip"
        if not path.is_file():
            raise FileNotFoundError(f"GeoNames archive missing: {path}")
        archives[code.upper()] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "bytes": path.stat().st_size}
    return archives


def markdown_report(report: dict, *, country_code: str, bbox: Sequence[float], archives: dict) -> str:
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


def run_measurement(args: argparse.Namespace) -> dict:
    bbox = tuple(args.bbox)
    expanded = expanded_bbox(bbox, ORDINARY_MAX_KM)
    geonames_dir = Path(args.geonames_dir)
    archives = _archive_metadata(geonames_dir, [args.country_code])
    archive = geonames_dir / f"{args.country_code.upper()}.zip"
    ordinary, admins, observed, total = load_country_records(archive, args.country_code, expanded, CANDIDATE_CODES)
    spots = load_spots(args.spots_dir)
    report = analyse(spots, ordinary, admins, observed)
    report.update({"region_bbox": list(bbox), "expanded_bbox": list(expanded), "country_code": args.country_code.upper(),
                   "geonames_records_in_expanded_bbox": total, "ordinary_records_indexed": len(ordinary),
                   "admin_records_indexed": {"ADM2": sum(r.feature_code == "ADM2" for r in admins),
                                              "ADM1": sum(r.feature_code == "ADM1" for r in admins)},
                   "archives": archives})
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spots-dir", required=True, help="Dossier des tuiles JSON après clip")
    parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX,
                        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    parser.add_argument("--country-code", default="FR", help="Code ISO du corpus (FR, ES ou GB)")
    parser.add_argument("--geonames-dir", default="data/geonames")
    parser.add_argument("--out-json", default="validation/naming_cascade_france_2025.json")
    parser.add_argument("--out-md", default="validation/naming_cascade_france_2025.md")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_measurement(args)
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(markdown_report(report, country_code=args.country_code.upper(), bbox=args.bbox,
                                                 archives=report["archives"]), encoding="utf-8")
    print(f"Wrote {args.out_json} and {args.out_md}: {report['spot_count']} spots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
