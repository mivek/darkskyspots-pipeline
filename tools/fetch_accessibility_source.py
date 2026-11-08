#!/usr/bin/env python3
"""Fetch only the dated Tenerife pilot source and its provider coverage polygon."""

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.accessibility_prepare import COVERAGE_URL, SOURCE_URL


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="input/accessibility/sources")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
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
