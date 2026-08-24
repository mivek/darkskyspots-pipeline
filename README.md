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

Output appears in `/output/spots/`. Publication also generates global clusters after copying the current region into the cloned data repository. Add `--no-push` to keep the run local.

## CLI reference

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--year` | Mode-dependent | — | Required for a normal run and cluster regeneration; not used by orphan audits or bbox previews. |
| `--region` | Mode-dependent | — | Required for a normal run; not used by cluster regeneration, orphan audits, or bbox previews. |
| `--data-repo-url` | Mode-dependent | — | Required for published runs and published cluster regeneration; optional for local, audit, and preview modes. |
| `--data-repo-branch` | No | `main` | Branch to push to in the data repo. |
| `--no-push` | No | `false` | Skip step 7 (publish). Output stays in `/output/spots/`. |
| `--no-clusters` | No | `false` | Skip cluster generation. |
| `--regenerate-clusters` | No | `false` | Published mode: clone/audit the complete repository, write clusters/, then commit/push; requires --year and --data-repo-url. With --no-push: read output/spots/, write clusters-local/, and perform no clone, audit, commit, or push; requires only --year. |
| `--audit-country-tags` | No | `false` | Strictly read-only audit of missing, invalid, ambiguous, and unconfigured spot country tags. `--list-orphans` is a deprecated alias. |
| `--migrate-country-tags` | No | `false` | Explicitly reclassify historical spots with Natural Earth geometry. Does not delete unresolved spots. |
| `--prune-orphan-spots` | No | `false` | Explicitly delete unresolved or unconfigured historical spots; use with `--migrate-country-tags`. |
| `--preview-bbox-migration` | No | `false` | Read-only preview of bbox ownership changes. |
| `--bbox-candidate` | No | — | Repeatable named bbox candidate for the preview. |
| `--input-dir` | No | `./input` | Directory containing per-region subdirectories with GeoTIFFs. |
| `--output-dir` | No | `./output` | Directory for output JSON files (subdir `spots/` is created). |
| `--budget-mb` | No | `500.0` | RAM budget for loading the input GeoTIFF (MB). If exceeded, the input is processed in slices. |
| `--verbose`, `-v` | No | `false` | Verbose logging. |



### Mode prerequisites

The three input flags are mode-dependent, not globally required:

- A normal published run requires --year, --region, and --data-repo-url.
- A normal local run with --no-push requires --year and --region; --data-repo-url is optional and is not contacted.
- Published --regenerate-clusters requires --year and --data-repo-url, but does not require --region.
- Local --regenerate-clusters --no-push requires only --year and reads output/spots/; it needs neither --region nor --data-repo-url.
- --preview-bbox-migration and --list-orphans require neither --year nor --region. With an optional --data-repo-url they inspect a cloned published repository; without it they inspect local output/spots/.

For the approved France preview (read-only):

~~~bash
python run.py --preview-bbox-migration \
  --bbox-candidate france=-6,41,8,51 \
  --data-repo-url https://github.com/mivek/darkskyspots-data.git
~~~

`--audit-country-tags` and `--list-orphans` are strictly read-only. The former
`--prune-orphans` spelling is rejected; migration and deletion require the two
separate explicit flags above.

## Generation and publication workflow

The data repository is the source of truth for published generation. The order
is: clone; audit country tags immediately after clone; process the raster;
merge the current region's country blocks into every affected tile; generate
clusters from the complete clone; commit and push once. An audit failure stops
publication until an explicit migration has been reviewed.

Clusters must be regenerated only after repository spot tiles are complete. In published mode, --regenerate-clusters clones and audits the repository, writes clusters/ from the complete clone, then commits and pushes. In --no-push mode, --regenerate-clusters reads output/spots/ and writes clusters-local/; it performs no clone, audit, commit, or push and requires only --year.

Normal --no-push also never clones or pushes: it leaves output/spots/ and, unless --no-clusters, writes the partial test artifact output/clusters-local/, filtered to tiles owned by the current region.

## Region bboxes and migrations

Bboxes in regions.yaml remain raster/GeoNames working envelopes, not tile
ownership. Regions may overlap. `osm_country_code` is a list of ISO alpha-2
codes; the Natural Earth 1:10m polygons decide which countries can publish.

The 300 km ALR margin avoids raster edge effects and remains in the luminosity
calculation, but it never creates candidates. Land masking and country clipping
happen before redundancy, with no coastal buffer; islands are retained.

Changing a published country configuration is a spot-level migration. Run the
read-only country audit first, then review `--migrate-country-tags` and (only if
needed) `--prune-orphan-spots` in a disposable clone. A spot's stable `country`
field, rather than `source_region`, controls replacement and makes publication
independent of run order.

Spot schema compatibility is strict for cluster generation: every spot must contain `id`, `lat`, `lon`, `darkness`, `bortle`, `near`, and `altitude`. A missing field is reported with its tile and spot index and aborts generation; it must not be silently ignored. Removing a field from the spot schema is therefore a data migration that must be handled before regenerating clusters. Extra source fields are ignored in the embedded cluster representative, whose contract remains the seven fields above.

Published tile spots additionally carry mandatory producer field `country`
(ISO alpha-2). The app may treat it as optional while reading historical
caches; cluster representatives continue to project only the existing seven
fields. Country is assigned from the Natural Earth clip, not from a region
name, so splitting or regrouping regions does not orphan published spots.

## Published cluster files and cache identity

The repository contains clusters/index.json and clusters/L1.json–L6.json. Every level exists, including empty levels whose JSON is []. Manifest levels[*].files is an array. Each file entry has a SHA-256 hash of the exact UTF-8 bytes; that per-file hash is the cache identity, so clients reuse or invalidate each level independently by hash. Deterministic serialization/order makes identical inputs byte-identical. generated is a human-readable generation date. data_year is informative metadata only: it is neither a cache key nor a substitute for published spot contents.

## Data directory

The `data/` directory contains:
- **`cities500.zip`** — GeoNames populated places database (versioned in git). Downloaded from [GeoNames](https://download.geonames.org/export/dump/cities500.zip).
- **`cities500.txt`** — extracted on first pipeline run (gitignored, ~50 MB).
- **`natural_earth/`** — versioned Natural Earth v5.1.1 1:10m land and
  admin-0 country layers. They are loaded locally; runs never download them.

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
