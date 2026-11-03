#!/usr/bin/env python3
"""Prepare an immutable offline E3 package; does not approve reviews."""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geographic_e3_prepare import prepare_e3


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-manifest", required=True, type=Path)
    p.add_argument("--worldcover-manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--reviews", type=Path)
    p.add_argument("--degraded-policies", type=Path)
    p.add_argument("--availability", type=Path)
    p.add_argument("--licensing-assessment", type=Path)
    args = p.parse_args()
    read = lambda x: json.loads(x.read_text()) if x else None
    manifest, contexts = prepare_e3(
        args.base_manifest,
        args.worldcover_manifest,
        args.output,
        reviews=read(args.reviews),
        degraded=read(args.degraded_policies),
        availability=read(args.availability),
        licensing=read(args.licensing_assessment),
    )
    print(json.dumps(dict(policy=manifest["policy"], contexts=contexts), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise
