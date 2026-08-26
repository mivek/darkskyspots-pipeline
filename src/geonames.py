"""GeoNames landmark loading and deterministic spot naming.

The pipeline deliberately keeps this module independent from the ``near``
(``cities500``) enrichment.  A :class:`GeoNamesIndex` contains the selected
ordinary feature codes and the administrative records used as the final
fallback.  Feature codes are supplied by the caller: the list is a product
parameter and must not be hidden in the loader.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import zipfile
from typing import Iterable, Mapping, Sequence

import numpy as np
from scipy.spatial import cKDTree

from .utils import haversine_km


_DEFAULT_MARGIN_KM = 40.0
_ADMIN_1 = "ADM1"
_ADMIN_2 = "ADM2"


@dataclass(frozen=True)
class GeoNameRecord:
    """The subset of a GeoNames row needed for nearest-name resolution."""

    geonameid: int
    name: str
    lat: float
    lon: float
    feature_class: str
    feature_code: str
    country_code: str
    admin1: str = ""
    admin2: str = ""


@dataclass(frozen=True)
class NamingResult:
    """Name selected for a spot and its presentation-independent metadata."""

    name: str
    name_distance_km: float | None
    feature_code: str
    feature_class: str
    geonameid: int
    administrative_fallback: bool = False

    def as_dict(self) -> dict[str, object]:
        """Return the wire-shaped fields consumed by pipeline enrichers."""
        return {
            "name": self.name,
            "nameDistanceKm": self.name_distance_km,
            "nameFeatureCode": self.feature_code,
            "nameFeatureClass": self.feature_class,
            "nameGeoNameId": self.geonameid,
        }


class _NearestIndex:
    """Spatial index whose final ordering is still exact haversine distance."""

    def __init__(self, records: Sequence[GeoNameRecord]) -> None:
        self.records = tuple(records)
        vectors = np.asarray([_unit_vector(r.lat, r.lon) for r in self.records], dtype=float)
        self.tree = cKDTree(vectors) if len(vectors) else None

    def nearest(self, lat: float, lon: float) -> tuple[float, GeoNameRecord] | None:
        if self.tree is None:
            return None
        vector = np.asarray(_unit_vector(lat, lon), dtype=float)
        # Chord distance and great-circle distance have the same ordering on
        # the unit sphere.  Querying a small radius around the nearest chord
        # point also captures coincident/tied points before exact comparison.
        chord, _ = self.tree.query(vector, k=1)
        indices = self.tree.query_ball_point(vector, r=float(chord) + 1e-12)
        best: tuple[float, GeoNameRecord] | None = None
        for index in indices:
            record = self.records[index]
            distance = haversine_km(lat, lon, record.lat, record.lon)
            if best is None or (distance, record.geonameid) < (best[0], best[1].geonameid):
                best = (distance, record)
        return best


class GeoNamesIndex:
    """Nearest-name index for one or more ISO country archives.

    ``ordinary`` records are selected by feature code and region bbox at load
    time.  Administrative records are retained separately so the ordinary
    list cannot accidentally make a department or region win by category.
    All nearest decisions are then made by exact great-circle distance; no
    feature-class priority is applied.
    """

    def __init__(
        self,
        *,
        ordinary_by_country: Mapping[str, Sequence[GeoNameRecord]],
        adm2_by_country: Mapping[str, Sequence[GeoNameRecord]],
        adm1_by_country: Mapping[str, Sequence[GeoNameRecord]],
    ) -> None:
        self.ordinary_by_country = {
            str(country).upper(): tuple(records)
            for country, records in ordinary_by_country.items()
        }
        self.adm2_by_country = {
            str(country).upper(): tuple(records)
            for country, records in adm2_by_country.items()
        }
        self.adm1_by_country = {
            str(country).upper(): tuple(records)
            for country, records in adm1_by_country.items()
        }
        self._ordinary_index = {
            country: _NearestIndex(records)
            for country, records in self.ordinary_by_country.items()
        }
        self._adm2_index = {
            country: _NearestIndex(records)
            for country, records in self.adm2_by_country.items()
        }
        self._adm1_index = {
            country: _NearestIndex(records)
            for country, records in self.adm1_by_country.items()
        }
        self.max_distance_km = _DEFAULT_MARGIN_KM
        countries = set(self.adm1_by_country)
        if not countries:
            raise ValueError("GeoNames index contains no ADM1 records")

    @classmethod
    def from_archives(
        cls,
        *,
        data_dir: str | Path = "data",
        countries: Iterable[str],
        feature_codes: Iterable[str],
        bbox: Sequence[float],
        margin_km: float = _DEFAULT_MARGIN_KM,
    ) -> "GeoNamesIndex":
        """Load country ZIPs from ``data_dir/geonames/{ISO}.zip``.

        The ordinary candidate list is filtered by ISO country, injected
        ``feature_codes`` and the region bbox expanded by ``margin_km``.  ADM1
        and ADM2 rows are loaded for the whole country so the final fallback
        remains available even when an administrative centroid is outside
        the region bbox; they are never mixed into the ordinary list.

        Raises:
            FileNotFoundError: an expected country archive is missing.
            ValueError: malformed bbox/archive, or no ADM1 exists for a
                loaded country.
        """
        bbox = _validate_bbox(bbox)
        if margin_km < 0 or not math.isfinite(float(margin_km)):
            raise ValueError("margin_km must be a finite non-negative number")
        if isinstance(feature_codes, str):
            feature_codes = [feature_codes]
        codes = {str(code).strip().upper() for code in feature_codes if str(code).strip()}
        if not codes:
            raise ValueError("feature_codes must contain at least one non-empty code")
        if isinstance(countries, str):
            countries = [countries]
        country_codes = tuple(dict.fromkeys(str(c).strip().upper() for c in countries if str(c).strip()))
        if not country_codes:
            raise ValueError("countries must contain at least one ISO code")

        ordinary: dict[str, list[GeoNameRecord]] = {}
        adm2: dict[str, list[GeoNameRecord]] = {}
        adm1: dict[str, list[GeoNameRecord]] = {}
        for country in country_codes:
            archive = _archive_path(Path(data_dir), country)
            if not archive.exists():
                raise FileNotFoundError(f"GeoNames archive not found: {archive}")
            rows = _load_archive(archive, country)
            ordinary[country] = []
            adm2[country] = []
            adm1[country] = []
            for row in rows:
                if row.feature_class == "A" and row.feature_code == _ADMIN_1:
                    adm1[country].append(row)
                elif row.feature_class == "A" and row.feature_code == _ADMIN_2:
                    adm2[country].append(row)
                elif row.feature_code in codes and _in_expanded_bbox(row, bbox, float(margin_km)):
                    ordinary[country].append(row)
            if not adm1[country]:
                raise ValueError(f"GeoNames archive {archive} contains no ADM1 records in bbox")

        index = cls(
            ordinary_by_country=ordinary,
            adm2_by_country=adm2,
            adm1_by_country=adm1,
        )
        index.max_distance_km = float(margin_km)
        return index

    def resolve(self, spot: Mapping[str, object], country: str | None = None) -> NamingResult:
        """Resolve one spot to a non-empty name.

        An ordinary feature within 40 km always wins over an administrative
        fallback, even if an administrative point happens to be closer.  The
        nearest ADM2 is used when no ordinary feature is within 40 km; if that
        country has no ADM2, the nearest ADM1 is used.  Equal distances are
        resolved by the lowest GeoNames ID for reproducibility.
        """
        try:
            lat = float(spot["lat"])
            lon = float(spot["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("spot must contain numeric lat and lon") from exc
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError("spot latitude and longitude must be finite")
        raw_country = country or spot.get("country") or spot.get("osm_country_code") or ""
        if isinstance(raw_country, (list, tuple, set)):
            raw_country = next(iter(raw_country), "")
        country_code = str(raw_country).strip().upper()
        if not country_code:
            raise ValueError("country is required to resolve a GeoNames name")
        if country_code not in self.adm1_by_country:
            raise ValueError(f"country {country_code!r} is not loaded in GeoNames index")

        best = self._ordinary_index.get(country_code, _NearestIndex(())).nearest(lat, lon)
        if best is not None and best[0] <= self.max_distance_km:
            distance, record = best
            return NamingResult(
                name=record.name,
                name_distance_km=round(distance, 3),
                feature_code=record.feature_code,
                feature_class=record.feature_class,
                geonameid=record.geonameid,
            )

        best_admin = self._adm2_index.get(country_code, _NearestIndex(())).nearest(lat, lon)
        if best_admin is None:
            best_admin = self._adm1_index[country_code].nearest(lat, lon)
        if best_admin is None:
            # Constructor validates ADM1, but retain the invariant at the API
            # boundary in case callers mutate the public mappings.
            raise ValueError(f"country {country_code!r} has no administrative fallback")
        _, record = best_admin
        return NamingResult(
            name=record.name,
            name_distance_km=None,
            feature_code=record.feature_code,
            feature_class=record.feature_class,
            geonameid=record.geonameid,
            administrative_fallback=True,
        )

    def enrich_spot(self, spot: Mapping[str, object], country: str | None = None) -> dict[str, object]:
        """Copy a spot and add the name contract fields."""
        result = dict(spot)
        result.update(self.resolve(result, country).as_dict())
        return result

    def enrich_spots(
        self, spots: Iterable[Mapping[str, object]], country: str | None = None
    ) -> list[dict[str, object]]:
        """Return copies of ``spots`` enriched with a guaranteed ``name``."""
        return [self.enrich_spot(spot, country) for spot in spots]


def _archive_path(data_dir: Path, country: str) -> Path:
    """Resolve both the documented ``data`` and direct archive-dir forms."""
    if data_dir.name.lower() == "geonames":
        return data_dir / f"{country}.zip"
    return data_dir / "geonames" / f"{country}.zip"


def _load_archive(path: Path, country: str) -> list[GeoNameRecord]:
    with zipfile.ZipFile(path) as archive:
        expected = f"{country}.txt".lower()
        names = [name for name in archive.namelist() if Path(name).name.lower() == expected]
        if not names:
            raise ValueError(f"GeoNames archive {path} contains no {country}.txt member")
        with archive.open(names[0], "r") as stream:
            rows: list[GeoNameRecord] = []
            for raw in stream:
                row = _parse_row(raw.decode("utf-8"), country)
                if row is not None:
                    rows.append(row)
            return rows


def _parse_row(line: str, expected_country: str) -> GeoNameRecord | None:
    columns = line.rstrip("\r\n").split("\t")
    if len(columns) < 15:
        return None
    try:
        geonameid = int(columns[0])
        lat = float(columns[4])
        lon = float(columns[5])
    except (ValueError, TypeError):
        return None
    name = columns[1].strip()
    feature_class = columns[6].strip().upper()
    feature_code = columns[7].strip().upper()
    country = columns[8].strip().upper()
    if not name or country != expected_country or not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return GeoNameRecord(
        geonameid=geonameid,
        name=name,
        lat=lat,
        lon=lon,
        feature_class=feature_class,
        feature_code=feature_code,
        country_code=country,
        admin1=columns[10].strip(),
        admin2=columns[11].strip(),
    )


def _validate_bbox(bbox: Sequence[float]) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError("bbox must be [lon_min, lat_min, lon_max, lat_max]")
    values = tuple(float(value) for value in bbox)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("bbox coordinates must be finite")
    lon_min, lat_min, lon_max, lat_max = values
    if not (-180 <= lon_min < lon_max <= 180 and -90 <= lat_min < lat_max <= 90):
        raise ValueError("bbox must be ordered and within geographic bounds")
    return values


def _in_expanded_bbox(row: GeoNameRecord, bbox: tuple[float, float, float, float], margin_km: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    margin_lat = margin_km / 111.0
    # The longitude span required for a fixed ground distance grows towards
    # the poles.  Use the poleward edge of the *expanded* latitude range,
    # rather than the bbox midpoint, so a feature near that edge cannot be
    # dropped from the ordinary index.  Clamp at 89.9° to keep the conversion
    # finite when a large margin reaches a pole.
    expanded_lat_min = max(-90.0, lat_min - margin_lat)
    expanded_lat_max = min(90.0, lat_max + margin_lat)
    poleward_lat = min(89.9, max(abs(expanded_lat_min), abs(expanded_lat_max)))
    cos_lat = max(math.cos(math.radians(poleward_lat)), 0.01)
    margin_lon = margin_km / (111.0 * cos_lat)
    return (
        lat_min - margin_lat <= row.lat <= lat_max + margin_lat
        and lon_min - margin_lon <= row.lon <= lon_max + margin_lon
    )


def _unit_vector(lat: float, lon: float) -> tuple[float, float, float]:
    lat_radians = math.radians(lat)
    lon_radians = math.radians(lon)
    cos_lat = math.cos(lat_radians)
    return (
        cos_lat * math.cos(lon_radians),
        cos_lat * math.sin(lon_radians),
        math.sin(lat_radians),
    )


__all__ = ["GeoNameRecord", "GeoNamesIndex", "NamingResult"]
