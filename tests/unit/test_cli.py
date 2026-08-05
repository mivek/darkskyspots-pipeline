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


def test_parser_data_repo_url_optional_with_no_push():
    """--data-repo-url is NOT required when --no-push is set."""
    from src.cli import parse_args
    args = parse_args(["--year", "2025", "--region", "france", "--no-push"])
    assert args.data_repo_url is None
    assert args.no_push is True


def test_parser_requires_data_repo_url_without_no_push():
    """--data-repo-url is required when --no-push is not set."""
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
    assert args.debug_raster is False
    assert args.input_dir == "./input"
    assert args.output_dir == "./output"
    assert args.budget_mb == 500.0
    assert args.verbose is False


def test_parser_debug_raster_default():
    """Without --debug-raster, debug_raster is False."""
    from src.cli import parse_args
    args = parse_args(
        ["--year", "2025", "--region", "france", "--data-repo-url", "git@x"]
    )
    assert args.debug_raster is False


def test_parser_debug_raster_enabled():
    """With --debug-raster, debug_raster is True."""
    from src.cli import parse_args
    args = parse_args(
        [
            "--year", "2025",
            "--region", "france",
            "--data-repo-url", "git@x",
            "--debug-raster",
        ]
    )
    assert args.debug_raster is True


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


def test_parser_preview_accepts_explicit_bbox_candidate():
    """Preview accepts a finite four-coordinate candidate for a known region."""
    from src.cli import parse_args

    args = parse_args(
        [
            "--preview-bbox-migration",
            "--bbox-candidate",
            "france=-6,41,8,51",
        ]
    )

    assert args.bbox_candidates == {"france": (-6.0, 41.0, 8.0, 51.0)}


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ("france=-5.5,41,8,51", "integer"),
        ("france=-181,41,8,51", "tile domain"),
        ("france=-6,51,8,41", "ordered"),
    ],
)
def test_parser_rejects_unpublishable_bbox_candidate(candidate, message, capsys):
    """Explicit candidates must already satisfy strict publishable geometry."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--preview-bbox-migration",
                "--bbox-candidate",
                candidate,
            ]
        )

    error = capsys.readouterr().err
    assert "--bbox-candidate" in error
    assert message in error


def test_parser_prune_requires_publishing():
    """Pruning cannot run on a no-push invocation."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--year",
                "2025",
                "--region",
                "france",
                "--no-push",
                "--prune-orphans",
            ]
        )


def test_parser_regeneration_requires_published_data_url():
    """Cluster regeneration is a publishing mode and therefore needs a URL."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--regenerate-clusters", "--year", "2025"])


def test_parser_regeneration_accepts_no_push_with_data_repo_url():
    """Local regeneration preserves an explicitly supplied repository URL."""
    from src.cli import parse_args

    args = parse_args(
        [
            "--regenerate-clusters",
            "--year",
            "2025",
            "--data-repo-url",
            "git@x",
            "--no-push",
        ]
    )

    assert args.no_push is True
    assert args.data_repo_url == "git@x"


def test_parser_regeneration_accepts_local_no_push_without_data_repo_url():
    """Offline cluster regeneration needs only its indicative year."""
    from src.cli import parse_args

    args = parse_args(["--regenerate-clusters", "--year", "2025", "--no-push"])

    assert args.no_push is True
    assert args.data_repo_url is None


def test_parser_regeneration_does_not_require_region():
    """Cluster regeneration operates on all published regions."""
    from src.cli import parse_args

    args = parse_args(
        ["--regenerate-clusters", "--year", "2025", "--data-repo-url", "git@x"]
    )

    assert args.region is None


def test_parser_list_orphans_requires_neither_year_nor_region():
    """The orphan audit remains a read-only mode without run inputs."""
    from src.cli import parse_args

    args = parse_args(["--list-orphans"])

    assert args.year is None
    assert args.region is None


def test_parser_modes_are_mutually_exclusive():
    """Only one dedicated CLI mode can be selected at once."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--list-orphans", "--preview-bbox-migration"])


@pytest.mark.parametrize(
    "candidate",
    [
        "france=-6,41,8",
        "france=-6,41,8,nan",
        "france=-6,51,8,41",
        "atlantis=-6,41,8,51",
    ],
)
def test_parser_rejects_invalid_bbox_candidate(candidate):
    """Candidates must be known, finite, ordered four-coordinate bboxes."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--preview-bbox-migration", "--bbox-candidate", candidate])


def test_parser_rejects_duplicate_bbox_candidate():
    """A region's proposed bbox is unambiguous."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--preview-bbox-migration",
                "--bbox-candidate",
                "france=-6,41,8,51",
                "--bbox-candidate",
                "france=-5,41,8,51",
            ]
        )


def test_parser_no_clusters_preserves_normal_no_push_mode():
    """The run mode can opt out of clusters without needing a publication URL."""
    from src.cli import parse_args

    args = parse_args(
        ["--year", "2025", "--region", "france", "--no-push", "--no-clusters"]
    )

    assert args.no_clusters is True


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--year", "2025", "--region", "france"], "--data-repo-url is required"),
        (["--regenerate-clusters", "--year", "2025"], "requires --data-repo-url"),
        (["--list-orphans", "--prune-orphans", "--data-repo-url", "git@x"], "audit or preview mode"),
        (["--preview-bbox-migration", "--bbox-candidate", "france=0,0,1"], "exactly four"),
    ],
)
def test_parser_reports_mode_constraint_errors(argv, message, capsys):
    """Invalid mode combinations explain the violated CLI constraint."""
    from src.cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(argv)

    assert message in capsys.readouterr().err
