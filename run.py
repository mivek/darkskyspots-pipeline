#!/usr/bin/env python3
"""
Dark Sky Spots Pipeline — orchestrator entrypoint.

Usage:
    python run.py --year 2025 --region france --data-repo-url git@github.com:user/data-repo.git
"""
import json
import logging
import sys
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import rasterio

from src.cli import parse_args
from src.config import (
    COVERAGE_RADIUS_KM,
    MIN_SPOTS_PER_AREA,
    MESH_KM,
    REDUNDANCY_KM,
    TILE_SIZE_DEG,
)
from src.coverage import attach_near_town, ensure_coverage, filter_sea_spots, load_places
from src.enrich import enrich_all
from src.extract import mesh_minima, redundancy_filter
from src.alr import slice_and_compute
from src.convert import alr_to_bortle, alr_to_darkness
from src.publish import (
    clone_data_repo,
    commit_and_push,
    compute_new_version,
    copy_spots_to_repo,
    merge_tile_envelopes,
    audit_country_spots,
    migrate_country_tags,
)
from src.clusters import write_cluster_files
from src.regions import get_region, load_regions
from src.geography import classify_candidates
from src.tile_export import (
    classify_spots_into_tiles,
    enumerate_tiles_in_bbox,
    write_tile_file,
)

logger = logging.getLogger("pipeline")


def _generated_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def run(args, current_owned_tile_ids: set[str] | None = None) -> int:
    """Execute the 7-step pipeline. Returns 0 on success, 1 on error."""
    try:
        region = get_region(args.region)
        logger.info("Region: %s (%s)", region["name"], args.region)

        input_path = Path(args.input_dir) / args.region / f"{args.year}.tif"
        if not input_path.exists():
            logger.error("Input not found: %s", input_path)
            return 1

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Bboxes remain raster/GeoNames envelopes only.  They do not confer
        # ownership of any tile; country clipping decides what is publishable.
        all_tile_ids = sorted(enumerate_tiles_in_bbox(tuple(region["bbox"]), TILE_SIZE_DEG))
        country_codes = region["osm_country_code"]
        if isinstance(country_codes, str):
            country_codes = [country_codes]

        # Step 0: Radiance -> ALR (returns data + geo metadata)
        logger.info("Step 0: Radiance -> ALR")
        slice_result = slice_and_compute(
            str(input_path),
            region["equal_area_epsg"],
            budget_mb=args.budget_mb,
        )
        alr_data = slice_result.data
        transform = slice_result.transform
        crs = slice_result.crs

        # Step 1: ALR -> darkness / Bortle
        logger.info("Step 1: ALR -> darkness / Bortle")
        darkness = alr_to_darkness(alr_data)
        bortle = alr_to_bortle(alr_data)

        if getattr(args, "debug_raster", False):
            darkness_path = output_dir / f"debug_darkness_{args.region}_{args.year}.tif"
            bortle_path = output_dir / f"debug_bortle_{args.region}_{args.year}.tif"
            profile = {
                "driver": "GTiff",
                "height": darkness.shape[0],
                "width": darkness.shape[1],
                "count": 1,
                "dtype": "float32",
                "crs": crs,
                "transform": transform,
            }
            with rasterio.open(darkness_path, "w", **profile) as dst:
                dst.write(darkness.astype("float32"), 1)
            with rasterio.open(bortle_path, "w", **profile) as dst:
                dst.write(bortle.astype("float32"), 1)
            logger.info("Debug rasters written to %s", output_dir)

        # Step 2: Mesh scan (local minima per cell)
        logger.info("Step 2: Mesh scan (local minima)")
        candidates = mesh_minima(darkness, transform, MESH_KM)
        logger.info("  Found %d candidate spots", len(candidates))

        # Step 2b: Attach bortle (and re-attach definitive darkness) to each candidate.
        # Without this, the redundancy filter degenerates because cand.get("bortle")
        # is None for every candidate and "keep nearby spots with different bortle"
        # never fires.
        for cand in candidates:
            r, c = int(cand["row"]), int(cand["col"])
            cand["bortle"] = int(bortle[r, c])
            cand["darkness"] = float(darkness[r, c])
        logger.info("  Attached bortle to %d candidates", len(candidates))

        # The ALR margin is retained above for radiance context, but candidates
        # are clipped before redundancy so an unpublished foreign/sea minimum
        # can never suppress a published candidate.
        logger.info("Step 2c: Natural Earth land mask and country clip")
        candidates, geography_stats = classify_candidates(
            candidates, country_codes, transform=transform, crs=crs
        )
        logger.info("  Geographic candidate stats: %s", geography_stats)

        # Step 3: Redundancy filter
        logger.info("Step 3: Redundancy filter")
        filtered = redundancy_filter(candidates, REDUNDANCY_KM)
        logger.info("  After redundancy filter: %d spots", len(filtered))

        # Step 4: Coverage guarantee via GeoNames places
        logger.info("Step 4: Coverage guarantee via GeoNames places")
        communes = load_places(region)
        logger.info("  Loaded %d localities (GeoNames)", len(communes))
        covered = ensure_coverage(
            filtered, candidates, communes, MIN_SPOTS_PER_AREA, COVERAGE_RADIUS_KM
        )
        logger.info("  After coverage guarantee: %d spots", len(covered))

        # Step 4b: Attach the nearest commune name as the "near" field.
        covered = attach_near_town(covered, communes)
        logger.info("  Attached nearest commune to %d spots", len(covered))

        # Step 5: Enrichment (id, near, altitude)
        logger.info("Step 5: Enrichment (id, near, altitude)")
        enriched = enrich_all(covered)
        logger.info("  Enriched %d spots", len(enriched))

        # Step 5b: Filter out sea spots (no nearby commune).
        # Limitation: this is a Western-Europe proxy based on the 25 km GeoNames
        # commune radius. When expanding beyond Western Europe, replace with a
        # Natural Earth coastline land/sea mask.
        enriched = filter_sea_spots(enriched)
        logger.info("  After sea-spot filter: %d spots", len(enriched))

        # Step 6: Tile export + version
        logger.info("Step 6: Tile export")
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        source = f"VIIRS/{args.year}/{args.region}"

        tiles_dict = classify_spots_into_tiles(enriched, TILE_SIZE_DEG)
        occupied_tile_ids = set(tiles_dict.keys())

        empty_tile_ids = [tid for tid in all_tile_ids if tid not in occupied_tile_ids]

        placeholder_version = f"{args.year}.0"
        new_envelopes: dict[str, dict] = {}
        for tile_id_str, spots in tiles_dict.items():
            new_envelopes[tile_id_str] = {
                "version": placeholder_version,
                "source": source,
                "generated": generated,
                "tile": tile_id_str,
                "spots": spots,
            }
        for tid in empty_tile_ids:
            new_envelopes[tid] = {
                "version": placeholder_version,
                "source": source,
                "generated": generated,
                "tile": tid,
                "spots": [],
            }

        # The data repo clone must stay alive through step 7 (publish),
        # so all git operations live inside the same with block.
        with _publication_clone_context() as clone_ctx:
            data_repo_dir = Path(clone_ctx)
            old_envelopes: dict[str, dict] = _PUBLISHED_OLD_ENVELOPES.copy()

            if not getattr(args, "no_push", False):
                if _PUBLISHED_CLONE is None:
                    clone_data_repo(args.data_repo_url, args.data_repo_branch, str(data_repo_dir))
                old_spots_dir = data_repo_dir / "spots"
                if old_spots_dir.exists():
                    for json_path in old_spots_dir.glob("*.json"):
                        with open(json_path) as f:
                            env = json.load(f)
                        old_envelopes[env["tile"]] = env

            comparison_envelopes = {
                tile_id_str: merge_tile_envelopes(
                    old_envelopes.get(tile_id_str), env, country_codes
                )
                for tile_id_str, env in new_envelopes.items()
            }
            for tile_id_str, envelope in old_envelopes.items():
                if tile_id_str not in comparison_envelopes:
                    stale = merge_tile_envelopes(
                        envelope,
                        {"tile": tile_id_str, "spots": []},
                        country_codes,
                    )
                    if stale.get("spots"):
                        comparison_envelopes[tile_id_str] = stale
            version, changed = compute_new_version(
                old_envelopes, comparison_envelopes, args.year
            )
            logger.info("  Version: %s (changed=%s)", version, changed)

            for env in new_envelopes.values():
                env["version"] = version

            for tile_id_str, env in new_envelopes.items():
                write_tile_file(
                    tile_id_str, env["spots"], str(output_dir), version, source, generated
                )

            logger.info(
                "  Wrote %d populated + %d empty tiles",
                len(tiles_dict),
                len(empty_tile_ids),
            )

            if not getattr(args, "no_push", False):
                logger.info("Step 7: Publish to data repo")
                copy_spots_to_repo(
                    str(output_dir / "spots"), data_repo_dir, country_codes=country_codes
                )
                if not getattr(args, "no_clusters", False):
                    write_cluster_files(
                        data_repo_dir / "spots",
                        data_repo_dir / "clusters",
                        data_year=args.year,
                        generated=_generated_date(),
                    )
                _ensure_clone_is_not_output(data_repo_dir, output_dir)
                commit_msg = f"data: update {args.region} spots v{version} ({args.year})"
                commit_and_push(str(data_repo_dir), commit_msg)
                logger.info(
                    "  Published version %s to %s (branch %s)",
                    version,
                    args.data_repo_url,
                    args.data_repo_branch,
                )
            else:
                logger.info("Step 7: Skipped (--no-push)")

        logger.info("Pipeline complete.")
        return 0
    except Exception:
        logger.exception("Pipeline failed")
        return 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()
    return run(args)



_PUBLISHED_CLONE = None
_PUBLISHED_OLD_ENVELOPES = {}


def _publication_clone_context():
    if _PUBLISHED_CLONE is not None:
        return nullcontext(str(_PUBLISHED_CLONE))
    return tempfile.TemporaryDirectory()


def _load_envelopes(spots_dir):
    envelopes = {}
    for json_path in spots_dir.glob("*.json"):
        with json_path.open(encoding="utf-8") as source:
            envelope = json.load(source)
        envelopes[envelope["tile"]] = envelope
    return envelopes


_legacy_run = run


def _ensure_clone_is_not_output(data_repo_dir: Path, output_dir: Path) -> None:
    if data_repo_dir.resolve() == output_dir.resolve():
        raise ValueError("The publication clone must not be the local output directory")


def _load_tile_counts(spots_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for json_path in sorted(spots_dir.glob("*.json")):
        with json_path.open(encoding="utf-8") as source:
            counts[json_path.stem] = len(json.load(source)["spots"])
    return counts


def _audit_before_write(spots_dir: Path, regions: dict[str, dict]) -> bool:
    """Gate publication on country-level anomalies, without mutating files."""
    audit = audit_country_spots(spots_dir, regions)
    problems = sum(len(audit[key]) for key in ("missing", "invalid", "unconfigured", "mismatched"))
    if problems:
        logger.error(
            "Country audit failed: missing=%d invalid=%d unconfigured=%d mismatched=%d; "
            "run --migrate-country-tags then --prune-orphan-spots explicitly",
            len(audit["missing"]), len(audit["invalid"]), len(audit["unconfigured"]), len(audit["mismatched"]),
        )
        return False
    return True


def run_list_orphans(args) -> int:
    try:
        regions = load_regions()
        if args.data_repo_url:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                audit = audit_country_spots(clone_dir / "spots", regions)
        else:
            audit = audit_country_spots(Path(args.output_dir) / "spots", regions)
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0 if not (audit["missing"] or audit["invalid"] or audit["unconfigured"] or audit["mismatched"]) else 1
    except Exception:
        logger.exception("Orphan audit failed")
        return 1


def run_preview_bbox_migration(args) -> int:
    try:
        from src.regions import build_bbox_migration_preview, format_bbox_migration_preview

        regions = load_regions(allow_legacy_geometry=True, validate_partition=False)
        if args.data_repo_url:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                counts = _load_tile_counts(clone_dir / "spots")
        else:
            counts = _load_tile_counts(Path(args.output_dir) / "spots")
        print(format_bbox_migration_preview(build_bbox_migration_preview(regions, counts, args.bbox_candidates)))
        return 0
    except Exception:
        logger.exception("BBox migration preview failed")
        return 1


def run_regenerate_clusters(args) -> int:
    try:
        regions = load_regions()
        output_dir = Path(args.output_dir)
        if args.no_push:
            if not args.no_clusters:
                spots_dir = output_dir / "spots"
                if not spots_dir.is_dir():
                    logger.error("Local cluster regeneration requires output/spots; it does not exist: %s", spots_dir)
                    return 1
                write_cluster_files(
                    spots_dir,
                    output_dir / "clusters-local",
                    data_year=args.year,
                    generated=_generated_date(),
                )
            return 0
        with tempfile.TemporaryDirectory() as clone_ctx:
            clone_dir = Path(clone_ctx)
            clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
            if not _audit_before_write(clone_dir / "spots", regions):
                return 1
            if not args.no_clusters:
                write_cluster_files(clone_dir / "spots", clone_dir / "clusters", data_year=args.year, generated=_generated_date())
            if not args.no_clusters:
                _ensure_clone_is_not_output(clone_dir, output_dir)
                commit_and_push(str(clone_dir), f"data: regenerate clusters ({args.year})")
        return 0
    except Exception:
        logger.exception("Cluster regeneration failed")
        return 1


def run_country_migration(args) -> int:
    """Explicitly authorized historical country-tag migration.

    Audit mode never calls this function.  Migration always runs in a cloned
    repository and commits only after the requested transformation succeeds.
    """
    try:
        regions = load_regions()
        configured = {
            code
            for region in regions.values()
            for code in (region["osm_country_code"] if isinstance(region["osm_country_code"], (list, tuple)) else [region["osm_country_code"]])
        }
        if args.no_push:
            spots_dir = Path(args.output_dir) / "spots"
            if not spots_dir.is_dir():
                logger.error("No local spots directory: %s", spots_dir)
                return 1
            report = migrate_country_tags(
                spots_dir,
                configured_codes=configured,
                delete_orphans=getattr(args, "prune_orphan_spots", False),
            )
        else:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                report = migrate_country_tags(
                    clone_dir / "spots",
                    configured_codes=configured,
                    delete_orphans=getattr(args, "prune_orphan_spots", False),
                )
                audit = audit_country_spots(clone_dir / "spots", regions)
                if not getattr(args, "prune_orphan_spots", False) and (
                    audit["missing"] or audit["invalid"] or audit["unconfigured"] or audit["mismatched"]
                ):
                    logger.error("Migration left unresolved spots; use --prune-orphan-spots explicitly")
                    return 1
                commit_and_push(str(clone_dir), f"data: migrate country tags ({args.year})")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except Exception:
        logger.exception("Country migration failed")
        return 1


def run(args) -> int:
    """Dispatch autonomous modes and add cluster generation to the raster run."""
    if getattr(args, "regenerate_clusters", False):
        return run_regenerate_clusters(args)
    if getattr(args, "list_orphans", False):
        return run_list_orphans(args)
    if getattr(args, "audit_country_tags", False):
        return run_list_orphans(args)
    if getattr(args, "migrate_country_tags", False) or getattr(args, "prune_orphan_spots", False):
        return run_country_migration(args)
    if getattr(args, "preview_bbox_migration", False):
        return run_preview_bbox_migration(args)

    if args.no_push:
        try:
            result = _legacy_run(args)
            if result or args.no_clusters:
                return result
            output_dir = Path(args.output_dir)
            write_cluster_files(
                output_dir / "spots",
                output_dir / "clusters-local",
                data_year=args.year,
                generated=_generated_date(),
            )
            return 0
        except Exception:
            logger.exception("Local pipeline failed")
            return 1

    try:
        regions = load_regions()
        get_region(args.region)
        input_path = Path(args.input_dir) / args.region / f"{args.year}.tif"
        if not input_path.exists():
            logger.error("Input not found: %s", input_path)
            return 1
        global _PUBLISHED_CLONE, _PUBLISHED_OLD_ENVELOPES
        with tempfile.TemporaryDirectory() as clone_ctx:
            clone_dir = Path(clone_ctx)
            clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
            # This audit must stay before _legacy_run: it gates all raster work.
            if not _audit_before_write(clone_dir / "spots", regions):
                return 1
            _PUBLISHED_CLONE = clone_dir
            _PUBLISHED_OLD_ENVELOPES = _load_envelopes(clone_dir / "spots")
            try:
                return _legacy_run(args)
            finally:
                _PUBLISHED_CLONE = None
                _PUBLISHED_OLD_ENVELOPES = {}
    except Exception:
        logger.exception("Pipeline failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
