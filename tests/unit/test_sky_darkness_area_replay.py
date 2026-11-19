import hashlib
import json

import pytest

from tools.replay_sky_darkness_area import verify_pinned_e3_manifest


def test_replay_rejects_a_different_valid_manifest_even_when_outcomes_match(tmp_path):
    # Keep the declared source files and replay outcomes identical; only the
    # internally valid root package revision differs.
    source_bytes = b"identical verified source bytes"
    source = tmp_path / "france.sqlite"
    source.write_bytes(source_bytes)
    package = {
        "schema": 2,
        "policy": "e3-p2-odbl-2026-11-04",
        "regions": {"france": {"bbox": [-6, 41, 8, 51]}},
        "files": {"index:france": {
            "path": "france.sqlite",
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "size": len(source_bytes),
        }},
        "expected_panel": {"halo": 1, "label": 6, "none": 1},
    }
    original = json.dumps(package, sort_keys=True).encode()
    original_path = tmp_path / "original-manifest.json"
    original_path.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest()
    assert verify_pinned_e3_manifest(original_path, expected) == expected

    alternate = {**package, "package_revision": "different-but-valid"}
    alternate_path = tmp_path / "alternate-manifest.json"
    alternate_path.write_text(json.dumps(alternate, sort_keys=True))
    assert json.loads(alternate_path.read_text())["expected_panel"] == package["expected_panel"]
    with pytest.raises(ValueError, match="root manifest digest mismatch"):
        verify_pinned_e3_manifest(alternate_path, expected)
