"""Tests for src/cli.py (argparse wrapper)."""
import pytest


def test_parser_requires_year():
    """--year is required."""
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--region", "france", "--data-repo-url", "x"])


def test_parser_requires_region():
    """--region is required."""
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--year", "2025", "--data-repo-url", "x"])


def test_parser_requires_data_repo_url():
    """--data-repo-url is required."""
    from src.cli import parse_args
    with pytest.raises(SystemExit):
        parse_args(["--year", "2025", "--region", "france"])


def test_parser_defaults():
    """Required args; verify defaults."""
    from src.cli import parse_args
    args = parse_args(
        ["--year", "2025", "--region", "france", "--data-repo-url", "git@x"]
    )
    assert args.year == 2025
    assert args.region == "france"
    assert args.data_repo_url == "git@x"
    assert args.data_repo_branch == "main"
    assert args.no_push is False
    assert args.input_dir == "./input"
    assert args.output_dir == "./output"
    assert args.budget_mb == 500.0
    assert args.verbose is False


def test_parser_no_push():
    """--no-push sets no_push=True."""
    from src.cli import parse_args
    args = parse_args(
        [
            "--year", "2025",
            "--region", "france",
            "--data-repo-url", "git@x",
            "--no-push",
        ]
    )
    assert args.no_push is True


def test_parser_custom_dirs():
    """Custom --input-dir and --output-dir."""
    from src.cli import parse_args
    args = parse_args(
        [
            "--year", "2025",
            "--region", "france",
            "--data-repo-url", "git@x",
            "--input-dir", "/tmp/in",
            "--output-dir", "/tmp/out",
        ]
    )
    assert args.input_dir == "/tmp/in"
    assert args.output_dir == "/tmp/out"


def test_parser_verbose():
    """-v sets verbose=True."""
    from src.cli import parse_args
    args = parse_args(
        ["--year", "2025", "--region", "france", "--data-repo-url", "git@x", "-v"]
    )
    assert args.verbose is True
