"""Task 7.6: SEC traffic, integrity & release qualification gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from filinglens.evaluation.qualification import (
    QualificationRecord,
    amendment_lineage_check,
    bulk_bootstrap_check,
    cache_behaviour_check,
    fair_access_load_test,
    immutability_check,
    run_qualification,
)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "fixtures" / "eval" / "corpus_v1"
SUBS = ROOT / "tests" / "fixtures" / "sec" / "submissions" / "CIK0001234567.json"


def test_fair_access_load_test_never_exceeds_governor() -> None:
    r = fair_access_load_test(rps=10.0, workers=6, per_worker=4)
    assert r.passed, r.detail
    assert r.detail["requests"] == 24 and r.detail["peak_per_second"] <= 10


def test_component_checks_pass() -> None:
    for check in (
        cache_behaviour_check(),
        bulk_bootstrap_check(SUBS),
        immutability_check(),
        amendment_lineage_check(),
    ):
        assert check.passed, (check.name, check.detail)


def _record(**kw: object) -> QualificationRecord:
    return run_qualification(
        eval_manifest=CORPUS / "manifest.json",
        eval_baseline=CORPUS / "baseline.json",
        submissions_fixture=SUBS,
        code_commit="abc123",
        clock=lambda: datetime(2026, 10, 14, tzinfo=UTC),
        **kw,
    )  # type: ignore[arg-type]


def test_qualification_record_complete() -> None:
    rec = _record()
    assert rec.automated_passed, [c for c in rec.checks if not c.passed]
    names = {c.name for c in rec.checks}
    assert names == {
        "fair_access_load",
        "cache_behaviour",
        "bulk_bootstrap_convergence",
        "raw_snapshot_immutability",
        "amendment_lineage",
        "golden_evaluation",
        "no_look_ahead",
        "citation_integrity",
    }
    assert rec.kpis["point_in_time_violations"] == 0
    assert rec.kpis["unsupported_claim_rate_in_accepted"] == 0
    assert rec.thresholds["comparator_accuracy"] == {"min": 0.98}
    assert rec.signed_off_by is None  # automation never signs off
    body = json.loads(rec.to_json())
    assert body["automated_passed"] is True and body["code_commit"] == "abc123"


def test_failed_check_blocks_unless_waived() -> None:
    rec = _record()
    rec.checks[0].passed = False
    assert not rec.automated_passed
    waived = _record(waivers=[{"check": "fair_access_load", "reason": "test", "approver": "x"}])
    waived.checks[0].passed = False
    assert waived.automated_passed and waived.waivers[0]["reason"] == "test"


def test_committed_v1_record_matches_gate() -> None:
    path = ROOT / "docs" / "release" / "qualification-v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gate_version"] == "release-gate/1" and data["automated_passed"] is True
    assert data["kpis"]["point_in_time_violations"] == 0
