#!/usr/bin/env python3
"""Fetch immutable accessibility source inputs without replacing retained files."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.accessibility_prepare import COVERAGE_URL, SOURCE_URL
from src.accessibility_prepare import _read_pbf_header

APPROVED_G2_SOURCE_PINS = {
    "great_britain": {
        "pbf_size": 2_198_249_304,
        "pbf_md5": "467783c028c3408888735e9a439c1742",
        "polygon_sha256": "7c07d7950683d58db41ee0d8eb472e33f4d314016e24ce1fc879e0fb41613fde",
    },
    "ireland_northern_ireland": {
        "pbf_size": 417_532_476,
        "pbf_md5": "4b52927f184b2c07e20988783f5b596f",
        "polygon_sha256": "fc56ea18191a8d272ae491fc14281ddc01ac9835d77e2cded56fb6d1829f0fe8",
    },
}


def download(url: str, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite retained source: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "darkskyspots-accessibility-pilot/1"})
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, urlopen(request, timeout=120) as response:
            headers = response.headers
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                size += len(block)
            metadata = {
                "url": url,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "http_status": getattr(response, "status", None),
                "last_modified": headers.get("Last-Modified"),
                "etag": headers.get("ETag"),
                "content_length": headers.get("Content-Length"),
                "content_type": headers.get("Content-Type"),
                "size": size,
                "sha256": digest.hexdigest(),
            }
        os.replace(temporary, destination)
        return metadata
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _http_metadata(response, url: str, size: int, digest: str) -> dict:
    headers = response.headers
    return {
        "url": url,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "http_status": getattr(response, "status", None),
        "last_modified": headers.get("Last-Modified"),
        "etag": headers.get("ETag"),
        "content_length": headers.get("Content-Length"),
        "content_type": headers.get("Content-Type"),
        "size": size,
        "sha256": digest,
        "headers": {key: value for key, value in headers.items()},
    }


def _fetch_g2_source(scope: str, output_dir: Path, specification: dict) -> dict:
    """Download, verify and atomically retain a dated PBF source lock."""
    source = specification["source"]
    urls = {
        "pbf": source["source_url"],
        "coverage_polygon": source["coverage_polygon_url"],
        "checksum_sidecar": source["source_url"] + ".md5",
    }
    if not urls["pbf"].endswith("-261107.osm.pbf"):
        raise ValueError("G2 source acquisition is pinned to the approved 261107 PBF pair")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to replace retained G2 source directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    request = Request(urls["pbf"], method="HEAD", headers={"User-Agent": "darkskyspots-accessibility-g2/1"})
    try:
        with urlopen(request, timeout=60) as response:
            expected_size = int(response.headers.get("Content-Length", "0"))
            head_status = getattr(response, "status", None)
            head_headers = {key: value for key, value in response.headers.items()}
    except (HTTPError, URLError, ValueError) as exc:
        raise RuntimeError(f"Unable to verify dated PBF availability and size: {urls['pbf']}: {exc}") from exc
    if not expected_size:
        raise ValueError("Dated PBF server did not provide a complete Content-Length")
    expected_pin = APPROVED_G2_SOURCE_PINS[scope]
    if expected_size != expected_pin["pbf_size"]:
        raise ValueError(f"Dated PBF size differs from the approved preflight pin: {expected_size}")
    free_bytes = shutil.disk_usage(output_dir.parent).free
    if free_bytes < expected_size + max(1024**3, expected_size // 10):
        raise OSError(
            f"Insufficient destination disk space: need {expected_size + max(1024**3, expected_size // 10)} bytes, "
            f"have {free_bytes}"
        )

    stage = Path(tempfile.mkdtemp(prefix=f".{scope}-source-lock-", dir=output_dir.parent))
    try:
        filenames = {
            "pbf": Path(source["pbf_path"]).name,
            "coverage_polygon": Path(source["coverage_polygon_path"]).name,
            "checksum_sidecar": Path(source["checksum_sidecar_path"]).name,
        }
        # All files are downloaded into a private sibling directory. No partial
        # source can appear under the authoritative source path.
        payloads: dict[str, bytes] = {}
        metadata: dict[str, dict] = {}
        for name, url in urls.items():
            request = Request(url, headers={"User-Agent": "darkskyspots-accessibility-g2/1"})
            digest = hashlib.sha256()
            size = 0
            path = stage / filenames[name]
            with urlopen(request, timeout=120) as response, path.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    size += len(block)
                output.flush()
                os.fsync(output.fileno())
                metadata[name] = _http_metadata(response, url, size, digest.hexdigest())
            advertised = metadata[name].get("content_length")
            if advertised is not None and int(advertised) != size:
                raise ValueError(f"HTTP byte count mismatch for {url}: advertised {advertised}, received {size}")
            if name == "pbf" and size != expected_size:
                raise ValueError(f"PBF byte count mismatch: expected {expected_size}, downloaded {size}")
        (stage / "download-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        sidecar_text = (stage / filenames["checksum_sidecar"]).read_text(encoding="ascii")
        match = re.match(r"\s*([0-9a-fA-F]{32})\s+\*?([^\s]+)\s*", sidecar_text)
        if not match or match.group(2) != filenames["pbf"]:
            raise ValueError("Geofabrik MD5 sidecar is malformed or names a different PBF")
        md5 = hashlib.md5()
        with (stage / filenames["pbf"]).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(block)
        if md5.hexdigest().lower() != match.group(1).lower():
            raise ValueError("Downloaded PBF does not match the Geofabrik provider checksum")
        if match.group(1).lower() != expected_pin["pbf_md5"]:
            raise ValueError("Geofabrik MD5 sidecar differs from the approved preflight pin")
        if metadata["coverage_polygon"]["sha256"] != expected_pin["polygon_sha256"]:
            raise ValueError("Retained provider polygon differs from its independent preflight pin")

        header = _read_pbf_header(stage / filenames["pbf"])
        replication = header.get("osmosis_replication_timestamp")
        sequence = header.get("osmosis_replication_sequence_number")
        if isinstance(replication, (tuple, list)) and len(replication) == 1:
            replication = replication[0]
        if isinstance(sequence, (tuple, list)) and len(sequence) == 1:
            sequence = sequence[0]
        if str(replication) not in {"1794086496", "2026-11-07T21:21:36Z"} or str(sequence) != "4957":
            raise ValueError(f"Dated PBF replication header mismatch: timestamp={replication!r}, sequence={sequence!r}")

        lock = {
            "schema": 1,
            "scope": scope,
            "provider": "Geofabrik",
            "pbf": {
                "url": urls["pbf"], "size": metadata["pbf"]["size"],
                "sha256": metadata["pbf"]["sha256"], "http_metadata": metadata["pbf"],
                "header": header,
                "provider_checksum": {
                    "algorithm": "md5", "value": match.group(1).lower(),
                    "sidecar_url": urls["checksum_sidecar"],
                    "sidecar_sha256": metadata["checksum_sidecar"]["sha256"],
                },
            },
            "coverage_polygon": {
                "url": urls["coverage_polygon"], "size": metadata["coverage_polygon"]["size"],
                "sha256": metadata["coverage_polygon"]["sha256"],
                "http_metadata": metadata["coverage_polygon"],
            },
            "checksum_sidecar": {
                "url": urls["checksum_sidecar"], "size": metadata["checksum_sidecar"]["size"],
                "sha256": metadata["checksum_sidecar"]["sha256"],
                "http_metadata": metadata["checksum_sidecar"], "text": sidecar_text,
            },
            "polygon_historical_authentication": False,
            "polygon_note": "Retained provider polygon bytes are current retrievals; no matching dated polygon URL was advertised.",
        }
        (stage / "source-lock.json").write_text(
            json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        # The lock is written last and the complete directory appears in one
        # rename, so consumers never mistake a partial acquisition for a lock.
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(output_dir)
        return {"path": str(output_dir), "files": filenames, "lock": lock}
    except BaseException as exc:
        # Retain interrupted bytes only with an explicit non-authoritative label.
        (stage / "NON_AUTHORITATIVE.md").write_text(
            f"Incomplete G2 source acquisition for {scope}. This directory has no valid source lock.\n"
            f"Failure: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"G2 source locking failed; retained non-authoritative artifacts at {stage}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("canaries", "great_britain", "ireland_northern_ireland"), default="canaries")
    parser.add_argument("--config", default="config/accessibility.yaml")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    if args.scope != "canaries":
        config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
        specification = config["scopes"][args.scope]
        output_dir = Path(args.output_dir or Path(specification["source"]["pbf_path"]).parent).resolve()
        result = _fetch_g2_source(args.scope, output_dir, specification)
        print(json.dumps({"scope": args.scope, "path": result["path"],
                          "source_lock": str(output_dir / "source-lock.json"),
                          "pbf": result["lock"]["pbf"],
                          "coverage_polygon": result["lock"]["coverage_polygon"]},
                         ensure_ascii=False, indent=2))
        return 0
    output_dir = Path(args.output_dir or "input/accessibility/sources")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Source directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    pbf = output_dir / "canary-islands-261107.osm.pbf"
    polygon = output_dir / "canary-islands.poly"
    pbf_metadata = download(SOURCE_URL, pbf)
    polygon_metadata = download(COVERAGE_URL, polygon)
    metadata_path = output_dir / (pbf.name + ".http.json")
    metadata_path.write_text(
        json.dumps({"pbf": pbf_metadata, "coverage_polygon": polygon_metadata},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"pbf": str(pbf), "coverage_polygon": str(polygon),
                      "metadata": str(metadata_path), "pbf_provenance": pbf_metadata,
                      "coverage_polygon_provenance": polygon_metadata},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
