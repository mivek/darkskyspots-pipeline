#!/usr/bin/env python3
"""Fetch the immutable Tenerife tile used by the accessibility pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


REPOSITORY = "mivek/darkskyspots-data"
COMMIT = "312bc366ccef674a232bde2f0a3a42067d083d19"
SOURCE_PATH = "spots/N028W017.json"
EXPECTED_SHA256 = "01e1b4d340a6ac1d4aadb6149aa2621fbfae67bb74f67b2fc6b22a835e1099fe"
SOURCE_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{SOURCE_PATH}"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "input/pilot/f02-1a/N028W017.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_baseline(output: Path) -> dict:
    output = output.resolve()
    ignored_input_root = (REPOSITORY_ROOT / "input").resolve()
    if not output.is_relative_to(ignored_input_root):
        raise ValueError(f"Baseline must be stored under ignored input/: {output}")
    if output.is_file():
        actual = sha256_file(output)
        if actual != EXPECTED_SHA256:
            raise ValueError(f"Existing baseline checksum mismatch: {actual}")
        return {"path": str(output), "sha256": actual, "source_url": SOURCE_URL,
                "repository": REPOSITORY, "commit": COMMIT, "source_path": SOURCE_PATH,
                "already_present": True}
    if output.exists():
        raise ValueError(f"Baseline output is not a regular file: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}-", dir=output.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    try:
        request = Request(SOURCE_URL, headers={"User-Agent": "darkskyspots-accessibility-pilot/1"})
        with os.fdopen(fd, "wb") as destination, urlopen(request, timeout=60) as response:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                destination.write(block)
                digest.update(block)
        actual = digest.hexdigest()
        if actual != EXPECTED_SHA256:
            raise ValueError(f"Frozen baseline checksum mismatch: expected {EXPECTED_SHA256}, got {actual}")
        os.replace(temporary, output)
        return {"path": str(output), "sha256": actual, "source_url": SOURCE_URL,
                "repository": REPOSITORY, "commit": COMMIT, "source_path": SOURCE_PATH,
                "already_present": False}
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(fetch_baseline(args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
