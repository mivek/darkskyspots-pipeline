#!/usr/bin/env python3
"""Audit country tags in produced or merged spot tiles.

Exit status 1 means at least one configured-forbidden Crown dependency was
found.  The JSON report is printed even on failure so the result is useful in
CI and in a post-publication review.
"""

import argparse
import json
import sys
from pathlib import Path


FORBIDDEN_CROWN_CODES = ("IM", "JE", "GG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count country tags and reject IM, JE or GG in spot tiles"
    )
    parser.add_argument("spots_dir", nargs="+", type=Path)
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.publish import audit_publication_country_codes

    reports = []
    failed = False
    for spots_dir in args.spots_dir:
        try:
            report = audit_publication_country_codes(
                spots_dir, forbidden_codes=FORBIDDEN_CROWN_CODES
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"audit failed for {spots_dir}: {exc}", file=sys.stderr)
            return 1
        reports.append(report)
        failed = failed or bool(report["forbidden_counts"])

    print(json.dumps(reports, ensure_ascii=False, indent=2, sort_keys=True))
    if failed:
        print("audit failed: forbidden Crown dependency country code found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
