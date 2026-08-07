import hashlib
import json
from pathlib import Path

import pytest

from src.clusters import aggregate_spot_files, write_cluster_files


def spot(identifier, lat, lon, darkness, altitude=120):
    return {
        "id": identifier,
        "lat": lat,
        "lon": lon,
        "darkness": darkness,
        "bortle": 3,
        "near": "A commune",
        "altitude": altitude,
    }


def write_tile(directory: Path, tile_id: str, spots: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tile_id}.json").write_text(
        json.dumps(
            {
                "version": "2025.1",
                "source": "test",
                "generated": "2026-08-03",
                "tile": tile_id,
                "spots": spots,
            }
        ),
        encoding="utf-8",
    )


def make_known_spots(tmp_path: Path) -> Path:
    spots_dir = tmp_path / "spots"
    write_tile(
        spots_dir,
        "N048E002",
        [spot("z", 48.1, 2.1, 0.9), spot("a", 48.2, 2.2, 0.9)],
    )
    write_tile(spots_dir, "N048E003", [spot("singleton", 48.6, 3.0, 0.7)])
    return spots_dir


def test_negative_coordinates_use_floor_and_normalize_zero(tmp_path):
    write_tile(tmp_path, "S001W001", [spot("a", lat=-0.0, lon=-0.0, darkness=0.5)])

    levels = aggregate_spot_files(tmp_path)

    assert any(cluster["id"].startswith("L1_0_0") for cluster in levels[1])
    assert all("-0.0" not in json.dumps(cluster) for cluster in levels[1])


def test_strictly_negative_coordinates_use_floor_cell_indices(tmp_path):
    write_tile(tmp_path, "S001W002", [spot("a", lat=-0.1, lon=-1.1, darkness=0.5)])

    cluster = aggregate_spot_files(tmp_path)[1][0]
    _, longitude_index, latitude_index = cluster["id"].split("_")

    assert (int(longitude_index), int(latitude_index)) == (-4, -1)
    assert cluster["id"] == "L1_-4_-1"


def test_representative_uses_darkness_then_id(tmp_path):
    write_tile(
        tmp_path,
        "N048E002",
        [
            {
                **spot("z", lat=48.1, lon=2.1, darkness=0.9, altitude=100),
                "name": "legacy field",
            },
            spot("a", lat=48.2, lon=2.2, darkness=0.9, altitude=120),
        ],
    )

    cluster = aggregate_spot_files(tmp_path)[1][0]

    assert cluster["rep"]["id"] == "a"
    assert cluster["rep"]["altitude"] == 120
    assert set(cluster["rep"]) == {"id", "lat", "lon", "darkness", "bortle", "near", "altitude"}


def test_missing_cluster_spot_field_names_tile_and_spot(tmp_path):
    write_tile(
        tmp_path,
        "N048E002",
        [{"id": "bad", "lat": 48.1, "lon": 2.1}],
    )

    with pytest.raises(ValueError, match=r"N048E002\.json.*spot 0.*darkness"):
        aggregate_spot_files(tmp_path)


def test_centroid_bbox_and_singleton(tmp_path):
    write_tile(
        tmp_path,
        "N048E002",
        [spot("a", 48.2, 2.4, 0.8), spot("b", 48.25, 2.45, 0.7)],
    )
    write_tile(tmp_path, "N048E003", [spot("singleton", 48.6, 3.0, 0.7)])

    clusters = aggregate_spot_files(tmp_path)
    cluster = next(c for c in clusters[1] if c["count"] == 2)

    assert cluster["lat"] == pytest.approx(48.225)
    assert cluster["lon"] == pytest.approx(2.425)
    assert cluster["bbox"] == [2.4, 48.2, 2.45, 48.25]
    assert any(c["count"] == 1 for c in clusters[1])


def test_empty_input_writes_six_files_and_manifest(tmp_path):
    output = tmp_path / "clusters"

    write_cluster_files(tmp_path / "spots", output, data_year=2025, generated="2026-08-03")

    assert [json.loads((output / f"L{i}.json").read_text()) for i in range(1, 7)] == [
        [],
        [],
        [],
        [],
        [],
        [],
    ]
    manifest = json.loads((output / "index.json").read_text())
    assert manifest["schema"] == 1
    assert manifest["generated"] == "2026-08-03"
    assert manifest["data_year"] == 2025
    assert [level["level"] for level in manifest["levels"]] == [1, 2, 3, 4, 5, 6]
    assert [level["cell_deg"] for level in manifest["levels"]] == [
        0.3,
        0.6,
        1.2,
        2.4,
        4.8,
        9.6,
    ]
    assert [level["width_km"] for level in manifest["levels"]] == [
        [100, 200],
        [200, 400],
        [400, 800],
        [800, 1600],
        [1600, 3200],
        [3200, 6400],
    ]
    assert all(isinstance(level["files"], list) for level in manifest["levels"])
    assert all(len(level["files"]) == 1 for level in manifest["levels"])
    assert [level["files"][0]["path"] for level in manifest["levels"]] == [
        "L1.json",
        "L2.json",
        "L3.json",
        "L4.json",
        "L5.json",
        "L6.json",
    ]


def test_level_files_are_compact_while_manifest_is_pretty(tmp_path):
    output = tmp_path / "clusters"

    write_cluster_files(
        make_known_spots(tmp_path), output, data_year=2025, generated="2026-08-03"
    )

    level_payload = (output / "L1.json").read_bytes()
    manifest_payload = (output / "index.json").read_bytes()
    assert b": " not in level_payload
    assert b": " in manifest_payload


def test_repeated_generation_is_byte_identical(tmp_path):
    spots_dir = make_known_spots(tmp_path)
    first = tmp_path / "first" / "clusters"
    second = tmp_path / "second" / "clusters"

    write_cluster_files(spots_dir, first, data_year=2025, generated="2026-08-03")
    write_cluster_files(spots_dir, second, data_year=2025, generated="2026-08-03")

    for name in ["index.json", *[f"L{i}.json" for i in range(1, 7)]]:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_manifest_hash_matches_exact_level_bytes(tmp_path):
    output = tmp_path / "clusters"

    write_cluster_files(make_known_spots(tmp_path), output, data_year=2025, generated="2026-08-03")

    manifest = json.loads((output / "index.json").read_text())
    for level in manifest["levels"]:
        entry = level["files"][0]
        payload = (output / entry["path"]).read_bytes()
        assert entry["hash"] == hashlib.sha256(payload).hexdigest()


def test_manifest_level_hash_can_change_independently(tmp_path):
    """A fine-level representative change leaves coarser level identities stable."""
    before_spots = tmp_path / "before" / "spots"
    after_spots = tmp_path / "after" / "spots"
    shared = [
        spot("a", lat=0.1, lon=0.1, darkness=0.8),
        spot("global", lat=0.1, lon=0.4, darkness=1.0),
    ]
    write_tile(
        before_spots,
        "N000E000",
        [*shared, spot("b", lat=0.2, lon=0.2, darkness=0.7)],
    )
    write_tile(
        after_spots,
        "N000E000",
        [*shared, spot("b", lat=0.2, lon=0.2, darkness=0.9)],
    )
    before_output = tmp_path / "before" / "clusters"
    after_output = tmp_path / "after" / "clusters"

    write_cluster_files(
        before_spots, before_output, data_year=2025, generated="2026-08-03"
    )
    write_cluster_files(
        after_spots, after_output, data_year=2025, generated="2026-08-03"
    )

    before_manifest = json.loads((before_output / "index.json").read_text())
    after_manifest = json.loads((after_output / "index.json").read_text())
    before_hashes = {
        level["level"]: level["files"][0]["hash"]
        for level in before_manifest["levels"]
    }
    after_hashes = {
        level["level"]: level["files"][0]["hash"]
        for level in after_manifest["levels"]
    }
    assert before_hashes[1] != after_hashes[1]
    assert {level: before_hashes[level] for level in range(2, 7)} == {
        level: after_hashes[level] for level in range(2, 7)
    }


def test_manifest_paths_resolve_from_manifest_directory_for_local_clusters(tmp_path):
    output_root = tmp_path / "output"
    clusters_dir = output_root / "clusters-local"

    write_cluster_files(
        tmp_path / "spots",
        clusters_dir,
        data_year=2025,
        generated="2026-08-03",
    )

    manifest = json.loads((clusters_dir / "index.json").read_text())
    for level in manifest["levels"]:
        entry = level["files"][0]
        declared_path = clusters_dir / entry["path"]
        assert declared_path.is_file()
        assert entry["path"] == f"L{level['level']}.json"
        assert entry["hash"] == hashlib.sha256(declared_path.read_bytes()).hexdigest()


def test_local_cluster_aggregation_skips_unowned_staging_tile(tmp_path):
    write_tile(tmp_path, "N048E002", [spot("inside", 48.2, 2.2, 0.8)])
    write_tile(tmp_path, "N051E000", [spot("outside", 51.5, 0.2, 0.99)])

    clusters = aggregate_spot_files(tmp_path, allowed_tile_ids={"N048E002"})

    ids = {cluster["rep"]["id"] for values in clusters.values() for cluster in values}
    assert ids == {"inside"}


def test_clusters_are_sorted_by_id_not_cell_tuple(tmp_path):
    write_tile(
        tmp_path,
        "S001W001",
        [
            spot("minus-two", -0.01, -0.4, 0.5),
            spot("minus-one", -0.01, -0.1, 0.5),
        ],
    )

    ids = [cluster["id"] for cluster in aggregate_spot_files(tmp_path)[1]]

    assert ids == sorted(ids)
