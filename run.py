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
from src.coverage import attach_near_town, ensure_coverage, load_places
from src.enrich import enrich_all
from src.extract import mesh_minima, redundancy_filter
from src.alr import slice_and_compute
from src.convert import alr_to_bortle, alr_to_darkness
from src.publish import (
    clone_data_repo,
    commit_and_push,
    compute_new_version,
    copy_spots_to_repo,
)
from src.regions import get_region
from src.tile_export import (
    classify_spots_into_tiles,
    enumerate_tiles_in_bbox,
    write_tile_file,
)

logger = logging.getLogger("pipeline")


def run(args) -> int:
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

        # Step 6: Tile export + version
        logger.info("Step 6: Tile export")
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        source = f"VIIRS/{args.year}/{args.region}"

        tiles_dict = classify_spots_into_tiles(enriched, TILE_SIZE_DEG)
        occupied_tile_ids = set(tiles_dict.keys())

        all_tile_ids = list(enumerate_tiles_in_bbox(tuple(region["bbox"]), TILE_SIZE_DEG))
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
        with tempfile.TemporaryDirectory() as clone_ctx:
            data_repo_dir = Path(clone_ctx)
            old_envelopes: dict[str, dict] = {}

            if not getattr(args, "no_push", False):
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(data_repo_dir))
                old_spots_dir = data_repo_dir / "spots"
                if old_spots_dir.exists():
                    for json_path in old_spots_dir.glob("*.json"):
                        with open(json_path) as f:
                            env = json.load(f)
                        old_envelopes[env["tile"]] = env

            version, changed = compute_new_version(
                old_envelopes, new_envelopes, args.year
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
                copy_spots_to_repo(str(output_dir / "spots"), data_repo_dir)
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


if __name__ == "__main__":
    sys.exit(main())
