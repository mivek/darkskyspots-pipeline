# Global Spot Clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Generate deterministic six-level global spot clusters from the complete published data repository while preserving scoped region publication and a faithful offline validation loop.

**Architecture:** Keep geometry and region ownership in src/regions.py, aggregation and manifest serialization in a new src/clusters.py, and scoped repository mutation/orphan cleanup in src/publish.py. run.py will dispatch the normal pipeline, local test generation, published regeneration, orphan audit, and bbox migration preview while keeping local artifacts outside every Git publish path.

**Tech Stack:** Python 3.10+, standard library (json, hashlib, math, argparse, pathlib), existing PyYAML/rasterio stack, and plain pytest.

## Global Constraints

- Published clusters read only from the cloned data repository’s spots/ directory.
- Normal --no-push keeps its current no-clone/no-network behavior, even if a URL is supplied.
- Local clusters are written only below --output-dir/clusters-local/ and read only tiles owned by the current region.
- copy_spots_to_repo() receives the current region’s owned tile IDs as a required argument and never performs a repository-wide purge.
- Region bboxes are finite integer coordinates and form a positive-area non-overlapping partition; normal loading rejects violations.
- --preview-bbox-migration is the only transition mode allowed to inspect legacy decimal/overlapping bboxes.
- The published workflow audits or purges orphans immediately after clone, repeats the audit after copy, then generates clusters and makes one commit/push.
- Clusters use the six fixed levels from the spec, mathematical floor, normalized zero, constant-memory cell accumulators, sorted input files, sorted cluster IDs, and complete representative spots.
- Level files contain no timestamp; manifest generated is a UTC date, data_year is indicative, and each manifest file entry contains a SHA-256 hash of exact bytes.
- Do not overwrite the user-owned regions.yaml migration in the implementation. The approved France candidate is [-6, 41, 8, 51]; the operator applies it after reviewing the preview and resolves calibration-region overlaps.

---

## File map

- Create: src/clusters.py — level definitions, streaming aggregation, deterministic level/index writers.
- Modify: src/regions.py — strict bbox validation, partition/ownership helpers, legacy preview calculations.
- Modify: src/publish.py — scoped tile copy, orphan scan/purge, merged-envelope version comparison.
- Modify: src/cli.py — normal, regeneration, audit, preview, prune, and cluster flags with mode validation.
- Modify: run.py — early clone audit, filtered local clusters, copy-before-generation ordering, autonomous modes.
- Modify: README.md — generation guide, bbox coverage contract, migration/audit commands, clusters output.
- Modify: tests/unit/test_regions.py — bbox and ownership tests.
- Create: tests/unit/test_clusters.py — aggregation, determinism, empty levels, hashes, and local filtering.
- Modify: tests/unit/test_publish.py — scoped copy, non-reintroduction, orphan scan/purge.
- Modify: tests/unit/test_cli.py — mode and candidate option validation.
- Modify: tests/unit/test_run.py — orchestration order, local filter, early audit, and publish safety.
- Create: tests/integration/test_clusters_publish.py — copy-before-generation and region preservation coverage.

Test helpers used by the new unit modules must be concrete and local to the
test files: write_regions(tmp_path, bboxes) writes complete YAML entries,
write_tile(directory, tile_id, spots) writes a valid tile envelope, spot(id,
lat, lon, darkness, altitude)
returns a complete spot dictionary including id/lat/lon/darkness/bortle/near/
altitude, and write_envelope(path, tile_id, spots) writes a JSON envelope.
The preview tests use the same write_regions helper with decimal calibration
bboxes and a published tile-count mapping.

## Task 1: Strict region geometry and ownership primitives

Files:
- Modify: src/regions.py
- Test: tests/unit/test_regions.py

Interfaces:

~~~python
def load_regions(
    path: str = "regions.yaml",
    *,
    allow_legacy_geometry: bool = False,
    validate_partition: bool = True,
) -> dict[str, dict]:

def validate_bbox_partition(regions: dict[str, dict]) -> None:

def tile_intersects_bbox(
    tile_id_str: str,
    bbox: tuple[float, float, float, float],
) -> bool:

def owner_for_tile(tile_id_str: str, regions: dict[str, dict]) -> str | None:

def owned_tile_ids(
    region_name: str,
    regions: dict[str, dict],
    tile_size_deg: float = 1.0,
) -> set[str]:
~~~

- load_regions() keeps YAML declaration order, validates required fields, finite coordinates, coordinate ordering, and integer coordinates unless allow_legacy_geometry=True.
- With strict loading, validate_bbox_partition() raises ValueError naming both regions when width and height of a pairwise intersection are both positive; edge contact is allowed.
- tile_intersects_bbox() uses half-open one-degree tile rectangles and strictly positive intersection.
- owner_for_tile() returns the first declaration-order match for defensive legacy/preview use; strict production configuration cannot have multiple matches.
- owned_tile_ids() enumerates only tiles fully covered by a strict integer bbox and rejects a non-1° tile size with ValueError.

- [ ] Step 1: Write failing tests for strict geometry.

~~~python
def test_load_regions_rejects_non_integer_bbox(tmp_path):
    path = tmp_path / "regions.yaml"
    path.write_text(
        "alpha:\n"
        "  bbox: [1.2, 2, 3, 4]\n"
        "  equal_area_epsg: 3035\n"
        "  admin_level: 8\n"
        "  osm_country_code: AA\n"
    )
    with pytest.raises(ValueError, match="alpha.*integer"):
        load_regions(str(path))


def test_load_regions_rejects_positive_area_overlap_with_names(tmp_path):
    path = write_regions(
        tmp_path,
        {
            "france": [-6, 41, 8, 51],
            "italy": [7, 40, 12, 47],
        },
    )
    with pytest.raises(ValueError, match="france.*italy|italy.*france"):
        load_regions(str(path))


def test_integer_bbox_contains_every_intersecting_tile(tmp_path):
    path = write_regions(tmp_path, {"france": [-6, 41, 8, 51]})
    regions = load_regions(str(path))
    tiles = owned_tile_ids("france", regions)
    assert "N041W006" in tiles
    assert "N050E007" in tiles
    assert "N051E000" not in tiles
    assert "N040E000" not in tiles
~~~

- [ ] Step 2: Run the focused tests and verify they fail for the missing integer/partition behavior.

Run: pytest tests/unit/test_regions.py -q

Expected: failures for non-integer acceptance, overlap acceptance, and missing ownership helpers.

- [ ] Step 3: Implement strict validation and half-open ownership.

Use math.isfinite() and float(value).is_integer() for each bbox coordinate, preserve the original numeric values, and report the coordinate index in a non-integer error. Compute pairwise overlap with:

~~~python
overlap_width = min(a[2], b[2]) - max(a[0], b[0])
overlap_height = min(a[3], b[3]) - max(a[1], b[1])
if overlap_width > 0 and overlap_height > 0:
    raise ValueError(f"Regions '{left_name}' and '{right_name}' overlap")
~~~

Use tile_bounds() from src/tile_id with its documented (lat_min, lon_min, lat_max, lon_max) order, or an equivalent parser, and keep all ownership decisions in one helper.

- [ ] Step 4: Add legacy fallback tests and make them pass.

~~~python
def test_legacy_owner_uses_declaration_order_without_strict_validation(tmp_path):
    path = write_regions(
        tmp_path,
        {
            "first": [0, 0, 2, 2],
            "second": [1, 1, 3, 3],
        },
    )
    regions = load_regions(
        str(path),
        allow_legacy_geometry=True,
        validate_partition=False,
    )
    assert owner_for_tile("N001E001", regions) == "first"
~~~

- [ ] Step 5: Run pytest tests/unit/test_regions.py -q and commit.

Commit: feat: validate region partitions and tile ownership

## Task 2: Streaming cluster aggregation and deterministic files

Files:
- Create: src/clusters.py
- Test: tests/unit/test_clusters.py

Interfaces:

~~~python
@dataclass(frozen=True)
class LevelSpec:
    level: int
    cell_deg: float
    width_km: tuple[int, int]


LEVELS: tuple[
    LevelSpec, LevelSpec, LevelSpec, LevelSpec, LevelSpec, LevelSpec
]

def aggregate_spot_files(
    spots_dir: str | Path,
    allowed_tile_ids: Collection[str] | None = None,
) -> dict[int, list[dict]]:

def write_cluster_files(
    spots_dir: str | Path,
    clusters_dir: str | Path,
    *,
    data_year: int,
    generated: str,
    allowed_tile_ids: Collection[str] | None = None,
) -> dict[int, Path]:
~~~

- Define the six level records in Python with (level, cell_deg, width_km), and build the manifest from those records.
- aggregate_spot_files() scans sorted(Path(spots_dir).glob("*.json")); when allowed_tile_ids is supplied, it skips every file whose stem is not in the set before opening it.
- Keep six maps of cell accumulators live, but load and release one tile envelope at a time. Each accumulator stores only the required count/sums/bounds/representative fields.
- Normalize coordinates with value + 0.0 before floor() and output; form IDs as L{level}_{ix}_{iy}.
- Replace the representative on higher darkness, or equal darkness with lexicographically smaller id, copying the entire spot dictionary including altitude.
- Write L1.json through L6.json as JSON arrays, including [] for empty levels. Use one deterministic JSON policy (sort_keys=True, fixed indentation/separators, UTF-8, final newline) for every level and index.json.
- After each exact level byte sequence is written, calculate lowercase hashlib.sha256(bytes).hexdigest() and place it beside the relative path in the one-element files array.
- The manifest contains schema, supplied generated, supplied data_year, and all six level records. It is the only file with a date.

- [ ] Step 1: Add failing core tests.

~~~python
def test_negative_coordinates_use_floor_and_normalize_zero(tmp_path):
    write_tile(tmp_path, "S001W001", [spot("a", lat=-0.0, lon=-0.0, darkness=0.5)])
    levels = aggregate_spot_files(tmp_path)
    assert any(cluster["id"].startswith("L1_0_0") for cluster in levels[1])
    assert all("-0.0" not in json.dumps(cluster) for cluster in levels[1])


def test_representative_uses_darkness_then_id(tmp_path):
    write_tile(
        tmp_path,
        "N048E002",
        [
            spot("z", lat=48.1, lon=2.1, darkness=0.9, altitude=100),
            spot("a", lat=48.2, lon=2.2, darkness=0.9, altitude=120),
        ],
    )
    cluster = aggregate_spot_files(tmp_path)[1][0]
    assert cluster["rep"]["id"] == "a"
    assert cluster["rep"]["altitude"] == 120


def test_centroid_bbox_and_singleton(tmp_path):
    write_tile(
        tmp_path,
        "N048E002",
        [
            spot("a", 48.2, 2.4, 0.8),
            spot("b", 48.25, 2.45, 0.7),
        ],
    )
    write_tile(tmp_path, "N048E003", [spot("singleton", 48.6, 3.0, 0.7)])
    clusters = aggregate_spot_files(tmp_path)
    cluster = next(c for c in clusters[1] if c["count"] == 2)
    assert cluster["lat"] == pytest.approx(48.225)
    assert cluster["lon"] == pytest.approx(2.425)
    assert cluster["bbox"] == [2.4, 48.2, 2.45, 48.25]
    assert any(c["count"] == 1 for c in clusters[1])
~~~

- [ ] Step 2: Run pytest tests/unit/test_clusters.py -q and verify the new module/tests fail.

- [ ] Step 3: Implement the six accumulators and representative selection with constant spot memory.

- [ ] Step 4: Add failing serialization tests for empty levels, deterministic bytes, and hashes.

~~~python
def test_empty_input_writes_six_files_and_manifest(tmp_path):
    output = tmp_path / "clusters"
    write_cluster_files(tmp_path / "spots", output, data_year=2025, generated="2026-08-03")
    assert [json.loads((output / f"L{i}.json").read_text()) for i in range(1, 7)] == [[], [], [], [], [], []]
    manifest = json.loads((output / "index.json").read_text())
    assert len(manifest["levels"]) == 6
    assert all(len(level["files"]) == 1 for level in manifest["levels"])


def test_repeated_generation_is_byte_identical(tmp_path):
    spots_dir = make_known_spots(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_cluster_files(spots_dir, first, data_year=2025, generated="2026-08-03")
    write_cluster_files(spots_dir, second, data_year=2025, generated="2026-08-03")
    for name in ["index.json", *[f"L{i}.json" for i in range(1, 7)]]:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_manifest_hash_matches_exact_level_bytes(tmp_path):
    output = tmp_path / "clusters"
    write_cluster_files(make_known_spots(tmp_path), output, data_year=2025, generated="2026-08-03")
    manifest = json.loads((output / "index.json").read_text())
    for level in manifest["levels"]:
        entry = level["files"][0]
        payload = (tmp_path / entry["path"]).read_bytes()
        assert entry["hash"] == hashlib.sha256(payload).hexdigest()
~~~

- [ ] Step 5: Implement deterministic JSON writing and manifest hash generation; run the focused tests.

Run: pytest tests/unit/test_clusters.py -q

Expected: all aggregation, empty-level, determinism, and hash tests pass.

- [ ] Step 6: Add and pass the local ownership filter test.

~~~python
def test_local_cluster_aggregation_skips_unowned_staging_tile(tmp_path):
    write_tile(tmp_path, "N048E002", [spot("inside", 48.2, 2.2, 0.8)])
    write_tile(tmp_path, "N051E000", [spot("outside", 51.5, 0.2, 0.99)])
    clusters = aggregate_spot_files(tmp_path, allowed_tile_ids={"N048E002"})
    ids = {cluster["rep"]["id"] for values in clusters.values() for cluster in values}
    assert ids == {"inside"}
~~~

- [ ] Step 7: Run pytest tests/unit/test_clusters.py -q and commit.

Commit: feat: add deterministic streaming spot clusters

## Task 3: Scoped publication and orphan lifecycle

Files:
- Modify: src/publish.py
- Test: tests/unit/test_publish.py

Interfaces:

~~~python
def copy_spots_to_repo(
    local_spots_dir: str,
    data_repo_dir: str,
    owned_tile_ids: Collection[str],
) -> None:

def scan_orphan_tiles(
    spots_dir: str | Path,
    regions: dict[str, dict],
) -> dict[str, int]:

def prune_orphan_tiles(
    spots_dir: str | Path,
    regions: dict[str, dict],
) -> dict[str, int]:
~~~

- copy_spots_to_repo() copies only source JSON files whose stem is in owned_tile_ids, removes stale destination JSON files only when their stem is in that set, and leaves every non-owned destination file byte-for-byte untouched.
- Update the existing copy test to use valid tile IDs and pass the required set; no default argument may preserve the old repository-wide purge.
- scan_orphan_tiles() returns sorted tile IDs mapped to len(envelope["spots"]), including zero for empty orphan files, and does not mutate.
- prune_orphan_tiles() calls the same scan, unlinks exactly those files, returns the pre-delete counts, and is invoked only against the temporary clone by run.py.
- Build the version comparison input as preserved_old_non_owned + new_owned, so preserved regions do not look deleted when computing the next version.

- [ ] Step 1: Update tests to call the required scoped-copy signature and add failing non-reintroduction coverage.

~~~python
def test_copy_does_not_reintroduce_non_owned_staging_tile(tmp_path):
    source = tmp_path / "staging"
    source.mkdir()
    (source / "N050E001.json").write_text('{"tile":"N050E001","spots":[]}')
    (source / "N051E000.json").write_text('{"tile":"N051E000","spots":[{"id":"ghost"}]}')
    repo = tmp_path / "repo"
    (repo / "spots").mkdir(parents=True)
    copy_spots_to_repo(str(source), str(repo), {"N050E001"})
    assert (repo / "spots" / "N050E001.json").exists()
    assert not (repo / "spots" / "N051E000.json").exists()
~~~

- [ ] Step 2: Run pytest tests/unit/test_publish.py -q and verify the old two-argument API/test fails.

- [ ] Step 3: Implement scoped copy and preserve non-owned destination files.

- [ ] Step 4: Add orphan scan/purge tests and make them pass.

~~~python
def test_scan_orphans_reports_counts_without_mutating(tmp_path):
    spots = tmp_path / "spots"
    spots.mkdir()
    write_envelope(spots / "N051E000.json", "N051E000", [{"id": "a"}, {"id": "b"}])
    regions = {"france": {"bbox": [-6, 41, 8, 51]}}
    assert scan_orphan_tiles(spots, regions) == {"N051E000": 2}
    assert (spots / "N051E000.json").exists()


def test_prune_orphans_deletes_only_orphans(tmp_path):
    spots = tmp_path / "spots"
    spots.mkdir()
    write_envelope(spots / "N051E000.json", "N051E000", [{"id": "a"}])
    write_envelope(spots / "N048E002.json", "N048E002", [])
    regions = {"france": {"bbox": [-6, 41, 8, 51]}}
    assert prune_orphan_tiles(spots, regions) == {"N051E000": 1}
    assert not (spots / "N051E000.json").exists()
    assert (spots / "N048E002.json").exists()
~~~

- [ ] Step 5: Add the merged-envelope version regression test, run the focused file, and commit.

Commit: fix: scope tile publication and orphan cleanup

## Task 4: CLI modes and bbox migration preview

Files:
- Modify: src/cli.py
- Modify: src/regions.py
- Test: tests/unit/test_cli.py
- Test: tests/unit/test_regions.py

Interfaces:

~~~python
def parse_bbox_candidate(value: str) -> tuple[str, tuple[float, float, float, float]]:

def build_bbox_migration_preview(
    regions: dict[str, dict],
    published_tile_counts: Mapping[str, int],
    candidates: Mapping[str, tuple[float, float, float, float]] | None = None,
) -> dict:

def format_bbox_migration_preview(preview: dict) -> str:
~~~

- Add mutually exclusive mode flags: --regenerate-clusters, --list-orphans, and --preview-bbox-migration.
- Add --no-clusters, --prune-orphans, and repeatable --bbox-candidate NAME=MIN_LON,MIN_LAT,MAX_LON,MAX_LAT.
- Normal mode requires --year, --region, and a data URL unless --no-push.
- Regeneration requires --year but not --region; published mode requires a URL unless --no-push.
- Audit/preview require neither year nor region. They never push. A URL requests a temporary read-only clone; without one they read local output.
- --prune-orphans is rejected with --no-push, no URL, audit, or preview modes.
- A normal run with --no-push and a URL still follows the old no-clone path.
- --bbox-candidate validates exactly four finite numbers and stores candidates by region; unknown region names and duplicate candidates are parser errors.
- Preview loads legacy decimal/overlapping bboxes with allow_legacy_geometry=True, computes floor/ceil candidates for unspecified regions, substitutes explicit candidates, reports proposed overlap pairs, full sorted tile sets, and every published old-owner → new-owner transition.

- [ ] Step 1: Add failing parser tests.

~~~python
def test_parser_preview_accepts_explicit_bbox_candidate():
    args = parse_args([
        "--preview-bbox-migration",
        "--bbox-candidate", "france=-6,41,8,51",
    ])
    assert args.bbox_candidates == {"france": (-6.0, 41.0, 8.0, 51.0)}


def test_parser_prune_requires_publishing():
    with pytest.raises(SystemExit):
        parse_args([
            "--year", "2025", "--region", "france", "--no-push",
            "--prune-orphans",
        ])


def test_parser_regeneration_does_not_require_region():
    args = parse_args(["--regenerate-clusters", "--year", "2025", "--no-push"])
    assert args.region is None
~~~

- [ ] Step 2: Run pytest tests/unit/test_cli.py -q and verify mode parsing fails.

- [ ] Step 3: Implement mode-aware argparse validation without changing normal no-push behavior.

- [ ] Step 4: Add failing preview tests using a decimal/overlapping temporary YAML and published tile envelopes.

~~~python
def test_preview_reports_candidate_and_ownership_transition(tmp_path):
    regions = legacy_regions_with_calibration_overlap(tmp_path)
    published = {"N051E000": 30, "N050E008": 0, "N050E007": 12}
    preview = build_bbox_migration_preview(
        regions,
        published,
        {"france": (-6, 41, 8, 51)},
    )
    assert preview["proposed"]["france"]["bbox"] == [-6, 41, 8, 51]
    assert "N051E000" in preview["proposed"]["orphans"]
    assert preview["transitions"][("france", None)] == ["N050E008"]
    assert ("france", "massif_central") in preview["overlaps"]
~~~

- [ ] Step 5: Implement preview calculation/formatting and test that it never writes YAML or spot files.

- [ ] Step 6: Run pytest tests/unit/test_cli.py tests/unit/test_regions.py -q and commit.

Commit: feat: add bbox migration preview and CLI modes

## Task 5: Normal orchestration, local filter, and early publication audit

Files:
- Modify: run.py
- Modify: tests/unit/test_run.py
- Create: tests/integration/test_clusters_publish.py

Interfaces: run(args), run_regenerate_clusters(args),
run_list_orphans(args), and run_preview_bbox_migration(args) each return an
integer status code. The standalone mode helpers receive the parsed argparse
namespace and are called before raster input validation.

- Dispatch standalone modes before input/raster processing.
- For a normal publishing run: validate strict regions, check the input path, clone, immediately scan orphans, prune only when args.prune_orphans, then load old envelopes and begin raster work.
- Keep staging output behavior intact, but calculate owned_ids = owned_tile_ids(args.region, regions) once and pass it both to copy_spots_to_repo() and to local generation as write_cluster_files(output_dir / "spots", output_dir / "clusters-local", data_year=args.year, generated=generated_date, allowed_tile_ids=owned_ids).
- For local --no-push, never create/use a data clone and write only output/clusters-local/; the cluster reader must skip staging files such as N051E000.json.
- For publishing, copy current owned files into the clone, scan for orphans again, generate global clusters from clone/spots/ into clone/clusters/, then commit/push once. A second audit failure aborts before commit.
- Use a merged old/new envelope map for version calculation so untouched regions do not appear removed.
- --no-clusters skips both local and published generation but does not disable scoped spot copy or explicit orphan purge.
- In published --regenerate-clusters, clone → early audit/prune → generate all clusters → final audit → one commit/push. In local regeneration, use the union of all strict region-owned tiles as the filter and write only clusters-local/.
- --list-orphans clones only for an explicitly supplied URL, reports sorted IDs/counts, and never calls commit_and_push.
- --preview-bbox-migration uses legacy loader and --bbox-candidate france=-6,41,8,51 without writing any file.
- Keep the local output path out of every repository-copy and commit call as a code-level guard.

- [ ] Step 1: Add failing orchestration tests for call order.

Extend the existing tracker pattern in tests/unit/test_run.py: patch
clone_data_repo to append "clone", scan_orphan_tiles to append "audit" and
return an empty mapping, and slice_and_compute to append "raster" and return
a MagicMock with data, transform, and crs attributes. Patch every remaining
raster/export/publish collaborator with the already-used empty return values.
Assert events.index("audit") < events.index("raster").

- [ ] Step 2: Add the local staging filter test.

Create output/spots/N048E002.json and output/spots/N051E000.json, patch the raster steps to produce those staging files, run with --no-push, and assert the generated local cluster representative IDs contain the first spot but never the N051 spot.

- [ ] Step 3: Run pytest tests/unit/test_run.py -q and verify the new ordering/filter tests fail.

- [ ] Step 4: Implement dispatch, early audit/prune, owned-ID propagation, second audit, and local/global cluster paths.

- [ ] Step 5: Add the copy-before-generation integration test.

Populate a fake clone with N048E002.json, put a newly generated N048E003.json in local staging, patch clone_data_repo() to use the fake clone, and have the patched write_cluster_files() read the clone’s spots/ directory. Assert that N048E003 is present when generation is called and that its spot ID occurs in the generated input. This test must fail if generation moves before copy_spots_to_repo().

- [ ] Step 6: Add the A-then-B preservation test and the explicit prune commit test, run focused orchestration/integration tests, and commit.

Commit: feat: integrate scoped clusters into pipeline publication

## Task 6: Generation guide and CLI documentation

Files:
- Modify: README.md

- [ ] Step 1: Document the new flags and mode requirements.

Add --no-clusters, --regenerate-clusters, --list-orphans, --prune-orphans, --preview-bbox-migration, and repeatable --bbox-candidate to the CLI table. Include the exact France preview command:

~~~bash
python run.py --preview-bbox-migration \
  --bbox-candidate france=-6,41,8,51 \
  --data-repo-url https://github.com/mivek/darkskyspots-data.git
~~~

- [ ] Step 2: Document the data-repository dependency and ordering.

State that published clusters read the complete cloned repository, that current-region staging is copied before cluster generation, and that normal --no-push remains local-only while clusters-local/ filters to owned tiles.

- [ ] Step 3: Document the bbox contract and migration consequences.

State that integer bboxes are deliberate coverage decisions, adjacent bboxes may touch but may not overlap, the 300 km ALR margin is not coverage, and removing/renaming/changing a published region is a data migration. Explain that a complete border tile is assigned to one region and use N041E008 (southern Corsica/northern Sardinia) as the indivisible-tile example.

- [ ] Step 4: Document orphan audit and purge safety.

Explain read-only --list-orphans, explicit publication-only --prune-orphans, the 11 N051 files and their 224 spots, and the additional empty E008/E009 tiles exposed by the approved France bbox. State that the first audit happens immediately after clone and that purge is included in the one publication commit.

- [ ] Step 5: Document cluster files and cache identity.

Describe clusters/index.json, L1.json–L6.json, empty levels, files-as-array, per-file SHA-256 hashes, deterministic bytes, generated date, and indicative data_year.

- [ ] Step 6: Run git diff --check and commit.

Commit: docs: document cluster generation and bbox migrations

## Task 7: Full verification and handoff

Files:
- Test changes from Tasks 1–5
- No configuration migration in regions.yaml

- [ ] Step 1: Run the focused unit suite.

Run:

~~~bash
pytest tests/unit/test_regions.py tests/unit/test_clusters.py \
  tests/unit/test_publish.py tests/unit/test_cli.py tests/unit/test_run.py -q
~~~

Expected: all focused tests pass, including negative indices, representative ties, singleton/empty levels, exact hashes, scoped copy, early audit, local filtering, and mode validation.

- [ ] Step 2: Run integration tests.

Run: pytest tests/integration/test_smoke.py tests/integration/test_clusters_publish.py -q

Expected: copy-before-generation includes the newly published region and a
subsequent region run preserves prior tiles.

- [ ] Step 3: Run the complete suite and diff checks.

Run:

~~~bash
pytest
git diff --check
git status --short
~~~

Expected: the complete pytest suite passes; only intentional feature files are modified; no generated cluster artifacts or user configuration are committed.

- [ ] Step 4: Verify the preview result against the remote state before handoff.

Run the read-only command with --bbox-candidate france=-6,41,8,51 and confirm:

- 11 N051 tile files remain orphaned, containing 224 spots;
- 20 empty E008/E009 files from N041 through N050 become orphaned under the new eastern boundary;
- 130 published tiles remain owned by France and contain 2,689 spots;
- no published tile becomes newly owned;
- remaining configured calibration bboxes are reported as overlaps and must be resolved before strict production loading.

- [ ] Step 5: Confirm there are no uncommitted generated artifacts or configuration edits, then hand off the worktree and the exact migration prerequisites.

The handoff must explicitly state that the operator still needs to edit regions.yaml to the approved integer France bbox and resolve/remove overlapping calibration entries before running a strict production command.
