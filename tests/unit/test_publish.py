"""Tests for src/publish.py (git operations + version management)."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


def test_copy_spots_to_repo_only_replaces_owned_tiles(tmp_path, caplog):
    """Owned tiles are copied/purged while unowned tiles remain byte-for-byte."""
    caplog.set_level(logging.INFO, logger="src.publish")
    from src.publish import copy_spots_to_repo
    src = tmp_path / "src_spots"
    src.mkdir()
    (src / "N001E001.json").write_bytes(b"{\"tile\":\"N001E001\",\"spots\":[]}")
    (src / "N999E999.json").write_bytes(b"must-not-copy")
    dst = tmp_path / "dst_repo"
    (dst / "spots").mkdir(parents=True)
    (dst / "spots" / "N001E001.json").write_bytes(b"old-owned")
    (dst / "spots" / "N001E002.json").write_bytes(b"stale-owned")
    unowned = dst / "spots" / "N999E999.json"
    unowned.write_bytes(b"preserve-this-exactly")

    copy_spots_to_repo(str(src), str(dst), {"N001E001", "N001E002"})

    spots_dst = dst / "spots"
    assert (spots_dst / "N001E001.json").read_bytes() == b"{\"tile\":\"N001E001\",\"spots\":[]}"
    assert not (spots_dst / "N001E002.json").exists()
    assert unowned.read_bytes() == b"preserve-this-exactly"
    assert not (spots_dst / "N999E999.json").read_bytes() == b"must-not-copy"
    assert "Purged 1 stale owned tile" in caplog.text


def _write_envelope(path, tile_id, spots):
    path.write_text(
        json.dumps({"version": "2026.1", "tile": tile_id, "spots": spots}),
        encoding="utf-8",
    )


def test_scan_orphan_tiles_reports_sorted_counts_without_mutating(tmp_path):
    from src.publish import scan_orphan_tiles
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "N051E001.json", "N051E001", [{"id": "a"}, {"id": "b"}])
    _write_envelope(spots / "N051E000.json", "N051E000", [])
    _write_envelope(spots / "N048E002.json", "N048E002", [{"id": "owned"}])
    (spots / "README.txt").write_text("keep", encoding="utf-8")
    before = (spots / "N051E000.json").read_bytes()
    regions = {"france": {"bbox": [-6, 41, 8, 51]}}

    assert scan_orphan_tiles(spots, regions) == {"N051E000": 0, "N051E001": 2}
    assert (spots / "N051E000.json").read_bytes() == before


def test_scan_orphan_tiles_names_invalid_envelope_file(tmp_path):
    from src.publish import scan_orphan_tiles

    spots = tmp_path / "spots"
    spots.mkdir()
    (spots / "N051E000.json").write_text(
        json.dumps({"tile": "N051E000"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"N051E000\.json.*spots"):
        scan_orphan_tiles(spots, {"france": {"bbox": [-6, 41, 8, 51]}})


def test_prune_orphan_tiles_removes_only_orphans_and_returns_sorted_counts(tmp_path):
    from src.publish import prune_orphan_tiles
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "N051E001.json", "N051E001", [{"id": "a"}, {"id": "b"}])
    _write_envelope(spots / "N051E000.json", "N051E000", [])
    _write_envelope(spots / "N048E002.json", "N048E002", [])
    (spots / "README.txt").write_bytes(b"keep")
    regions = {"france": {"bbox": [-6, 41, 8, 51]}}

    assert prune_orphan_tiles(spots, regions) == {"N051E000": 0, "N051E001": 2}
    assert (spots / "N048E002.json").exists()
    assert not (spots / "N051E000.json").exists()
    assert not (spots / "N051E001.json").exists()
    assert (spots / "README.txt").read_bytes() == b"keep"


def test_compute_new_version_compares_merged_envelopes():
    """Unowned envelopes are retained before comparing the complete dataset."""
    from src.publish import compute_new_version
    old_unowned = {"version": "2026.1", "spots": [{"id": "unowned"}]}
    old_owned = {"version": "2026.1", "spots": [{"id": "owned"}]}
    new_owned = {"version": "2026.1", "spots": [{"id": "owned"}, {"id": "new"}]}

    assert compute_new_version(
        {"N001E001": old_owned, "N999E999": old_unowned},
        {"N001E001": new_owned, "N999E999": old_unowned},
        2026,
    ) == ("2026.2", True)


def test_compute_new_version_stays_monotone_across_double_digit_minor_versions():
    """Preserved regional envelopes cannot make 2025.11 regress to 2025.10."""
    from src.publish import compute_new_version

    preserved_2025_9 = {"version": "2025.9", "spots": [{"id": "preserved-a"}]}
    preserved_2025_10 = {
        "version": "2025.10",
        "spots": [{"id": "preserved-b"}],
    }
    old_owned = {"version": "2025.9", "spots": [{"id": "owned"}]}
    new_owned = {
        "version": "2025.9",
        "spots": [{"id": "owned"}, {"id": "new"}],
    }

    result = compute_new_version(
        {
            "N041W006": old_owned,
            "N042W006": preserved_2025_9,
            "N043W006": preserved_2025_10,
        },
        {
            "N041W006": new_owned,
            "N042W006": preserved_2025_9,
            "N043W006": preserved_2025_10,
        },
        2025,
    )

    assert result == ("2025.11", True)


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
