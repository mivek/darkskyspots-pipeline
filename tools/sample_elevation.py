#!/usr/bin/env python3
"""Sample local elevation at a supplied WGS84 coordinate."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.elevation import ElevationSampler

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest', required=True)
parser.add_argument('--cache-dir', default='input/elevation')
parser.add_argument('--lat', type=float, required=True)
parser.add_argument('--lon', type=float, required=True)
args = parser.parse_args()
with ElevationSampler(args.manifest, args.cache_dir) as sampler:
    result = sampler.sample(args.lat, args.lon)
    print(json.dumps({'lat': args.lat, 'lon': args.lon, 'altitude_m': result, 'counts': dict(sampler.counts)}))
