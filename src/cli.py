"""argparse wrapper for the dark-sky pipeline."""
import argparse
import math


def parse_bbox_candidate(value: str) -> tuple[str, tuple[float, float, float, float]]:
    """Parse ``NAME=min_lon,min_lat,max_lon,max_lat`` into a named bbox."""
    try:
        name, coordinates = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--bbox-candidate must use NAME=MIN_LON,MIN_LAT,MAX_LON,MAX_LAT"
        ) from exc
    if not name:
        raise argparse.ArgumentTypeError("--bbox-candidate region name cannot be empty")

    parts = coordinates.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "--bbox-candidate must contain exactly four coordinates"
        )
    try:
        bbox = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--bbox-candidate coordinates must be numbers"
        ) from exc
    if not all(math.isfinite(coordinate) for coordinate in bbox):
        raise argparse.ArgumentTypeError(
            "--bbox-candidate coordinates must be finite"
        )
    for index, coordinate in enumerate(bbox):
        axis = "longitude" if index in (0, 2) else "latitude"
        lower, upper = (-180, 180) if axis == "longitude" else (-90, 90)
        if not lower <= coordinate <= upper:
            raise argparse.ArgumentTypeError(
                f"--bbox-candidate {axis} coordinate {index} is outside "
                f"the tile domain [{lower}, {upper}]"
            )
        if not coordinate.is_integer():
            raise argparse.ArgumentTypeError(
                "--bbox-candidate coordinates must be integers for "
                "publishable configuration"
            )
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise argparse.ArgumentTypeError(
            "--bbox-candidate coordinates must be ordered"
        )
    return name, bbox


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkskyspots-pipeline",
        description="Transform VIIRS radiance GeoTIFF into per-tile dark-sky spot JSON files.",
    )
    parser.add_argument("--year", type=int, required=False, help="Year of the input data (e.g. 2025)")
    parser.add_argument("--region", type=str, required=False, help="Region name from regions.yaml")
    parser.add_argument("--data-repo-url", type=str, required=False, help="SSH URL of the data repo")
    parser.add_argument("--data-repo-branch", type=str, default="main", help="Data repo branch")
    parser.add_argument("--no-push", action="store_true", help="Skip step 7 (publish)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--regenerate-clusters",
        action="store_true",
        help="Regenerate published global cluster files without raster processing",
    )
    modes.add_argument(
        "--list-orphans",
        action="store_true",
        help="Deprecated alias for the read-only country-tag audit",
    )
    modes.add_argument(
        "--audit-country-tags",
        action="store_true",
        help="Read-only audit of spot country tags",
    )
    modes.add_argument(
        "--migrate-country-tags",
        action="store_true",
        help="Explicitly reclassify historical spots by Natural Earth",
    )
    parser.add_argument(
        "--prune-orphan-spots",
        action="store_true",
        help="Explicitly delete unresolved/unconfigured historical spots",
    )
    # Keep the old spelling parseable solely to provide a safe migration error;
    # it is never accepted as an operational alias.
    parser.add_argument("--prune-orphans", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument(
        "--preview-bbox-migration",
        action="store_true",
        help="Read-only preview of legacy bbox ownership changes",
    )
    parser.add_argument(
        "--no-clusters", action="store_true", help="Skip cluster generation"
    )
    parser.add_argument(
        "--bbox-candidate",
        action="append",
        type=parse_bbox_candidate,
        default=[],
        metavar="NAME=MIN_LON,MIN_LAT,MAX_LON,MAX_LAT",
        help="Proposed bbox for --preview-bbox-migration; repeatable",
    )
    parser.add_argument("--debug-raster", action="store_true", help="Sauvegarde les rasters intermédiaires darkness et bortle en GeoTIFF dans le dossier de sortie")
    parser.add_argument("--input-dir", type=str, default="./input", help="Directory with input GeoTIFFs")
    parser.add_argument("--output-dir", type=str, default="./output", help="Directory for output JSONs")
    parser.add_argument("--budget-mb", type=float, default=500.0, help="RAM budget for input loading (MB)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args; exposed for testability."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.prune_orphans:
        if args.list_orphans or args.audit_country_tags or args.preview_bbox_migration:
            parser.error("--prune-orphans cannot be used in audit or preview mode")
        parser.error("--prune-orphans was removed; use --migrate-country-tags and, separately, --prune-orphan-spots")

    candidates: dict[str, tuple[float, float, float, float]] = {}
    if args.bbox_candidate:
        from .regions import load_regions

        try:
            regions = load_regions(
                allow_legacy_geometry=True,
                validate_partition=False,
            )
        except ValueError as exc:
            parser.error(str(exc))
        for name, bbox in args.bbox_candidate:
            if name not in regions:
                parser.error(f"Unknown --bbox-candidate region {name!r}")
            if name in candidates:
                parser.error(f"Duplicate --bbox-candidate for region {name!r}")
            candidates[name] = bbox
    args.bbox_candidates = candidates
    del args.bbox_candidate

    audit_mode = args.list_orphans or args.audit_country_tags or args.preview_bbox_migration
    migration_mode = args.migrate_country_tags or args.prune_orphan_spots
    if args.prune_orphan_spots and not args.migrate_country_tags:
        parser.error("--prune-orphan-spots requires --migrate-country-tags")
    if args.bbox_candidates and not args.preview_bbox_migration:
        parser.error("--bbox-candidate requires --preview-bbox-migration")
    if migration_mode:
        if args.no_push:
            # Local output migrations are useful in a temporary checkout and
            # remain explicitly authorized by the migration flag.
            pass
        if not args.no_push and args.data_repo_url is None:
            parser.error("country migration requires --data-repo-url unless --no-push is set")
        if audit_mode:
            parser.error("country migration cannot be combined with audit or preview mode")

    if args.regenerate_clusters:
        if args.year is None:
            parser.error("--year is required with --regenerate-clusters")
        if not args.no_push and args.data_repo_url is None:
            parser.error("--regenerate-clusters requires --data-repo-url")
    elif not audit_mode and not migration_mode:
        if args.year is None:
            parser.error("--year is required for a normal run")
        if args.region is None:
            parser.error("--region is required for a normal run")
        if not args.no_push and args.data_repo_url is None:
            parser.error("--data-repo-url is required unless --no-push is set")
    return args
