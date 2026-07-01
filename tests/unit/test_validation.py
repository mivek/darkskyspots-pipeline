"""Tests for src/validation.py (control-point comparison runner)."""
import json

import pytest


def test_load_checkpoints_valid(tmp_path):
    """Load from a valid JSON fixture."""
    from src.validation import load_checkpoints
    cp_path = tmp_path / "cp.json"
    cp_path.write_text(
        json.dumps([
            {"lat": 44.2, "lon": 3.6, "expected_bortle": 2, "name": "Cévennes"},
        ]),
        encoding="utf-8",
    )
    cps = load_checkpoints(str(cp_path))
    assert len(cps) == 1
    assert cps[0]["expected_bortle"] == 2


def test_load_checkpoints_invalid_not_array(tmp_path):
    """Non-array raises ValueError."""
    from src.validation import load_checkpoints
    cp_path = tmp_path / "bad.json"
    cp_path.write_text('{"not": "an array"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_checkpoints(str(cp_path))


def test_load_checkpoints_missing_field(tmp_path):
    """Missing lat/lon/expected_bortle raises ValueError."""
    from src.validation import load_checkpoints
    cp_path = tmp_path / "bad2.json"
    cp_path.write_text(
        json.dumps([{"name": "no-coords"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_checkpoints(str(cp_path))


def test_validate_bortle_match():
    """Match within ±1 counts as passing."""
    from src.validation import validate_bortle
    control_points = [{"lat": 44.2, "lon": 3.6, "expected_bortle": 2}]

    def darkness_fn(lat, lon):
        return 0.9

    def bortle_fn(lat, lon):
        return 3  # ±1 from 2 → match

    results = validate_bortle(control_points, darkness_fn, bortle_fn)
    assert results[0]["match"] is True
    assert results[0]["got_bortle"] == 3


def test_validate_bortle_tolerance_at_boundary():
    """Exactly ±1 boundary is still a match."""
    from src.validation import validate_bortle
    control_points = [{"lat": 44.2, "lon": 3.6, "expected_bortle": 2}]

    def darkness_fn(lat, lon):
        return 0.5

    def bortle_fn(lat, lon):
        return 1  # exactly ±1 from 2 → still a match

    results = validate_bortle(control_points, darkness_fn, bortle_fn)
    assert results[0]["match"] is True


def test_validate_bortle_outside_tolerance():
    """Difference > 1 → no match."""
    from src.validation import validate_bortle
    control_points = [{"lat": 44.2, "lon": 3.6, "expected_bortle": 2}]

    def darkness_fn(lat, lon):
        return 0.1

    def bortle_fn(lat, lon):
        return 5  # ±3 from 2 → no match

    results = validate_bortle(control_points, darkness_fn, bortle_fn)
    assert results[0]["match"] is False


def test_append_checkpoint_results(tmp_path):
    """Append results and verify they are persisted."""
    from src.validation import load_checkpoints, append_checkpoint_results
    cp_path = tmp_path / "cp.json"
    cp_path.write_text("[]", encoding="utf-8")

    results = [
        {"lat": 44.2, "lon": 3.6, "expected_bortle": 2, "got_bortle": 3, "match": True},
    ]
    append_checkpoint_results(str(cp_path), results, run_id="test-001")
    cps = load_checkpoints(str(cp_path))
    assert len(cps) == 1
    assert cps[0]["run_id"] == "test-001"
    assert cps[0]["got_bortle"] == 3
