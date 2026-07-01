# darkskyspots-pipeline

Local Python batch pipeline that transforms VIIRS radiance GeoTIFFs into per-tile JSON spot files for the ["ciel nocturne"](../app-dark-sky) mobile app.

**Status:** ✅ Implementation complete. All 139 tests passing.

## What this is

- **Input:** VIIRS radiance GeoTIFF (NASA Black Marble / lightpollutionmap raw) + OpenStreetMap data
- **Processing:** ALR (All-sky Light pollution Ratio) via the [`nightskyquality`](https://github.com/mivek/nightskyquality) Python package (pinned at `v1.0.0`)
- **Output:** per-tile JSON files (`spots/<tileId>.json`) written locally, then pushed by step 7 to a separate data repo consumed by the app
- **Frequency:** ~1×/year (annual VIIRS composite)
- **Runtime:** local script on a Mini PC; **not a server, not an API**

## What this is not

- Not coupled to the app's source code — the only contract is the JSON schema defined in `app-dark-sky/spec-technique.md`
- Not a permanent process — a single `python run.py --year YYYY --region <region>` invocation
- Not a vendored copy of `nightskyquality` — that fork is installed as a pip dependency from a Git tag
- Not the data repo — `/spots/` is local staging; the data repo (consumed by the app) is a separate clone managed by step 7

## Repository layout

```
.
├── docs/
│   ├── designs/   # approved design documents
│   └── plans/     # implementation plans (one per feature/worktree)
├── src/           # pipeline modules (created in the implementation worktree)
├── run.py         # orchestrator entrypoint (created in the implementation worktree)
├── requirements.txt
├── validation/    # hand-curated control points for the §6 validation procedure
└── README.md
```

## Documentation

- **[Design doc](docs/designs/2026-06-30-darkskyspots-pipeline.md)** — the approved design, including the user's spec verbatim and the decisions taken during the design phase
- Implementation plan — coming soon, on the `feature/dark-sky-pipeline-mvp` worktree

## Quick start

```bash
# 1. Clone this repo + install dependencies
pip install -r requirements.txt

# 2. Place your VIIRS radiance GeoTIFF at the expected location
#    /input/<region>/<year>.tif  (e.g. /input/france/2025.tif)

# 3. Run the pipeline
python run.py \
    --year 2025 \
    --region france \
    --data-repo-url git@github.com:user/data-repo.git \
    --data-repo-branch main

# 4. Output appears in /output/spots/ (local staging)
#    Step 7 pushes to the data repo (add --no-push to skip)
```

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

## Environment

- **SSH key:** Step 7 (publish) uses `git push` over SSH. Ensure your SSH key is loaded (`ssh-add -l`) or `SSH_AUTH_SOCK` is set.
- **`TMPDIR`:** Large temporary files (ALR slices, git clones) are written to the system temp directory. Override via `TMPDIR` if space is limited.
- **Python:** 3.10+ required. Tested on 3.12.

## Input layout

```
/input/
  france/
    2025.tif       ← VIIRS radiance GeoTIFF for France, year 2025
  germany/         ← future regions (one subdir per region)
    2025.tif
```

The pipeline does **not** auto-download VIIRS data. The user places the GeoTIFF at the expected path before running. Supported sources: NASA Black Marble (VNP46A4 / VJ146A4) or [lightpollutionmap.info](https://www.lightpollutionmap.info) raw exports.

The input GeoTIFF must be:
- Float64 (float32 is accepted but converted internally).
- In EPSG:4326 (WGS84) or a CRS the fork can reproject to 3035 / the region's `equal_area_epsg`.
- A single-band radiance raster (units: nW/cm²/sr).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Input not found: /input/france/2025.tif` | GeoTIFF not placed at the expected path | Check `--input-dir` and the file name convention `<year>.tif`. |
| All-NaN output for a small test input | The fork has a 666-pixel NaN halo (R_px). Input must be > 666 px on each side to get meaningful results. | Use a larger GeoTIFF (minimum 700×700 px). |
| `Overpass API returned error` | OSM Overpass rate-limited or the region config is wrong. | Wait a few minutes, or set up a local Overpass instance. |
| `git clone` fails in step 7 | SSH key not loaded or data repo URL is wrong. | Run `ssh -T git@github.com` to verify. Use `--no-push` to bypass. |
| Out of memory | The input GeoTIFF is very large. | Reduce `--budget-mb` to force slice-based processing (500 MB default). |

## Validation procedure

The pipeline includes a validation framework (§6 of the design doc) to calibrate the Bortle scale for non-US regions. After each run:

1. Manually record Bortle estimates for 3–4 control points from [lightpollutionmap.info](https://www.lightpollutionmap.info) (Sky Brightness layer).
2. Run the validation script (to be written as part of the annual run procedure) to compare `expected_bortle` vs `got_bortle`.
3. Tolerance: ±1 Bortle class (D4).
4. If mismatched, adjust `ALR_CALIB_C` in `src/config.py` first (the principal EU-tuning lever), then re-run.

Control points are stored in `validation/checkpoints.json`. The initial set covers France (Cévennes, Massif Central, Toulouse outskirts, Paris).

## Tile ID contract (DO NOT CHANGE)

The pipeline writes tile files with **3-digit zero-padded lat/lon** in the filename and inside the `tile` field of each JSON envelope:

- Format: `N{lat:03d}E{lon:03d}` (N/E) or `S{lat:03d}E{lon:03d}` / `N{lat:03d}W{lon:03d}` / `S{lat:03d}W{lon:03d}` (all 4 quadrants)
- Examples: `N042E001`, `S003W042`, `N089E179`, `W180` (antimeridian), `S090E000` (south pole band)
- Full specification: see `docs/designs/2026-06-30-darkskyspots-pipeline.md` §7 Decision 3

This format is the **contract with the mobile app's `tiles.ts`**. Both sides MUST produce and consume the same format.

⚠️ **If the app's seed files currently use 2-digit padding (e.g. `N49E003`, `N48E002`), they are NON-CONFORMING and must be regenerated to 3-digit padding** (`N049E003`, `N048E002`). The pipeline will not produce matching files otherwise. This is the app-side fix; the pipeline side is locked at 3 digits.

## Credits

- **ALR method:** Duriscoe, D. et al. (2018). *A simplified model of all-sky artificial sky glow derived from VIIRS Day/Night band data.* J. Quant. Spectrosc. Radiat. Transf. 214, 133–145. Implemented in Python by Katy Abbott (NPS) and maintained at [github.com/mivek/nightskyquality](https://github.com/mivek/nightskyquality) (MIT).
- **VIIRS radiance data:** NASA Black Marble products (VNP46A4 / VJ146A4) — CC0.
- **Light pollution map redistribution:** Jurij Stare, [lightpollutionmap.info](https://www.lightpollutionmap.info).
- **Place names and administrative boundaries:** [OpenStreetMap](https://www.openstreetmap.org) contributors (ODbL).
