# darkskyspots-pipeline

Local Python batch pipeline that transforms VIIRS radiance GeoTIFFs into per-tile JSON spot files for the ["ciel nocturne"](../app-dark-sky) mobile app.

**Input:** VIIRS radiance GeoTIFF (NASA Black Marble / lightpollutionmap raw) + OSM data.  
**Processing:** ALR (All-sky Light pollution Ratio) via the [`nightskyquality`](https://github.com/mivek/nightskyquality) package.  
**Output:** per-tile JSON files (`spots/<tileId>.json`) pushed to a separate data repo.  
**Frequency:** ~1×/year (annual VIIRS composite). Not a server, not an API — a single `python run.py` invocation.

## Quick start

```bash
pip install -r requirements.txt

# Place your GeoTIFF at /input/<region>/<year>.tif (e.g. /input/france/2025.tif)

python run.py \
    --year 2025 \
    --region france \
    --data-repo-url git@github.com:user/data-repo.git \
    --data-repo-branch main
```

Output appears in `/output/spots/`. Add `--no-push` to skip the publish step.

## CLI reference

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--year` | Yes | — | Year of the input data (e.g. `2025`). Determines the version prefix. |
| `--region` | Yes | — | Region name from `regions.yaml` (e.g. `france`). |
| `--data-repo-url` | Yes | — | SSH URL of the data repo (e.g. `git@github.com:user/data-repo.git`). |
| `--data-repo-branch` | No | `main` | Branch to push to in the data repo. |
| `--no-push` | No | `false` | Skip step 7 (publish). Output stays in `/output/spots/`. |
| `--input-dir` | No | `./input` | Directory containing per-region subdirectories with GeoTIFFs. |
| `--output-dir` | No | `./output` | Directory for output JSON files (subdir `spots/` is created). |
| `--budget-mb` | No | `500.0` | RAM budget for loading the input GeoTIFF (MB). If exceeded, the input is processed in slices. |
| `--verbose`, `-v` | No | `false` | Verbose logging. |

## Data directory

The `data/` directory contains:
- **`cities500.zip`** — GeoNames populated places database (versioned in git). Downloaded from [GeoNames](https://download.geonames.org/export/dump/cities500.zip).
- **`cities500.txt`** — extracted on first pipeline run (gitignored, ~50 MB).

No other data files are required.

## Input

Place your GeoTIFF at `--input-dir/<region>/<year>.tif` before running. Supported sources: NASA Black Marble (VNP46A4 / VJ146A4) or [lightpollutionmap.info](https://www.lightpollutionmap.info) raw exports.

The input must be:
- Float64 (float32 is accepted but converted internally)
- EPSG:4326 (WGS84) or a CRS the fork can reproject to EPSG:3035
- Single-band radiance raster (nW/cm²/sr)
- Larger than 666 px on each side (the fork has a NaN halo of that size)

## Environment

- **Python 3.10+** required (tested on 3.12).
- **SSH key** loaded (`ssh-add -l`) for the publish step (uses `git push` over SSH). Use `--no-push` to skip.
- **`TMPDIR`** controls temp file location (ALR slices, git clones). Override if disk space is limited.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|------|
| `Input not found` | GeoTIFF not at expected path | Check `--input-dir` and `<region>/<year>.tif`. |
| All-NaN output | Input too small (< 666 px per side) | Use a larger GeoTIFF (minimum 700×700 px). |
| `GeoNames data not found` | Missing `data/cities500.zip` | Download from [GeoNames](https://download.geonames.org/export/dump/cities500.zip) into `data/`. |
| `git clone` fails in step 7 | SSH key not loaded or bad URL | Run `ssh -T git@github.com` to verify. Use `--no-push`. |
| Out of memory | GeoTIFF too large for default budget | Reduce `--budget-mb` to force slice-based processing. |

## Tile ID contract

Tile IDs use **3-digit zero-padded lat/lon**: `N{lat:03d}E{lon:03d}` (e.g. `N042E001`). This is the contract with the mobile app's `tiles.ts`. Do not change.

## Validation

After each run, manually record Bortle estimates for the control points in `validation/checkpoints.json` against [lightpollutionmap.info](https://www.lightpollutionmap.info) (Sky Brightness layer). Tolerance: ±1 Bortle class. Tune `ALR_CALIB_C` in `src/config.py` if mismatched.

## Credits

- **ALR method:** Duriscoe et al. (2018). Implemented in Python by Katy Abbott (NPS) at [github.com/mivek/nightskyquality](https://github.com/mivek/nightskyquality) (MIT).
- **VIIRS radiance data:** NASA Black Marble products (VNP46A4 / VJ146A4) — CC0.
- **Light pollution map redistribution:** Jurij Stare, [lightpollutionmap.info](https://www.lightpollutionmap.info).
- **Place names and administrative boundaries:** OpenStreetMap contributors (ODbL).
- **Populated places (GeoNames):** [GeoNames](https://www.geonames.org) data used under the [Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/). Data is sourced from the [cities500](https://download.geonames.org/export/dump/cities500.zip) export.
