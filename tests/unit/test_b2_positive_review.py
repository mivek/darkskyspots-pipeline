"""A structured human risk resolution is mandatory; acknowledgement is not one."""

import copy

import pytest
from shapely.geometry import box

from src.geographic_data import E3_POLICY, validate_review_document, GeographicDataset
from src.geographic_e3_prepare import prepare_e3
from tests.geographic_helpers import make_e3_dataset, feature, SPOT, REGION
from tests.unit.test_e3 import review

REASON = "mapped_surface_water_disagreement"


def document():
    return dict(
        schema=4,
        policy=E3_POLICY,
        decisions=[review(box(-1, 44, 1, 46), REASON, "resolve_risk")],
    )


@pytest.mark.parametrize(
    "change",
    [
        {"conclusion": None},
        {"conclusion": "tidal risk acknowledged"},
        {
            "conclusion": dict(
                outcome="uncertainty_acknowledged",
                generally_above_water=True,
                basis="Risk is acknowledged",
            )
        },
        {
            "conclusion": dict(
                outcome="concern_resolved",
                generally_above_water=False,
                basis="May be flooded",
            )
        },
        {
            "conclusion": dict(
                outcome="concern_resolved",
                generally_above_water=1,
                basis="Invalid boolean",
            )
        },
        {
            "conclusion": dict(
                outcome="concern_resolved", generally_above_water=True, basis=" "
            )
        },
        {"reviewed_by": ""},
        {"reviewed_by": None},
        {"review_reference": " "},
        {"evidence": []},
        {"evidence": ["water risk acknowledged"]},
        {"evidence": [dict(source="generic imagery")]},
        {
            "evidence": [
                dict(
                    source="survey",
                    reference="survey-1",
                    resolved_reason="coastal_support_disagreement",
                    finding="Unrelated risk reviewed",
                )
            ]
        },
        {
            "evidence": [
                dict(
                    source="survey",
                    reference="",
                    resolved_reason=REASON,
                    finding="Dry ground",
                )
            ]
        },
        {
            "evidence": [
                dict(
                    source="survey",
                    reference="survey-1",
                    resolved_reason=REASON,
                    finding=" ",
                )
            ]
        },
    ],
)
def test_incomplete_or_nonpositive_resolution_cannot_be_prepared(tmp_path, change):
    source = make_e3_dataset(tmp_path / "source")
    doc = document()
    doc["decisions"][0]["resolutions"][0].update(change)
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match="B2 resolution"):
        prepare_e3(
            source.path,
            source.entries[source.manifest["worldcover"]],
            output,
            reviews=doc,
            regions={"france": REGION},
        )
    assert not output.exists()


def test_schema3_risk_resolution_requires_explicit_renewal_even_if_positive(tmp_path):
    doc = document()
    doc["schema"] = 3
    with pytest.raises(ValueError, match="explicit renewal"):
        validate_review_document(doc)
    old = make_e3_dataset(
        tmp_path,
        extra=[feature(box(-1, 44, 1, 46), "water")],
        e3_decisions=document()["decisions"],
    )
    assert old.review_document["schema"] == 4
    result = old.audit([SPOT])["findings"][0]
    assert result["confidence"] == "B2" and result["status"] == "resolved_risk"
    # Constructor and preparation use the same validation; this cannot be
    # bypassed by directly loading a checksummed schema-3 decision package.
    import json
    from src.geographic_data import file_digest

    path = old.entries["reviews"]
    reviews = json.loads(path.read_text())
    reviews["schema"] = 3
    path.write_text(json.dumps(reviews))
    manifest = json.loads(old.path.read_text())
    manifest["files"]["reviews"].update(
        size=path.stat().st_size, sha256=file_digest(path)
    )
    old.path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="explicit renewal"):
        GeographicDataset(old.path, regions={"france": REGION})
    output = tmp_path / "explicit-renewal"
    prepare_e3(
        old.path,
        old.entries[old.manifest["worldcover"]],
        output,
        reviews=dict(schema=4, policy=E3_POLICY, decisions=reviews["decisions"]),
        regions={"france": REGION},
    )
    renewed = GeographicDataset(output / "manifest.json", regions={"france": REGION})
    assert renewed.audit([SPOT])["findings"][0]["status"] == "resolved_risk"
    assert json.loads(path.read_text())["schema"] == 3


def test_each_reason_needs_its_own_substantive_resolution():
    doc = document()
    second = copy.deepcopy(doc["decisions"][0]["resolutions"][0])
    second["reason"] = "coastal_support_disagreement"
    doc["decisions"][0]["resolutions"].append(second)
    with pytest.raises(ValueError, match="specific reason"):
        validate_review_document(doc)
    second["evidence"][0]["resolved_reason"] = second["reason"]
    validate_review_document(doc)


def test_final_publication_gate_rechecks_positive_resolution(tmp_path):
    from tests.unit.test_geographic_publication import repository
    from src.geographic_publication import approve_tree

    ds = make_e3_dataset(
        tmp_path / "geo",
        extra=[feature(box(-1, 44, 1, 46), "water")],
        e3_decisions=document()["decisions"],
    )
    repo = repository(tmp_path / "repo")
    assert approve_tree(repo, dataset=ds)["status"] == "passed"
    ds.decisions[0]["resolutions"][0]["conclusion"][
        "outcome"
    ] = "tidal_risk_acknowledged"
    with pytest.raises(ValueError, match="positive geographic conclusion"):
        approve_tree(repo, dataset=ds)
