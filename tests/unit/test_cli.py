"""Tests for the command-line mode and safety gates."""

import pytest


def test_parser_requires_year():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--region", "france", "--data-repo-url", "x"])


def test_parser_requires_region():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--year", "2025", "--data-repo-url", "x"])


def test_parser_data_repo_url_optional_with_no_push():
    from src.cli import parse_args
    args = parse_args(["--year", "2025", "--region", "france", "--no-push"])
    assert args.data_repo_url is None
    assert args.no_push is True


def test_parser_requires_data_repo_url_without_no_push():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--year", "2025", "--region", "france"])


def test_parser_defaults():
    from src.cli import parse_args
    args = parse_args(["--year", "2025", "--region", "france", "--data-repo-url", "git@x"])
    assert args.year == 2025
    assert args.region == "france"
    assert args.data_repo_url == "git@x"
    assert args.data_repo_branch == "main"
    assert args.no_push is False
    assert args.debug_raster is False
    assert args.input_dir == "./input"
    assert args.output_dir == "./output"
    assert args.budget_mb == 500.0
    assert args.verbose is False


def test_parser_debug_raster_and_verbose():
    from src.cli import parse_args
    args = parse_args([
        "--year", "2025", "--region", "france", "--data-repo-url", "git@x",
        "--debug-raster", "-v",
    ])
    assert args.debug_raster is True
    assert args.verbose is True


def test_parser_custom_dirs_and_no_clusters():
    from src.cli import parse_args
    args = parse_args([
        "--year", "2025", "--region", "france", "--no-push", "--no-clusters",
        "--input-dir", "/tmp/in", "--output-dir", "/tmp/out",
    ])
    assert args.input_dir == "/tmp/in"
    assert args.output_dir == "/tmp/out"
    assert args.no_clusters is True


def test_parser_list_orphans_is_read_only_mode():
    from src.cli import parse_args
    args = parse_args(["--list-orphans"])
    assert args.year is None
    assert args.region is None


def test_parser_modes_are_mutually_exclusive():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--list-orphans", "--audit-country-tags"])


def test_parser_prune_orphans_is_explicit_tombstone(capsys):
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--year", "2025", "--region", "france", "--no-push", "--prune-orphans"])
    assert "was removed" in capsys.readouterr().err


def test_parser_prune_orphan_spots_requires_migration():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--prune-orphan-spots", "--no-push"])


def test_parser_migration_requires_url_when_publishing():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--migrate-country-tags"])


def test_parser_migration_accepts_local_no_push():
    from src.cli import parse_args
    args = parse_args(["--migrate-country-tags", "--no-push"])
    assert args.migrate_country_tags is True


def test_parser_regeneration_requires_year_and_url():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--regenerate-clusters"])
    args = parse_args(["--regenerate-clusters", "--year", "2025", "--no-push"])
    assert args.regenerate_clusters is True


def test_parser_rejects_audit_migration_combination():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--audit-country-tags", "--migrate-country-tags", "--no-push"])
