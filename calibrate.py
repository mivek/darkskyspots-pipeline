#!/usr/bin/env python3
"""
Calibration Europe — compare le Bortle produit par le pipeline aux points de
contrôle lightpollutionmap (vérité-terrain), pour décider si ALR_CALIB_C doit
être ajusté.

Méthode : pour chaque point de contrôle, trouve le spot pipeline le plus proche
dans les tuiles JSON et compare son bortle au bortle de référence.

Usage :
    python calibrate.py --spots-dir /chemin/vers/tuiles_massif_central

Note : c'est la méthode APPROCHÉE (option B) — on compare via le spot le plus
proche, pas la valeur exacte au point. L'écart spatial (colonne "dist") indique
la fiabilité : sous ~3 km c'est fiable, au-delà l'écart de pollution peut fausser.
"""
import argparse
import json
import glob
import math
import os

# Points de contrôle lightpollutionmap (SQM 2025) fournis par l'utilisateur.
# SQM (mag/arcsec²) converti en Bortle via l'échelle standard.
CONTROL_POINTS = [
    {"label": "Sombre (Massif Central)", "lat": 45.53190, "lon": 2.63111, "sqm": 21.94, "bortle_ref": 2},
    {"label": "Rural",                    "lat": 45.77543, "lon": 3.29274, "sqm": 21.51, "bortle_ref": 4},
    {"label": "Périurbain",               "lat": 45.54908, "lon": 3.24947, "sqm": 20.89, "bortle_ref": 4},
    {"label": "Ville (près Clermont)",    "lat": 45.78652, "lon": 3.10737, "sqm": 19.69, "bortle_ref": 5},
]


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spots-dir", required=True, help="dossier des tuiles JSON du Massif Central")
    args = ap.parse_args()

    # Charger tous les spots
    spots = []
    for sf in glob.glob(os.path.join(args.spots_dir, "**", "*.json"), recursive=True):
        try:
            d = json.load(open(sf, encoding="utf-8"))
            spots.extend(d.get("spots", []))
        except Exception:
            continue
    if not spots:
        print(f"Aucun spot trouvé dans {args.spots_dir}")
        return
    print(f"Spots chargés : {len(spots)}\n")

    print("=== COMPARAISON pipeline vs lightpollutionmap ===\n")
    header = f"{'Point':26s} {'SQM':>5s} {'B.réf':>6s} {'B.pipe':>7s} {'Δ':>4s} {'dark':>6s} {'dist':>7s}"
    print(header)
    print("-" * len(header))

    deltas = []
    rows = []
    for cp in CONTROL_POINTS:
        best = None; best_km = float("inf")
        for sp in spots:
            dkm = haversine_km(cp["lat"], cp["lon"], sp["lat"], sp["lon"])
            if dkm < best_km:
                best_km = dkm; best = sp
        if best is None:
            continue
        b_pipe = best.get("bortle")
        dark = best.get("darkness")
        delta = b_pipe - cp["bortle_ref"] if b_pipe is not None else None
        deltas.append(delta)
        rows.append((cp, best, best_km, b_pipe, dark, delta))
        dstr = f"{delta:+d}" if delta is not None else "?"
        darkstr = f"{dark:.3f}" if dark is not None else "?"
        print(f"{cp['label']:26s} {cp['sqm']:5.2f} {cp['bortle_ref']:6d} "
              f"{b_pipe if b_pipe is not None else '?':>7} {dstr:>4s} {darkstr:>6s} {best_km:6.1f}km")

    print()
    # Analyse du décalage
    valid = [d for d in deltas if d is not None]
    if valid:
        mean_delta = sum(valid) / len(valid)
        print(f"Décalage moyen (Bortle pipeline − référence) : {mean_delta:+.2f}")
        all_same_sign = all(d > 0 for d in valid) or all(d < 0 for d in valid)
        print()
        if all(d == 0 for d in valid):
            print("→ CALIBRATION DÉJÀ BONNE : aucun écart. ALR_CALIB_C n'a pas besoin d'ajustement.")
        elif all_same_sign:
            direction = "SUR-estime" if mean_delta > 0 else "SOUS-estime"
            print(f"→ DÉCALAGE SYSTÉMATIQUE : le pipeline {direction} la pollution "
                  f"de ~{abs(mean_delta):.1f} classe(s) Bortle en moyenne.")
            print("   Un décalage systématique dans le même sens = ALR_CALIB_C à ajuster.")
            print("   (On calcule le facteur d'ajustement à partir de ces écarts.)")
        else:
            print("→ ÉCARTS DE SIGNES MIXTES : pas un simple décalage d'échelle.")
            print("   Peut venir de l'approximation 'spot le plus proche' (regarde la colonne dist),")
            print("   ou d'une non-linéarité. Vérifier avec la méthode exacte (option A) avant d'ajuster.")

    print()
    print("Attention : colonne 'dist' = distance spot pipeline ↔ point de contrôle.")
    print("Si dist > 3 km, la comparaison est approximative (surtout près des villes,")
    print("où la pollution varie vite). Ces lignes-là sont à prendre avec prudence.")
    print()
    print("Rappel conversion SQM → Bortle (repère) :")
    print("  ≥21.99 B1 | 21.89-21.99 B2 | 21.69-21.89 B3 | 20.49-21.69 B4")
    print("  19.50-20.49 B4.5-5 | 18.94-19.50 B5-6 | <18.94 B6+")


if __name__ == "__main__":
    main()
