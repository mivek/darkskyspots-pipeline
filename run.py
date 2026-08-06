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
    prune_orphan_tiles,
    scan_orphan_tiles,
)
from src.clusters import write_cluster_files
from src.regions import get_region, load_regions, owned_tile_ids, owner_for_tile
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
        regions = load_regions()
        logger.info("Region: %s (%s)", region["name"], args.region)

        input_path = Path(args.input_dir) / args.region / f"{args.year}.tif"
        if not input_path.exists():
            logger.error("Input not found: %s", input_path)
            return 1

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        current_owned_tile_ids = (
            owned_tile_ids(args.region, regions)
            if current_owned_tile_ids is None
            else current_owned_tile_ids
        )
        all_tile_ids = _assert_owned_tile_ids_match(
            args.region, region["bbox"], current_owned_tile_ids
        )

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

        if current_owned_tile_ids is None:
            raise AssertionError("current_owned_tile_ids must be initialized before tile export")
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

            preserved_old_non_owned = {
                tile_id_str: envelope
                for tile_id_str, envelope in old_envelopes.items()
                if owner_for_tile(tile_id_str, regions) != args.region
            }
            comparison_envelopes = preserved_old_non_owned | {
                tile_id_str: envelope
                for tile_id_str, envelope in new_envelopes.items()
                if tile_id_str in current_owned_tile_ids
            }
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
                    str(output_dir / "spots"), data_repo_dir, current_owned_tile_ids
                )
                if scan_orphan_tiles(data_repo_dir / "spots", regions):
                    logger.error("Orphan tiles remain after publication copy")
                    return 1
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


def _assert_owned_tile_ids_match(region_name: str, bbox, owned_ids: set[str]) -> list[str]:
    enumerated = set(enumerate_tiles_in_bbox(tuple(bbox), TILE_SIZE_DEG))
    owned = set(owned_ids)
    if enumerated != owned:
        missing_from_enumeration = sorted(owned - enumerated)
        unexpected_from_enumeration = sorted(enumerated - owned)
        raise ValueError(
            f"Tile ownership invariant failed for {region_name!r}: "
            f"owned-only={missing_from_enumeration}, "
            f"enumerated-only={unexpected_from_enumeration}"
        )
    return sorted(enumerated)


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


def _audit_before_write(spots_dir: Path, regions: dict[str, dict], prune: bool) -> tuple[bool, dict[str, int]]:
    orphans = scan_orphan_tiles(spots_dir, regions)
    if not orphans:
        return True, {}
    if not prune:
        logger.error("Orphan tiles found: %s", ", ".join(sorted(orphans)))
        return False, {}
    pruned = prune_orphan_tiles(spots_dir, regions)
    logger.warning("Pruned orphan tiles: %s", ", ".join(sorted(pruned)))
    return True, pruned


def run_list_orphans(args) -> int:
    try:
        regions = load_regions()
        if args.data_repo_url:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                orphans = scan_orphan_tiles(clone_dir / "spots", regions)
        else:
            orphans = scan_orphan_tiles(Path(args.output_dir) / "spots", regions)
        for tile_id_str, count in sorted(orphans.items()):
            print(f"{tile_id_str}: {count} spots")
        print(f"Total: {len(orphans)} tiles, {sum(orphans.values())} spots")
        return 0
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
                allowed_tile_ids = set().union(*(owned_tile_ids(name, regions) for name in regions))
                write_cluster_files(
                    spots_dir,
                    output_dir / "clusters-local",
                    data_year=args.year,
                    generated=_generated_date(),
                    allowed_tile_ids=allowed_tile_ids,
                )
            return 0
        with tempfile.TemporaryDirectory() as clone_ctx:
            clone_dir = Path(clone_ctx)
            clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
            ok, pruned = _audit_before_write(clone_dir / "spots", regions, args.prune_orphans)
            if not ok:
                return 1
            if not args.no_clusters:
                write_cluster_files(clone_dir / "spots", clone_dir / "clusters", data_year=args.year, generated=_generated_date())
            if scan_orphan_tiles(clone_dir / "spots", regions):
                logger.error("Orphan tiles remain after cluster regeneration")
                return 1
            if not args.no_clusters or pruned:
                _ensure_clone_is_not_output(clone_dir, output_dir)
                commit_and_push(str(clone_dir), f"data: regenerate clusters ({args.year})")
        return 0
    except Exception:
        logger.exception("Cluster regeneration failed")
        return 1


def run(args) -> int:
    """Dispatch autonomous modes and add cluster generation to the raster run."""
    if getattr(args, "regenerate_clusters", False):
        return run_regenerate_clusters(args)
    if getattr(args, "list_orphans", False):
        return run_list_orphans(args)
    if getattr(args, "preview_bbox_migration", False):
        return run_preview_bbox_migration(args)

    if args.no_push:
        try:
            regions = load_regions()
            current_owned_tile_ids = owned_tile_ids(args.region, regions)
            result = _legacy_run(args, current_owned_tile_ids=current_owned_tile_ids)
            if result or args.no_clusters:
                return result
            output_dir = Path(args.output_dir)
            write_cluster_files(
                output_dir / "spots",
                output_dir / "clusters-local",
                data_year=args.year,
                generated=_generated_date(),
                allowed_tile_ids=current_owned_tile_ids,
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
            ok, _pruned = _audit_before_write(
                clone_dir / "spots", regions, args.prune_orphans
            )
            if not ok:
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
