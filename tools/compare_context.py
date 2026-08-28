#!/usr/bin/env python3
"""Compare two already-generated darkness rasters; never launches a run."""

from __future__ import annotations

import argparse

from measure_landmarks import compare_context_rasters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--darkness-300", required=True, dest="raster_300")
    parser.add_argument("--darkness-350", required=True, dest="raster_350")
    parser.add_argument("--bortle-300", default=None)
    parser.add_argument("--bortle-350", default=None)
    parser.add_argument("--threshold", type=float, default=0.1)
    args = parser.parse_args(argv)
    import json
    print(json.dumps(compare_context_rasters(
        args.raster_300, args.raster_350,
        bortle_300=args.bortle_300, bortle_350=args.bortle_350,
        threshold=args.threshold,
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
