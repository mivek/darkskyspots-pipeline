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
| `--year` | Mode-dependent | — | Required for a normal run and cluster regeneration; not used by country-tag audits. |
| `--region` | Mode-dependent | — | Required for a normal run; not used by cluster regeneration or country-tag audits. |
| `--data-repo-url` | Mode-dependent | — | Required for published runs and published cluster regeneration; optional for local and audit modes. |
| `--data-repo-branch` | No | `main` | Branch to push to in the data repo. |
| `--no-push` | No | `false` | Skip step 7 (publish). Output stays in `/output/spots/`. |
| `--no-clusters` | No | `false` | Skip cluster generation. |
| `--regenerate-clusters` | No | `false` | Published mode: clone/audit the complete repository, write clusters/, then commit/push; requires --year and --data-repo-url. With --no-push: read output/spots/, write clusters-local/, and perform no clone, audit, commit, or push; requires only --year. |
| `--audit-country-tags` | No | `false` | Strictly read-only audit of missing, invalid, unconfigured, mismatched, ambiguous, and unassignable spots, with a projected migration summary. `--list-orphans` is a deprecated alias. |
| `--migrate-country-tags` | No | `false` | Explicitly reclassify historical spots with Natural Earth geometry. Does not delete unresolved spots. |
| `--prune-orphan-spots` | No | `false` | Explicitly delete unresolved or unconfigured historical spots; use with `--migrate-country-tags`. |
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
- --audit-country-tags and --list-orphans require neither --year nor --region. With an optional --data-repo-url they inspect a cloned published repository; without it they inspect local output/spots/.

`--audit-country-tags` and `--list-orphans` are strictly read-only. The former
`--prune-orphans` spelling is rejected; migration and deletion require the two
separate explicit flags above.

The audit reports current tag-state counters (`missing`, `invalid`,
`unconfigured`, `mismatched`, `ambiguous`, and `unassignable`) plus projected
actions. `reclassifiable_to_configured` counts spots that can receive a unique
configured country, `resolved_unconfigured` counts spots resolving uniquely to
a country with no configured producer, and `correctable_mismatched` counts
valid tags that disagree with their coordinates. The `projection` object
contains both `migration_only` and `migration_and_prune` summaries, including
rewritten/deleted files and final spot counts.

## Generation and publication workflow

The data repository is the source of truth for published generation. The order
is: clone; audit country tags immediately after clone; process the raster;
merge the current region's country blocks into every affected tile; generate
clusters from the complete clone; commit and push once. An audit failure stops
publication until an explicit migration has been reviewed.

Clusters must be regenerated only after repository spot tiles are complete. In published mode, --regenerate-clusters clones and audits the repository, writes clusters/ from the complete clone, then commits and pushes. In --no-push mode, --regenerate-clusters reads output/spots/ and writes clusters-local/; it performs no clone, audit, commit, or push and requires only --year.

Normal --no-push also never clones or pushes: it leaves output/spots/ and, unless --no-clusters, writes the local output/clusters-local/ artifact.

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

For a calibration audit, read the SQM values manually from [lightpollutionmap.info](https://www.lightpollutionmap.info) and fill `sqm` in `validation/calibration_points.json`. Then compare the exact raster pixels:

```bash
python calibrate_exact.py \
  --darkness output/debug_darkness_france_2025.tif \
  --bortle output/debug_bortle_france_2025.tif
```

The report compares continuous SQM-equivalent values and, secondarily, Bortle. It also reports the displayed integer score under clear sky, new moon, and a complete astronomical night. `SQM_naturel = 22.0` is an explicit conversion assumption, not a pipeline constant; an error of ±0.20 mag shifts absolute SQM deltas uniformly, while relative country comparisons remain comparable. It does not derive or change `ALR_CALIB_C`. The SQM value from lightpollutionmap is a model, not a ground measurement: the comparison measures divergence between models, not which one is correct. This audit is for western Europe; a second raster is required for high-latitude validation such as Scandinavia.

The extended raster cross-check uses the versioned 2025 Sky Brightness export and
reads the exact containing pixel in both grids:

```bash
python calibrate_exact.py \
  --darkness output/crosscheck/debug_darkness_france_2025.tif \
  --bortle output/crosscheck/debug_bortle_france_2025.tif \
  --sky-brightness validation/sky_brightness/sb_2025_western_europe.tif \
  --spots-dir output/crosscheck/spots \
  --elevation-overrides validation/france_spot_elevations.json \
  --foreign-samples validation/crosscheck_samples.json \
  --json-out output/crosscheck/report.json
```

`validation/sky_brightness/manifest.json` freezes the export provenance and
`validation/crosscheck_samples.json` contains the deterministic Spain/UK grid
sample. The Sky Brightness conversion follows lightpollutionmap FAQ31 and its
22.00 mag/arcsec² natural-sky anchor. Both models still share the same VIIRS
input, so this is a cross-model divergence measurement, not independent field
validation; it cannot establish which model is correct or justify changing
`ALR_CALIB_C` by itself. The latitude span is western Europe only and says
nothing about Scandinavia or other high latitudes.

The score section reports the distribution of absolute integer differences
(`0`, `1`, `2`, `3+`), the signed mean/median, and the significant count
`abs(delta_score) >= 1`. Because the displayed score is already an integer,
the latter is mathematically the same as any non-zero score difference; the
distribution gives the useful magnitude. It also reports Spearman correlation
and discordant continuous SQM pairs to measure whether a systematic bias
preserves the spot ranking. `validation/france_spot_elevations.json` supplies
the same modeled ASTER30m/OpenTopoData altitude covariate for the French spots;
it is provenance-frozen and is not a pipeline input or a calibration
parameter. The report then gives Pearson/Spearman altitude correlations,
simple slopes, univariate covariate correlations, and descriptive standardized
multiple-regression betas for altitude, darkness, Bortle, latitude, and
longitude. Darkness and Bortle are related pipeline outputs, so these are
associations rather than causal effects. Foreign and French altitudes are
modeled elevations, not terrain measurements.

The dated investigation and decision are recorded in
[`validation/calibration_crosscheck_2026-08-25.md`](validation/calibration_crosscheck_2026-08-25.md).

## Credits

- **ALR method:** Duriscoe et al. (2018). Implemented in Python by Katy Abbott (NPS) at [github.com/mivek/nightskyquality](https://github.com/mivek/nightskyquality) (MIT).
- **VIIRS radiance data:** NASA Black Marble products (VNP46A4 / VJ146A4) — CC0.
- **Light pollution map redistribution:** Jurij Stare, [lightpollutionmap.info](https://www.lightpollutionmap.info).
- **Place names and administrative boundaries:** OpenStreetMap contributors (ODbL).
- **Populated places (GeoNames):** [GeoNames](https://www.geonames.org) data used under the [Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/). Data is sourced from the [cities500](https://download.geonames.org/export/dump/cities500.zip) export.
