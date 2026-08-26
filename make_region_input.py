#!/usr/bin/env python3
"""
Découpe le GeoTIFF VIIRS mondial en une sous-zone (bbox + marge de contexte 300 km),
pour que le pipeline tourne vite sur un test régional au lieu de traiter la planète.

Usage :
    python make_region_input.py \\
        --src input/viirs_2025_raw.tif \\
        --bbox 2.4 48.1 3.6 49.1 \\
        --out input/seine_et_marne/2025.tif

La marge de 300 km (~2.7° de latitude, un peu plus en longitude) est ajoutée
automatiquement autour de la bbox pour que le calcul ALR ait tout son contexte.
Le pipeline ne produira que les tuiles de la bbox, mais l'ALR sera correct grâce
à cette marge.

Ne charge que la fenêtre découpée en mémoire, pas le fichier mondial entier.
"""
import argparse
import math
from pathlib import Path

import rasterio
from rasterio.windows import from_bounds

CONTEXT_KM = 300.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="GeoTIFF VIIRS mondial")
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
                    help="bbox de la zone de sortie (WGS84, degrés)")
    ap.add_argument("--out", required=True, help="GeoTIFF de sortie (entrée du pipeline)")
    ap.add_argument("--context-km", type=float, default=CONTEXT_KM,
                    help="marge de contexte autour de la bbox (défaut 300 km)")
    args = ap.parse_args()

    lon_min, lat_min, lon_max, lat_max = args.bbox

    # Marge : 1° de latitude ≈ 111 km. En longitude, on divise par cos(lat) pour
    # élargir davantage aux latitudes élevées (la marge doit rester 300 km au sol).
    margin_lat = args.context_km / 111.0
    expanded_lat_min = max(-90.0, lat_min - margin_lat)
    expanded_lat_max = min(90.0, lat_max + margin_lat)
    poleward_lat = min(89.9, max(abs(expanded_lat_min), abs(expanded_lat_max)))
    margin_lon = args.context_km / (111.0 * max(0.01, math.cos(math.radians(poleward_lat))))

    ext_lon_min = lon_min - margin_lon
    ext_lon_max = lon_max + margin_lon
    ext_lat_min = lat_min - margin_lat
    ext_lat_max = lat_max + margin_lat

    print(f"bbox sortie   : lon [{lon_min}, {lon_max}]  lat [{lat_min}, {lat_max}]")
    print(f"marge contexte: {args.context_km} km  (lon ±{margin_lon:.2f}°, lat ±{margin_lat:.2f}°)")
    print(f"fenêtre lue   : lon [{ext_lon_min:.2f}, {ext_lon_max:.2f}]  lat [{ext_lat_min:.2f}, {ext_lat_max:.2f}]")

    with rasterio.open(args.src) as src:
        # Clampe la fenêtre étendue à l'emprise du fichier source
        b = src.bounds
        ext_lon_min = max(ext_lon_min, b.left)
        ext_lon_max = min(ext_lon_max, b.right)
        ext_lat_min = max(ext_lat_min, b.bottom)
        ext_lat_max = min(ext_lat_max, b.top)

        window = from_bounds(ext_lon_min, ext_lat_min, ext_lon_max, ext_lat_max,
                             transform=src.transform)
        window = window.round_offsets().round_lengths()
        data = src.read(1, window=window)
        win_transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update({
            "height": int(window.height),
            "width": int(window.width),
            "transform": win_transform,
            "compress": "lzw",
        })

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(data, 1)

    print(f"\nÉcrit : {out_path}")
    print(f"Dimensions : {profile['width']} x {profile['height']} pixels")
    ram_mb = profile["width"] * profile["height"] * 8 / (1024 * 1024)
    print(f"RAM approx si chargé en float64 : {ram_mb:.0f} MB")


if __name__ == "__main__":
    main()
