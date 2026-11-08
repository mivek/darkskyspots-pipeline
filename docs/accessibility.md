# Accessibility evidence pilot and Canary Islands G1

This is an offline, optional enrichment for existing spots. It records nearby
mapped roads, tracks, pedestrian paths and parking features as straight-line
evidence. It does not create spots, calculate routes, infer connectivity, score
difficulty, establish legal permission, recommend stopping places, or determine
whether parking is available at night. The preserved legacy pilot profile is
limited to the supported Tenerife core bbox `[-17.00, 27.95, -16.10, 28.65]`.
The generalized G1 profile assigns the Canary archipelago from the existing
Spain ownership geometry clipped to the Canary publication bbox. It prepares
that core with a 6 km buffer in EPSG:32628 and uses a 1 km public search radius.
The public spot payload remains schema 1 with one immutable source identity.

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

The Tenerife comparison uses strict JSON equality for every query evidence
field, including selected and candidate features, exact and rounded distances,
access evidence, elevation samples and deltas, coverage, statuses, unknown
reasons, restrictions, coordinates and OSM metadata. It excludes only
`metrics.querySeconds` and the top-level source identity fields `sourceId`,
`source_id`, `sourceIdentity`, `source_identity` and `datasetId`. Candidate
selection and exact elevation values are never excluded or rounded for
comparison.

### Reviewed Tenerife regression exception — 2026-11-08

**Decision: ACCEPTED EXPECTED DIFFERENCE.** Independent review accepted the
strict comparison difference between legacy Tenerife source
`tfe-f021a-2726a3c584faee70` and Canary Islands G1 source
`can-g1-a7ffe1cfa7d88565`. Both use the pinned OSM PBF with SHA-256
`0ae95a9e75c666800e947e09d50503e827021c9a611dd8e6163771941f228742`.

The strict comparator reports 72 differences across 72 queries. Every
difference is only `nearbyRestrictionsComplete`: `false` for Tenerife and
`true` for G1. G1 resolves the 33 restriction relations previously unresolved
by the Tenerife preparation. Their geometries are outside the legacy Tenerife
prepared domain; the nearest is relation `10364067`, at 4,967.213 m. Candidate
and nearby-restriction records, distances, access, elevation, coverage and
query statuses remain identical across the compared results. The global
completeness flag change was investigated and independently accepted.

The comparison is tied to data repository commit
`312bc366ccef674a232bde2f0a3a42067d083d19`. Local evidence is retained in
`output/accessibility-review/canaries-g1-v1-review-finding-fixes-final-verified/audit/tenerife-completeness-investigation.json`,
`output/accessibility-review/canaries-g1-v1-review-finding-fixes-final-verified/audit/previous-g1-comparison.json`,
and `output/accessibility-review/tenerife-canaries-g1-v1-review3/tenerife-regression.json`.
This exception does not establish legal access or guarantee complete mapping
of real-world restrictions.

The audit retains raw tags, access conditions, selected and other candidates,
nearest-point coordinates, restriction relations, feature-specific access-tag
references, geometry diagnostics, DEM/WBM samples, candidate counts, timings
and payload deltas. Access-tagged candidates remain separate from unrelated
restriction relations. None of those audit-only details are copied into public
spot JSON.

## Canary Islands G1 preparation and snapshot audit

G1 uses the pinned source and regional DEM/WBM inputs described above. Freeze
all nine published spot tiles and the six cluster levels from the reconciled
data commit before preparing an index:

```bash
darkspots/bin/python tools/fetch_accessibility_baseline.py
```

The baseline is stored read-only under
`input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19/`.
The declarative scope in `config/accessibility.yaml` binds assignment region,
CRS, buffer, supported radii, source URLs and regional DEM manifest.
Preparation rejects mismatched source paths, URLs, policy revisions or
manifest identity and writes a new immutable directory:

```bash
darkspots/bin/python tools/prepare_accessibility.py \
  --config config/accessibility.yaml \
  --scope canaries \
  --pbf input/accessibility/sources/canary-islands-261107.osm.pbf \
  --source-url https://download.geofabrik.de/africa/canary-islands-261107.osm.pbf \
  --coverage-polygon input/accessibility/sources/canary-islands.poly \
  --dem-manifest data/elevation/manifests/canaries.json \
  --dem-cache-dir input/elevation \
  --output-dir input/accessibility/datasets/canaries-261107-g1-v1
```

Repeat the Tenerife pilot against the generalized manifest in a new output
directory, then compare its 72 query records with the validated pilot:

```bash
darkspots/bin/python tools/pilot_accessibility.py \
  --spots input/pilot/f02-1a/N028W017.json \
  --accessibility-manifest input/accessibility/datasets/canaries-261107-g1-v1/manifest.json \
  --dem-manifest data/elevation/manifests/canaries.json \
  --dem-cache-dir input/elevation \
  --controls validation/accessibility/tenerife-controls.json \
  --radii-m 500 1000 3000 5000 \
  --output-radius-m 1000 \
  --output-dir output/accessibility-review/tenerife-canaries-g1-v1

darkspots/bin/python tools/compare_accessibility_pilots.py \
  --reference-queries output/accessibility-pilot/tenerife-261107-review-fixes/audit/queries.jsonl \
  --candidate-queries output/accessibility-review/tenerife-canaries-g1-v1/audit/queries.jsonl \
  --output-dir output/accessibility-review/tenerife-canaries-g1-v1/audit/regression-comparison
```

Run the complete suite and save its summary and full log, then audit the frozen
published snapshot. The audit reads all 66 spots directly, preserves original
tile fields and order, adds accessibility only to matching cluster
representatives, and includes the Tenerife comparison and test results:

```bash
darkspots/bin/python tools/run_accessibility_g1_tests.py \
  --output output/accessibility-review/pytest-results.json

darkspots/bin/python tools/audit_accessibility.py \
  --spots-dir input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19/spots \
  --clusters-dir input/accessibility/baselines/312bc366ccef674a232bde2f0a3a42067d083d19/clusters \
  --accessibility-manifest input/accessibility/datasets/canaries-261107-g1-v1/manifest.json \
  --dem-manifest data/elevation/manifests/canaries.json \
  --dem-cache-dir input/elevation \
  --radius-m 1000 \
  --test-results output/accessibility-review/pytest-results.json \
  --tenerife-regression output/accessibility-review/tenerife-canaries-g1-v1/audit/regression-comparison/tenerife-regression.json \
  --output-dir output/accessibility-review/canaries-g1-v1-review-packet-final
```

G1 is local review work. The CLI and direct dispatcher publication guards
remain in force, and these commands do not publish enriched spots or clusters.

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
