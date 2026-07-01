"""Tests for src/publish.py (git operations + version management)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_bump_version_changed():
    """Spots changed -> minor++."""
    from src.publish import bump_version
    assert bump_version("2025.1", True) == "2025.2"


def test_bump_version_no_change():
    """No change -> return current as-is."""
    from src.publish import bump_version
    assert bump_version("2025.1", False) == "2025.1"


def test_bump_version_new_year():
    """Cross-year bump just increments minor (year handled by caller)."""
    from src.publish import bump_version
    assert bump_version("2026.1", True) == "2026.2"


def test_get_current_version_no_existing(tmp_path):
    """Empty dir with year=2026 -> '2026.1' (first run, derived from --year)."""
    from src.publish import get_current_version
    assert get_current_version(str(tmp_path), year=2026) == "2026.1"


def test_get_current_version_first_run_derives_from_year_2025(tmp_path):
    """Empty dir, year=2025 -> '2025.1'."""
    from src.publish import get_current_version
    assert get_current_version(str(tmp_path), year=2025) == "2025.1"


def test_get_current_version_first_run_derives_from_year_2030(tmp_path):
    """Empty dir, year=2030 -> '2030.1'."""
    from src.publish import get_current_version
    assert get_current_version(str(tmp_path), year=2030) == "2030.1"


def test_get_current_version_existing(tmp_path):
    """Dir with version 2025.2 -> '2025.2'."""
    from src.publish import get_current_version
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    (spots_dir / "N042E001.json").write_text(
        json.dumps({"version": "2025.2", "tile": "N042E001", "spots": []}),
        encoding="utf-8",
    )
    assert get_current_version(str(tmp_path), year=2026) == "2025.2"


def test_get_current_version_max_picks_highest(tmp_path):
    """When multiple versions exist, the highest is picked."""
    from src.publish import get_current_version
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    for ver in ("2025.1", "2025.10", "2025.2"):
        (spots_dir / f"{ver}.json").write_text(
            json.dumps({"version": ver, "tile": "N042E001", "spots": []}),
            encoding="utf-8",
        )
    assert get_current_version(str(tmp_path), year=2026) == "2025.10"


def test_clone_data_repo_calls_git():
    """subprocess.run is called with the right git command."""
    from src.publish import clone_data_repo
    with patch("src.publish.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        clone_data_repo("git@github.com:user/data.git", "main", "/tmp/clone")
    args = mock_run.call_args.args[0]
    assert args[0] == "git"
    assert args[1] == "clone"
    assert "--branch" in args
    assert "main" in args
    assert "git@github.com:user/data.git" in args
    assert "/tmp/clone" in args


def test_copy_spots_to_repo(tmp_path):
    """Create source with 2 files, dest with 1 stale file; verify copy + purge."""
    from src.publish import copy_spots_to_repo
    src = tmp_path / "src_spots"
    src.mkdir()
    (src / "a.json").write_text("{}", encoding="utf-8")
    (src / "b.json").write_text("{}", encoding="utf-8")
    dst = tmp_path / "dst_repo"
    (dst / "spots").mkdir(parents=True)
    (dst / "spots" / "stale.json").write_text("{}", encoding="utf-8")
    (dst / "spots" / "a.json").write_text("{}", encoding="utf-8")

    copy_spots_to_repo(str(src), str(dst))

    spots_dst = dst / "spots"
    names = {p.name for p in spots_dst.iterdir()}
    assert "a.json" in names
    assert "b.json" in names
    assert "stale.json" not in names


def test_commit_and_push_calls_git():
    """subprocess.run is called 3 times: add, commit, push."""
    from src.publish import commit_and_push
    with patch("src.publish.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        commit_and_push("/tmp/repo", "test message")
    assert mock_run.call_count == 3
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert cmds[0] == ["git", "add", "."]
    assert cmds[1][0:3] == ["git", "commit", "-m"]
    assert cmds[1][3] == "test message"
    assert cmds[2] == ["git", "push"]


# --- compute_new_version (Task 9.2) ---

def test_compute_new_version_first_run():
    """old_envelopes={} -> (f'{year}.1', True)."""
    from src.publish import compute_new_version
    out = compute_new_version(
        old_envelopes={},
        new_envelopes={"N042E001": {"version": "2026.1", "spots": [{"id": "a"}]}},
        year=2026,
    )
    assert out == ("2026.1", True)


def test_compute_new_version_no_change():
    """Identical old and new -> (max_old, False)."""
    from src.publish import compute_new_version
    env = {"version": "2026.1", "spots": [{"id": "a", "darkness": 0.9}]}
    out = compute_new_version(
        old_envelopes={"N042E001": env},
        new_envelopes={"N042E001": env},
        year=2026,
    )
    assert out == ("2026.1", False)


def test_compute_new_version_spot_changed():
    """One spot added -> ('2026.2', True)."""
    from src.publish import compute_new_version
    old = {"version": "2026.1", "spots": [{"id": "a"}]}
    new = {"version": "2026.1", "spots": [{"id": "a"}, {"id": "b"}]}
    out = compute_new_version(
        old_envelopes={"N042E001": old},
        new_envelopes={"N042E001": new},
        year=2026,
    )
    assert out == ("2026.2", True)


def test_compute_new_version_tile_added():
    """New tile id -> same-year bump."""
    from src.publish import compute_new_version
    old = {"version": "2026.1", "spots": []}
    new = {"version": "2026.1", "spots": [{"id": "a"}]}
    out = compute_new_version(
        old_envelopes={"N042E001": old},
        new_envelopes={"N042E001": old, "N042E002": new},
        year=2026,
    )
    assert out == ("2026.2", True)


def test_compute_new_version_cross_year():
    """Old max='2025.3', year=2026, change -> ('2026.1', True) — not '2025.4'."""
    from src.publish import compute_new_version
    old = {"version": "2025.3", "spots": [{"id": "a"}]}
    new = {"version": "2025.3", "spots": [{"id": "a"}, {"id": "b"}]}
    out = compute_new_version(
        old_envelopes={"N042E001": old},
        new_envelopes={"N042E001": new},
        year=2026,
    )
    assert out == ("2026.1", True)
