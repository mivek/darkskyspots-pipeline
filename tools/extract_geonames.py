#!/usr/bin/env python3
"""Create the small, auditable GeoNames extracts used by the pipeline."""

from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import date
from pathlib import Path
import sys
import zipfile
from collections.abc import Iterable

import yaml

# Allow ``python tools/extract_geonames.py`` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geonames import FILTERED_EXTRACT_COLUMNS, FILTERED_FEATURE_CODES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_countries(countries: Iterable[str]) -> tuple[str, ...]:
    """Return unique, upper-case country codes in their requested order."""
    if isinstance(countries, str):
        countries = (countries,)
    values = tuple(
        dict.fromkeys(
            str(country).strip().upper()
            for country in countries
            if str(country).strip()
        )
    )
    if not values:
        raise ValueError("at least one country is required")
    return values


def _normalise_codes(codes: Iterable[str] | None) -> tuple[str, ...]:
    """Return the exact feature-code set applied to every extracted row.

    ADM1 and ADM2 are part of the effective set because the runtime always
    loads them as its administrative fallback, including when a caller asks
    for a custom ordinary-feature list.  The same normalized tuple is passed
    to extraction and recorded in ``codes_applied``.
    """
    if codes is None:
        codes = FILTERED_FEATURE_CODES
    if isinstance(codes, str):
        codes = (codes,)
    values: set[str] = set()
    for value in codes:
        if value is None:
            continue
        for code in str(value).split(","):
            code = code.strip().upper()
            if code:
                values.add(code)
    if not values:
        raise ValueError("at least one feature code is required")
    values.update({"ADM1", "ADM2"})
    return tuple(sorted(values))


def _read_existing_manifest(path: Path) -> dict[str, object]:
    """Load an existing manifest for a merge, or return a fresh skeleton."""
    if not path.is_file():
        return {
            "schema_version": 1,
            "feature_codes_source": "src.geonames.FILTERED_FEATURE_CODES",
            "countries": {},
        }
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read GeoNames manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"GeoNames manifest {path} must contain a mapping")
    countries = payload.get("countries")
    if countries is None:
        payload["countries"] = {}
    elif not isinstance(countries, dict):
        raise ValueError(f"GeoNames manifest {path} must contain a countries mapping")
    payload.setdefault("schema_version", 1)
    payload.setdefault("feature_codes_source", "src.geonames.FILTERED_FEATURE_CODES")
    return payload


def extract_country(
    source: str | Path,
    destination: str | Path,
    country: str,
    codes: tuple[str, ...] = FILTERED_FEATURE_CODES,
) -> int:
    """Filter one national ZIP by feature code, never by geographic bounds."""
    source_path, destination_path = Path(source), Path(destination)
    country = country.upper()
    effective_codes = _normalise_codes(codes)
    code_set = set(effective_codes)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[tuple[int, list[str]]] = []
    with zipfile.ZipFile(source_path) as archive:
        members = [name for name in archive.namelist() if Path(name).name.upper() == f"{country}.TXT"]
        if not members:
            raise ValueError(f"GeoNames archive {source_path} contains no {country}.txt member")
        with archive.open(members[0], "r") as stream:
            for raw in stream:
                parts = raw.decode("utf-8").rstrip("\r\n").split("\t")
                if len(parts) < 19 or parts[8].strip().upper() != country:
                    continue
                if parts[7].strip().upper() not in code_set:
                    continue
                try:
                    geonameid = int(parts[0])
                    float(parts[4])
                    float(parts[5])
                except (TypeError, ValueError):
                    continue
                if not parts[1].strip():
                    continue
                records.append((geonameid, [
                    str(geonameid), parts[1], parts[4], parts[5],
                    parts[6].strip().upper(), parts[7].strip().upper(), country,
                ]))
    records.sort(key=lambda item: item[0])
    with destination_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(FILTERED_EXTRACT_COLUMNS)
        writer.writerows(row for _, row in records)
    return len(records)


def build_manifest(
    geonames_dir: str | Path,
    countries: tuple[str, ...],
    downloaded_at: dict[str, str],
    source_urls: dict[str, str] | None = None,
    codes: Iterable[str] | None = None,
    manifest_path: str | Path | None = None,
) -> dict:
    root = Path(geonames_dir)
    requested_countries = _normalise_countries(countries)
    effective_codes = _normalise_codes(codes)
    manifest_file = Path(manifest_path) if manifest_path is not None else root / "manifest.yaml"
    manifest = _read_existing_manifest(manifest_file)
    existing_countries = manifest.get("countries")
    country_entries = dict(existing_countries) if isinstance(existing_countries, dict) else {}
    provided_urls = {
        str(country).strip().upper(): str(url)
        for country, url in (source_urls or {}).items()
    }
    provided_dates = {
        str(country).strip().upper(): downloaded
        for country, downloaded in downloaded_at.items()
    }
    for country in requested_countries:
        source = root / "sources" / f"{country}.zip"
        extract = root / "extracts" / f"{country}.tsv"
        if not source.is_file():
            raise FileNotFoundError(f"GeoNames source archive missing: {source}")
        if not extract.is_file():
            raise FileNotFoundError(f"GeoNames filtered extract missing: {extract}")
        if country not in provided_dates:
            raise ValueError(f"missing downloaded date for country {country}")
        country_entries[country] = {
            "source_url": provided_urls.get(
                country,
                f"https://download.geonames.org/export/dump/{country}.zip",
            ),
            "source_path": f"sources/{country}.zip",
            "downloaded_at": provided_dates[country],
            "source_sha256": _sha256(source),
            "extract_path": f"extracts/{country}.tsv",
            "extract_sha256": _sha256(extract),
            "codes_applied": list(effective_codes),
        }
    manifest["countries"] = country_entries
    return manifest


def _parse_download_dates(values: list[str], countries: tuple[str, ...]) -> dict[str, str]:
    dates = {country: date.today().isoformat() for country in countries}
    for value in values:
        try:
            country, downloaded_at = value.split("=", 1)
            country = country.strip().upper()
            date.fromisoformat(downloaded_at.strip())
        except ValueError as exc:
            raise ValueError("--downloaded-at expects COUNTRY=YYYY-MM-DD") from exc
        if country not in dates:
            raise ValueError(f"unknown country in --downloaded-at: {country}")
        dates[country] = downloaded_at.strip()
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geonames-dir", default="data/geonames")
    parser.add_argument("--country", action="append", dest="countries", required=True)
    parser.add_argument(
        "--code", "--codes", action="extend", nargs="+", dest="codes", default=None,
        metavar="FEATURE_CODE",
        help=(
            "feature code(s) to extract; repeat --code or pass several values; "
            "ADM1 and ADM2 are included for administrative fallback"
        ),
    )
    parser.add_argument("--downloaded-at", action="append", default=[], metavar="COUNTRY=YYYY-MM-DD")
    args = parser.parse_args()
    try:
        countries = _normalise_countries(args.countries)
        codes = _normalise_codes(args.codes)
        dates = _parse_download_dates(args.downloaded_at, countries)
    except ValueError as exc:
        parser.error(str(exc))
    root = Path(args.geonames_dir)
    for country in countries:
        extract_country(
            root / "sources" / f"{country}.zip",
            root / "extracts" / f"{country}.tsv",
            country,
            codes,
        )
    manifest = build_manifest(root, countries, dates, codes=codes)
    manifest_path = root / "manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
