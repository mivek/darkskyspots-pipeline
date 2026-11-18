#!/usr/bin/env python3
"""Run the complete pytest suite and save a verifiable machine-readable result."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    command = [sys.executable, "-m", "pytest", "-q"]
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    duration = time.perf_counter() - started
    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    output_match = re.search(
        r"(?P<summary>\d+ passed(?:, \d+ (?:failed|errors?|skipped|xfailed|xpassed|warnings))*(?: in [\d.]+s)?)",
        combined,
    )
    if output_match is None:
        summary = None
        counts = {}
    else:
        summary = output_match.group("summary")
        counts = {key: int(value) for value, key in re.findall(
            r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|warnings)", summary
        )}
    output_path = args.output.resolve()
    log_path = (args.log or output_path.with_suffix(".log")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(combined, encoding="utf-8")
    record = {
        "schema": 1,
        "command": " ".join(command),
        "exit_code": result.returncode,
        "summary": summary,
        **counts,
        "duration_seconds": round(duration, 2),
        "log_path": str(log_path),
        "log_sha256": hashlib.sha256(combined.encode("utf-8")).hexdigest(),
        "failed_tests": re.findall(r"FAILED ([^\s]+)", combined),
        "stderr": result.stderr,
    }
    output_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: record[key] for key in (
        "exit_code", "summary", "duration_seconds", "log_path", "log_sha256"
    )}, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
