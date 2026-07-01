"""Step 7: git operations and version management."""
import json
import re
import shutil
import subprocess
from pathlib import Path

VERSION_PATTERN = re.compile(r'"version":\s*"(\d{4})\.(\d+)"')


def clone_data_repo(url: str, branch: str, target_dir: str) -> str:
    """Git clone --depth 1 --branch {branch} {url} {target_dir}. Returns target_dir."""
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", branch, url, target_dir],
        check=True,
        capture_output=True,
        text=True,
    )
    return target_dir


def copy_spots_to_repo(local_spots_dir: str, data_repo_dir: str) -> None:
    """
    Copy all JSON files from local_spots_dir to {data_repo_dir}/spots/.
    Removes existing files in the target that are not in source (purge stale tiles).
    """
    src = Path(local_spots_dir)
    dst = Path(data_repo_dir) / "spots"
    dst.mkdir(parents=True, exist_ok=True)

    dst_files = set(f.name for f in dst.glob("*.json"))
    src_files = set(f.name for f in src.glob("*.json"))
    for stale in dst_files - src_files:
        (dst / stale).unlink(missing_ok=True)

    for f in src.glob("*.json"):
        shutil.copy2(str(f), str(dst / f.name))


def commit_and_push(data_repo_dir: str, message: str) -> None:
    """Git add . -> commit -> push inside data_repo_dir."""
    subprocess.run(
        ["git", "add", "."], cwd=data_repo_dir, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=data_repo_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "push"], cwd=data_repo_dir, check=True, capture_output=True, text=True
    )


def get_current_version(data_repo_dir: str, year: int) -> str:
    """
    Scan existing tile JSONs in {data_repo_dir}/spots/ for the max version.
    Returns f"{year}.1" if no existing files (derives from --year, not hardcoded).
    """
    spots_dir = Path(data_repo_dir) / "spots"
    if not spots_dir.exists():
        return f"{year}.1"
    max_major = 0
    max_minor = 0
    for f in spots_dir.glob("*.json"):
        try:
            content = f.read_text(encoding="utf-8")
            m = VERSION_PATTERN.search(content)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                if (major, minor) > (max_major, max_minor):
                    max_major, max_minor = major, minor
        except (OSError, json.JSONDecodeError):
            continue
    if max_major == 0 and max_minor == 0:
        return f"{year}.1"
    return f"{max_major}.{max_minor}"


def bump_version(current: str, spots_changed: bool) -> str:
    """
    If spots_changed: increment the minor version.
    If not changed: return current as-is (no bump).
    """
    if not spots_changed:
        return current
    parts = current.split(".")
    major, minor = int(parts[0]), int(parts[1])
    return f"{major}.{minor + 1}"


def compute_new_version(
    old_envelopes: dict[str, dict],
    new_envelopes: dict[str, dict],
    year: int,
) -> tuple[str, bool]:
    """
    Compute the new tile-envelope version by diffing old vs new.

    Pure function — no I/O.

    Returns (new_version, changed: bool).

    Logic:
    - If old_envelopes is empty -> first run -> (f"{year}.1", True).
    - Find max version in old_envelopes (lexicographic on "YYYY.N").
    - Compare `spots` arrays for every tile id. If any tile is added/removed/
      its `spots` differs, set changed=True.
    - If not changed -> (max_old_version, False).
    - If changed:
        - if max old version's year != year -> (f"{year}.1", True)
        - else (f"{year}.{old_minor + 1}", True)
    """
    if not old_envelopes:
        return f"{year}.1", True

    max_old_version = max(env["version"] for env in old_envelopes.values())
    old_year_str, old_minor_str = max_old_version.split(".")
    old_year, old_minor = int(old_year_str), int(old_minor_str)

    all_tile_ids = set(old_envelopes) | set(new_envelopes)
    changed = False
    for tid in all_tile_ids:
        old_env = old_envelopes.get(tid)
        new_env = new_envelopes.get(tid)
        if old_env is None or new_env is None:
            changed = True
            break
        if old_env.get("spots") != new_env.get("spots"):
            changed = True
            break

    if not changed:
        return max_old_version, False

    if old_year != year:
        return f"{year}.1", True
    return f"{year}.{old_minor + 1}", True
