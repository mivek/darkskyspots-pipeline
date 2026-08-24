"""argparse wrapper for the dark-sky pipeline."""
import argparse


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
    parser.add_argument(
        "--no-clusters", action="store_true", help="Skip cluster generation"
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
        if args.list_orphans or args.audit_country_tags:
            parser.error("--prune-orphans cannot be used in audit mode")
        parser.error("--prune-orphans was removed; use --migrate-country-tags and, separately, --prune-orphan-spots")

    audit_mode = args.list_orphans or args.audit_country_tags
    migration_mode = args.migrate_country_tags or args.prune_orphan_spots
    if args.prune_orphan_spots and not args.migrate_country_tags:
        parser.error("--prune-orphan-spots requires --migrate-country-tags")
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
