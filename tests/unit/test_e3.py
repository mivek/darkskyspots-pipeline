"""Production E3 evidence, selection and fail-closed publication contracts."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from shapely.geometry import box, Polygon, Point, mapping

from src.geographic_confidence import confidence, priority
from src.geographic_geometry import canonical, domain, contains
from src.geographic_index import build_index, GeographicIndex
from src.geographic_data import GeographicDataset, E3_POLICY, geometry_digest
from src.geographic_e3_prepare import prepare_e3
from src.geographic_e3 import reviewed_contexts
from src.extract import mesh_darkest, redundancy_filter
from src.coverage import ensure_coverage
from src.enrich import enrich_spot
from src.tile_export import classify_spots_into_tiles
from tests.geographic_helpers import (
    SPOT,
    REGION,
    feature,
    make_dataset,
    make_e3_dataset,
    decision,
)


@pytest.fixture(autouse=True)
def offline():
    with patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("Unexpected network"),
    ):
        yield


def review(extent, reason, conclusion="accept_uncertainty", identifier="review"):
    d = decision(extent, "hold", identifier)
    d["conclusion"] = conclusion
    d["resolutions" if conclusion == "resolve_risk" else "acceptances"] = [
        dict(
            reason=reason,
            context_sha256="0" * 64,
            evidence=[
                {
                    "source": "independently reviewed feature",
                    "conclusion": "generally above water",
                }
            ],
            provenance=[{"reference": "synthetic reviewed record"}],
        )
    ]
    if conclusion=='resolve_risk':
        part=d['resolutions'][0]
        part.update(conclusion=dict(outcome='concern_resolved',generally_above_water=True,
                        basis='Synthetic surveyed feature resolves this specific mapped concern'),
                    reviewed_by='Synthetic geographic reviewer',review_reference='test-review-2026-11',
                    evidence=[dict(source='synthetic survey',reference='test-survey-1',
                                   resolved_reason=reason,finding='Survey establishes generally above water for this concern')])
    return d


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("wetland_bog", "B1"),
        ("wetland_fen", "B1"),
        ("wetland_wet_meadow", "B1"),
        ("wetland_marsh", "B2"),
        ("wetland_saltmarsh", "B2"),
        ("wetland_tidalflat", "B2"),
        ("wetland_reedbed", "B2"),
        ("wetland_swamp", "B2"),
        ("wetland_mangrove", "B2"),
        ("wetland", "B2"),
    ],
)
def test_contextual_wetland_types(tmp_path, kind, expected):
    ds = make_e3_dataset(
        tmp_path, extra=[feature(box(-1, 44, 1, 46), "wetland", **{"class": kind})]
    )
    finding = ds.audit([SPOT])["findings"][0]
    assert finding["confidence"] == expected and finding["status"] == "hold"
    ds.close()


def test_tidal_evidence_overrides_moderate_subtype(tmp_path):
    ds = make_e3_dataset(
        tmp_path,
        extra=[
            feature(
                box(-1, 44, 1, 46),
                "wetland",
                **{"class": "wetland_bog", "tidal": "yes"},
            )
        ],
    )
    f = ds.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B2" and "explicit_tidal_influence" in f["reasons"]


def test_B1_acceptance_is_scoped_and_never_promotes_confidence(tmp_path):
    extent = box(-1, 44, 1, 46)
    reason = "peatland_meadow_or_fen_uncertainty"
    ds = make_e3_dataset(
        tmp_path,
        extra=[feature(extent, "wetland", **{"class": "wetland_bog"})],
        e3_decisions=[review(extent, reason)],
    )
    f = ds.audit([SPOT])["findings"][0]
    assert f["status"] == "accepted_uncertainty" and f["confidence"] == "B1"
    ds.decisions[0]["acceptances"][0]["context_sha256"] = "a" * 64
    assert ds.audit([SPOT])["status"] == "blocked"


def test_B2_requires_positive_resolution_for_every_reason(tmp_path):
    extent = box(-1, 44, 1, 46)
    ds = make_e3_dataset(
        tmp_path,
        extra=[feature(extent, "water", id="river")],
        e3_decisions=[
            review(extent, "mapped_surface_water_disagreement", "resolve_risk")
        ],
    )
    f = ds.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B2" and f["status"] == "resolved_risk"
    changed = make_e3_dataset(
        tmp_path / "new",
        extra=[
            feature(extent, "water", id="river"),
            feature(extent, "wetland", **{"class": "wetland_tidalflat"}),
        ],
        e3_decisions=ds.review_document["decisions"],
        approve_decisions=False,
    )
    f = changed.audit([SPOT])["findings"][0]
    assert f["status"] == "hold" and f["stale_decisions"] == ["review"]
    assert "higher_risk_or_untyped_wetland" in f["unreviewed_reasons"]


def test_uncertainty_acceptance_cannot_be_migrated_into_B2_resolution(tmp_path):
    extent = box(-1, 44, 1, 46)
    with pytest.raises(ValueError, match="acceptance category"):
        make_e3_dataset(
            tmp_path, e3_decisions=[review(extent, "mapped_surface_water_disagreement")]
        )


def test_changed_source_identity_invalidates_resolution(tmp_path):
    extent = box(-1, 44, 1, 46)
    old = make_e3_dataset(
        tmp_path / "old",
        extra=[feature(extent, "water", id="one", source="first")],
        e3_decisions=[
            review(extent, "mapped_surface_water_disagreement", "resolve_risk")
        ],
    )
    new = make_e3_dataset(
        tmp_path / "new",
        extra=[feature(extent, "water", id="two", source="second")],
        e3_decisions=old.review_document["decisions"],
        approve_decisions=False,
    )
    assert not new.decisions[0]["active"] and new.audit([SPOT])["status"] == "blocked"


def test_unrelated_changes_outside_extent_preserve_resolution(tmp_path):
    extent = box(-1, 44, 1, 46)
    f = feature(extent, "water", id="one")
    old = make_e3_dataset(
        tmp_path / "old",
        extra=[f],
        e3_decisions=[
            review(extent, "mapped_surface_water_disagreement", "resolve_risk")
        ],
    )
    new = make_e3_dataset(
        tmp_path / "new",
        extra=[f, feature(box(3, 48, 4, 49), "water", id="far")],
        e3_decisions=old.review_document["decisions"],
        approve_decisions=False,
    )
    assert new.decisions[0]["active"] and new.audit([SPOT])["status"] == "passed"


def test_stale_explicit_hold_without_other_disagreement_still_blocks(tmp_path):
    extent = box(-1, 44, 1, 46)
    hold = decision(extent, "hold")
    original = make_e3_dataset(tmp_path / "old", e3_decisions=[hold])
    changed = make_e3_dataset(
        tmp_path / "new",
        e3_decisions=original.review_document["decisions"],
        extra=[feature(box(0.5, 45.2, 0.6, 45.3), "water")],
        approve_decisions=False,
    )
    f = changed.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B2" and f["status"] == "hold" and f["stale_decisions"]
    assert f["reasons"] == ["explicit_review_hold"]


def test_hold_withdrawal_requires_explicit_reviewed_revision(tmp_path):
    ds = make_e3_dataset(
        tmp_path / "held", e3_decisions=[decision(box(-1, 44, 1, 46), "hold")]
    )
    doc = dict(schema=3, policy=E3_POLICY, decisions=[])
    with pytest.raises(ValueError, match="hold withdrawal"):
        prepare_e3(
            ds.path,
            ds.entries[ds.manifest["worldcover"]],
            tmp_path / "bad",
            reviews=doc,
            regions={"france": REGION},
        )
    doc.update(
        reviewed_by="synthetic reviewer",
        rationale="Explicit withdrawal",
        supersedes=ds.decisions_revision,
    )
    prepare_e3(
        ds.path,
        ds.entries[ds.manifest["worldcover"]],
        tmp_path / "ok",
        reviews=doc,
        regions={"france": REGION},
    )
    assert (
        GeographicDataset(
            tmp_path / "ok/manifest.json", regions={"france": REGION}
        ).audit([SPOT])["status"]
        == "passed"
    )


def policy(reason):
    return dict(
        schema=1,
        policy=E3_POLICY,
        policies=[
            dict(
                id="region-policy",
                version="1",
                region="france",
                extent=mapping(box(-1, 44, 1, 46)),
                reasons=[reason],
                approved_by="synthetic reviewer",
                approval_reference="reviewed test revision",
                documented_limitation="Documented upstream source gap",
                provenance=[{"source": "test upstream coverage inventory"}],
                context_sha256="0" * 64,
            )
        ],
    )


def test_degraded_policy_requires_authorization_and_only_sole_legitimate_gap(tmp_path):
    extent = box(-1, 44, 1, 46)
    reason = "incomplete_hydrographic_collection"
    evidence = [feature(box(*REGION["bbox"]), "marine_land")]
    gap = [
        dict(
            source="hydrography",
            reason="upstream_coverage_gap",
            extent=mapping(extent),
            evidence="frozen upstream inventory",
        )
    ]
    ds = make_e3_dataset(
        tmp_path / "ok", evidence=evidence, degraded=policy(reason), availability=gap
    )
    f = ds.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B1" and f["status"] == "accepted_uncertainty"
    assert ds.audit([SPOT])["status"] == "passed"
    unknown = make_e3_dataset(
        tmp_path / "unknown", evidence=evidence, degraded=policy(reason)
    )
    assert unknown.audit([SPOT])["status"] == "blocked"
    mixed = make_e3_dataset(
        tmp_path / "mixed",
        evidence=evidence + [feature(extent, "wetland", **{"class": "wetland_bog"})],
        degraded=policy(reason),
        availability=gap,
    )
    assert mixed.audit([SPOT])["status"] == "blocked"
    unauthorized = policy(reason)
    unauthorized["policies"][0].pop("approved_by")
    with pytest.raises(ValueError, match="explicit reviewed authorization"):
        make_e3_dataset(tmp_path / "unauthorized", degraded=unauthorized)


def test_degraded_policy_cannot_waive_water_or_stale_holds_or_missing_assets(tmp_path):
    ds = make_e3_dataset(
        tmp_path / "water",
        wc_value=80,
        degraded=policy("WorldCover_unavailable_or_partial"),
    )
    assert ds.audit([SPOT])["findings"][0]["confidence"] == "B2"
    assert ds.audit([SPOT])["status"] == "blocked"
    ds.entries["worldcover:N45E000"].unlink()
    with pytest.raises(ValueError):
        GeographicDataset(ds.path, regions={"france": REGION}, require_e3=True)


def test_native_WorldCover_nodata_is_B1_not_C(tmp_path):
    ds = make_e3_dataset(tmp_path, wc_value=0)
    f = ds.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B1" and f["status"] == "hold"


def test_index_is_lookup_only_holes_and_fragment_union(tmp_path):
    island = Polygon(
        [(-1, -1), (1, -1), (1, 1), (-1, 1)],
        holes=[[(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]],
    )
    features = [
        feature(box(-2, -2, 0, 2), "marine_land"),
        feature(box(0, -2, 2, 2), "marine_land"),
        feature(box(-2, -2, 2, 2), "inland_coverage"),
        feature(island, "water"),
    ]
    build_index(tmp_path / "index", features)
    index = GeographicIndex(tmp_path / "index")
    f = index.observe([{"lat": 0, "lon": 0}], [box(-0.01, -0.01, 0.01, 0.01)])[0]
    assert f["marine_support_land"] and not f["water_features"]
    # Bounding-box hits inside the real hole are not water predicates.
    assert len(index.query([box(-0.01, -0.01, 0.01, 0.01)])) == 4
    index.close()


def test_rtree_outward_precision_cannot_omit_narrow_geometry(tmp_path):
    g = box(0.1234567890123, 1, 0.1234567890124, 2)
    build_index(tmp_path / "index", [feature(g, "water")])
    index = GeographicIndex(tmp_path / "index")
    assert index.observe([{"lat": 1.5, "lon": 0.12345678901235}], [g])[0][
        "water_features"
    ]
    index.close()


def test_antimeridian_original_geometry_and_island_hole(tmp_path):
    raw = Polygon(
        [(179, -2), (-179, -2), (-179, 2), (179, 2)],
        holes=[[(179.8, -0.2), (-179.8, -0.2), (-179.8, 0.2), (179.8, 0.2)]],
    )
    # The planar pre-unwrapping polygon is not valid with a crossing hole;
    # canonical form is the valid authoritative WGS84 representation.
    normalized = canonical(raw)
    assert normalized.is_valid
    assert not contains(normalized, Point(180, 0))
    assert contains(normalized, Point(-180, 1))
    build_index(tmp_path / "index", [feature(normalized, "water")])
    index = GeographicIndex(tmp_path / "index")
    points = [{"lat": 0, "lon": 179.9}, {"lat": 1, "lon": -179.9}]
    f = index.observe(
        points, [box(179.89, -0.01, 179.91, 0.01), box(-179.91, 0.99, -179.89, 1.01)]
    )
    assert not f[0]["water_features"] and f[1]["water_features"]
    assert domain([179, -2, -179, 2]).covers(normalized)
    index.close()


def test_strict_tier_selection_and_genuine_mixed_coverage():
    # One 3x3 mesh. A is less dark than B1/B2; stable ties are row-major.
    d = np.array([[0.99, 0.95, 0.8], [0.8, 0.7, 0.6], [0.5, 0.4, 0.3]])

    def qualify(ps):
        for p in ps:
            p["_geographic_level"] = (
                "B2" if p["col"] == 0 else "B1" if p["col"] == 1 else "A"
            )
        return ps

    out = mesh_darkest(
        d,
        rasterio.Affine(0.01, 0, 0, 0, -0.01, 1),
        qualify=qualify,
        priority_key=priority,
    )
    assert len(out) == 1 and (out[0]["row"], out[0]["col"]) == (0, 2)
    pool = [
        dict(
            lat=0,
            lon=i * 0.01,
            darkness=0.9 - i * 0.1,
            bortle=3,
            _geographic_level=level,
        )
        for i, level in enumerate(["A", "A", "B1", "B2"])
    ]
    kept = redundancy_filter(pool, priority_key=priority)
    assert kept == pool[:1]
    final = ensure_coverage(kept, pool, [{"lat": 0, "lon": 0}], priority_key=priority)
    assert final == pool


def test_confidence_metadata_cannot_leak_publicly():
    p = dict(SPOT, _geographic_level="A")
    assert "_geographic_level" not in enrich_spot(p)
    with pytest.raises(ValueError, match="Internal geographic"):
        classify_spots_into_tiles([p])


def test_legacy_package_cannot_approve_production_publication(tmp_path):
    ds = make_dataset(tmp_path)
    with pytest.raises(ValueError, match="requires a verified E3"):
        ds.require_e3()


@pytest.mark.parametrize("failure", ["reference", "sample", "technical"])
def test_E3_audit_requires_verified_WBM_for_every_region(
    tmp_path, failure, monkeypatch
):
    ds = make_e3_dataset(tmp_path)
    if failure == "reference":
        ds.manifest["terrain"].clear()
        with pytest.raises(ValueError, match="Missing verified WBM"):
            ds.audit([SPOT])
    else:
        from src.elevation import ElevationSampler

        if failure == "sample":
            monkeypatch.setattr(ElevationSampler, "wbm_classes", lambda *_: None)
            with pytest.raises(ValueError, match="Invalid WBM audit samples"):
                ds.audit([SPOT])
        else:

            def broken(*_):
                raise OSError("unreadable WBM")

            monkeypatch.setattr(ElevationSampler, "wbm_classes", broken)
            with pytest.raises(OSError, match="unreadable WBM"):
                ds.audit([SPOT])


def test_hold_cannot_be_shrunk_under_same_identifier_without_reviewed_withdrawal(
    tmp_path,
):
    ds = make_e3_dataset(
        tmp_path / "held", e3_decisions=[decision(box(-1, 44, 1, 46), "hold")]
    )
    doc = copy.deepcopy(ds.review_document)
    doc["decisions"][0]["extent"] = mapping(box(0.5, 45.2, 0.6, 45.3))
    with pytest.raises(ValueError, match="hold withdrawal"):
        prepare_e3(
            ds.path,
            ds.entries[ds.manifest["worldcover"]],
            tmp_path / "bad",
            reviews=doc,
            regions={"france": REGION},
        )


def test_B2_resolution_does_not_accept_moderate_uncertainty(tmp_path):
    extent = box(-1, 44, 1, 46)
    ds = make_e3_dataset(
        tmp_path,
        extra=[
            feature(extent, "water"),
            feature(extent, "wetland", **{"class": "wetland_bog"}),
        ],
        e3_decisions=[
            review(extent, "mapped_surface_water_disagreement", "resolve_risk")
        ],
    )
    f = ds.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B2" and f["status"] == "hold"
    assert f["unreviewed_reasons"] == ["peatland_meadow_or_fen_uncertainty"]


def test_degraded_policy_requires_documentation_for_valid_upstream_NoData(tmp_path):
    reason = "WorldCover_unavailable_or_partial"
    undocumented = make_e3_dataset(
        tmp_path / "unknown", wc_value=0, degraded=policy(reason)
    )
    assert undocumented.audit([SPOT])["status"] == "blocked"
    gap = [
        dict(
            source="WorldCover",
            reason="valid_upstream_NoData",
            extent=mapping(box(-1, 44, 1, 46)),
            evidence="Synthetic frozen upstream coverage limitation",
        )
    ]
    documented = make_e3_dataset(
        tmp_path / "documented", wc_value=0, degraded=policy(reason), availability=gap
    )
    f = documented.audit([SPOT])["findings"][0]
    assert f["confidence"] == "B1" and f["status"] == "accepted_uncertainty"


def test_preparation_is_reproducible_and_does_not_issue_approvals(tmp_path):
    ds = make_e3_dataset(tmp_path / "source")
    _, a = prepare_e3(
        ds.path,
        ds.entries[ds.manifest["worldcover"]],
        tmp_path / "a",
        regions={"france": REGION},
    )
    _, b = prepare_e3(
        ds.path,
        ds.entries[ds.manifest["worldcover"]],
        tmp_path / "b",
        regions={"france": REGION},
    )
    assert (tmp_path / "a/manifest.json").read_bytes() == (
        tmp_path / "b/manifest.json"
    ).read_bytes()
    assert a == b == []


def test_high_latitude_regional_policy_is_explicit_versioned_and_not_tier_promotion(
    tmp_path,
):
    polar = dict(
        bbox=[-2, 83, 2, 87], osm_country_code=["FR"]
    )  # synthetic owner, no national rule
    evidence = [
        feature(box(*polar["bbox"]), "marine_land"),
        feature(box(*polar["bbox"]), "inland_coverage"),
    ]
    doc = policy("WorldCover_unavailable_or_partial")
    doc["policies"][0]["extent"] = mapping(box(-1, 84, 1, 86))
    ds = make_e3_dataset(
        tmp_path / "approved",
        regions={"france": polar},
        evidence=evidence,
        wbm_lat=85,
        degraded=doc,
    )
    f = ds.audit([dict(SPOT, lat=85)])["findings"][0]
    assert (
        f["confidence"] == "B1"
        and f["worldcover"]["unavailable_reason"] == "outside_product_support"
    )
    assert f["status"] == "accepted_uncertainty" and f["decisions"] == ["region-policy"]
    ds2 = make_e3_dataset(
        tmp_path / "unapproved",
        regions={"france": polar},
        evidence=evidence,
        wbm_lat=85,
    )
    assert ds2.audit([dict(SPOT, lat=85)])["status"] == "blocked"


def test_native_support_keeps_water_just_outside_regional_boundary(tmp_path):
    # Centre is inside a narrow preparation region; the real WBM pixel extends
    # across it. The water is outside the region, but relevant to that support.
    region = dict(bbox=[0, 44, 0.1, 46], osm_country_code=["FR"])
    evidence = [
        feature(box(-1, 43, 1, 47), "marine_land"),
        feature(box(-1, 43, 1, 47), "inland_coverage"),
        feature(box(0.15, 44.9, 0.2, 45.1), "water", id="outside-boundary"),
    ]
    ds = make_e3_dataset(tmp_path, regions={"france": region}, evidence=evidence)
    f = ds.audit([dict(SPOT, lon=0.05)])["findings"][0]
    assert f["confidence"] == "B2"
    assert "mapped_surface_water_disagreement" in f["reasons"]
    # Legacy packages lacking a full-support inventory must never silently
    # assume that omitted outside-region evidence is terrestrial.
    ds.regions["france"].pop("evidence_domain")
    with pytest.raises(ValueError, match="leaves verified evidence domain"):
        ds.audit([dict(SPOT, lon=0.05)])
