# Dark Sky Spots Pipeline Specification

This document describes the behavior implemented by the pipeline. It covers a
normal batch run from a VIIRS radiance GeoTIFF to published spot tiles and
precomputed clusters. The entrypoint is `python run.py`; it is not a server.

## Inputs and Preflight

The input raster is read from
`<input-dir>/<region>/<year>.tif`. The pipeline does not download a raster.
The raster is a single-band radiance GeoTIFF. The `region` entry in
`regions.yaml` supplies the working bbox, equal-area CRS, and the list of
configured ISO alpha-2 countries.

Before raster processing, the orchestrator:

- loads and validates the region registry and its Natural Earth country
  configuration;
- validates every required filtered GeoNames extract against
  `data/geonames/manifest.yaml`, including its exact feature-code list and
  SHA-256 hash;
- checks that the input GeoTIFF exists.

For a remote publication, after the input existence check it clones the data
repository and audits the existing `spots/` directory. Any missing, invalid,
unconfigured, mismatched, ambiguous, or unassignable country tag stops the run
before raster work.

## Processing Stages

### 1. Radiance to ALR

`src/alr.py` calls the installed `nightskyquality` fork to convert radiance to
ALR (All-sky Light pollution Ratio). The computation uses the region's
equal-area CRS and the configured ALR parameters, including the 300 km maximum
context and the European calibration constant.

If the estimated float64 input size exceeds `--budget-mb`, the raster is
processed in windows and the ALR results are stitched back into the original
raster shape. Windows overlap by the ALR context radius so edge pixels retain
the required surrounding context. The result keeps the raster transform and
CRS for the later coordinate conversion.

### 2. ALR to darkness and Bortle

`src/convert.py` derives two arrays from ALR:

- `darkness` is the clipped [0, 1] inverse logarithmic normalization between
  the configured dark and bright ALR bounds;
- `bortle` is the integer class from the inclusive ALR threshold table. NaN
  values map to class 9.

The optional debug-raster mode writes these two arrays as GeoTIFFs. It does not
change the spot output.

### 3. Mesh darkest-pixel scan

`src/extract.py` divides the darkness array into approximately 5 km mesh
cells. It selects one pixel per cell with `np.nanargmax` on the `darkness`
array (the darkest pixel), skips cells containing only NaN values, and converts
the selected pixel center to latitude and longitude. Equal maxima select the
first pixel in row-major order. Each candidate initially contains its
coordinates, darkness, and source `row`/`col`. The orchestrator then attaches
the definitive darkness and Bortle values from the selected raster pixels.

### 4. Natural Earth land and country clip

`src/geography.py` applies two local Natural Earth 1:10m layers before any
redundancy or coverage decision:

1. The Natural Earth land geometry rejects sea points.
2. The Natural Earth admin-0 country geometries determine the country for the
   point; only countries configured for the region are retained.

Country resolution is global before the configured-country filter. When a
source raster pixel intersects more than one country, the country occupying
the largest equal-area portion of that pixel wins. A missing pixel footprint or
an exact area tie uses the lexical ISO code as the final deterministic
tie-breaker.

The ALR context can produce candidates outside the nominal region. After the
Natural Earth classification, the orchestrator applies the inclusive region
bbox and removes those halo candidates. The resulting list is the one passed
to redundancy, coverage, naming, and tile export.

### 5. Redundancy filter

Candidates are sorted from darkest to least dark. A candidate is removed only
when an already-kept candidate of the same Bortle class is less than 15 km
away. Candidates of different Bortle classes are retained even when they are
closer than that distance.

### 6. GeoNames locality coverage

`src/coverage.py` loads GeoNames `cities500.zip`, extracting
`cities500.txt` on first use. It keeps configured-country localities within the
region bbox plus a 100 km prefilter margin.

For each retained locality, the pipeline counts spots within 100 km. If fewer
than four are available, it adds the darkest unused points from the already
filtered mesh candidate pool within that radius. This is an attempted
per-locality coverage rule: if the candidate pool is smaller than the target,
the code does not invent spots and does not fail the run.

The nearest locality within 25 km is written to `near`. A spot with no such
locality receives the empty string. An existing non-`None` `near` value is
preserved by the helper.

### 7. GeoNames naming cascade

`src/geonames.py` loads the versioned, country-filtered TSV extracts referenced
by the GeoNames manifest. The runtime uses the canonical naming feature-code
list. `ADM1` and `ADM2` are also present in the extracts for administrative
fallbacks.

For each spot, resolution is scoped to its Natural Earth `country`:

1. The nearest ordinary feature with a configured feature code wins when it is
   no more than 40 km away.
2. Otherwise, the nearest `ADM2` record is used when one exists.
3. If no `ADM2` record exists, the nearest `ADM1` record is used.

Distances are exact great-circle distances and equal distances are broken by
the lowest GeoNames ID. Ordinary features are never displaced by a closer
administrative fallback. The cascade always supplies a non-empty `name`; an
administrative fallback has `nameDistanceKm: null`.

The naming stage adds `name`, `nameDistanceKm`, `nameFeatureCode`,
`nameFeatureClass`, and `nameGeoNameId`.

### 8. Final spot enrichment

`src/enrich.py` creates the deterministic coordinate-based `id`, preserves
`near`, sets `altitude` to `null`, and removes the transient raster `row` and
`col` fields. It does not call a network service or derive elevation.

### 9. Tile assignment and versioning

Spots are assigned to one-degree latitude/longitude tiles using the canonical
zero-padded ID. For example, `(42.7283, 1.6492)` becomes `N042E001`.

Local output writes an envelope to `output/spots/<tile>.json` for every tile in
the nominal region bbox, including empty tiles:

```json
{
    "version": "2025.1",
    "source": "VIIRS/2025/france",
    "generated": "2026-08-28T12:00:00Z",
    "tile": "N042E001",
    "spots": []
}
```

The version is computed by comparing spot arrays with the existing published
envelopes. A first dataset version is `<year>.1`; a changed dataset increments
the minor component, or starts at `<year>.1` when the year changes. Metadata
changes alone do not bump the version.

### 10. Country-scoped tile publication

In publishing mode, the current run's envelopes are copied into the cloned
data repository. Publication is country-scoped, not bbox- or tile-owned:

- every incoming spot must have a `country` in the current run's configured
  country set;
- the old spots for those countries are removed from every existing tile,
  including tiles outside the current run's bbox;
- the current country blocks are inserted into affected tiles;
- spots belonging to other countries are preserved in the same tiles.

Blocks are ordered deterministically by country. A tile remains in the
published repository when its merged spot list is non-empty; a tile emptied by
the merge is deleted.

### 11. Cluster generation

After the complete cloned `spots/` tree has been merged, the pipeline
regenerates all six cluster levels. The levels use cell widths of 0.3, 0.6,
1.2, 2.4, 4.8, and 9.6 degrees, corresponding to the configured approximate
100-200, 200-400, 400-800, 800-1600, 1600-3200, and 3200-6400 km ranges.

Each occupied cell becomes a cluster containing:

- a deterministic cluster ID;
- the arithmetic-mean latitude and longitude of its spots;
- the spot count;
- the west/south/east/north spot bbox;
- a representative spot selected by highest darkness, then lowest string ID
  on an exact darkness tie.

The representative projects these fields from the source spot:
`id`, `lat`, `lon`, `darkness`, `bortle`, `near`, `name`, `nameDistanceKm`,
and `altitude`. Every level is written, including an empty `L<n>.json` when
that level has no occupied cells. Cluster generation rejects a source tile
spot that lacks any of those nine fields or has an invalid `name` or
`nameDistanceKm` value.

`clusters/index.json` has schema 1 and records the generation timestamp,
informative `data_year`, each level's cell size and width range, and the
SHA-256 hash of each level file. Serialization and ordering are deterministic.

### 12. Commit and push

The publication step stages the cloned repository, validates the staged
changes, commits, and pushes the selected branch.

If any path under `spots/` is staged, the guard requires a staged,
JSON-readable `clusters/index.json` whose non-empty `generated` value differs
from the value in `HEAD`. Missing, unreadable, or unchanged manifests reject
the publication before commit. A clusters-only change is allowed. Remote
normal runs reject `--no-clusters`; that option is for local `--no-push` runs.

## Published Spot Contract

The published data repository is the source of truth. A published tile has the
envelope keys `version`, `source`, `generated`, `tile`, and `spots`. Each spot
produced by the pipeline has these fields:

| Field | Published value |
|---|---|
| `id` | String formatted as `<latitude to 4 decimals>_<longitude to 4 decimals>` |
| `lat`, `lon` | Spot coordinates in WGS84 decimal degrees |
| `darkness` | ALR-derived darkness value |
| `bortle` | Integer Bortle class from 1 to 9 |
| `country` | Natural Earth ISO alpha-2 country code |
| `near` | Nearest GeoNames `cities500` locality within 25 km, or `""` |
| `name` | Non-empty GeoNames landmark or administrative fallback name |
| `nameDistanceKm` | Distance to the ordinary selected feature, rounded to 3 decimals, or `null` for an administrative fallback |
| `nameFeatureCode` | GeoNames feature code selected by the cascade |
| `nameFeatureClass` | GeoNames feature class selected by the cascade |
| `nameGeoNameId` | GeoNames identifier selected by the cascade |
| `altitude` | Always `null` in the current pipeline |

The transient `row` and `col` fields are not published. The tile envelope's
`country` values are the basis for future country-scoped replacement.

## External Data Sources

- **VIIRS:** supplies the input radiance GeoTIFF. The pipeline consumes the
  file supplied by the caller and does not download it.
- **Natural Earth:** supplies the versioned local land mask and admin-0 country
  geometries under `data/natural_earth/`. It controls sea rejection and
  country attribution; runs do not download these layers.
- **GeoNames:** supplies `cities500` localities for coverage and `near`, plus
  versioned country-filtered feature extracts for naming. The manifest records
  source URLs, source provenance, extract hashes, and the exact applied code
  list. Runtime naming reads the filtered extracts, not the national ZIP
  archives.

The current pipeline code does not make OSM or Overpass requests.

## Other Runtime Modes

- `--no-push` runs the raster-to-tile stages without cloning, copying to a
  remote repository, committing, or pushing. Unless `--no-clusters` is also
  set, it writes clusters to `output/clusters-local/` from local `output/spots/`.
- `--regenerate-clusters` regenerates clusters from a complete published clone
  in remote mode, after the same country audit, or from local `output/spots/`
  into `output/clusters-local/` with `--no-push`.
- `--audit-country-tags` is read-only. `--migrate-country-tags` applies an
  explicitly requested country reclassification; remote migration also
  regenerates clusters before committing. `--prune-orphan-spots` additionally
  deletes unresolved or unconfigured historical spots and is not implicit in
  an audit.
