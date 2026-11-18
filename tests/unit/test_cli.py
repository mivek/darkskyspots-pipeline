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


@pytest.mark.parametrize(
    "args",
    [
        ["--year", "2025", "--region", "france", "--no-push"],
        ["--migrate-country-tags", "--no-push"],
        ["--regenerate-clusters", "--year", "2025", "--no-push"],
    ],
)
def test_parser_accepts_no_clusters_for_local_modes(args):
    from src.cli import parse_args

    parsed = parse_args([*args, "--no-clusters"])

    assert parsed.no_clusters is True


@pytest.mark.parametrize(
    "extra_args",
    [
        ["--year", "2025", "--region", "france", "--data-repo-url", "git@x"],
        ["--migrate-country-tags", "--data-repo-url", "git@x"],
        ["--regenerate-clusters", "--year", "2025", "--data-repo-url", "git@x"],
    ],
)
def test_parser_rejects_no_clusters_when_publishing(extra_args, capsys):
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args([*extra_args, "--no-clusters"])

    error = capsys.readouterr().err
    assert "--no-clusters cannot be used when publishing" in error
    assert "--no-push" in error


@pytest.mark.parametrize("mode", ["--list-orphans", "--audit-country-tags"])
def test_parser_accepts_no_clusters_for_read_only_mode(mode):
    from src.cli import parse_args

    args = parse_args([mode, "--no-clusters"])

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


def test_accessibility_manifest_is_local_canaries_only():
    from src.cli import parse_args

    args = parse_args([
        "--year", "2025", "--region", "canaries", "--no-push",
        "--accessibility-manifest", "input/accessibility/datasets/tenerife/manifest.json",
    ])
    assert args.accessibility_manifest.endswith("manifest.json")
    with pytest.raises(SystemExit):
        parse_args([
            "--year", "2025", "--region", "france", "--no-push",
            "--accessibility-manifest", "input/accessibility/manifest.json",
        ])


def test_accessibility_manifest_rejects_publishing_mode():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args([
            "--year", "2025", "--region", "canaries", "--data-repo-url", "git@x",
            "--accessibility-manifest", "input/accessibility/manifest.json",
        ])


def test_accessibility_dataset_requires_both_uk_ireland_scopes():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args([
            "--year", "2025", "--region", "uk_ireland", "--no-push",
            "--accessibility-dataset", "great_britain=gb.json",
        ])


def test_accessibility_dataset_parses_complete_local_mapping():
    from src.cli import parse_args

    args = parse_args([
        "--year", "2025", "--region", "uk_ireland", "--no-push",
        "--accessibility-dataset", "great_britain=gb.json",
        "--accessibility-dataset", "ireland_northern_ireland=ie.json",
    ])
    assert args.accessibility_datasets == {
        "great_britain": "gb.json", "ireland_northern_ireland": "ie.json",
    }


@pytest.mark.parametrize("entries", [
    ["great_britain=gb.json", "great_britain=other.json", "ireland_northern_ireland=ie.json"],
    ["great_britain=gb.json", "ireland_northern_ireland="],
    ["unknown=manifest.json"],
])
def test_accessibility_dataset_rejects_duplicate_malformed_and_unknown_scopes(entries):
    from src.cli import parse_args

    argv = ["--year", "2025", "--region", "uk_ireland", "--no-push"]
    for entry in entries:
        argv.extend(["--accessibility-dataset", entry])
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_accessibility_dataset_rejects_publication():
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args([
            "--year", "2025", "--region", "uk_ireland", "--data-repo-url", "data.git",
            "--accessibility-dataset", "great_britain=gb.json",
            "--accessibility-dataset", "ireland_northern_ireland=ie.json",
        ])


def test_parser_rejects_audit_migration_combination():
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--audit-country-tags", "--migrate-country-tags", "--no-push"])
