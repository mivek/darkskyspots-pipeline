"""Step 7: git operations and version management."""
import json
import logging
import re
from collections import Counter
from collections.abc import Collection, Mapping
import subprocess
from pathlib import Path

from .regions import RegionOwnership, build_region_ownerships


logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r'"version":\s*"(\d{4})\.(\d+)"')
CLUSTER_MANIFEST_PATH = "clusters/index.json"


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
    local_spots_dir: str | Path | None,
    data_repo_dir: str | Path,
    *,
    ownership: RegionOwnership | None = None,
    envelopes: Mapping[str, dict] | None = None,
) -> dict[str, int]:
    """Publish one region's envelopes while preserving spots outside its extent.

    ``envelopes`` is the preferred input: it is the current run's in-memory
    output and cannot accidentally contain stale files from a previous run.
    ``local_spots_dir`` remains as a narrow compatibility adapter for callers
    that explicitly provide a staging directory.  It is never used when
    ``envelopes`` is supplied.

    The former tile-ownership argument is deliberately absent.  An old caller
    therefore fails at the Python boundary rather than silently deleting a
    neighbour's spots.
    """
    if ownership is None:
        raise ValueError("ownership is required for geographic publication")
    if envelopes is None:
        if local_spots_dir is None:
            raise ValueError("envelopes or local_spots_dir is required")
        src = Path(local_spots_dir)
        envelopes = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(src.glob("*.json"))
        }

    _validate_unique_ids(envelopes)
    for tile_id, envelope in envelopes.items():
        for spot in envelope.get("spots", []):
            if not ownership.owns(spot):
                raise ValueError(
                    f"envelope {tile_id} contains spot {spot.get('id')!r} "
                    "outside the published region extent"
                )

    dst = Path(data_repo_dir) / "spots"
    dst.mkdir(parents=True, exist_ok=True)

    old_envelopes: dict[str, dict] = {}
    # Inspect every old tile: a region can have stale spots outside today's
    # raster envelope but inside its currently declared geographic extent.
    for target in dst.glob("*.json"):
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        old_envelopes[target.stem] = existing

    merged_envelopes = merge_publication_envelopes(
        old_envelopes, dict(envelopes), ownership
    )
    names_to_merge = {
        tile_id
        for tile_id, envelope in envelopes.items()
        if any(ownership.owns(spot)
               for spot in envelope.get("spots", []))
    }
    names_to_merge.update(
        tile_id
        for tile_id, envelope in old_envelopes.items()
        if any(ownership.owns(spot)
               for spot in envelope.get("spots", []))
    )
    changed = 0
    deleted = 0
    for name in sorted(names_to_merge):
        target = dst / f"{name}.json"
        merged = merged_envelopes.get(name)
        if merged is None or not merged["spots"]:
            if target.exists():
                target.unlink()
                changed += 1
                deleted += 1
            continue
        content = json.dumps(merged, indent=4, ensure_ascii=False) + "\n"
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
            changed += 1
    logger.info("Merged %d regional publication tile(s), deleted %d", changed, deleted)
    return {"changed": changed, "deleted": deleted}


def merge_tile_envelopes(
    old: dict | None,
    new: dict,
    ownership: RegionOwnership,
) -> dict:
    """Replace only old spots owned by the current regional extent."""
    old_spots = list((old or {}).get("spots", []))
    new_spots = list(new.get("spots", []))
    preserved = [spot for spot in old_spots if not ownership.owns(spot)]
    incoming = list(new_spots)
    if any(not ownership.owns(spot) for spot in incoming):
        raise ValueError("incoming spot outside the published region extent")
    _validate_unique_spots(old_spots, context="old envelope")
    _validate_unique_spots(new_spots, context="new envelope")
    # Country blocks are stable across run order; preserve pipeline order in
    # each block to avoid a repository-wide reorder during first migration.
    blocks: dict[str, list[dict]] = {}
    for spot in preserved + incoming:
        key = str(spot.get("country", "")).upper()
        blocks.setdefault(key, []).append(spot)
    preserved_codes = {str(spot.get("country", "")).upper() for spot in preserved}
    incoming_codes = {str(spot.get("country", "")).upper() for spot in incoming}
    shared_in_tile = preserved_codes & incoming_codes & ownership.shared_codes
    ordered = [
        spot
        for key in sorted(blocks)
        for spot in (
            sorted(blocks[key], key=lambda item: str(item.get("id", "")))
            if key in shared_in_tile else blocks[key]
        )
    ]
    # An empty current-region envelope is not a publication event for this
    # tile.  Do not let its run timestamp/source rewrite a neighbour-only tile.
    metadata_source = new if incoming else {"tile": new.get("tile", (old or {}).get("tile"))}
    envelope = _stable_envelope_metadata(old, metadata_source)
    envelope["spots"] = ordered
    _validate_unique_spots(ordered, context="merged envelope")
    return envelope


def _stable_envelope_metadata(old: dict | None, new: dict) -> dict:
    """Combine envelope metadata associatively and deterministically.

    ``version`` advances monotonically; descriptive fields use the
    lexicographically smallest value seen.  This makes a shared tile's full
    JSON independent of whether country A or B was published first, while
    leaving metadata unchanged when the incoming envelope carries no metadata.
    """
    old = old or {}
    result: dict = {}
    for key in ("source", "generated"):
        values = [value for value in (old.get(key), new.get(key)) if value is not None]
        if values:
            result[key] = min(values, key=lambda value: str(value))
    versions = [value for value in (old.get("version"), new.get("version")) if value]
    if versions:
        try:
            result["version"] = max(
                versions,
                key=lambda value: tuple(int(part) for part in str(value).split(".")),
            )
        except (TypeError, ValueError):
            result["version"] = max(str(value) for value in versions)
    tile = new.get("tile", old.get("tile"))
    if tile is not None:
        result["tile"] = tile
    # Preserve any forward-compatible envelope fields deterministically.
    for key in sorted(set(old) | set(new)):
        if key in {"spots", "source", "generated", "version", "tile"}:
            continue
        values = [value for envelope in (old, new) if (value := envelope.get(key)) is not None]
        if values:
            result[key] = min(values, key=lambda value: json.dumps(value, sort_keys=True))
    return result


def merge_publication_envelopes(
    old_envelopes: Mapping[str, dict],
    new_envelopes: Mapping[str, dict],
    ownership: RegionOwnership,
) -> dict[str, dict]:
    """Purely merge a complete current run into a published dataset.

    Tiles are considered as a set, so a region's previous spots are removed
    even when its current run has no replacement in that tile.  Empty result
    tiles are omitted, which is the publication instruction to delete them.
    Inputs are never mutated and output ordering is independent of run order.
    """
    if not ownership.countries:
        raise ValueError("at least one country geometry is required")
    _validate_unique_ids(old_envelopes)
    _validate_unique_ids(new_envelopes)
    result: dict[str, dict] = {}
    for tile_id in sorted(set(old_envelopes) | set(new_envelopes)):
        old = old_envelopes.get(tile_id)
        new = new_envelopes.get(tile_id)
        if new is None:
            new = {"tile": tile_id, "spots": []}
        merged = merge_tile_envelopes(old, new, ownership)
        if merged.get("spots"):
            result[tile_id] = merged
    _validate_unique_ids(result)
    return result


def _validate_unique_spots(spots: Collection[dict], *, context: str) -> None:
    ids: set[object] = set()
    for spot in spots:
        if "id" not in spot:
            continue
        spot_id = spot["id"]
        if spot_id in ids:
            raise ValueError(f"duplicate spot id {spot_id!r} in {context}")
        ids.add(spot_id)


def _validate_unique_ids(envelopes: Mapping[str, dict]) -> None:
    seen: dict[object, str] = {}
    for tile_id, envelope in envelopes.items():
        for spot in envelope.get("spots", []):
            if "id" not in spot:
                continue
            spot_id = spot["id"]
            previous = seen.get(spot_id)
            if previous is not None:
                raise ValueError(
                    f"duplicate spot id {spot_id!r} in tiles {previous} and {tile_id}"
                )
            seen[spot_id] = str(tile_id)


def _spot_analysis(spot: dict, geography, configured: set[str]) -> dict:
    """Classify one historical spot without changing it."""
    from shapely.geometry import Point

    raw_tag = spot.get("country")
    valid_tag = isinstance(raw_tag, str) and len(raw_tag) == 2 and raw_tag.isalpha()
    tag = raw_tag.upper() if valid_tag else None
    if raw_tag is None or raw_tag == "":
        tag_status = "missing"
    elif valid_tag:
        tag_status = "valid"
    else:
        tag_status = "invalid"
    try:
        point = Point(float(spot["lon"]), float(spot["lat"]))
        matches = geography.country_candidates(point)
    except (KeyError, TypeError, ValueError):
        matches = []
    matches = sorted(set(str(code).upper() for code in matches))
    if len(matches) > 1:
        kind = "ambiguous"
        resolved = None
    elif not matches:
        kind = "unassignable"
        resolved = None
    else:
        resolved = matches[0]
        if not valid_tag:
            kind = tag_status
        elif tag != resolved:
            kind = "mismatched"
        elif tag not in configured:
            kind = "unconfigured"
        else:
            kind = "valid"
    return {
        "kind": kind,
        "tag_status": tag_status,
        "tag": tag,
        "resolved": resolved,
        "matches": matches,
    }


def plan_country_tag_migration(
    envelopes: Mapping[str, dict],
    geography,
    regions: Mapping[str, dict],
    *,
    delete_orphans: bool = False,
) -> dict:
    """Pure migration/audit planner over in-memory tile envelopes.

    The returned envelopes are deep enough not to mutate the input spots.  A
    spot with exactly one geographic country is reclassified. Unconfigured,
    maritime and outside-region spots remain untouched unless ``delete_orphans``
    is requested. Country-ambiguous points are never auto-assigned; points with
    ambiguous regional ownership block migration before any write.
    """
    import copy

    ownerships = build_region_ownerships(dict(regions), geography=geography)
    configured = {code for ownership in ownerships.values() for code in ownership.countries}
    projected = {tile_id: copy.deepcopy(envelope) for tile_id, envelope in envelopes.items()}
    report = {
        "total_files": len(envelopes),
        "total_spots": 0,
        "valid": 0,
        "missing": 0,
        "invalid": 0,
        "unconfigured": 0,
        "mismatched": 0,
        "ambiguous": 0,
        "unassignable": 0,
        "outside_declared_extents": 0,
        "ambiguous_ownership": 0,
        # Projection-oriented counters.  These are deliberately distinct
        # from the current tag-state counters above: a missing tag can be
        # reclassifiable, and an untagged spot can resolve to an unconfigured
        # country without being an ``unconfigured`` tag today.
        "reclassifiable_to_configured": 0,
        "resolved_unconfigured": 0,
        "correctable_mismatched": 0,
        "reclassified": 0,
        "deleted": 0,
    }
    changed_files: set[str] = set()
    deleted_files: set[str] = set()
    for tile_id, envelope in envelopes.items():
        output_spots = []
        for spot in envelope.get("spots", []):
            report["total_spots"] += 1
            analysis = _spot_analysis(spot, geography, configured)
            kind = analysis["kind"]
            tag_status = analysis["tag_status"]
            if tag_status in {"missing", "invalid"}:
                report[tag_status] += 1
            if kind in {"unconfigured", "mismatched", "ambiguous", "unassignable", "valid"}:
                report[kind] += 1
            resolved = analysis["resolved"]
            owner_count = 0
            if resolved and resolved in configured:
                resolved_spot = {**spot, "country": resolved}
                owner_count = sum(
                    ownership.owns(resolved_spot)
                    for ownership in ownerships.values()
                )
                if owner_count == 0:
                    report["outside_declared_extents"] += 1
                elif owner_count > 1:
                    report["ambiguous_ownership"] += 1
            if resolved and len(analysis["matches"]) == 1:
                if resolved in configured:
                    if analysis["tag"] != resolved:
                        report["reclassifiable_to_configured"] += 1
                    if tag_status == "valid" and analysis["tag"] != resolved:
                        report["correctable_mismatched"] += 1
                else:
                    report["resolved_unconfigured"] += 1
            if (delete_orphans and resolved in configured and owner_count == 0):
                report["deleted"] += 1
                changed_files.add(tile_id)
            elif resolved and len(analysis["matches"]) == 1 and resolved in configured:
                if spot.get("country") != resolved:
                    updated = copy.deepcopy(spot)
                    updated["country"] = resolved
                    output_spots.append(updated)
                    report["reclassified"] += 1
                    changed_files.add(tile_id)
                else:
                    output_spots.append(copy.deepcopy(spot))
            elif delete_orphans and (
                # A unique country outside the configured set is an orphan
                # even when its historical tag claims a different country
                # (for example FR tag, coordinates resolved to AD).
                (resolved and len(analysis["matches"]) == 1 and resolved not in configured)
                or kind in {"unassignable", "ambiguous", "missing", "invalid"}
            ):
                report["deleted"] += 1
                changed_files.add(tile_id)
            else:
                output_spots.append(copy.deepcopy(spot))
        projected[tile_id] = copy.deepcopy(envelope)
        projected[tile_id]["spots"] = output_spots
        if delete_orphans and not output_spots:
            deleted_files.add(tile_id)
    for tile_id in deleted_files:
        projected.pop(tile_id, None)
    report["rewritten_files"] = len(changed_files)
    report["deleted_files"] = len(deleted_files)
    report["final_spots"] = sum(len(e.get("spots", [])) for e in projected.values())
    return {"envelopes": projected, "report": report}


def _read_envelopes(spots_dir: str | Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in sorted(Path(spots_dir).glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            envelope = json.load(handle)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("spots"), list):
            raise ValueError(f"Invalid spot tile {path}: missing spots array")
        result[path.stem] = envelope
    return result


def audit_publication_country_codes(
    spots_dir: str | Path,
    *,
    forbidden_codes: Collection[str] = (),
) -> dict:
    """Count country tags in published tiles without changing any file.

    The returned report is suitable for a reproducible post-publication audit.
    ``forbidden_codes`` is intentionally evaluated after normalising tags to
    upper case, so a malformed lower-case tag cannot evade the Crown
    dependency check performed by the audit tool.
    """
    directory = Path(spots_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Spot tile directory does not exist: {directory}")
    envelopes = _read_envelopes(directory)
    counts: Counter[str] = Counter()
    total_spots = 0
    for envelope in envelopes.values():
        for spot in envelope["spots"]:
            raw_country = spot.get("country")
            country = (
                raw_country.strip().upper()
                if isinstance(raw_country, str) and raw_country.strip()
                else "<missing>"
            )
            counts[country] += 1
            total_spots += 1

    forbidden = {
        code: counts.get(code, 0)
        for code in sorted({str(code).strip().upper() for code in forbidden_codes})
        if counts.get(code, 0)
    }
    return {
        "spots_dir": str(spots_dir),
        "tile_count": len(envelopes),
        "spot_count": total_spots,
        "country_counts": dict(sorted(counts.items())),
        "forbidden_counts": forbidden,
    }


def audit_country_spots(
    spots_dir: str | Path,
    regions: Mapping[str, dict],
    *,
    geography=None,
) -> dict:
    """Read-only audit with compact projected migration reports."""
    from .geography import load_geography

    geography = geography or load_geography()
    envelopes = _read_envelopes(spots_dir)
    migration = plan_country_tag_migration(envelopes, geography, regions)
    pruned = plan_country_tag_migration(
        envelopes, geography, regions, delete_orphans=True
    )
    report = migration["report"]
    # Keep the legacy keys as counts so callers can gate publication without
    # serializing thousands of individual references.  ``summary`` is the
    # stable compact representation intended for CLI output.
    result = {key: report.get(key, 0) for key in (
        "missing", "invalid", "unconfigured", "mismatched", "ambiguous", "valid",
        "unassignable", "reclassifiable_to_configured", "resolved_unconfigured",
        "correctable_mismatched", "outside_declared_extents", "ambiguous_ownership",
    )}
    result["summary"] = report
    result["projection"] = {
        "migration_only": migration["report"],
        "migration_and_prune": pruned["report"],
    }
    return result


def migrate_country_tags(
    spots_dir: str | Path,
    *,
    geography=None,
    data_dir=None,
    delete_orphans: bool = False,
    regions: Mapping[str, dict],
) -> dict:
    """Apply an already fully planned country migration atomically by plan.

    All files are parsed and classified before the first write.  A planning or
    validation error therefore leaves the repository untouched.
    """
    if geography is None:
        from .geography import load_geography
        geography = load_geography(data_dir) if data_dir else load_geography()
    directory = Path(spots_dir)
    envelopes = _read_envelopes(directory)
    planned = plan_country_tag_migration(
        envelopes, geography, regions, delete_orphans=delete_orphans
    )
    result = planned["report"]
    if result["ambiguous_ownership"]:
        raise ValueError("Historical spots have ambiguous regional ownership")
    for tile_id, envelope in planned["envelopes"].items():
        if tile_id not in envelopes or envelope != envelopes[tile_id]:
            (directory / f"{tile_id}.json").write_text(
                json.dumps(envelope, indent=4, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    if delete_orphans:
        for tile_id in set(envelopes) - set(planned["envelopes"]):
            (directory / f"{tile_id}.json").unlink(missing_ok=True)
    return result


def _read_manifest_generated(raw: str, source: str) -> str:
    """Return a valid cache-invalidation value from a cluster manifest."""
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"Cannot publish spots: {source} is not valid JSON. "
            "Regenerate clusters before publishing."
        ) from exc
    generated = manifest.get("generated") if isinstance(manifest, dict) else None
    if not isinstance(generated, str) or not generated.strip():
        raise RuntimeError(
            f"Cannot publish spots: {source} has no valid 'generated' value. "
            "Regenerate clusters before publishing."
        )
    return generated


def _validate_spot_publication(data_repo_dir: str | Path) -> None:
    """Ensure staged spot changes have a newly generated cluster manifest."""
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--"],
        cwd=data_repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    staged_paths = {path for path in staged.split("\0") if path}
    if not any(path == "spots" or path.startswith("spots/") for path in staged_paths):
        return

    if CLUSTER_MANIFEST_PATH not in staged_paths:
        raise RuntimeError(
            "Cannot publish spots: clusters/index.json is not staged. "
            "Regenerate clusters before publishing spot changes."
        )

    staged_manifest = subprocess.run(
        ["git", "show", f":{CLUSTER_MANIFEST_PATH}"],
        cwd=data_repo_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if staged_manifest.returncode != 0:
        raise RuntimeError(
            "Cannot publish spots: clusters/index.json is missing or unreadable. "
            "Regenerate clusters before publishing."
        )
    new_generated = _read_manifest_generated(
        staged_manifest.stdout,
        "the staged cluster manifest",
    )

    old_result = subprocess.run(
        ["git", "show", f"HEAD:{CLUSTER_MANIFEST_PATH}"],
        cwd=data_repo_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if old_result.returncode == 0:
        old_generated = _read_manifest_generated(
            old_result.stdout,
            "the HEAD cluster manifest",
        )
        if old_generated == new_generated:
            raise RuntimeError(
                "Cannot publish spots: clusters/index.json.generated is unchanged. "
                "Regenerate clusters before publishing spot changes."
            )


def commit_and_push(data_repo_dir: str, message: str) -> None:
    """Git add . -> validate -> commit -> push inside data_repo_dir."""
    subprocess.run(
        ["git", "add", "."], cwd=data_repo_dir, check=True, capture_output=True, text=True
    )
    _validate_spot_publication(data_repo_dir)
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
