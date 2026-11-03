# Geographic artifact retention and offline installation

Selection and publication read a local immutable revision. They never fetch
geographic assets. A `/tmp` simulation package is not a production release.
The initial package's manifest SHA-256 is
`031977e58b4773783b958b141e046ffb3a418abfabd2fd6ecc6b4e0965fe4f4c`.
It remains a blocked validation package, with only one corroborated exclusion
and no generated-spot licensing approval.

A verified working copy and frozen reproduction inputs are now retained on
the project volume at `input/geography/retained/<manifest-sha256>/`. Its
`retention-record.json` inventories file sizes/hashes and the recorded build
environment. This prevents dependence on the ephemeral `/tmp` package, but
is a local validation cache, not evidence of a configured external archive
or a tested backup. It does not authorize production promotion.

## Retain before promotion

Store release objects under an access-controlled durable artifact prefix:
`geography/sha256/<manifest-sha256>/`. Use an immutable/versioned object store
or a backed-up artifact volume, with retention preventing overwrite and
deletion of revisions referenced by publication reports. Keep the version ID
or backup snapshot identifier in the release record. This repository does not
provision a bucket, backup system, or credentials; none is presumed to exist.

Retain these independently checksummed objects:

1. The complete prepared package: manifest, every referenced file, reviews and
   review contexts. Never overwrite a revision or use an unversioned `latest`.
2. The frozen source index and availability inventory, original processed
   OSMCoastline archive (which can restore its verified members), every selected Geofabrik member
   and `source.json`, original Copernicus manifests and WBM source files.
   Geofabrik range downloads retain the extracted members, not the whole ZIP;
   archive those members because upstream dated URLs can disappear. Also keep
   evidence and its usage/distribution permissions referenced by decisions.
3. Preparation code commit, exact Python/package/native-library versions,
   commands, licensing/attribution notices and SHA-256 inventory. Keep human
   decisions separately available for reuse; a publication attempt report is
   never a reusable decision.
4. WorldCover 2021 v200 regional COGs and manifest (source URLs/ETags, sizes,
   SHA-256, native grid and CC-BY-4.0 citation), schema-3/4 E3 decisions, degraded
   policies/availability inventories, SQLite indexes and frozen D/E2/E3 references.
   Preserve original geometries as well as indexes.

An operator records the object URI, version ID, archive SHA-256, manifest
SHA-256, build commit and retained-source inventory in a reviewed release
record stored outside the mutable package. Authenticate that record through
the reviewed Git commit or the artifact store's signed release metadata.
A checksum downloaded from the same untrusted location alone is insufficient.
Copy the source recipe and package to a second retention location and exercise
a restore before promotion. Keep OSM-derived databases and imagery private
until their respective distribution obligations have been satisfied; see
[licensing](geographic-licensing.md). Public spot-data licensing remains a
separate assessment.

## Install explicitly, then verify offline

Outside any generation/publication run, retrieve the exact archive named by
the reviewed release record using the artifact store's authenticated client.
Compare its SHA-256 with the independently retained release record. Extract
only into a fresh directory after checking archive member paths and rejecting
symlinks. Install it at `input/geography/revisions/<manifest-sha256>/` on the
production host, which is outside Git. Copying an already verified package
from a trusted artifact volume is equally valid. Do not edit the installation.

```bash
# Run with the expected hash from the reviewed release record.
sha256sum input/geography/revisions/<manifest-sha256>/manifest.json
darkspots/bin/python -c 'import sys; from src.geographic_data import GeographicDataset; d=GeographicDataset(sys.argv[1]); print(d.revision)' input/geography/revisions/<manifest-sha256>/manifest.json
```

The second command verifies the schema, region registry, every referenced
file's size/SHA-256 and decision compatibility without network access. The
publication audit additionally requires every audited region's verified WBM
manifest and every final coordinate's sample. File, sample and read failures
are blocking; nothing is repaired or downloaded automatically.

Pass this exact manifest path with `--geographic-manifest` to generation,
publication and migration. Preserve the resulting Git-tree-bound report and
its geographic/review revisions alongside the release record. A change in
reviews or licensing creates a new package revision and a new release record.

## Reproduce and restore

Restore the frozen source inventory rather than downloading today's data.
Recreate the recorded software environment, run the recorded preparation
command and compare the resulting manifest hash and every referenced file.
Compression/library differences may prevent byte-identical output in another
environment; that output is a new revision requiring review, not a substitute
for the verified release. The retained prepared archive supplies exact-byte
restoration even if an upstream URL or old software environment disappears.

Production acceptance requires a verified restore from durable storage. The
validation results in this PR do not assert that an external store or backup
has been configured. Until a release record and restore proof exist, the
package must not be promoted from the local validation cache.

## E3 preparation

WorldCover retrieval is an explicit preparation step. Restore the retained
2021 v200 regional manifest and exact files. If retrieval is necessary, acquire
only its recorded URLs and verify retained sizes/SHA-256; ETags alone do not
prove integrity. No runtime command retrieves an unversioned replacement.

```bash
darkspots/bin/python tools/prepare_e3.py \
  --base-manifest /retained/geographic-source/manifest.json \
  --worldcover-manifest /retained/worldcover/manifest.json \
  --output input/geography/revisions/<new-revision>
```

Preparation is offline and verifies every copied source. Explicit `--reviews`,
`--degraded-policies`, `--availability` and `--licensing-assessment` create new
immutable revisions. E3 review updates through `prepare_geography.py
--base-manifest` also use E3 contexts, never legacy acceptance semantics.
Generated contexts never approve decisions.

Preparation upgrades old SQLite indexes to storage schema 2 in the new output
package, retaining full authoritative WKB and its feature identity. Source
geometries are unchanged; subdivision is a storage/index operation. Record the
exact Shapely/GEOS versions with the release; minimum preparation capability is
Shapely 2.1 / GEOS 3.10. Existing packages remain unchanged and readable.

B2 review renewal requires an explicit schema-4 document supplied via `--reviews`.
It must contain positive reason-specific conclusions, reviewer/reference and
identifiable supporting evidence. Reading an obsolete schema-3 B2 document for
preparation does not authorize its use in generation or publication. No decision
is promoted, renewed or filled automatically. Review fingerprints remain based
on original source WKB rather than fragment storage.

Validation retains 24 WorldCover COGs (1.18 GB) and 851 MB of indexes alongside
source evidence/WBM under ignored `input/geography/`. A second offline build
reproduces manifest and index bytes. These remain private validation artifacts.
Deployment still needs a durable release URI, authenticated release record,
backup/version ID and tested restore. No external store is provisioned or assumed.
