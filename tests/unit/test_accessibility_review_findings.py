import copy
import hashlib
import json

import pytest

from tools.audit_accessibility import (
    _cluster_index_payload,
    _load_pinned_cluster_index,
    pretty_bytes,
    sha256_file,
)
from tools.compare_accessibility_pilots import compare


def _query_record():
    candidate = {
        "osm": "way/42",
        "osm_type": "way",
        "osm_id": "42",
        "distanceMExact": 10.125,
        "distanceM": 10,
        "access": {"foot": {"state": "unknown"}, "motorcar": {"state": "unknown"}},
        "nearestPoint": [-16.5, 28.3],
        "tags": {"highway": "primary"},
    }
    return {
        "location": {"id": "spot-1", "role": "generated", "lat": 28.3, "lon": -16.5},
        "radiusM": 500,
        "sourceId": "tfe-source",
        "coverageStatus": "complete",
        "coverageReason": None,
        "metrics": {"querySeconds": 0.1, "geometryReads": 2},
        "referenceAltitudeUsedM": 51,
        "referenceElevation": {"elevationM": 51.046, "reason": None, "waterClass": 0},
        "nearbyRestrictionsComplete": True,
        "nearbyRestrictions": [{"osm": "relation/9", "restriction": "no_entry"}],
        "classes": {
            "road": {
                "status": "found",
                "reason": None,
                "selected": copy.deepcopy(candidate),
                "nearestCandidate": copy.deepcopy(candidate),
                "candidates": [copy.deepcopy(candidate)],
                "nearestElevation": {
                    "elevationDeltaM": -1,
                    "elevationUnknownReason": None,
                    "evidence": {
                        "elevationDeltaMExact": -1.269512,
                        "featureSample": {"elevationM": 52.269512, "reason": None, "waterClass": 0},
                        "referenceSample": {"elevationM": 51.046, "reason": None, "waterClass": 0},
                    },
                },
            },
        },
    }


def _write_queries(tmp_path, reference, candidate):
    reference_path = tmp_path / "reference.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    reference_path.write_text(json.dumps(reference) + "\n", encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
    return reference_path, candidate_path


def test_tenerife_comparator_accepts_identical_complete_evidence(tmp_path):
    row = _query_record()
    reference_path, candidate_path = _write_queries(tmp_path, row, copy.deepcopy(row))

    result = compare(reference_path, candidate_path)

    assert result["query_count"] == result["exact_match_count"] == 1
    assert result["difference_count"] == 0
    assert result["schema"] == 2


def test_tenerife_comparator_detects_selected_feature_change(tmp_path):
    reference = _query_record()
    candidate = copy.deepcopy(reference)
    candidate["classes"]["road"]["selected"]["osm"] = "way/43"
    reference_path, candidate_path = _write_queries(tmp_path, reference, candidate)

    result = compare(reference_path, candidate_path)

    assert result["difference_count"] == 1
    assert "classes.road.selected.osm" in result["differences"][0]["changed"]


def test_tenerife_comparator_detects_removed_selected_feature(tmp_path):
    reference = _query_record()
    candidate = copy.deepcopy(reference)
    del candidate["classes"]["road"]["selected"]
    reference_path, candidate_path = _write_queries(tmp_path, reference, candidate)

    result = compare(reference_path, candidate_path)

    assert result["difference_count"] == 1
    assert "classes.road.selected" in result["differences"][0]["changed"]


def test_tenerife_comparator_detects_exact_elevation_change_with_same_rounding(tmp_path):
    reference = _query_record()
    candidate = copy.deepcopy(reference)
    candidate["classes"]["road"]["nearestElevation"]["evidence"]["elevationDeltaMExact"] = -1.269513
    assert round(reference["classes"]["road"]["nearestElevation"]["evidence"]["elevationDeltaMExact"]) == \
        round(candidate["classes"]["road"]["nearestElevation"]["evidence"]["elevationDeltaMExact"])
    reference_path, candidate_path = _write_queries(tmp_path, reference, candidate)

    result = compare(reference_path, candidate_path)

    assert result["difference_count"] == 1
    assert "classes.road.nearestElevation.evidence.elevationDeltaMExact" in \
        result["differences"][0]["changed"]


def test_tenerife_comparator_uses_strict_json_number_types(tmp_path):
    reference = _query_record()
    candidate = copy.deepcopy(reference)
    reference["classes"]["road"]["nearestCandidate"]["distanceMExact"] = 10.0
    candidate["classes"]["road"]["nearestCandidate"]["distanceMExact"] = 10
    reference_path, candidate_path = _write_queries(tmp_path, reference, candidate)

    result = compare(reference_path, candidate_path)

    assert result["difference_count"] == 1
    assert "classes.road.nearestCandidate.distanceMExact" in result["differences"][0]["changed"]


def test_tenerife_comparator_excludes_only_declared_identity_and_timing_fields(tmp_path):
    reference = _query_record()
    candidate = copy.deepcopy(reference)
    candidate["sourceId"] = "can-g1-source"
    candidate["metrics"]["querySeconds"] = 9.75
    reference_path, candidate_path = _write_queries(tmp_path, reference, candidate)

    result = compare(reference_path, candidate_path)

    assert result["difference_count"] == 0
    assert "classes.road.selected" not in result["comparison_policy"]["excluded_paths"]
    assert "metrics.querySeconds" in result["comparison_policy"]["excluded_paths"]


def test_cluster_index_enrichment_keeps_the_pinned_object_and_file_unchanged(tmp_path):
    base_index = {
        "schema": 1,
        "generated": "pinned-time",
        "levels": [{
            "level": 1,
            "width_km": [100, 200],
            "files": [{"path": "L1.json", "hash": "published-hash"}],
        }],
    }
    index_path = tmp_path / "index.json"
    source_bytes = pretty_bytes(base_index)
    index_path.write_bytes(source_bytes)
    published_index = json.loads(index_path.read_text(encoding="utf-8"))
    expected_published = copy.deepcopy(published_index)

    enriched_index = _cluster_index_payload(
        published_index, {"L1.json": {"sha256": "enriched-hash", "size": 321}},
    )

    assert index_path.read_bytes() == source_bytes
    assert published_index == expected_published
    assert published_index == json.loads(source_bytes)
    assert enriched_index is not published_index
    assert enriched_index["levels"] is not published_index["levels"]
    assert enriched_index["levels"][0]["files"][0] == {
        "path": "L1.json", "hash": "enriched-hash", "size": 321,
        "baseline_hash": "published-hash",
    }
    assert enriched_index["levels"][0]["width_km"] == [100, 200]

    enriched_index["levels"][0]["files"][0]["hash"] = "later-local-change"
    assert published_index == expected_published


def test_audit_loads_published_cluster_index_only_when_pinned_hash_matches(tmp_path):
    source = {
        "schema": 1,
        "levels": [{"level": 1, "width_km": [100, 200], "files": [{"path": "L1.json"}]}],
    }
    index_path = tmp_path / "index.json"
    original_bytes = pretty_bytes(source)
    index_path.write_bytes(original_bytes)
    inventory = {"size": len(original_bytes), "sha256": hashlib.sha256(original_bytes).hexdigest()}

    assert _load_pinned_cluster_index(index_path, inventory) == source
    changed = copy.deepcopy(source)
    changed["levels"][0]["width_km"] = [100, 250]
    index_path.write_bytes(pretty_bytes(changed))
    with pytest.raises(ValueError, match="Frozen baseline integrity mismatch: clusters/index.json"):
        _load_pinned_cluster_index(index_path, inventory)
    assert sha256_file(index_path) != inventory["sha256"]
