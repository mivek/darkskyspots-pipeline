#!/usr/bin/env python3
"""Compare Copernicus at the original ASTER witness coordinates."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.elevation import ElevationSampler


def compare(sampler, witness):
    differences, excluded, outliers = [], [], []
    for coordinate, reference in witness['elevations_m'].items():
        lat, lon = map(float, coordinate.split('_'))
        actual = sampler.sample(lat, lon)
        if actual is None or reference is None:
            excluded.append(coordinate)
            continue
        delta = actual - float(reference)
        differences.append(delta)
        if abs(delta) > 30:
            outliers.append({'coordinate': coordinate, 'aster': reference, 'copernicus': actual, 'delta': delta})
    result = {'total': len(witness['elevations_m']), 'paired': len(differences), 'excluded': excluded,
              'sample_counts': dict(sampler.counts), 'outliers_over_30m': outliers}
    if differences:
        result.update(bias_median=float(np.median(differences)), absolute_error_median=float(np.median(np.abs(differences))),
                      absolute_error_p95=float(np.percentile(np.abs(differences), 95)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='data/elevation/manifests/france.json')
    parser.add_argument('--cache-dir', default='input/elevation')
    parser.add_argument('--witness', default='validation/france_spot_elevations.json')
    args = parser.parse_args()
    with ElevationSampler(args.manifest, args.cache_dir) as sampler:
        result = compare(sampler, json.loads(Path(args.witness).read_text()))
    print(json.dumps(result, indent=2))
    return 0 if result['total'] and result['paired'] / result['total'] >= 0.9 else 1

if __name__ == '__main__':
    sys.exit(main())
