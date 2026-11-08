#!/usr/bin/env python3
"""Build an immutable, local accessibility index from a pinned OSM PBF."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.accessibility_prepare import prepare_accessibility


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--scope", required=True, choices=("tenerife",))
    parser.add_argument("--dem-manifest", required=True)
    parser.add_argument("--dem-cache-dir", default="input/elevation")
    parser.add_argument("--coverage-polygon")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = prepare_accessibility(
        args.pbf, args.source_url, args.scope, args.dem_manifest, args.output_dir,
        coverage_polygon=args.coverage_polygon, dem_cache_dir=args.dem_cache_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
