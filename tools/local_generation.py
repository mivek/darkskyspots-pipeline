#!/usr/bin/env python3
"""Inspect a validated active local generation."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.local_generation import resolve_complete


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    ref = resolve_complete(Path(args.output_dir))
    print(ref.path)


if __name__ == "__main__":
    main()
