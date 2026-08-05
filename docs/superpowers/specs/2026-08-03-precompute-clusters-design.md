# Global spot clusters and region-safe publication

## Context

The data repository currently contains the published spot tiles. Its main
branch is at 16535cb (data: update france spots v2025.3 (2025)), with 161
France tile files and no clusters directory. The complete history from
53f3d2f through 16535cb contains no deleted spot file, so this inspection
found no already-lost tile to recover. The current repository also contains
224 spots outside the current France bbox, all in 11 non-empty N051... files
whose metadata identifies them as generated from France. The cause is
confirmed: the raster/minima sweep currently processes the input/margin
extent and does not clip candidates back to the region bbox. These are
unwanted out-of-scope artefacts, not published coverage. Because the app
loads spot tiles by identifier at high zoom, they are also a visible bug:
the N051 files expose spots over Wales, Ireland and nearby areas that were
never in the France region. They must be purged explicitly before the first
global cluster generation; the implementation will never aggregate unowned
tiles silently.

Two related changes are required:

1. Fix publication so processing region B cannot delete the tiles previously
   published for region A.
2. Generate six fixed-degree cluster levels from every spot in the resulting
   data repository.

The first change is a prerequisite for the second: clusters must aggregate
the complete repository, not the current region after an accidental purge.

## Goals

- Preserve the existing run.py --no-push contract: no data-repository clone,
  no network or Git mutation, and all artifacts are written below
  --output-dir.
- Provide local cluster artifacts for the offline validation loop without
  making them publishable.
- Publish global clusters automatically after current-region spots have been
  copied into the data-repository clone.
- Provide --no-clusters to skip cluster generation.
- Provide an autonomous --regenerate-clusters mode.
- Keep cluster memory proportional to the number of occupied cells, not the
  number of spots.
- Make level files deterministic and self-describing through
  clusters/index.json.
- Prevent a region publication from purging another region's tiles.
- Require region bboxes to align with whole one-degree tiles.
- Provide a read-only orphan audit and an explicit orphan purge for published
  data.

## Non-goals

- No client-side MapLibre clustering.
- No change to the existing spot tile schema or three-digit tile IDs.
- No region manifest is introduced.
- Raster candidate clipping is a separate correction. This change adds an
  ownership check so out-of-scope tiles cannot silently enter global
  clusters, but does not change the existing sweep yet.
- No automatic rounding or overlap repair is performed for region bboxes.

## Region-safe tile publication

### Region bbox contract

Every bbox in regions.yaml must contain four finite integer coordinates:
[lon_min, lat_min, lon_max, lat_max]. These coordinates are a deliberate
coverage decision, not a formatting conversion. The loader never rounds
silently; a non-integer coordinate is rejected during startup with an
explicit region and coordinate error.

The tile grid is one degree and uses half-open intervals. With integer bbox
bounds, every tile that intersects a region bbox is fully contained in it;
there is no partial tile at a region edge. This invariant is required for
publication: the owner of a tile can fill the whole tile without leaving a
border portion to another region.

At a physical border, choosing an integer boundary assigns a complete
one-degree band to one region. That region must have the raster and coverage
inputs needed to generate the whole band. A mechanical floor/ceil expansion
may be useful as a candidate, but it can incorrectly assign a band such as
51–52°N to France and legitimize the known N051 artefacts. Maintainers must
choose whether the band belongs to France, a neighboring region better placed
to cover it, or neither; the last choice is an intentional coverage hole that
must not contain published tiles. The 300 km ALR raster margin preserves
calculation continuity at edges and does not justify overlapping bboxes.

The complete regions.yaml must be a partition: two configured bboxes may
touch at an edge, but their intersection must not have both positive width
and positive height. Startup rejects an overlap with both region names.
The first-declaration ownership rule remains as a defensive guard for
legacy/preview inputs, but it cannot trigger in a valid published
configuration.

### Ownership calculation

The current region's candidate tile set is derived from its bbox in
regions.yaml; no repository state is added.

- Region bboxes use half-open intervals
  [lon_min, lon_max) x [lat_min, lat_max).
- A one-degree tile is the half-open square
  [tile_lon, tile_lon + 1) x [tile_lat, tile_lat + 1).
- A tile belongs to a region when the two rectangles overlap with strictly
  positive width and strictly positive height.
- Touching only at a bbox or tile edge is not an intersection.
- If a tile intersects several configured region bboxes, its unique owner is
  the first matching region in regions.yaml declaration order. Declaration
  order is therefore part of the publication contract: reordering or adding
  regions can intentionally migrate tile ownership and must be treated as a
  data migration. Valid configurations reject the overlap that would make
  this fallback necessary.
- A tile intersecting at least one configured bbox always has exactly one
  owner. A tile intersecting no configured bbox is an orphan and has no
  publication owner.

When copying a region's staged spots to the clone:

- stale files are purged only when their tile ID is owned by the current
  region;
- staged files are copied only when their tile ID is owned by the current
  region;
- if a legacy/preview input presents an overlapping tile, the declaration
  order selects one writer and the non-owner leaves it intact; production
  validation rejects this input before publication;
- an owned current-region tile whose new staging file has
  "spots": [] is copied normally, preserving the existing empty-tile
  replacement behavior;
- files outside the current region are left untouched.

This policy is deterministic because the configured bboxes form a validated
partition. With indivisible one-degree tile files, each owned tile is entirely
inside exactly one bbox and only that region writes it. The declaration-order
rule is retained only as a defensive fallback for legacy or preview inputs; it
must never resolve an overlap in a valid production configuration. Integer
bounds ensure an owner can fill its whole tile, and half-open intervals make
edge-only contact non-overlap. Spot-level merging is therefore unnecessary.

copy_spots_to_repo() receives the calculated owned tile IDs as a required
argument. It cannot fall back to the old repository-wide purge. The run
performs this scoped copy before invoking cluster generation.

### Orphan audit and explicit purge

An orphan is a JSON file in spots/ whose one-degree tile has no owner under
the complete regions.yaml registry. The audit reads files in sorted filename
order and reports each orphan tile ID with the number of spots in its
envelope, followed by the total. It changes no file and never commits or
pushes.

The standalone --list-orphans mode is always read-only. With
--data-repo-url it uses a temporary clone as its source and never calls
commit_and_push; without a URL it reads --output-dir/spots/ locally and
performs no clone or network operation. Thus the normal --no-push pipeline
keeps its existing no-clone/no-network behavior, even when a URL is present.
The temporary clone is an exception available only to the explicitly
requested standalone audit mode.

--prune-orphans is an explicit publication-only action. It requires a data
repository URL and push-enabled mode, cannot be combined with
--list-orphans, and is rejected in local --no-push mode. When the early audit
finds orphans in the temporary clone, the flag deletes exactly those tile
files, logs their IDs and spot counts, and includes the deletions in the
single publication commit. Without the flag, any orphan aborts the run before
raster processing. The flag never deletes a local output file.

regions.yaml is a permanent registry of published coverage. Removing,
renaming, or changing the bbox of a region that has already published tiles
can transfer tile ownership to another region or make tiles orphans, which
blocks future publication. --prune-orphans would delete newly orphaned tiles
only when explicitly requested. Every such configuration change is therefore
a data migration, not a harmless edit.

### Bbox migration preview

The standalone --preview-bbox-migration mode is the transition tool for a
legacy regions.yaml. It requires neither --year nor --region, never writes
regions.yaml or spot files, and never commits or pushes. It may inspect a
temporary clone when --data-repo-url is supplied; without a URL it reads
--output-dir/spots/ locally and performs no network operation.

This mode intentionally bypasses only the normal integer-bound and
non-overlap checks so that the existing decimal configuration can be
inspected. It still validates YAML structure, finite coordinates, and bbox
ordering. For each region it displays:

- the current decimal bbox;
- a proposed integer bbox calculated with floor for minima and ceil for
  maxima, explicitly labeled as a candidate rather than an automatic choice;
- any explicit candidate supplied as
  --bbox-candidate region=lon_min,lat_min,lon_max,lat_max, which replaces the
  floor/ceil candidate for that region only;
- the complete sorted tile set covered by that candidate;
- the published tiles newly assigned to the region, tiles it would cease to
  own, and the published tiles that would have no owner after the proposal.

The comparison computes the old owner from the current bboxes and the new
owner from the displayed integer candidates, using the same half-open
geometry and declaration-order fallback. It reports every published
transition old owner → new owner, plus proposed bbox overlap pairs. This makes
the 51–52°N ownership consequence visible before a maintainer edits the
configuration. The mode is rerun after deliberate integer bounds are chosen;
it does not apply its floor/ceil candidate.

For the current France migration, the deliberately chosen target is
[-6, 41, 8, 51]. It excludes the 51–52°N N051 band and the 8–9°E band,
leaving those complete tiles for future regions that can cover them. The
preview must show this target as an explicit candidate rather than replacing
it with floor/ceil expansion.

Normal --list-orphans remains subject to the production configuration
validation. Before legacy bboxes are corrected, --preview-bbox-migration is
the read-only mode that can inspect the repository and expose the orphan and
ownership consequences without being blocked by those checks.

### Regression coverage

The publication tests will cover:

1. publish region A, then region B: A's exclusive tile remains;
2. an exclusive current-region tile that becomes empty is overwritten with
   "spots": [];
3. a legacy/preview overlapping tile follows the declaration-order fallback,
   while the production loader rejects the overlap;
4. no stale tile outside the current bbox is removed.
5. a staging tile not owned by the current region is not copied to the
   repository;
6. an orphan tile is listed with its spot count without modification;
7. an orphan tile blocks publication without --prune-orphans;
8. --prune-orphans removes the orphan in the publication commit;
9. an integer-aligned bbox makes every intersecting tile fully contained;
10. a non-integer bbox is rejected during region loading.

## Cluster aggregation

### Levels

src/clusters.py owns the Python definitions and emits them into the
manifest; clients read the manifest instead of duplicating these constants:

| Level | Visible width (km, lower inclusive / upper exclusive) | Cell (degrees) |
|---|---|---:|
| 1 | 100–200 | 0.3 |
| 2 | 200–400 | 0.6 |
| 3 | 400–800 | 1.2 |
| 4 | 800–1600 | 2.4 |
| 5 | 1600–3200 | 4.8 |
| 6 | 3200–6400 | 9.6 |

For each input spot and each level:

~~~text
ix = floor(lon / cell_deg)
iy = floor(lat / cell_deg)
~~~

Coordinates are normalized with value + 0.0 before calculation and
accumulation so -0.0 cannot leak into IDs or numeric output. Negative
coordinates therefore use mathematical floor, not integer truncation.

The accumulator for a cell contains only:

- count;
- sum_lat, sum_lon;
- min_lat, min_lon, max_lat, max_lon;
- the best complete spot seen so far.

The representative is replaced when its darkness is higher, or when
darkness is equal and its id is lexicographically smaller. The representative
is copied with the complete spot schema, including altitude.

Input files are read in sorted filename order, one tile file at a time. A
tile's spot list is released before reading the next tile; all six level
accumulator maps remain live. Output clusters are sorted by cluster ID.

### Cluster object

Each occupied cell produces one object:

~~~json
{
  "id": "L4_1_20",
  "lat": 48.4123,
  "lon": 2.7891,
  "count": 37,
  "bbox": [2.40, 48.20, 3.05, 48.62],
  "rep": {
    "id": "48.4038_2.9021",
    "lat": 48.4038,
    "lon": 2.9021,
    "darkness": 0.87,
    "bortle": 3,
    "near": "Fontainebleau",
    "altitude": 120
  }
}
~~~

lat and lon are the arithmetic centroid of all members. bbox is ordered
[min_lon, min_lat, max_lon, max_lat]. A one-spot cell is still emitted as a
cluster with count: 1.

### Files and determinism

Every run writes all six files, including empty files:

~~~text
clusters/index.json
clusters/L1.json
clusters/L2.json
clusters/L3.json
clusters/L4.json
clusters/L5.json
clusters/L6.json
~~~

Each level file is a JSON array. An empty level is therefore [], never a
missing file. JSON objects use sorted keys, level entries are sorted by
cluster ID, and level files contain no timestamp. The only temporal field is
the UTC calendar date in the manifest; tests inject a fixed date when
asserting byte-for-byte determinism.

The manifest is:

~~~json
{
  "schema": 1,
  "generated": "2026-08-03",
  "data_year": 2025,
  "levels": [
    {
      "level": 1,
      "cell_deg": 0.3,
      "width_km": [100, 200],
      "files": [{
        "path": "clusters/L1.json",
        "hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
      }]
    }
  ]
}
~~~

files is always an array, with one entry today. Each entry contains the
relative path and the lowercase SHA-256 hash of the exact bytes written to
that level file. The generator must not assume one file per level so later
bbox-partitioned blocks can be added without changing the manifest shape. A
repository with no spot files produces the valid manifest plus six empty
level files. generated is a human-readable UTC calendar date. data_year is
indicative only: it records the CLI-selected source year for this
regeneration, not a guarantee that every region currently present in a
staggered repository was generated in that year. Neither field is the cache
identity; file hashes are.

## Execution modes and safety guards

### Normal run

- With --no-push, the pipeline does not clone the data repository. After
  writing output/spots/, it generates local test clusters at
  output/clusters-local/ from only the tiles owned by the current region.
  Staging files outside that ownership set, including N051 artefacts, are
  excluded so local validation follows the published copy path. These remain
  explicitly partial-region artifacts, not published data.
- With publication enabled, the pipeline first clones the repository and
  immediately audits its orphan tiles, before any raster processing. Without
  --prune-orphans, an orphan aborts the run at this point. With the explicit
  flag, the orphan files are removed from the temporary clone. The pipeline
  then computes the current-region spot output, performs the scoped copy
  above, repeats the orphan validation to catch a copy invariant violation,
  generates clusters from the clone's complete spots/ directory, and finally
  performs one commit/push containing spots and clusters.
- --no-clusters skips both published and local test cluster generation.
- A local cluster directory is never passed to repository-copy or
  commit/push code. The published write path requires a live data-repository
  clone, providing a code-level guard against publishing partial clusters.

### Autonomous regeneration

The existing flat CLI gains:

~~~bash
python run.py --regenerate-clusters --year 2025 \
  --data-repo-url https://github.com/mivek/darkskyspots-data.git
~~~

This mode skips raster processing and region selection. With a repository URL
and push enabled, it clones the repository, immediately audits orphans, and
either aborts or explicitly purges them with --prune-orphans. It then reads
all remaining published spots, regenerates all cluster files, performs the
final ownership validation, and commits/pushes them. With --no-push or
without a repository URL, it uses --output-dir/spots/ as a local test source,
writes only --output-dir/clusters-local/, and cannot invoke any Git publish
operation. Missing local spots in that mode is a clear error.

--regenerate-clusters requires --year for the indicative data_year field but
does not require --region. --list-orphans requires neither --year nor
--region. Normal raster runs continue to require both.

The transition preview is invoked as:

~~~bash
python run.py --preview-bbox-migration \
  --bbox-candidate france=-6,41,8,51 \
  --data-repo-url https://github.com/mivek/darkskyspots-data.git
~~~

It is read-only even without --no-push. With no repository URL it compares
against --output-dir/spots/ and remains fully local.

The parser preserves the current rule that --data-repo-url is optional under
--no-push and required for any operation that can publish.

--list-orphans is a standalone audit mode and --prune-orphans is rejected
unless a real data-repository clone can be published. Both options are
therefore guarded in the parser and again in the run code; a local
clusters-local directory can never become the source of a purge or commit.

## Verification

Unit and integration tests will verify:

- negative and zero-normalized cell indices;
- representative darkness and ID tie-breaking;
- centroid, bbox, and singleton cells;
- integer bbox validation and full-tile containment at region edges;
- rejection of positive-area bbox overlaps with both region names;
- legacy decimal bbox preview, proposed tile sets, and published ownership
  transitions, including explicit --bbox-candidate overrides;
- byte-identical level files and manifest structure;
- SHA-256 hashes in the manifest match the exact level-file bytes and change
  independently when only one level changes;
- six declared level files always exist, including empty levels;
- an empty spots repository still produces a valid six-level manifest;
- source files are read from the clone, never from local output in publish
  mode;
- local clusters exclude staging tiles outside the current region ownership
  set;
- copy-before-generation ordering, including the newly published region in
  generated clusters;
- the scoped tile purge regression suite described above;
- the out-of-scope N051 artefact guard;
- early orphan validation immediately after clone, before raster work;
- CLI validation for normal, local --no-push, orphan audit/purge, and
  autonomous modes, including the no-clone guarantee for a normal --no-push
  run even when a repository URL is supplied;
- read-only bbox migration preview, including the guarantee that its
  floor/ceil candidate is never written.

The generation guide (README.md) will document the published-data dependency,
the local-test artifact path, the autonomous command, and the need to
regenerate only after the repository's spot tiles are complete. It will also
state that region bboxes must be chosen deliberately on integer degrees,
that a complete border band belongs to one region, that adjacent bboxes must
not overlap, that the ALR margin is distinct from bbox coverage, that
regions.yaml is a permanent registry, and show --list-orphans,
--prune-orphans, and --preview-bbox-migration. The indivisible-tile
limitation will include N041E008 as a concrete case: it spans southern
Corsica and northern Sardinia, so France or a future Italy region must own
and cover the entire tile; no sub-tile merge is attempted.
