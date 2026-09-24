"""Tests for src/publish.py (git operations + version management)."""
import json
import hashlib
import logging
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
from shapely.geometry import box


class _CodeOwnership:
    """Test double for legacy country-block examples without coordinates."""

    def __init__(self, codes):
        self.countries = {code: None for code in codes}
        self.shared_codes = frozenset()

    def owns(self, spot):
        return spot.get("country") in self.countries


def _ownership(codes):
    return _CodeOwnership(codes)


def _regions(codes):
    return {"test": {"bbox": [-180, -90, 180, 90], "osm_country_code": codes}}


class _GeometryGeography:
    def __init__(self, countries):
        self.countries = countries

    def country_candidates(self, point):
        return sorted(code for code, geometry in self.countries.items() if geometry.covers(point))


def _es_regions():
    return {
        "canaries": {"bbox": [-19, 27, -13, 30], "osm_country_code": ["ES"]},
        "mainland": {"bbox": [-10, 35, 5, 45], "osm_country_code": ["ES"]},
    }


def _es_spot(spot_id, lat, lon):
    return {"id": spot_id, "country": "ES", "lat": lat, "lon": lon}


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


def test_copy_spots_to_repo_merges_country_and_preserves_neighbor(tmp_path):
    """A publication replaces only its country block in a shared tile."""
    from src.publish import copy_spots_to_repo
    dst = tmp_path / "dst_repo"
    (dst / "spots").mkdir(parents=True)
    _write_envelope(dst / "spots" / "N001E001.json", "N001E001", [
        {"id": "fr-old", "country": "FR"},
        {"id": "es", "country": "ES"},
    ])
    incoming = {"N001E001": {"tile": "N001E001", "version": "2", "spots": [
        {"id": "fr-new", "country": "FR"},
    ]}}
    copy_spots_to_repo(None, dst, ownership=_ownership(["FR"]), envelopes=incoming)
    result = json.loads((dst / "spots" / "N001E001.json").read_text())
    assert [spot["id"] for spot in result["spots"]] == ["ES".lower(), "fr-new"]


def test_multi_country_publication_replaces_only_gb_ie_and_preserves_fr(tmp_path):
    """The first GB/IE publication keeps FR in a genuinely shared tile."""
    from src.publish import copy_spots_to_repo

    repo = tmp_path / "repo"
    spots_dir = repo / "spots"
    spots_dir.mkdir(parents=True)
    _write_envelope(
        spots_dir / "N050W001.json",
        "N050W001",
        [
            {"id": "fr", "country": "FR"},
            {"id": "gb-old", "country": "GB"},
            {"id": "ie-old", "country": "IE"},
            {"id": "es", "country": "ES"},
        ],
    )

    copy_spots_to_repo(
        None,
        repo,
        ownership=_ownership(["GB", "IE"]),
        envelopes={
            "N050W001": {
                "tile": "N050W001",
                "version": "2026.1",
                "spots": [
                    {"id": "gb-new", "country": "GB"},
                    {"id": "ie-new", "country": "IE"},
                ],
            }
        },
    )

    result = json.loads((spots_dir / "N050W001.json").read_text(encoding="utf-8"))
    assert [spot["id"] for spot in result["spots"]] == [
        "es", "fr", "gb-new", "ie-new"
    ]


def test_empty_gb_ie_envelope_is_deterministic_and_keeps_neighbor(tmp_path):
    """An empty UK/Ireland run cannot rewrite or remove a neighbour tile."""
    from src.publish import copy_spots_to_repo, merge_publication_envelopes

    old = {
        "N050W001": {
            "version": "2026.4",
            "source": "france",
            "generated": "2026-08-01T00:00:00Z",
            "tile": "N050W001",
            "spots": [{"id": "fr", "country": "FR"}],
        }
    }
    empty_run = {
        "N050W001": {
            "version": "2026.5",
            "source": "uk_ireland",
            "generated": "2026-08-02T00:00:00Z",
            "tile": "N050W001",
            "spots": [],
        }
    }
    expected = merge_publication_envelopes(old, empty_run, _ownership(["GB", "IE"]))
    assert expected == old
    assert merge_publication_envelopes(old, empty_run, _ownership(["IE", "GB"])) == expected

    repo = tmp_path / "repo"
    spots_dir = repo / "spots"
    spots_dir.mkdir(parents=True)
    path = spots_dir / "N050W001.json"
    original = json.dumps(old["N050W001"], ensure_ascii=False) + "\n"
    path.write_text(original, encoding="utf-8")
    copy_spots_to_repo(None, repo, ownership=_ownership(["GB", "IE"]), envelopes=empty_run)
    assert path.read_text(encoding="utf-8") == original
    copy_spots_to_repo(None, repo, ownership=_ownership(["IE", "GB"]), envelopes=empty_run)
    assert path.read_text(encoding="utf-8") == original


def _write_envelope(path, tile_id, spots):
    path.write_text(
        json.dumps({"version": "2026.1", "tile": tile_id, "spots": spots}),
        encoding="utf-8",
    )


def test_merge_publication_is_commutative_and_detects_duplicate_ids():
    from src.publish import merge_publication_envelopes
    a = {"T": {"version": "1", "spots": [{"id": "a", "country": "FR"}]}}
    b = {"T": {"version": "1", "spots": [{"id": "b", "country": "AD"}]}}
    ab = merge_publication_envelopes(
        merge_publication_envelopes({}, a, _ownership(["FR"])), b, _ownership(["AD"])
    )
    ba = merge_publication_envelopes(
        merge_publication_envelopes({}, b, _ownership(["AD"])), a, _ownership(["FR"])
    )
    assert ab == ba
    duplicate = {"T": {"spots": [{"id": "a", "country": "FR"}, {"id": "a", "country": "FR"}]}}
    with pytest.raises(ValueError, match="duplicate spot id"):
        merge_publication_envelopes({}, duplicate, _ownership(["FR"]))


def test_merge_publication_stabilizes_metadata_across_run_order():
    from src.publish import merge_publication_envelopes
    a = {"T": {"version": "2026.2", "source": "z", "generated": "2026-02-02", "tile": "T", "spots": [{"id": "a", "country": "FR"}]}}
    b = {"T": {"version": "2026.1", "source": "a", "generated": "2026-01-01", "tile": "T", "spots": [{"id": "b", "country": "AD"}]}}
    ab = merge_publication_envelopes(merge_publication_envelopes({}, a, _ownership(["FR"])), b, _ownership(["AD"]))
    ba = merge_publication_envelopes(merge_publication_envelopes({}, b, _ownership(["AD"])), a, _ownership(["FR"]))
    assert ab == ba
    assert ab["T"]["version"] == "2026.2"
    assert ab["T"]["source"] == "a"


def test_real_natural_earth_andorra_and_france_share_merged_tile():
    """Andorra remains present when France republishes their shared tile."""
    from shapely.geometry import Point
    from src.geography import load_geography
    from src.publish import merge_publication_envelopes

    geography = load_geography()
    assert geography.country_candidates(Point(1.55, 42.55)) == ["AD"]
    assert geography.country_candidates(Point(1.50, 42.70)) == ["FR"]
    france = {"N042E001": {"tile": "N042E001", "version": "1", "spots": [
        {"id": "fr", "country": "FR", "lat": 42.70, "lon": 1.50},
    ]}}
    andorra = {"N042E001": {"tile": "N042E001", "version": "1", "spots": [
        {"id": "ad", "country": "AD", "lat": 42.55, "lon": 1.55},
    ]}}
    merged = merge_publication_envelopes(
        merge_publication_envelopes({}, france, _ownership(["FR"])), andorra, _ownership(["AD"])
    )
    assert {spot["country"] for spot in merged["N042E001"]["spots"]} == {"AD", "FR"}


@pytest.mark.parametrize("first", ["canaries", "mainland"])
def test_two_es_regions_publish_without_erasing_each_other(tmp_path, first):
    from shapely.ops import unary_union
    from src.publish import copy_spots_to_repo
    from src.regions import build_region_ownerships

    geo = _GeometryGeography({"ES": unary_union([
        box(-18, 27, -13, 30), box(-10, 35, 5, 45),
    ])})
    ownerships = build_region_ownerships(_es_regions(), geography=geo)
    runs = {
        "canaries": {"N028W018": {"tile": "N028W018", "spots": [
            _es_spot("canary", 28.5, -17.5),
        ]}},
        "mainland": {"N040W004": {"tile": "N040W004", "spots": [
            _es_spot("mainland", 40.5, -3.5),
        ]}},
    }
    with pytest.raises(ValueError, match="outside the published region extent"):
        copy_spots_to_repo(None, tmp_path, ownership=ownerships["mainland"], envelopes=runs["canaries"])
    assert not (tmp_path / "spots").exists()
    order = [first, "mainland" if first == "canaries" else "canaries"]
    for name in order:
        copy_spots_to_repo(None, tmp_path, ownership=ownerships[name], envelopes=runs[name])
    assert {path.stem for path in (tmp_path / "spots").glob("*.json")} == {
        "N028W018", "N040W004",
    }
    copy_spots_to_repo(None, tmp_path, ownership=ownerships["mainland"], envelopes={
        "N040W004": {"tile": "N040W004", "spots": [_es_spot("mainland-new", 40.6, -3.6)]},
    })
    assert [s["id"] for s in json.loads((tmp_path / "spots" / "N028W018.json").read_text())["spots"]] == ["canary"]
    assert [s["id"] for s in json.loads((tmp_path / "spots" / "N040W004.json").read_text())["spots"]] == ["mainland-new"]
    copy_spots_to_repo(None, tmp_path, ownership=ownerships["mainland"], envelopes={})
    assert (tmp_path / "spots" / "N028W018.json").exists()
    assert not (tmp_path / "spots" / "N040W004.json").exists()


def test_same_country_spots_in_shared_tile_have_stable_order():
    from src.publish import merge_publication_envelopes
    from src.regions import RegionOwnership

    west = RegionOwnership({"ES": box(0, 0, 0.5, 1)}, frozenset({"ES"}))
    east = RegionOwnership({"ES": box(0.5, 0, 1, 1)}, frozenset({"ES"}))
    a = {"T": {"tile": "T", "spots": [
        _es_spot("z-west", 0.5, 0.25), _es_spot("b-west", 0.6, 0.25),
    ]}}
    b = {"T": {"tile": "T", "spots": [_es_spot("a-east", 0.5, 0.75)]}}
    a_only = merge_publication_envelopes({}, a, west)
    assert [s["id"] for s in a_only["T"]["spots"]] == ["z-west", "b-west"]
    ab = merge_publication_envelopes(a_only, b, east)
    ba = merge_publication_envelopes(merge_publication_envelopes({}, b, east), a, west)
    assert ab == ba
    assert [s["id"] for s in ab["T"]["spots"]] == ["a-east", "b-west", "z-west"]


def test_shrunken_bbox_preserves_old_spot_but_audit_projects_explicit_prune(tmp_path):
    from src.publish import audit_country_spots, merge_publication_envelopes, migrate_country_tags
    from src.regions import build_region_ownership

    geo = _GeometryGeography({"ES": box(0, 0, 3, 1)})
    regions = {"mainland": {"bbox": [0, 0, 1, 1], "osm_country_code": ["ES"]}}
    ownership = build_region_ownership(regions["mainland"], geography=geo)
    old = {
        "T0": {"tile": "T0", "spots": [_es_spot("inside-old", 0.5, 0.5)]},
        "T2": {"tile": "T2", "spots": [_es_spot("outside-old", 0.5, 2.5)]},
    }
    new = {"T0": {"tile": "T0", "spots": [_es_spot("inside-new", 0.6, 0.6)]}}
    merged = merge_publication_envelopes(old, new, ownership)
    assert [s["id"] for s in merged["T0"]["spots"]] == ["inside-new"]
    assert merged["T2"] == old["T2"]

    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    for tile_id, envelope in old.items():
        (spots_dir / f"{tile_id}.json").write_text(json.dumps(envelope))
    before = {p.name: p.read_bytes() for p in spots_dir.glob("*.json")}
    audit = audit_country_spots(spots_dir, regions, geography=geo)
    assert audit["outside_declared_extents"] == 1
    assert audit["projection"]["migration_only"]["deleted"] == 0
    assert audit["projection"]["migration_and_prune"]["deleted"] == 1
    assert audit["projection"]["migration_and_prune"]["deleted_files"] == 1
    assert {p.name: p.read_bytes() for p in spots_dir.glob("*.json")} == before
    from run import _audit_before_write
    with patch("run.audit_country_spots", side_effect=lambda path, configured: audit_country_spots(
        path, configured, geography=geo,
    )):
        assert _audit_before_write(spots_dir, regions) is False
    assert {p.name: p.read_bytes() for p in spots_dir.glob("*.json")} == before

    migrate_country_tags(spots_dir, geography=geo, regions=regions)
    assert {p.name: p.read_bytes() for p in spots_dir.glob("*.json")} == before
    migrate_country_tags(spots_dir, geography=geo, regions=regions, delete_orphans=True)
    assert (spots_dir / "T0.json").exists()
    assert not (spots_dir / "T2.json").exists()


def test_shared_boundary_spot_reports_ambiguous_ownership(tmp_path):
    from src.publish import audit_country_spots, migrate_country_tags

    geo = _GeometryGeography({"ES": box(0, 0, 2, 1)})
    regions = {
        "west": {"bbox": [0, 0, 1, 1], "osm_country_code": ["ES"]},
        "east": {"bbox": [1, 0, 2, 1], "osm_country_code": ["ES"]},
    }
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    _write_envelope(spots_dir / "T.json", "T", [_es_spot("boundary", 0.5, 1)])
    original = (spots_dir / "T.json").read_bytes()
    assert audit_country_spots(spots_dir, regions, geography=geo)["ambiguous_ownership"] == 1
    with pytest.raises(ValueError, match="ambiguous regional ownership"):
        migrate_country_tags(spots_dir, geography=geo, regions=regions, delete_orphans=True)
    assert (spots_dir / "T.json").read_bytes() == original


class _FakeGeography:
    country_codes = ("AD", "FR", "ES")

    def __init__(self, matches):
        self.matches = matches
        self.countries = {code: box(-180, -90, 180, 90) for code in self.country_codes}

    def country_candidates(self, point):
        return self.matches.get((round(point.y, 3), round(point.x, 3)), [])


def test_migration_reclassifies_corrects_preserves_and_prunes(tmp_path):
    from src.publish import migrate_country_tags

    spots = tmp_path / "spots"
    spots.mkdir()
    records = [
        {"id": "missing", "lat": 1, "lon": 1},
        {"id": "wrong", "country": "ES", "lat": 2, "lon": 2},
        {"id": "unconfigured", "country": "AD", "lat": 3, "lon": 3},
        {"id": "sea", "country": "FR", "lat": 4, "lon": 4},
        {"id": "ambiguous", "country": "FR", "lat": 5, "lon": 5},
    ]
    _write_envelope(spots / "T.json", "T", records)
    geo = _FakeGeography({
        (1, 1): ["FR"], (2, 2): ["FR"], (3, 3): ["AD"],
        (4, 4): [], (5, 5): ["FR", "ES"],
    })
    report = migrate_country_tags(spots, geography=geo, regions=_regions(["FR"]))
    assert report["reclassified"] == 2
    after = json.loads((spots / "T.json").read_text())["spots"]
    assert {s["id"] for s in after} == {"missing", "wrong", "unconfigured", "sea", "ambiguous"}
    assert {s["id"]: s.get("country") for s in after}["missing"] == "FR"
    assert {s["id"]: s.get("country") for s in after}["wrong"] == "FR"

    report = migrate_country_tags(
        spots, geography=geo, regions=_regions(["FR"]), delete_orphans=True
    )
    assert report["deleted"] == 3
    after = json.loads((spots / "T.json").read_text())["spots"]
    assert {s["id"] for s in after} == {"missing", "wrong"}


def test_migration_prunes_configured_tag_resolved_to_unconfigured_country(tmp_path):
    from src.publish import migrate_country_tags
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "T.json", "T", [
        {"id": "historical-fr", "country": "FR", "lat": 3, "lon": 3},
    ])
    report = migrate_country_tags(
        spots,
        geography=_FakeGeography({(3, 3): ["AD"]}),
        regions=_regions(["FR"]),
        delete_orphans=True,
    )
    assert report["deleted"] == 1
    assert not (spots / "T.json").exists()


def test_audit_returns_projection_without_mutating_files(tmp_path):
    from src.publish import audit_country_spots
    spots = tmp_path / "spots"
    spots.mkdir()
    path = spots / "T.json"
    _write_envelope(path, "T", [{"id": "x", "country": "ES", "lat": 1, "lon": 1}])
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    result = audit_country_spots(
        spots, _regions(["FR"]),
        geography=_FakeGeography({(1, 1): ["FR"]}),
    )
    assert result["mismatched"] == 1
    assert result["reclassifiable_to_configured"] == 1
    assert result["correctable_mismatched"] == 1
    assert result["resolved_unconfigured"] == 0
    assert result["projection"]["migration_and_prune"]["deleted"] == 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime


def test_audit_publication_country_codes_counts_and_reports_crown_dependencies(tmp_path):
    from src.publish import audit_publication_country_codes

    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(
        spots / "N050W001.json",
        "N050W001",
        [
            {"id": "fr", "country": "fr"},
            {"id": "gb", "country": "GB"},
            {"id": "im", "country": "IM"},
            {"id": "missing"},
        ],
    )

    report = audit_publication_country_codes(
        spots, forbidden_codes=["IM", "JE", "GG"]
    )

    assert report["tile_count"] == 1
    assert report["spot_count"] == 4
    assert report["country_counts"] == {
        "GB": 1, "FR": 1, "IM": 1, "<missing>": 1
    }
    assert report["forbidden_counts"] == {"IM": 1}


def test_publication_audit_tool_reports_counts_and_fails_for_crown_code(tmp_path):
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(
        spots / "N050W001.json",
        "N050W001",
        [{"id": "gb", "country": "GB"}, {"id": "gg", "country": "GG"}],
    )

    result = subprocess.run(
        [sys.executable, "tools/audit_publication.py", str(spots)],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)[0]
    assert report["country_counts"] == {"GB": 1, "GG": 1}
    assert report["forbidden_counts"] == {"GG": 1}
    assert "forbidden Crown dependency" in result.stderr


def test_audit_distinguishes_missing_invalid_ambiguous_and_unassignable(tmp_path):
    from src.publish import audit_country_spots
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "A.json", "A", [
        {"id": "missing", "lat": 1, "lon": 1},
        {"id": "invalid", "country": "FRA", "lat": 1, "lon": 1},
        {"id": "ambiguous", "country": "FR", "lat": 2, "lon": 2},
        {"id": "sea", "country": "FR", "lat": 3, "lon": 3},
    ])
    result = audit_country_spots(
        spots, _regions(["FR"]),
        geography=_FakeGeography({(1, 1): ["FR"], (2, 2): ["FR", "ES"], (3, 3): []}),
    )
    assert result["missing"] == 1
    assert result["invalid"] == 1
    assert result["ambiguous"] == 1
    assert result["unassignable"] == 1
    assert result["reclassifiable_to_configured"] == 2
    assert result["resolved_unconfigured"] == 0
    assert result["correctable_mismatched"] == 0
    assert result["projection"]["migration_and_prune"]["deleted"] == 2


def test_audit_reports_untagged_spot_resolved_to_unconfigured_country(tmp_path):
    from src.publish import audit_country_spots
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "A.json", "A", [{"id": "ad", "lat": 1, "lon": 1}])
    result = audit_country_spots(
        spots, _regions(["FR"]),
        geography=_FakeGeography({(1, 1): ["AD"]}),
    )
    assert result["missing"] == 1
    assert result["reclassifiable_to_configured"] == 0
    assert result["resolved_unconfigured"] == 1
    assert result["projection"]["migration_and_prune"]["resolved_unconfigured"] == 1


def _git_snapshot(path):
    files = {}
    for file_path in sorted(path.rglob("*.json")):
        files[str(file_path.relative_to(path))] = (
            file_path.read_bytes(), file_path.stat().st_mtime_ns,
            hashlib.sha256(file_path.read_bytes()).hexdigest(),
        )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=path,
        check=True, capture_output=True, text=True,
    ).stdout
    return files, status


def _init_test_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=path, check=True)


def test_audit_success_preserves_content_sha_mtime_and_git_status(tmp_path):
    from src.publish import audit_country_spots
    repo = tmp_path / "repo"
    spots = repo / "spots"
    spots.mkdir(parents=True)
    _write_envelope(spots / "A.json", "A", [{"id": "x", "country": "ES", "lat": 1, "lon": 1}])
    _init_test_git_repo(repo)
    before = _git_snapshot(repo)
    audit_country_spots(
        spots, _regions(["FR"]),
        geography=_FakeGeography({(1, 1): ["FR"]}),
    )
    assert _git_snapshot(repo) == before


def test_audit_error_mid_scan_preserves_content_sha_mtime_and_git_status(tmp_path):
    from src.publish import audit_country_spots
    repo = tmp_path / "repo"
    spots = repo / "spots"
    spots.mkdir(parents=True)
    _write_envelope(spots / "A.json", "A", [{"id": "x", "country": "ES", "lat": 1, "lon": 1}])
    (spots / "B.json").write_text('{"tile":"B","spots":', encoding="utf-8")
    _init_test_git_repo(repo)
    before = _git_snapshot(repo)
    with pytest.raises(json.JSONDecodeError):
        audit_country_spots(
            spots, _regions(["FR"]),
            geography=_FakeGeography({(1, 1): ["FR"]}),
        )
    assert _git_snapshot(repo) == before


def test_migration_error_after_first_file_is_non_mutating(tmp_path):
    from src.publish import migrate_country_tags
    spots = tmp_path / "spots"
    spots.mkdir()
    _write_envelope(spots / "A.json", "A", [{"id": "a", "country": "ES", "lat": 1, "lon": 1}])
    _write_envelope(spots / "B.json", "B", [{"id": "b", "country": "ES", "lat": 2, "lon": 2}])
    before = {path: path.read_bytes() for path in spots.glob("*.json")}

    class ExplodingGeography(_FakeGeography):
        def country_candidates(self, point):
            if round(point.y, 3) == 2:
                raise RuntimeError("mid-migration failure")
            return super().country_candidates(point)

    with pytest.raises(RuntimeError, match="mid-migration failure"):
        migrate_country_tags(
            spots, geography=ExplodingGeography({(1, 1): ["FR"]}),
            regions=_regions(["FR"]),
        )
    assert {path: path.read_bytes() for path in spots.glob("*.json")} == before


def test_copy_uses_memory_and_ignores_stale_staging_file(tmp_path):
    from src.publish import copy_spots_to_repo
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_envelope(staging / "T.json", "T", [{"id": "stale", "country": "FR"}])
    repo = tmp_path / "repo"
    (repo / "spots").mkdir(parents=True)
    _write_envelope(repo / "spots" / "T.json", "T", [{"id": "old", "country": "FR"}])
    current = {"T": {"tile": "T", "version": "2", "spots": [{"id": "fresh", "country": "FR"}]}}
    copy_spots_to_repo(staging, repo, ownership=_ownership(["FR"]), envelopes=current)
    ids = [s["id"] for s in json.loads((repo / "spots" / "T.json").read_text())["spots"]]
    assert ids == ["fresh"]


def test_empty_current_envelope_does_not_rewrite_neighbor_tile(tmp_path):
    from src.publish import copy_spots_to_repo
    repo = tmp_path / "repo"
    (repo / "spots").mkdir(parents=True)
    path = repo / "spots" / "T.json"
    original = b'{"version":"1","generated":"old","tile":"T","spots":[{"id":"es","country":"ES"}]}\n'
    path.write_bytes(original)
    copy_spots_to_repo(
        None, repo, ownership=_ownership(["FR"]),
        envelopes={"T": {"version": "2", "generated": "new", "tile": "T", "spots": []}},
    )
    assert path.read_bytes() == original


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
    """The central guard runs between git add and commit."""
    from src.publish import commit_and_push
    with patch("src.publish.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        commit_and_push("/tmp/repo", "test message")
    assert mock_run.call_count == 4
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert cmds[0] == ["git", "add", "."]
    assert cmds[1] == ["git", "diff", "--cached", "--name-only", "-z", "--"]
    assert cmds[2][0:3] == ["git", "commit", "-m"]
    assert cmds[2][3] == "test message"
    assert cmds[3] == ["git", "push"]


def _init_publish_git_repo(path, *, generated="old", with_manifest=True):
    """Create a temporary repository with a local push target."""
    spots = path / "spots"
    spots.mkdir(parents=True)
    _write_envelope(
        spots / "T.json", "T", [{"id": "old", "country": "FR"}]
    )
    if with_manifest:
        clusters = path / "clusters"
        clusters.mkdir()
        (clusters / "index.json").write_text(
            json.dumps({"generated": generated, "data_year": 2025}),
            encoding="utf-8",
        )
    _init_test_git_repo(path)
    remote = path.parent / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=path, check=True)
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=path, check=True, capture_output=True)
    return path


def _change_spot(path):
    spot_path = path / "spots" / "T.json"
    envelope = json.loads(spot_path.read_text(encoding="utf-8"))
    envelope["spots"][0]["id"] = "new"
    spot_path.write_text(json.dumps(envelope), encoding="utf-8")


def _change_manifest(path, generated):
    manifest_path = path / "clusters" / "index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated"] = generated
    manifest["test_marker"] = generated
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _git_head(path, *args):
    return subprocess.run(
        ["git", "show", "HEAD:" + "/".join(args)],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_commit_and_push_rejects_spot_only_publication(tmp_path):
    from src.publish import commit_and_push
    repo = _init_publish_git_repo(tmp_path / "repo")
    _change_spot(repo)

    with pytest.raises(RuntimeError, match="Regenerate clusters before publishing"):
        commit_and_push(str(repo), "spot change")

    assert json.loads(_git_head(repo, "spots", "T.json"))["spots"][0]["id"] == "old"


def test_commit_and_push_rejects_unchanged_manifest_generated(tmp_path):
    from src.publish import commit_and_push
    repo = _init_publish_git_repo(tmp_path / "repo", generated="same")
    _change_spot(repo)
    _change_manifest(repo, "same")

    with pytest.raises(RuntimeError, match="generated is unchanged"):
        commit_and_push(str(repo), "spot change")

    assert json.loads(_git_head(repo, "spots", "T.json"))["spots"][0]["id"] == "old"


def test_commit_and_push_allows_spot_publication_with_changed_manifest(tmp_path):
    from src.publish import commit_and_push
    repo = _init_publish_git_repo(tmp_path / "repo", generated="old")
    _change_spot(repo)
    _change_manifest(repo, "new")

    commit_and_push(str(repo), "spot change")

    assert json.loads(_git_head(repo, "spots", "T.json"))["spots"][0]["id"] == "new"
    assert json.loads(_git_head(repo, "clusters", "index.json"))["generated"] == "new"


def test_commit_and_push_allows_cluster_only_publication(tmp_path):
    from src.publish import commit_and_push
    repo = _init_publish_git_repo(tmp_path / "repo", generated="old")
    _change_manifest(repo, "new")
    (repo / "clusters" / "L1.json").write_text("[]", encoding="utf-8")

    commit_and_push(str(repo), "cluster refresh")

    assert json.loads(_git_head(repo, "clusters", "index.json"))["generated"] == "new"
    assert json.loads(_git_head(repo, "spots", "T.json"))["spots"][0]["id"] == "old"


def test_commit_and_push_allows_first_manifest_with_spot_publication(tmp_path):
    from src.publish import commit_and_push
    repo = _init_publish_git_repo(tmp_path / "repo", with_manifest=False)
    _change_spot(repo)
    clusters = repo / "clusters"
    clusters.mkdir()
    (clusters / "index.json").write_text(
        json.dumps({"generated": "first", "data_year": 2025}),
        encoding="utf-8",
    )

    commit_and_push(str(repo), "initial clusters")

    assert json.loads(_git_head(repo, "clusters", "index.json"))["generated"] == "first"


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
