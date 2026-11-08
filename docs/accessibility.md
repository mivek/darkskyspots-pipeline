# Tenerife accessibility evidence pilot

This is an offline, optional enrichment for existing spots. It records nearby
mapped roads, tracks, pedestrian paths and parking features as straight-line
evidence. It does not create spots, calculate routes, infer connectivity, score
difficulty, establish legal permission, recommend stopping places, or determine
whether parking is available at night. Enrichment is limited to the supported
Tenerife core bbox `[-17.00, 27.95, -16.10, 28.65]`; other Canary Islands are
left without the optional field.

## Preparation

The normal pipeline requirements do not include Pyosmium. Install its pinned
preparation-only dependency in the project environment. `pyproj` is included
in the normal `requirements.txt` because runtime accessibility queries import
it. Fetch the frozen tile from its exact data-repository commit; the fetcher
stores it under ignored `input/` and verifies the expected SHA-256:

```bash
darkspots/bin/python -m pip install -r requirements-accessibility.txt
darkspots/bin/python tools/fetch_pilot_baseline.py
```

The frozen tile is from `mivek/darkskyspots-data` commit
`312bc366ccef674a232bde2f0a3a42067d083d19`, path
`spots/N028W017.json`, SHA-256
`01e1b4d340a6ac1d4aadb6149aa2621fbfae67bb74f67b2fc6b22a835e1099fe`.
The fetcher refuses a different existing file and verifies the hash again in
the pilot.

Fetch the exact dated OSM extract and prepare/validate the local Canaries DEM
inputs. A clean DEM preparation requires an installed geographic bundle as
described in the [geographic quick start](../README.md#quick-start) and
[artifact workflow](geographic-artifacts.md), plus CDSE S3 credentials;
`tools/prepare_elevation.py` verifies a retained cache
when present and otherwise retrieves GLO-30 2024_1 DEM/WBM files:

```bash
darkspots/bin/python tools/fetch_accessibility_source.py \
  --output-dir input/accessibility/sources
# Set CDSE_ACCESS_KEY and CDSE_SECRET_KEY in the shell environment first.
darkspots/bin/python tools/prepare_elevation.py \
  --region canaries --version 2024_1
darkspots/bin/python tools/prepare_accessibility.py \
  --pbf input/accessibility/sources/canary-islands-261107.osm.pbf \
  --source-url https://download.geofabrik.de/africa/canary-islands-261107.osm.pbf \
  --scope tenerife \
  --dem-manifest data/elevation/manifests/canaries.json \
  --dem-cache-dir input/elevation \
  --output-dir input/accessibility/datasets/tenerife-f02-1a-review-fixes
```

The fetch tool refuses to overwrite retained source files. Preparation verifies
the recorded retrieval metadata and hashes, the Geofabrik coverage polygon, the
existing Canaries GLO-30 `2024_1` DEM/WBM manifest and cached files, and the PBF
header. It streams the source with a disk-backed `sparse_file_array` node cache,
assembles parking areas while filtering area relations to the required parking
tag, stores compressed projected WKB in the separate SQLite/RTree index, validates
checksums and SQLite integrity, then finalizes a new immutable revision. A
revision directory is never overwritten. Normal runtime code does not import
Pyosmium and makes no HTTP requests.

The manifest records Python, Pyosmium, Shapely, PyProj/PROJ, Rasterio and SQLite
versions. These versions join the source hashes, CRS, radii and policy revisions
in the content-derived source ID. Retrieval dates are provenance only; the PBF
replication timestamp comes from its header.

The SQLite RTree narrows candidate lookup; exact geometry distance is computed
in EPSG:32628. Ways use closest points on their complete line geometry, parking
nodes use their mapped point and parking areas use their polygon, including
holes. Search radius inclusion and ordering use unrounded metres. Ties use OSM
type order (node, way, relation), numeric ID and canonical nearest-point order.
Only public distances are rounded to integer metres.

An empty result is reported only when the complete search disk is covered by
both the prepared domain and provider extract extent and the relevant feature
class has no unresolved geometry errors. Otherwise the query is `unknown`.
Completeness refers only to supported features in this OSM extract; OSM is not a
complete inventory of real-world features. Restriction-relation audit
completeness is recorded separately; unlocated via members make that audit
incomplete but do not change completeness of the four public physical classes.

## Local pilot

The pilot verifies the frozen 16-spot tile hash, version, order, IDs and exact
coordinates. The Pico Viejo mapped summit and Minas de San José coordinates are
controls only. It compares all 18 locations at four radii and refuses to
overwrite an existing run directory:

```bash
darkspots/bin/python tools/pilot_accessibility.py \
  --spots input/pilot/f02-1a/N028W017.json \
  --accessibility-manifest input/accessibility/datasets/tenerife-f02-1a-review-fixes/manifest.json \
  --dem-manifest data/elevation/manifests/canaries.json \
  --dem-cache-dir input/elevation \
  --controls validation/accessibility/tenerife-controls.json \
  --radii-m 500 1000 3000 5000 \
  --output-radius-m 1000 \
  --output-dir output/accessibility-pilot/tenerife-261107-review-fixes
```

The pilot requires the approved radii in the given order: 500, 1,000, 3,000 and
5,000 m. Its output radius is fixed at 1,000 m. The output includes the experimental enriched tile, generated-only cluster
comparisons, exact input copies and hashes, 72 JSONL location/radius audits,
source metadata, a Markdown report and machine-readable measurements. The 1 km
output radius is provisional. Controls are excluded from tile and cluster
outputs. Reports state pipeline revision and worktree cleanliness; a dirty
pipeline worktree is not publication evidence.

The pilot verifies that every frozen spot retains its original fields, ID and
order, and checks cluster membership, geometry and representative IDs at every
level. A local normal `canaries` run using `--accessibility-manifest` writes the
matching source record under `output/accessibility/sources/`. That CLI option
requires `--no-push`.

The audit retains raw tags, access conditions, selected and other candidates,
nearest-point coordinates, restriction relations, feature-specific access-tag
references, geometry diagnostics, DEM/WBM samples, candidate counts, timings
and payload deltas. Access-tagged candidates remain separate from unrelated
restriction relations. None of those audit-only details are copied into public
spot JSON.

## Public spot contract

`accessibility` is optional; legacy spots stay valid. Schema 1 contains a
content-derived source ID, one shared positive integer radius and one query for
each physical class: `road`, `track`, `path`, and `parking`. Each query is
`found`, `not_found_within_radius`, or `unknown`. A found result contains its
OSM type/ID, integer straight-line distance, nullable `elevationDeltaM`, and
both `access.foot` and `access.motorcar`. Road `highway`, `service`, explicit
`motorroad`, and recognized `sacScale` values are retained when present.

Access precedence is `foot → access` for walking and
`motorcar → motor_vehicle → vehicle → access` for passenger cars. Recognized
values remain literal mapped evidence. Missing or unsupported values stay
unknown; specific unsupported tags do not fall through to broader affirmative
tags. Conditional rules are not evaluated and remain audit-only. A generic
affirmative `access` tag on a pedestrian path does not imply motor-vehicle
permission. A feature's access evidence applies only to that feature. Nearby
restrictions never become spot warnings.

Elevation delta is the preserved spot altitude minus the unrounded DEM value at
the nearest point on the selected feature. The pinned reference DEM sample is
checked against the preserved spot altitude first. Water, NoData, out-of-coverage,
unknown reference altitude, mismatched published altitude, bridges, tunnels and
non-ground levels yield a null delta with a reason. No smoothing, OSM `ele`
fallback, ascent, slope or difficulty inference is performed.

Each compact spot payload is limited to 4 KiB. It contains no raw tags,
conditional expressions, geometry, nearest-point coordinates, additional
candidates, or `nearbyRestrictions`. Cache identity includes the immutable source
and policy revisions. Optional accessibility is projected into cluster
representatives only when present; the required spot fields and representative
selection rule remain unchanged.

## Licensing

OSM-derived structured accessibility evidence is data. Keep “© OpenStreetMap
contributors”, source identification, ODbL 1.0 references, and the applicable
derivative database/data offer and preparation recipe with any later
distribution. The local pilot is not permission to distribute or publish its
data. Preserve the existing Copernicus derived-product and liability notices
for DEM-derived fields. See [geographic licensing](geographic-licensing.md).
