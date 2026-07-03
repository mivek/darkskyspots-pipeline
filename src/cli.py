"""argparse wrapper for the dark-sky pipeline."""
import argparse


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkskyspots-pipeline",
        description="Transform VIIRS radiance GeoTIFF into per-tile dark-sky spot JSON files.",
    )
    parser.add_argument("--year", type=int, required=True, help="Year of the input data (e.g. 2025)")
    parser.add_argument("--region", type=str, required=True, help="Region name from regions.yaml")
    parser.add_argument("--data-repo-url", type=str, required=False, help="SSH URL of the data repo")
    parser.add_argument("--data-repo-branch", type=str, default="main", help="Data repo branch")
    parser.add_argument("--no-push", action="store_true", help="Skip step 7 (publish)")
    parser.add_argument("--input-dir", type=str, default="./input", help="Directory with input GeoTIFFs")
    parser.add_argument("--output-dir", type=str, default="./output", help="Directory for output JSONs")
    parser.add_argument("--budget-mb", type=float, default=500.0, help="RAM budget for input loading (MB)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args; exposed for testability."""
    parser = create_parser()
    args = parser.parse_args(argv)
    if not args.no_push and args.data_repo_url is None:
        parser.error("--data-repo-url is required unless --no-push is set")
    return args
