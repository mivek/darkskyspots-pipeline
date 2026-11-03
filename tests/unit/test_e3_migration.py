"""One complete, immutable approval; the source is never a migration target."""

import json
from unittest.mock import patch

import pytest
from shapely.geometry import box

from src.e3_migration import assemble_snapshot
from src.geographic_publication import (
    GeographicApprovalError,
    payload_files,
    verify_approval,
)
from src.enrich import spot_id
from tests.geographic_helpers import SPOT, feature, make_e3_dataset, write_spots
from tests.unit.test_geographic_publication import repository, git


REGIONS = {
    name: dict(bbox=[w, 44, e, 46], osm_country_code=["FR"])
    for name, w, e in [("one", 0, 0.3), ("two", 0.3, 1), ("three", 1, 1.4)]
}


def inputs():
    return {
        name: {
            f"N045E{int(lon):03d}": dict(
                tile=f"N045E{int(lon):03d}",
                source="synthetic",
                version="2025.0",
                generated="fixed",
                spots=[
                    dict(
                        SPOT,
                        lon=lon,
                        id=spot_id(45, lon),
                        altitude=10,
                        nameDistanceKm=1.0,
                    )
                ],
            )
        }
        for name, lon in [("one", 0.1), ("two", 0.4), ("three", 1.1)]
    }


def test_coordinated_all_regions_cluster_once_then_approve_one_actual_tree(tmp_path):
    repo = repository(tmp_path / "source")
    before = payload_files(repo)
    head = git(repo, "rev-parse", "HEAD")
    ds = make_e3_dataset(tmp_path / "geo", regions=REGIONS)
    from src.clusters import write_cluster_files
    from src.geographic_publication import approve_tree

    with (
        patch(
            "src.e3_migration.write_cluster_files", wraps=write_cluster_files
        ) as clusters,
        patch("src.e3_migration.approve_tree", wraps=approve_tree) as approval,
    ):
        report = assemble_snapshot(
            repo,
            head,
            inputs(),
            tmp_path / "snapshot",
            ds,
            regions=REGIONS,
            year=2025,
            generated="2026-11-02T00:00:00Z",
            report_dir=tmp_path / "reports",
        )
    assert report["status"] == "passed" and report["final_spot_count"] == 3
    assert clusters.call_count == approval.call_count == 1
    assert report["tree_oid"] == git(tmp_path / "snapshot", "write-tree")
    assert git(tmp_path / "snapshot", "remote") == ""
    assert git(repo, "rev-parse", "HEAD") == head and payload_files(repo) == before
    assert git(repo, "status", "--porcelain") == ""
    write_spots(tmp_path / "snapshot", [dict(SPOT, altitude=999)])
    git(tmp_path / "snapshot", "add", "spots")
    with pytest.raises(ValueError, match="tree changed after"):
        verify_approval(tmp_path / "snapshot", report, ds)


def test_unresolved_region_blocks_complete_snapshot_and_source_is_unchanged(tmp_path):
    repo = repository(tmp_path / "source")
    before = payload_files(repo)
    head = git(repo, "rev-parse", "HEAD")
    ds = make_e3_dataset(
        tmp_path / "geo",
        regions=REGIONS,
        extra=[feature(box(1.05, 44.9, 1.15, 45.1), "water", id="legacy-unresolved")],
    )
    # Keeping a region's historical output is not an audit bypass.
    with pytest.raises(GeographicApprovalError) as error:
        assemble_snapshot(
            repo,
            head,
            inputs(),
            tmp_path / "snapshot",
            ds,
            regions=REGIONS,
            year=2025,
            generated="2026-11-02T00:00:00Z",
        )
    report = error.value.report
    assert report["final_spot_count"] == 3 and report["status"] == "blocked"
    held = [f for f in report["findings"] if f["status"] == "hold"]
    assert [f["region"] for f in held] == ["three"]
    assert held[0]["spot"]["lon"] == 1.1
    assert payload_files(repo) == before and git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""


def test_partial_migration_and_source_output_alias_are_rejected_before_clone(tmp_path):
    repo = repository(tmp_path / "source")
    ds = make_e3_dataset(tmp_path / "geo", regions=REGIONS)
    with pytest.raises(ValueError, match="every configured region"):
        assemble_snapshot(
            repo,
            "HEAD",
            {"one": inputs()["one"]},
            tmp_path / "snapshot",
            ds,
            regions=REGIONS,
            year=2025,
            generated="fixed",
        )
    with pytest.raises(ValueError, match="separate from the source"):
        assemble_snapshot(
            repo,
            "HEAD",
            inputs(),
            repo / "snapshot",
            ds,
            regions=REGIONS,
            year=2025,
            generated="fixed",
        )
