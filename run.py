#!/usr/bin/env python3
"""
Dark Sky Spots Pipeline — orchestrator entrypoint.

Usage:
    python run.py --year 2025 --region france --data-repo-url git@github.com:user/data-repo.git
"""
import json
import hashlib
import logging
import shutil
import sys
import subprocess
import tempfile
import time
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
from src.coverage import attach_near_town, ensure_coverage, load_places
from src.enrich import enrich_all, spot_id
from src.elevation import ElevationSampler
from src.geonames import (
    GeoNamesIndex,
    NAMING_FEATURE_CODES,
    validate_geonames_manifest,
)
from src.extract import mesh_darkest, redundancy_filter
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
from src.clusters import LEVELS, write_cluster_files
from src.regions import build_region_ownerships, get_region, load_regions
from src.geography import classify_candidates, validate_country_codes
from src.selection import CandidateQualifier
from src.geographic_confidence import priority as geographic_priority
from src.geographic_data import GeographicDataset
from src.geographic_publication import guarded_apply, DEFAULT_REPORT_DIR
from src.tile_export import (
    classify_spots_into_tiles,
    enumerate_tiles_in_bbox,
    write_tile_file,
)
from src.sky_darkness_area import measure_sky_darkness_area

logger = logging.getLogger("pipeline")

# These are deliberately imported from the GeoNames module: the extractor,
# manifest validator, runtime and naming audit must share one product list.
AUDIT_PROBLEM_KEYS = (
    "missing",
    "invalid",
    "unconfigured",
    "mismatched",
    "ambiguous",
    "unassignable",
    "outside_declared_extents",
    "ambiguous_ownership",
)


def _audit_count(audit: dict, key: str) -> int:
    value = audit.get(key, 0)
    return value if isinstance(value, int) else len(value)


def _f01_audit_document(region: str, year: int, manifest_sha256: str, spots: list[dict]) -> dict:
    """Bind per-spot F01 evidence to the verified regional input package."""
    return {
        "schema": 1,
        "region": region,
        "year": year,
        "e3_manifest_sha256": manifest_sha256,
        "spots": spots,
    }


def _audit_has_problems(audit: dict) -> bool:
    return any(_audit_count(audit, key) for key in AUDIT_PROBLEM_KEYS)


_LAST_GENERATED_NS = 0


def _generated_date() -> str:
    """Return a process-monotonic UTC timestamp for cluster manifests."""
    global _LAST_GENERATED_NS

    generated_ns = time.time_ns()
    if generated_ns <= _LAST_GENERATED_NS:
        generated_ns = _LAST_GENERATED_NS + 1
    _LAST_GENERATED_NS = generated_ns

    seconds, nanoseconds = divmod(generated_ns, 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, timezone.utc)
    return f"{timestamp:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z"


def _producer_revision() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _region_country_codes(region: dict) -> list[str]:
    """Return the configured country list in one stable, runtime shape."""
    raw_codes = region["osm_country_code"]
    if isinstance(raw_codes, str):
        raw_codes = [raw_codes]
    country_codes = [str(code).strip().upper() for code in raw_codes]
    validate_country_codes(country_codes)
    return country_codes


def _validate_geonames_inputs(country_codes: list[str]) -> None:
    """Fail before raster work when filtered GeoNames data is unavailable/stale."""
    validate_geonames_manifest(
        data_dir="data",
        countries=country_codes,
        feature_codes=NAMING_FEATURE_CODES,
    )


def _load_geonames_index(country_codes: list[str], bbox) -> GeoNamesIndex:
    """Load only the versioned, code-filtered GeoNames extracts."""
    loader = getattr(GeoNamesIndex, "from_filtered_extracts", None)
    if not callable(loader):
        raise RuntimeError(
            "Filtered GeoNames support is unavailable: "
            "GeoNamesIndex.from_filtered_extracts is required; ZIP archives "
            "are not accepted by the pipeline"
        )
    return loader(
        data_dir="data",
        countries=country_codes,
        feature_codes=NAMING_FEATURE_CODES,
        bbox=bbox,
        margin_km=40,
    )


def _filter_candidates_to_bbox(candidates: list[dict], bbox) -> tuple[list[dict], int]:
    """Keep only candidates in the region's nominal, inclusive bbox.

    The raster deliberately includes an ALR halo. Mesh darkest pixels can therefore
    be found outside the region even after the Natural Earth country clip;
    those candidates must not participate in redundancy, coverage, naming, or
    tile export.
    """
    from src.geographic_geometry import inside
    kept: list[dict] = []
    rejected = 0
    for candidate in candidates:
        try:
            lat = float(candidate["lat"])
            lon = float(candidate["lon"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if inside(lon, lat, bbox):
            kept.append(candidate)
        else:
            rejected += 1
    return kept, rejected

def run(args) -> int:
    """Execute the 7-step pipeline. Returns 0 on success, 1 on error."""
    accessibility_manifest = getattr(args, "accessibility_manifest", None)
    sampler = None
    accessibility_sampler = None
    geographic_dataset = None
    accessibility_index = None
    accessibility_resolver = None
    generation_lock = None
    publication_tmp = None
    owns_publication_clone = False
    active_generation = None
    try:
        region = get_region(args.region)
        logger.info("Region: %s (%s)", region["name"], args.region)

        country_codes = _region_country_codes(region)
        ownership = build_region_ownerships(load_regions())[args.region]
        # This is intentionally before input/raster handling.  A stale or
        # incomplete filtered extract must never yield a partial run.
        _validate_geonames_inputs(country_codes)

        sampler = ElevationSampler(Path(args.dem_manifest_dir) / f"{args.region}.json", args.dem_cache_dir)
        accessibility_sampler = sampler
        geographic_dataset = GeographicDataset(getattr(args, 'geographic_manifest', 'input/geography/manifest.json'),require_e3=True)
        geographic_dataset.require_region(args.region, region)
        accessibility_enricher = None
        if accessibility_manifest:
            from src.accessibility import AccessibilityEnricher
            from src.accessibility_index import AccessibilityIndex
            accessibility_index = AccessibilityIndex(accessibility_manifest)
            accessibility_enricher = AccessibilityEnricher(accessibility_index, sampler)
        elif getattr(args, "accessibility_datasets", None):
            from src.accessibility_resolver import AccessibilityResolver
            if set(args.accessibility_datasets) == {"great_britain", "ireland_northern_ireland"}:
                from src.accessibility_scope import GeographicSpecification
                spec = GeographicSpecification.load(
                    "config/accessibility.yaml", "great_britain",
                )
                from src.accessibility_dem import validate_accessibility_dem_manifest
                validate_accessibility_dem_manifest(
                    spec.dem_manifest, args.dem_cache_dir,
                    "great_britain", "great-britain-g2-v1",
                    geographic_config="config/accessibility.yaml",
                )
                accessibility_sampler = ElevationSampler(spec.dem_manifest, args.dem_cache_dir)
            accessibility_resolver = AccessibilityResolver(
                args.accessibility_datasets, accessibility_sampler,
                geographic_config="config/accessibility.yaml",
            )
            accessibility_enricher = accessibility_resolver

        input_path = Path(args.input_dir) / args.region / f"{args.year}.tif"
        if not input_path.exists():
            logger.error("Input not found: %s", input_path)
            return 1

        logical_output_dir = Path(args.output_dir)
        from src.local_generation import initialize, new_attempt, writer_lock
        generation_lock = writer_lock(logical_output_dir)
        generation_lock.__enter__()
        initialize(logical_output_dir, args.region, args.year)
        attempt_id, attempt_dir = new_attempt(logical_output_dir, args.region, args.year)
        output_dir = attempt_dir / "package"
        debug_dir = logical_output_dir / "debug" / attempt_id

        # Keep the audited publication snapshot alive before any raster work.
        # This is only for published execution; local runs never touch a repo.
        global _PUBLISHED_CLONE, _PUBLISHED_OLD_ENVELOPES
        if not getattr(args, "no_push", False) and _PUBLISHED_CLONE is None:
            publication_tmp = tempfile.TemporaryDirectory()
            _PUBLISHED_CLONE = Path(publication_tmp.name)
            owns_publication_clone = True
            clone_data_repo(args.data_repo_url, args.data_repo_branch, str(_PUBLISHED_CLONE))
            if not _audit_before_write(_PUBLISHED_CLONE / "spots", load_regions()):
                raise ValueError("Publication country-audit preflight failed")
            _PUBLISHED_OLD_ENVELOPES = _load_envelopes(_PUBLISHED_CLONE / "spots")

        # Bboxes remain raster/GeoNames envelopes only.  They do not confer
        # ownership of any tile; country clipping decides what is publishable.
        all_tile_ids = sorted(enumerate_tiles_in_bbox(tuple(region["bbox"]), TILE_SIZE_DEG))
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
            debug_dir.mkdir(parents=True, exist_ok=True)
            darkness_path = debug_dir / f"debug_darkness_{args.region}_{args.year}.tif"
            bortle_path = debug_dir / f"debug_bortle_{args.region}_{args.year}.tif"
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
            logger.info("Debug rasters written to %s", debug_dir)

        # Step 2: Mesh scan (darkest pixel per cell)
        logger.info("Step 2: Mesh scan (darkest pixel per cell)")
        selection_stats = {}
        rejection_path = output_dir / 'reviewed-water-rejections.jsonl'
        with rejection_path.open('w',encoding='utf-8') as rejection_stream:
            qualifier = CandidateQualifier(
                region, transform, crs, sampler, classify=classify_candidates,
                reviewed_water=geographic_dataset,
                region_name=args.region,
                rejection_sink=lambda record: rejection_stream.write(json.dumps(record)+'\n'))
            candidates = mesh_darkest(darkness, transform, MESH_KM, qualify=qualifier,
                                      crs=crs, stats=selection_stats,priority_key=geographic_priority)
        logger.info("  Mesh selection stats: %s; qualification: %s", selection_stats, dict(qualifier.counts))
        if qualifier.counts['wbm_nodata']:
            logger.warning("WBM NoData candidates rejected: %d", qualifier.counts['wbm_nodata'])
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
        # are clipped before redundancy so an unpublished foreign/sea darkest pixel
        # can never suppress a published candidate.
        logger.info("Step 2c: Natural Earth land mask and country clip")
        candidates, geography_stats = classify_candidates(
            candidates,
            country_codes,
            transform=transform,
            crs=crs,
            equal_area_epsg=region["equal_area_epsg"],
        )
        logger.info("  Geographic candidate stats: %s", geography_stats)

        candidates, bbox_rejected = _filter_candidates_to_bbox(
            candidates, region["bbox"]
        )
        logger.info(
            "  Nominal bbox filter: %d kept, %d rejected outside %s (ALR halo)",
            len(candidates),
            bbox_rejected,
            region["bbox"],
        )
        classes = sampler.wbm_classes([p['lat'] for p in candidates], [p['lon'] for p in candidates])
        if len(classes) != len(candidates) or any(value != 0 for value in classes):
            raise ValueError('WBM qualification failed: a non-land candidate reached redundancy')

        # Step 3: Redundancy filter
        logger.info("Step 3: Redundancy filter")
        filtered = redundancy_filter(candidates, REDUNDANCY_KM,priority_key=geographic_priority)
        logger.info("  After redundancy filter: %d spots", len(filtered))

        # Step 4: Coverage guarantee via GeoNames places
        logger.info("Step 4: Coverage guarantee via GeoNames places")
        communes = load_places(region)
        logger.info("  Loaded %d localities (GeoNames)", len(communes))
        covered = ensure_coverage(
            filtered, candidates, communes, MIN_SPOTS_PER_AREA, COVERAGE_RADIUS_KM,priority_key=geographic_priority
        )
        logger.info("  After coverage guarantee: %d spots", len(covered))

        # Step 4b: Attach the nearest commune name as the "near" field.
        covered = attach_near_town(covered, communes)
        logger.info("  Attached nearest commune to %d spots", len(covered))

        # Step 5: Naming cascade + enrichment.  ``near`` remains the nearest
        # cities500 locality and may be empty; name is supplied independently
        # by the country-scoped GeoNames feature-code index and is guaranteed
        # before tile export.
        logger.info("Step 5: GeoNames naming cascade + enrichment")
        naming_index = _load_geonames_index(country_codes, region["bbox"])
        named = naming_index.enrich_spots(covered)
        if any(not isinstance(spot.get("name"), str) or not spot["name"].strip() for spot in named):
            raise ValueError("GeoNames naming cascade produced an empty name")
        # F01 is measured from the same un-clipped ALR that selected each
        # anchor. Attach only its optional wire value; all detailed evidence
        # stays in the local generation audit.
        geographic_dataset.classify([], args.region, sampler)
        f01_index = geographic_dataset._e3_engine.for_region(args.region)
        f01_audit = []
        for spot in named:
            exported_id = spot_id(float(spot["lat"]), float(spot["lon"]))
            payload, evidence = measure_sky_darkness_area(
                {**spot, "id": exported_id}, alr_data, transform, sampler, f01_index
            )
            evidence["id"] = exported_id
            if payload is not None:
                spot["skyDarknessArea"] = payload
            else:
                spot.pop("skyDarknessArea", None)
            f01_audit.append(evidence)
        (attempt_dir / "sky-darkness-area-audit.json").write_text(
            json.dumps(_f01_audit_document(args.region, args.year,
                                           geographic_dataset.revision, f01_audit),
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("  F01 measurement outcomes: %s",
                    {outcome: sum(1 for item in f01_audit if
                     ("halo" if item["radiusM"] else "label" if item["label"] else "none") == outcome)
                     for outcome in ("halo", "label", "none")})
        if accessibility_resolver is not None or (
            accessibility_index is not None and accessibility_index.manifest.get("schema") == 2
        ):
            assignment_spots = [
                {**spot, "id": spot.get("id") or spot_id(float(spot["lat"]), float(spot["lon"]))}
                for spot in named
            ]
            assignment_counts = (accessibility_resolver.audit_assignments(assignment_spots)
                                 if accessibility_resolver is not None
                                 else accessibility_enricher.audit_assignments(assignment_spots))
            logger.info("  Accessibility assignments: %s", assignment_counts)
        enriched = enrich_all(named, elevation_sampler=sampler,
                              accessibility_enricher=accessibility_enricher)
        if accessibility_resolver is not None or (
            accessibility_index is not None and accessibility_index.manifest.get("schema") == 2
        ):
            accessibility_counts = (accessibility_resolver.audit_assignments(enriched, enriched=True)
                                    if accessibility_resolver is not None
                                    else accessibility_enricher.audit_assignments(enriched, enriched=True))
            if accessibility_counts["enriched"] != accessibility_counts["assigned"]:
                raise ValueError("Accessibility enrichment was partial for assigned spots")
            logger.info("  Accessibility enrichment outcomes: %s", accessibility_counts)
        logger.info("  Elevation sample counts: %s", dict(sampler.counts))
        logger.info("  WBM IO stats: %s", dict(sampler.wbm_io))
        logger.info("  Enriched %d spots", len(enriched))

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
                    old_envelopes.get(tile_id_str), env, ownership
                )
                for tile_id_str, env in new_envelopes.items()
            }
            for tile_id_str, envelope in old_envelopes.items():
                if tile_id_str not in comparison_envelopes:
                    stale = merge_tile_envelopes(
                        envelope,
                        {"tile": tile_id_str, "spots": []},
                        ownership,
                    )
                    if stale.get("spots"):
                        comparison_envelopes[tile_id_str] = stale
            version, changed = compute_new_version(
                old_envelopes, comparison_envelopes, args.year
            )
            logger.info("  Version: %s (changed=%s)", version, changed)

            (output_dir / "spots").mkdir(parents=True, exist_ok=True)

            for env in new_envelopes.values():
                env["version"] = version

            for tile_id_str, env in new_envelopes.items():
                write_tile_file(
                    tile_id_str, env["spots"], str(output_dir), version, source, generated
                )

            if accessibility_index is not None:
                from src.accessibility_prepare import write_accessibility_source_record
                source_record = write_accessibility_source_record(
                    output_dir, accessibility_index.manifest
                )
                logger.info("  Wrote accessibility source record %s", source_record)
            if accessibility_resolver is not None:
                from src.accessibility_prepare import write_accessibility_source_record
                for scope, index in accessibility_resolver.indices.items():
                    source_record = write_accessibility_source_record(output_dir, index.manifest)
                    logger.info("  Wrote %s accessibility source record %s", scope, source_record)

            if not getattr(args, "no_clusters", False):
                write_cluster_files(output_dir / "spots", output_dir / "clusters-local",
                                    data_year=args.year, generated=_generated_date(), levels=LEVELS)
            from src.local_generation import promote
            cluster_parameters = ([{"level": level.level, "cell_deg": level.cell_deg,
                                   "width_km": list(level.width_km)} for level in LEVELS]
                                  if not getattr(args, "no_clusters", False) else None)
            active_generation = promote(
                logical_output_dir, attempt_id, region=args.region, year=args.year,
                context={"producer": "run.py", "bbox": list(region["bbox"]),
                         "tile_size_deg": TILE_SIZE_DEG, "source": source,
                         "version": version, "generated": generated,
                         "producer_revision": _producer_revision(),
                         "cluster_parameters": cluster_parameters},
                intended_tiles=sorted(set(tiles_dict) | set(all_tile_ids)),
                clusters=not getattr(args, "no_clusters", False),
                cluster_parameters=cluster_parameters,
            )
            logger.info("Activated local generation %s at %s", active_generation.generation_id,
                        active_generation.path)

            logger.info(
                "  Wrote %d populated + %d empty tiles",
                len(tiles_dict),
                len(empty_tile_ids),
            )

            if not getattr(args, "no_push", False):
                logger.info("Step 7: Publish to data repo")
                copy_spots_to_repo(
                    None,
                    data_repo_dir,
                    ownership=ownership,
                    envelopes=new_envelopes,
                )
                if not getattr(args, "no_clusters", False):
                    write_cluster_files(
                        data_repo_dir / "spots",
                        data_repo_dir / "clusters",
                        data_year=args.year,
                        generated=_generated_date(),
                    )
                _ensure_clone_is_not_output(data_repo_dir, logical_output_dir)
                commit_msg = f"data: update {args.region} spots v{version} ({args.year})"
                commit_and_push(str(data_repo_dir), commit_msg,
                                geographic_dataset=geographic_dataset,
                                geographic_report_dir=getattr(args, 'geographic_report_dir', DEFAULT_REPORT_DIR))
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
        if active_generation is not None:
            logger.exception("Remote publication failed after local generation %s was activated and remains usable", active_generation.generation_id)
        else:
            logger.exception("Pipeline failed before local generation activation")
        return 1
    finally:
        if generation_lock is not None:
            generation_lock.__exit__(None, None, None)
        if owns_publication_clone:
            _PUBLISHED_CLONE = None
            _PUBLISHED_OLD_ENVELOPES = {}
            publication_tmp.cleanup()
        if sampler is not None:
            sampler.close()
        if accessibility_index is not None:
            accessibility_index.close()
        if accessibility_resolver is not None:
            accessibility_resolver.close()
        if accessibility_sampler is not None and accessibility_sampler is not sampler:
            accessibility_sampler.close()
        if geographic_dataset is not None:
            geographic_dataset.close()


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


def _audit_before_write(spots_dir: Path, regions: dict[str, dict]) -> bool:
    """Gate publication on country and regional anomalies without writing."""
    audit = audit_country_spots(spots_dir, regions)
    if _audit_has_problems(audit):
        logger.error(
            "Country audit failed: missing=%s invalid=%s unconfigured=%s mismatched=%s "
            "ambiguous=%s unassignable=%s outside_declared_extents=%s "
            "ambiguous_ownership=%s; "
            "review --audit-country-tags; migrate tags or explicitly prune eligible orphans",
            *(_audit_count(audit, key) for key in AUDIT_PROBLEM_KEYS),
        )
        return False
    return True


def _migration_data_year(clone_dir: Path, args) -> int:
    """Resolve cluster metadata without making ``data_year`` authoritative."""
    manifest_path = clone_dir / "clusters" / "index.json"
    try:
        with manifest_path.open(encoding="utf-8") as source:
            manifest = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        manifest = None

    if isinstance(manifest, dict) and manifest.get("schema") == 1:
        data_year = manifest.get("data_year")
        # This field is copied only because the cluster writer requires it as
        # informative metadata. It is not used to decide cache identity or
        # whether the migration is current. An unknown manifest schema is not
        # a supported source for the metadata, so --year remains the fallback.
        if isinstance(data_year, int) and not isinstance(data_year, bool):
            return data_year

    data_year = getattr(args, "year", None)
    if data_year is None:
        raise ValueError(
            "Cannot regenerate clusters: no data_year in a valid clusters/index.json "
            "and no --year fallback was provided"
        )
    return data_year


def run_list_orphans(args) -> int:
    try:
        regions = load_regions()
        if args.data_repo_url:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                audit = audit_country_spots(clone_dir / "spots", regions)
        else:
            # Classify and consume one canonical target.  A lexical alias may
            # sit outside a managed root while resolving into a finalized
            # package, history directory, or unfinished candidate.
            local_root = Path(args.output_dir).resolve()
            from src.local_generation import resolve_spots_dir
            managed = next((path for path in (local_root, *local_root.parents)
                            if (path / "identity.json").is_file()), None)
            if managed is not None:
                spots_dir = resolve_spots_dir(local_root)
            else:
                spots_dir = local_root if local_root.name == "spots" else local_root / "spots"
            audit = audit_country_spots(spots_dir, regions)
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0 if not _audit_has_problems(audit) else 1
    except Exception:
        logger.exception("Orphan audit failed")
        return 1


def run_regenerate_clusters(args) -> int:
    try:
        output_dir = Path(args.output_dir)
        if args.no_push:
            _regenerate_local_clusters(args, output_dir)
            return 0
        regions = load_regions()
        with tempfile.TemporaryDirectory() as clone_ctx:
            clone_dir = Path(clone_ctx)
            clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
            if not _audit_before_write(clone_dir / "spots", regions):
                return 1
            if not args.no_clusters:
                write_cluster_files(clone_dir / "spots", clone_dir / "clusters", data_year=args.year, generated=_generated_date())
            if not args.no_clusters:
                _ensure_clone_is_not_output(clone_dir, output_dir)
                commit_and_push(str(clone_dir), f"data: regenerate clusters ({args.year})",
                                geographic_dataset=GeographicDataset(getattr(args, 'geographic_manifest', 'input/geography/manifest.json')),
                                geographic_report_dir=getattr(args, 'geographic_report_dir', DEFAULT_REPORT_DIR))
        return 0
    except Exception:
        logger.exception("Error: cluster regeneration failed")
        return 1


def _regenerate_local_clusters(args, output_dir: Path) -> None:
    from src.local_generation import writer_lock, resolve_complete, new_attempt, promote
    if args.no_clusters:
        logger.info("Local cluster regeneration skipped (--no-clusters); no generation created")
        return
    with writer_lock(output_dir):
        identity_path = output_dir / "identity.json"
        if not identity_path.exists():
            raise ValueError(f"Local cluster regeneration requires a managed generation; use a fresh dedicated --output-dir: {output_dir}")
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        supplied_region = getattr(args, "region", None)
        if supplied_region and supplied_region != identity.get("region"):
            raise ValueError("Supplied --region does not match managed output identity")
        if identity.get("year") != args.year:
            raise ValueError("--year does not match managed output identity")
        region_name = identity["region"]
        parent = resolve_complete(output_dir)
        attempt_id, attempt = new_attempt(output_dir, region_name, args.year, parent=parent.generation_id)
        package = attempt / "package"
        for child in parent.path.iterdir():
            if child.name in {"generation.json", "validation.json", "clusters-local"}:
                continue
            target = package / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        cluster_generated = _generated_date()
        write_cluster_files(package / "spots", package / "clusters-local",
                            data_year=args.year, generated=cluster_generated, levels=LEVELS)
        cluster_parameters = [{"level": level.level, "cell_deg": level.cell_deg,
                               "width_km": list(level.width_km)} for level in LEVELS]
        promote(output_dir, attempt_id, region=region_name, year=args.year,
                context={"spot_context": parent.manifest.get("context", {}),
                         "cluster_operation": {
                             "operation": "cluster-regeneration",
                             "parent": parent.generation_id,
                             "parent_manifest_sha256": hashlib.sha256((parent.path / "generation.json").read_bytes()).hexdigest(),
                             "generated": cluster_generated,
                             "producer_revision": _producer_revision(),
                             "levels": cluster_parameters,
                         }},
                intended_tiles=parent.manifest.get("intended_tiles", []), clusters=True,
                cluster_parameters=cluster_parameters, parent=parent)


def run_country_migration(args) -> int:
    """Explicitly authorized historical country-tag migration.

    Audit mode never calls this function.  Migration always runs in a cloned
    repository and commits only after the requested transformation succeeds.
    """
    try:
        regions = load_regions()
        if args.no_push:
            from src.local_generation import reject_managed_destination
            reject_managed_destination(args.output_dir)
            spots_dir = Path(args.output_dir) / "spots"
            if not spots_dir.is_dir():
                logger.error("No local spots directory: %s", spots_dir)
                return 1
            def transform(stage):
                result = migrate_country_tags(
                    stage / 'spots', regions=regions,
                    delete_orphans=getattr(args, 'prune_orphan_spots', False))
                write_cluster_files(stage / 'spots', stage / 'clusters',
                                    data_year=_migration_data_year(stage, args), generated=_generated_date())
                return result
            report = guarded_apply(
                Path(args.output_dir), transform,
                dataset=GeographicDataset(getattr(args, 'geographic_manifest', 'input/geography/manifest.json')),
                report_dir=getattr(args, 'geographic_report_dir', DEFAULT_REPORT_DIR))
        else:
            with tempfile.TemporaryDirectory() as clone_ctx:
                clone_dir = Path(clone_ctx)
                clone_data_repo(args.data_repo_url, args.data_repo_branch, str(clone_dir))
                report = migrate_country_tags(
                    clone_dir / "spots",
                    regions=regions,
                    delete_orphans=getattr(args, "prune_orphan_spots", False),
                )
                audit = audit_country_spots(clone_dir / "spots", regions)
                if _audit_has_problems(audit):
                    logger.error("Migration left unresolved spots; review the audit and use --prune-orphan-spots where applicable")
                    return 1
                write_cluster_files(
                    clone_dir / "spots",
                    clone_dir / "clusters",
                    data_year=_migration_data_year(clone_dir, args),
                    generated=_generated_date(),
                )
                commit_and_push(str(clone_dir), f"data: migrate country tags ({args.year})",
                                geographic_dataset=GeographicDataset(getattr(args, 'geographic_manifest', 'input/geography/manifest.json')),
                                geographic_report_dir=getattr(args, 'geographic_report_dir', DEFAULT_REPORT_DIR))
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except Exception:
        logger.exception("Error: country migration failed")
        return 1


def _validate_accessibility_dispatch(args) -> None:
    """Validate local-only accessibility modes before dispatcher side effects."""
    legacy_manifest = getattr(args, "accessibility_manifest", None)
    datasets = getattr(args, "accessibility_datasets", None)
    raw_entries = getattr(args, "accessibility_dataset", None)
    if datasets is None and raw_entries:
        datasets = {}
        for entry in raw_entries:
            if not isinstance(entry, str) or "=" not in entry:
                raise ValueError("--accessibility-dataset must use SCOPE=MANIFEST")
            scope, manifest = entry.split("=", 1)
            if not scope or not manifest:
                raise ValueError("--accessibility-dataset must use SCOPE=MANIFEST")
            if scope in datasets:
                raise ValueError(f"Duplicate --accessibility-dataset scope: {scope}")
            datasets[scope] = manifest
    if legacy_manifest and datasets:
        raise ValueError("--accessibility-manifest and --accessibility-dataset cannot be combined")
    if not legacy_manifest and not datasets:
        args.accessibility_datasets = None
        return
    flag = "--accessibility-manifest" if legacy_manifest else "--accessibility-dataset"
    if not getattr(args, "no_push", False):
        raise ValueError(f"{flag} requires --no-push; accessibility runs cannot publish")
    incompatible_modes = (
        "regenerate_clusters", "list_orphans", "audit_country_tags",
        "migrate_country_tags", "prune_orphan_spots", "prune_orphans",
    )
    selected = [mode for mode in incompatible_modes if getattr(args, mode, False)]
    if selected:
        raise ValueError(
            f"{flag} supports only a normal local run; "
            f"incompatible mode(s): {', '.join(selected)}"
        )
    if legacy_manifest:
        if getattr(args, "region", None) != "canaries":
            raise ValueError("--accessibility-manifest is supported only for the canaries region")
        args.accessibility_datasets = None
        return
    if not isinstance(datasets, dict) or not datasets:
        raise ValueError("--accessibility-dataset must provide a non-empty scope mapping")
    if any(scope not in {"canaries", "great_britain", "ireland_northern_ireland"}
           or not isinstance(path, (str, Path)) or not str(path)
           for scope, path in datasets.items()):
        raise ValueError("--accessibility-dataset contains an unknown scope or empty manifest path")
    if getattr(args, "region", None) == "uk_ireland":
        if set(datasets) != {"great_britain", "ireland_northern_ireland"}:
            raise ValueError("uk_ireland accessibility runs require both G2 scopes")
    elif getattr(args, "region", None) == "canaries":
        if set(datasets) != {"canaries"}:
            raise ValueError("canaries accessibility runs require exactly the canaries scope")
    else:
        raise ValueError("--accessibility-dataset is supported only for canaries or uk_ireland")
    args.accessibility_datasets = {scope: str(path) for scope, path in datasets.items()}


def run(args) -> int:
    """Dispatch autonomous modes and add cluster generation to the raster run."""
    _validate_accessibility_dispatch(args)
    if getattr(args, "regenerate_clusters", False):
        return run_regenerate_clusters(args)
    if getattr(args, "list_orphans", False):
        return run_list_orphans(args)
    if getattr(args, "audit_country_tags", False):
        return run_list_orphans(args)
    if getattr(args, "migrate_country_tags", False) or getattr(args, "prune_orphan_spots", False):
        return run_country_migration(args)
    if args.no_push:
        try:
            return _legacy_run(args)
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
        from src.local_generation import initialize, writer_lock
        logical_output_dir = Path(args.output_dir)
        with writer_lock(logical_output_dir):
            initialize(logical_output_dir, args.region, args.year)
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
