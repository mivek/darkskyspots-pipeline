from pathlib import Path

from tools import audit_accessibility


def test_implementation_fingerprint_ignores_local_plan_but_tracks_versioned_files(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    monkeypatch.setattr(audit_accessibility, "__file__", str(root / "tools/audit_accessibility.py"))

    for relative in audit_accessibility.FINGERPRINT_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"versioned: {relative}\n", encoding="utf-8")

    plan = root / "docs/plans/2026-11-08-f02-1a-g1-canary-islands-generalization.md"
    before = audit_accessibility._implementation_fingerprint()
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("local planning notes A\n", encoding="utf-8")
    present = audit_accessibility._implementation_fingerprint()
    plan.write_text("local planning notes B\n", encoding="utf-8")
    modified = audit_accessibility._implementation_fingerprint()
    plan.unlink()
    absent = audit_accessibility._implementation_fingerprint()

    assert present == before == modified == absent

    relevant = root / "docs/accessibility.md"
    relevant.write_text("versioned documentation changed\n", encoding="utf-8")
    assert audit_accessibility._implementation_fingerprint() != before
