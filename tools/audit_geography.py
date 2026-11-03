#!/usr/bin/env python3
"""Audit an immutable local Git publication snapshot without commit or push."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.geographic_data import GeographicDataset
from src.geographic_publication import approve_tree, GeographicApprovalError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path, help='Temporary/staged publication repository')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--report-dir', required=True, type=Path)
    args = parser.parse_args()
    # Does not stage files: callers deliberately choose the index to audit.
    report = approve_tree(args.repo, dataset=GeographicDataset(args.manifest), report_dir=args.report_dir)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except GeographicApprovalError as exc:
        print(json.dumps(exc.report, indent=2))
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)
