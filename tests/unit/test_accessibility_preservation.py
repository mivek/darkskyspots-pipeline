from tools.audit_accessibility import add_accessibility_to_clusters, add_accessibility_to_envelope


def test_tile_enrichment_preserves_envelope_spots_and_order():
    original = {
        "version": "2025.3", "source": "VIIRS/2025/uk_ireland",
        "generated": "2026-11-07T00:00:00Z", "tile": "N052W006",
        "extension": {"kept": True},
        "spots": [
            {"id": "first", "lat": 52.1, "lon": -6.1, "altitude": 12, "near": None},
            {"id": "other", "lat": 52.2, "lon": -6.2, "altitude": 18, "near": "Place"},
            {"id": "last", "lat": 52.3, "lon": -6.3, "altitude": 21, "near": "Town"},
        ],
    }
    payload = {"schema": 1, "source": "g2-ie-ni-test", "radiusM": 1000, "road": {"status": "unknown"}}
    updated, preserved = add_accessibility_to_envelope(original, {"last": payload})

    assert preserved
    assert updated["version"] == original["version"]
    assert updated["source"] == original["source"]
    assert updated["generated"] == original["generated"]
    assert updated["tile"] == original["tile"]
    assert updated["extension"] == original["extension"]
    assert [spot["id"] for spot in updated["spots"]] == ["first", "other", "last"]
    assert "accessibility" not in updated["spots"][0]
    assert "accessibility" not in updated["spots"][1]
    assert updated["spots"][2]["accessibility"] == payload
    assert {key: value for key, value in updated["spots"][2].items() if key != "accessibility"} == original["spots"][2]


def test_cluster_enrichment_preserves_membership_geometry_and_representative_selection():
    original = [
        {"id": "cluster-1", "members": ["a", "b"], "geometry": {"type": "Point", "coordinates": [1, 2]},
         "rep": {"id": "a", "lat": 2.0, "lon": 1.0}},
        {"id": "cluster-2", "members": ["c", "d"], "geometry": {"type": "Point", "coordinates": [3, 4]},
         "rep": {"id": "outside", "lat": 4.0, "lon": 3.0}},
    ]
    payload = {"schema": 1, "source": "g2-gb-test", "radiusM": 1000}
    updated, preserved = add_accessibility_to_clusters(original, {"a": payload})

    assert preserved
    assert [record["id"] for record in updated] == ["cluster-1", "cluster-2"]
    assert updated[0]["members"] == original[0]["members"]
    assert updated[0]["geometry"] == original[0]["geometry"]
    assert updated[0]["rep"]["id"] == "a"
    assert updated[0]["rep"]["accessibility"] == payload
    assert updated[1] == original[1]
