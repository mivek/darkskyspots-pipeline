"""Tests for src/tile_export.py (atomic JSON write, spot diff, classification, empty tiles)."""
import json
import os
from pathlib import Path


def test_tile_envelope_structure():
    """Envelope has the strict key order: version, source, generated, tile, spots."""
    from src.tile_export import tile_envelope
    env = tile_envelope(
        tile_id_str="N042E001",
        spots=[{"id": "x", "lat": 42.0, "lon": 1.0, "darkness": 0.9}],
        version="2025.1",
        source="VIIRS 2025",
        generated="2026-02-15",
    )
    assert list(env.keys()) == ["version", "source", "generated", "tile", "spots"]


def test_write_tile_file_creates_file(tmp_path):
    """write_tile_file creates the file in {output_dir}/spots/."""
    from src.tile_export import write_tile_file
    out = write_tile_file(
        "N042E001",
        [{"id": "x", "lat": 42.0, "lon": 1.0, "darkness": 0.9}],
        str(tmp_path),
        "2025.1",
        "src",
        "2026-02-15",
    )
    assert Path(out).exists()
    assert Path(out).name == "N042E001.json"
    assert "spots" in str(out)


def test_write_tile_file_content(tmp_path):
    """Read back the JSON, verify envelope structure."""
    from src.tile_export import write_tile_file
    filepath = write_tile_file(
        "N042E001",
        [{"id": "x", "lat": 42.0, "lon": 1.0, "darkness": 0.9}],
        str(tmp_path),
        "2025.1",
        "src",
        "2026-02-15",
    )
    with open(filepath, encoding="utf-8") as f:
        env = json.load(f)
    assert env["version"] == "2025.1"
    assert env["tile"] == "N042E001"
    assert env["source"] == "src"
    assert env["generated"] == "2026-02-15"
    assert len(env["spots"]) == 1


def test_write_tile_file_utf8_no_bom(tmp_path):
    """First 3 bytes are not a UTF-8 BOM (\\xef\\xbb\\xbf)."""
    from src.tile_export import write_tile_file
    filepath = write_tile_file("N042E001", [], str(tmp_path), "v", "s", "g")
    with open(filepath, "rb") as f:
        first = f.read(3)
    assert first != b"\xef\xbb\xbf", "BOM detected"


def test_write_tile_file_trailing_newline(tmp_path):
    """File ends with \\n (D11)."""
    from src.tile_export import write_tile_file
    filepath = write_tile_file("N042E001", [], str(tmp_path), "v", "s", "g")
    with open(filepath, "rb") as f:
        content = f.read()
    assert content.endswith(b"\n"), f"File does not end with newline, last bytes: {content[-3:]!r}"


def test_write_tile_file_atomic(tmp_path):
    """The temporary file is cleaned up after the write."""
    from src.tile_export import write_tile_file
    filepath = write_tile_file("N042E001", [], str(tmp_path), "v", "s", "g")
    spots_dir = Path(tmp_path) / "spots"
    # No leftover .json.tmp / tmpXXXX files
    tmp_files = list(spots_dir.glob("*.tmp")) + list(spots_dir.glob("tmp*"))
    assert tmp_files == [], f"Leftover temp files: {tmp_files}"


# --- spot_diff tests ---

def test_spot_diff_identical():
    """No changes when both lists are identical."""
    from src.tile_export import spot_diff
    spots = [{"id": "a", "darkness": 0.9}, {"id": "b", "darkness": 0.7}]
    d = spot_diff(spots, spots)
    assert d["changed"] is False
    assert d["added"] == 0
    assert d["removed"] == 0
    assert d["modified"] == 0


def test_spot_diff_added():
    """One new spot -> added=1."""
    from src.tile_export import spot_diff
    old = [{"id": "a", "darkness": 0.9}]
    new = [{"id": "a", "darkness": 0.9}, {"id": "b", "darkness": 0.7}]
    d = spot_diff(old, new)
    assert d["changed"] is True
    assert d["added"] == 1
    assert d["removed"] == 0


def test_spot_diff_removed():
    """One spot removed -> removed=1."""
    from src.tile_export import spot_diff
    old = [{"id": "a", "darkness": 0.9}, {"id": "b", "darkness": 0.7}]
    new = [{"id": "a", "darkness": 0.9}]
    d = spot_diff(old, new)
    assert d["changed"] is True
    assert d["removed"] == 1


def test_spot_diff_modified():
    """Same id, different darkness -> modified=1."""
    from src.tile_export import spot_diff
    old = [{"id": "a", "darkness": 0.9}]
    new = [{"id": "a", "darkness": 0.8}]
    d = spot_diff(old, new)
    assert d["changed"] is True
    assert d["modified"] == 1


def test_spot_diff_all_empty():
    """Both empty -> no change."""
    from src.tile_export import spot_diff
    d = spot_diff([], [])
    assert d["changed"] is False


# --- classify_spots_into_tiles + load_existing_tile ---

def test_classify_spots_into_tiles():
    """5 spots across 2 tiles -> grouped correctly."""
    from src.tile_export import classify_spots_into_tiles
    spots = [
        {"lat": 42.0, "lon": 1.0, "darkness": 0.9},  # N042E001
        {"lat": 42.5, "lon": 1.5, "darkness": 0.8},  # N042E001
        {"lat": 43.0, "lon": 1.0, "darkness": 0.7},  # N043E001
        {"lat": 43.5, "lon": 1.5, "darkness": 0.6},  # N043E001
        {"lat": 42.2, "lon": 1.1, "darkness": 0.5},  # N042E001
    ]
    tiles = classify_spots_into_tiles(spots)
    assert "N042E001" in tiles and "N043E001" in tiles
    assert len(tiles["N042E001"]) == 3
    assert len(tiles["N043E001"]) == 2


def test_classify_spots_empty():
    """Empty list -> empty dict."""
    from src.tile_export import classify_spots_into_tiles
    assert classify_spots_into_tiles([]) == {}


def test_load_existing_tile_missing(tmp_path):
    """Nonexistent path -> None."""
    from src.tile_export import load_existing_tile
    assert load_existing_tile(str(tmp_path / "nope.json")) is None


def test_load_existing_tile_valid(tmp_path):
    """Write then read back."""
    from src.tile_export import write_tile_file, load_existing_tile
    filepath = write_tile_file(
        "N042E001",
        [{"id": "x", "lat": 42.0, "lon": 1.0, "darkness": 0.9}],
        str(tmp_path),
        "2025.1",
        "src",
        "2026-02-15",
    )
    env = load_existing_tile(filepath)
    assert env is not None
    assert env["tile"] == "N042E001"


# --- enumerate_tiles_in_bbox + write_empty_tiles (Task 8.4) ---

def test_enumerate_tiles_in_bbox():
    """bbox covering 4 tiles yields exactly 4 tile IDs."""
    from src.tile_export import enumerate_tiles_in_bbox
    # bbox = (-5, 41, -3, 43) covers lon [-5, -3), lat [41, 43) -> 2 lon × 2 lat = 4
    tiles = list(enumerate_tiles_in_bbox((-5, 41, -3, 43)))
    assert len(tiles) == 4
    assert set(tiles) == {"N041W005", "N041W004", "N042W005", "N042W004"}


def test_enumerate_tiles_in_bbox_empty():
    """Degenerate bbox (no area) yields nothing."""
    from src.tile_export import enumerate_tiles_in_bbox
    # bbox with lat_end <= lat_start
    assert list(enumerate_tiles_in_bbox((0, 0, 0, 0))) == []


def test_write_empty_tiles_all_empty(tmp_path):
    """All 4 tiles get a file with `spots: []`."""
    from src.tile_export import write_empty_tiles
    tiles = ["N041W005", "N041W004", "N042W005", "N042W004"]
    n = write_empty_tiles(tiles, str(tmp_path), "2025.1", "src", "2026-02-15")
    assert n == 4
    for tid in tiles:
        p = tmp_path / "spots" / f"{tid}.json"
        assert p.exists()
        with open(p, encoding="utf-8") as f:
            env = json.load(f)
        assert env["spots"] == []


def test_write_empty_tiles_partially_populated(tmp_path):
    """If 1 tile is already populated, only 3 empty tiles are written."""
    from src.tile_export import write_empty_tiles, write_tile_file
    # Populate N042E001
    write_tile_file(
        "N042E001",
        [{"id": "x", "lat": 42.0, "lon": 1.0, "darkness": 0.9}],
        str(tmp_path),
        "2025.1",
        "src",
        "2026-02-15",
    )
    tiles = ["N041E000", "N041E001", "N042E000", "N042E001"]
    n = write_empty_tiles(tiles, str(tmp_path), "2025.1", "src", "2026-02-15")
    assert n == 3
    # The populated one keeps its spot
    with open(tmp_path / "spots" / "N042E001.json", encoding="utf-8") as f:
        env = json.load(f)
    assert len(env["spots"]) == 1
