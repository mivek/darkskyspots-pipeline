"""Step 7: git operations and version management."""
import json
import logging
import re
import shutil
from collections.abc import Collection
import subprocess
from pathlib import Path
from .regions import owner_for_tile


logger = logging.getLogger(__name__)

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


def copy_spots_to_repo(
    local_spots_dir: str, data_repo_dir: str, owned_tile_ids: Collection[str] | None = None,
    *, country_codes: Collection[str] | None = None,
) -> None:
    """Merge a region's country block into published tile files.

    ``owned_tile_ids`` is retained as a compatibility parameter for callers
    from the old bbox-ownership model.  It no longer controls publication.
    """
    src = Path(local_spots_dir)
    dst = Path(data_repo_dir) / "spots"
    dst.mkdir(parents=True, exist_ok=True)
    src_files = {f.name: f for f in src.glob("*.json")}
    if country_codes is None:
        # Legacy behavior is useful for external callers, but the pipeline
        # always passes country_codes and uses the merge path below.
        names = {f"{tile_id}.json" for tile_id in (owned_tile_ids or ())}
        src_files = {name: path for name, path in src_files.items() if name in names}
        for name in sorted(names - src_files.keys()):
            (dst / name).unlink(missing_ok=True)
        for name, source in src_files.items():
            shutil.copy2(str(source), str(dst / name))
        logger.info("Purged %d stale owned tile(s)", len(names - src_files.keys()))
        return

    codes = {str(code).upper() for code in country_codes}
    changed = 0
    names_to_merge = set(src_files)
    # A country may have historical spots outside today's raster envelope.
    # Inspect all existing tiles and remove that country's old block even when
    # the current run has no replacement tile there.
    for target in dst.glob("*.json"):
        if target.name in names_to_merge:
            continue
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any(str(spot.get("country", "")).upper() in codes for spot in existing.get("spots", [])):
            names_to_merge.add(target.name)
    for name in sorted(names_to_merge):
        target = dst / name
        old_env = {}
        if target.exists():
            with target.open(encoding="utf-8") as handle:
                old_env = json.load(handle)
        if name in src_files:
            with src_files[name].open(encoding="utf-8") as handle:
                new_env = json.load(handle)
        else:
            new_env = dict(old_env)
            new_env["spots"] = []
        merged = merge_tile_envelopes(old_env, new_env, codes)
        if not merged["spots"]:
            if target.exists():
                target.unlink()
                changed += 1
            continue
        content = json.dumps(merged, indent=4, ensure_ascii=False) + "\n"
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
            changed += 1
    logger.info("Merged %d country publication tile(s)", changed)


def merge_tile_envelopes(
    old: dict | None,
    new: dict,
    country_codes: Collection[str],
) -> dict:
    """Return deterministic country-block merge without mutating inputs."""
    codes = {str(code).upper() for code in country_codes}
    old_spots = list((old or {}).get("spots", []))
    new_spots = list(new.get("spots", []))
    preserved = [spot for spot in old_spots if str(spot.get("country", "")).upper() not in codes]
    incoming = [spot for spot in new_spots if str(spot.get("country", "")).upper() in codes or not codes]
    # Country blocks are stable across run order; preserve pipeline order in
    # each block to avoid a repository-wide reorder during first migration.
    blocks: dict[str, list[dict]] = {}
    for spot in preserved + incoming:
        key = str(spot.get("country", "")).upper()
        blocks.setdefault(key, []).append(spot)
    ordered = [spot for key in sorted(blocks) for spot in blocks[key]]
    envelope = dict(old or new)
    for key in ("version", "source", "generated", "tile"):
        if key in new:
            envelope[key] = new[key]
    envelope["spots"] = ordered
    return envelope


def scan_orphan_tiles(
    spots_dir: str | Path,
    regions: dict[str, dict],
) -> dict[str, int]:
    """Legacy tile audit retained for compatibility; not used by pipeline."""
    orphan_counts: dict[str, int] = {}
    for json_path in sorted(Path(spots_dir).glob("*.json")):
        if owner_for_tile(json_path.stem, regions) is None:
            with open(json_path, encoding="utf-8") as source:
                envelope = json.load(source)
            if not isinstance(envelope, dict) or "spots" not in envelope:
                raise ValueError(
                    f"Invalid spot tile {json_path}: missing spots field"
                )
            spots = envelope["spots"]
            if not isinstance(spots, list):
                raise ValueError(
                    f"Invalid spot tile {json_path}: spots must be an array"
                )
            orphan_counts[json_path.stem] = len(spots)
    return orphan_counts


def audit_country_spots(spots_dir: str | Path, regions: dict[str, dict], *, geography=None) -> dict:
    """Read-only audit of country tags and configured-region coverage."""
    from .geography import load_geography
    from shapely.geometry import Point
    geography = geography or load_geography()
    configured = {
        str(code).upper()
        for region in regions.values()
        for code in (region.get("osm_country_code", []) if isinstance(region.get("osm_country_code", []), (list, tuple, set)) else [region.get("osm_country_code")])
    }
    result = {"missing": [], "invalid": [], "unconfigured": [], "mismatched": [], "ambiguous": [], "valid": 0}
    for path in sorted(Path(spots_dir).glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        for index, spot in enumerate(envelope.get("spots", [])):
            country = spot.get("country")
            ref = {"tile": path.stem, "index": index, "id": spot.get("id")}
            if not country:
                result["missing"].append(ref)
            elif not isinstance(country, str) or len(country) != 2 or not country.isalpha():
                result["invalid"].append(ref)
            elif country.upper() not in configured:
                result["unconfigured"].append({**ref, "country": country.upper()})
            else:
                try:
                    matches = geography.country_candidates(Point(float(spot["lon"]), float(spot["lat"])))
                except (KeyError, TypeError, ValueError):
                    matches = []
                if country.upper() not in matches:
                    result["mismatched"].append({**ref, "country": country.upper()})
                elif len(matches) > 1:
                    result["ambiguous"].append({**ref, "countries": matches})
                else:
                    result["valid"] += 1
    return result


def migrate_country_tags(
    spots_dir: str | Path,
    *,
    geography=None,
    data_dir=None,
    delete_orphans: bool = False,
    configured_codes: Collection[str] | None = None,
) -> dict:
    """Reclassify legacy spots; optionally delete unresolved country orphans.

    Existing tags are not trusted blindly: a configured tag whose coordinates
    now resolve to another country is reclassified as well.  Point-only
    boundary cases remain unresolved, because choosing a country without the
    source raster pixel would be arbitrary.
    """
    from .geography import classify_candidates
    from shapely.geometry import Point

    configured = {str(code).upper() for code in (configured_codes or ())}
    if geography is None:
        from .geography import load_geography
        geography = load_geography(data_dir) if data_dir else load_geography()
    all_codes = set(geography.country_codes)
    report = {"reclassified": 0, "deleted": 0, "ambiguous": 0}
    for path in sorted(Path(spots_dir).glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        spots = envelope.get("spots", [])
        pending = []
        for spot in spots:
            country = spot.get("country")
            try:
                matches = geography.country_candidates(
                    Point(float(spot["lon"]), float(spot["lat"]))
                )
            except (KeyError, TypeError, ValueError):
                matches = []
            if (
                not isinstance(country, str)
                or len(country) != 2
                or country.upper() not in matches
                or len(matches) != 1
                or (configured and country.upper() not in configured)
            ):
                pending.append(spot)
        if pending:
            classified, stats = classify_candidates(
                pending,
                all_codes,
                geography=geography,
                data_dir=data_dir,
                reject_ambiguous=True,
            )
            report["ambiguous"] += stats["ambiguous_country_candidates"]
            tagged = {id(spot): spot for spot in classified}
            pending_ids = {id(spot) for spot in pending}
            updated = []
            for spot in spots:
                if id(spot) in tagged:
                    resolved = str(spot.get("country", "")).upper()
                    # A valid but unconfigured country is still an orphan for
                    # this publication. It is only removed when deletion was
                    # explicitly authorized.
                    if configured and resolved not in configured and delete_orphans:
                        report["deleted"] += 1
                    else:
                        spot["country"] = resolved
                        updated.append(spot)
                        report["reclassified"] += 1
                elif id(spot) in pending_ids:
                    if delete_orphans:
                        report["deleted"] += 1
                    else:
                        updated.append(spot)
                else:
                    updated.append(spot)
            if not updated and delete_orphans:
                path.unlink()
                continue
            envelope["spots"] = updated
            path.write_text(
                json.dumps(envelope, indent=4, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return report


def prune_orphan_tiles(
    spots_dir: str | Path,
    regions: dict[str, dict],
) -> dict[str, int]:
    """Delete orphan tile JSONs and return their pre-delete spot counts."""
    orphan_counts = scan_orphan_tiles(spots_dir, regions)
    directory = Path(spots_dir)
    for tile_id in orphan_counts:
        (directory / f"{tile_id}.json").unlink()
    return orphan_counts


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
    - Find max version in old_envelopes by numeric (major, minor) components.
    - Compare `spots` arrays for every tile id. If any tile is added/removed/
      its `spots` differs, set changed=True.
    - If not changed -> (max_old_version, False).
    - If changed:
        - if max old version's year != year -> (f"{year}.1", True)
        - else (f"{year}.{old_minor + 1}", True)
    """
    if not old_envelopes:
        return f"{year}.1", True

    max_old_version = max(
        (env["version"] for env in old_envelopes.values()),
        key=lambda version: tuple(int(part) for part in version.split(".")),
    )
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
