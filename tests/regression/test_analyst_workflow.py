"""Task 5.3: filing change analyst workflow (curated fixtures, fake provider)."""

from __future__ import annotations

import json
from decimal import Decimal

from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.analytics.watch_signals import WatchSignal, WatchStatus
from filinglens.diff.detectors import ChangeSignal, Direction
from filinglens.diff.text_diff import EvidenceRef
from filinglens.llm.client import (
    CallStatus,
    FakeProvider,
    InterpretationStatus,
    StructuredLLMClient,
)
from filinglens.llm.workflow import Basis, FilingChangeWorkflow, build_evidence

RISK = ChangeSignal(
    "sig-risk",
    "risk_factor",
    "1A",
    ("supplier_concentration",),
    Direction.NEW,
    "added",
    (
        EvidenceRef(
            "s2", "b9", "/x", "New export controls may restrict sales of advanced products."
        ),
    ),
    "chg-1",
)
MDNA = ChangeSignal(
    "sig-mdna",
    "mdna_topic",
    "7",
    ("liquidity",),
    Direction.STRENGTHENED,
    "r",
    (
        EvidenceRef("s1", "b1", "/a", "Liquidity is supported by cash of $10 billion."),
        EvidenceRef(
            "s2", "b2", "/b", "Liquidity is supported by cash of $6 billion and borrowings."
        ),
    ),
    "chg-2",
)
METRICS = {
    "revenue_growth": MetricResult(
        "revenue_growth",
        MetricStatus.OK,
        Decimal("0.020220"),
        "pure",
        "(revenue_t - revenue_t-1) / |revenue_t-1|",
        (),
    )
}
WATCH = [
    WatchSignal(
        "liquidity_debt_shift",
        WatchStatus.FLAGGED,
        None,
        "Cash declined while long-term debt increased.",
        "f",
        "t",
    )
]


def finding(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "finding_type": "new_risk_factor",
        "document_type": "10-K",
        "section_or_item": "Item 1A",
        "direction": "new",
        "severity": "high",
        "confidence": 0.8,
        "summary": "A new export-control risk factor was added.",
        "why_it_matters": "May limit sales.",
        "evidence_ids": ["ev-sig-risk"],
        "open_questions": [],
        "interpretation_status": "supported",
    }
    base.update(kw)
    return base


def run(findings: list[dict[str, object]], form: str = "10-K", amendment: bool = False):  # type: ignore[no-untyped-def]
    provider = FakeProvider(
        [json.dumps({"schema_version": "finding-schema/1", "findings": findings})]
    )
    ev = build_evidence(signals=[RISK, MDNA], metrics=METRICS, watch=WATCH)
    return FilingChangeWorkflow(StructuredLLMClient(provider)).run(
        form=form, is_amendment=amendment, evidence=ev
    ), provider


def test_curated_fixture_expected_categories() -> None:
    res, _ = run(
        [
            finding(),
            finding(
                finding_type="liquidity_change",
                section_or_item="Item 7",
                direction="decrease",
                summary="Cash supporting liquidity fell from $10 billion to $6 billion and borrowings were added.",
                evidence_ids=["ev-sig-mdna", "ev-watch-liquidity_debt_shift"],
            ),
            finding(
                finding_type="revenue_trend",
                direction="increase",
                severity="low",
                summary="Revenue growth was 0.020220 year over year.",
                evidence_ids=["ev-met-revenue_growth"],
            ),
        ]
    )
    assert res.status is CallStatus.OK
    cats = {f.output.finding_type: f for f in res.findings}
    assert set(cats) == {"new_risk_factor", "liquidity_change", "revenue_trend"}
    assert all(f.interpretation_status is InterpretationStatus.SUPPORTED for f in res.findings)
    assert cats["new_risk_factor"].basis == (Basis.SOURCE_FACT,)
    assert set(cats["liquidity_change"].basis) == {
        Basis.SOURCE_FACT,
        Basis.DETERMINISTIC_CALCULATION,
    }
    assert cats["revenue_trend"].basis == (Basis.DETERMINISTIC_CALCULATION,)


def test_absent_evidence_becomes_not_supported() -> None:
    res, _ = run([finding(evidence_ids=[]), finding(evidence_ids=["ev-made-up"])])
    for f in res.findings:
        assert f.interpretation_status is InterpretationStatus.NOT_SUPPORTED
    assert "cites_unknown_evidence" in res.findings[1].flags


def test_no_numeric_invention() -> None:
    res, _ = run(
        [
            finding(
                summary="Revenue grew 12% to $400 billion.", evidence_ids=["ev-met-revenue_growth"]
            )
        ]
    )
    (f,) = res.findings
    assert f.interpretation_status is InterpretationStatus.UNCERTAIN
    assert any(fl.startswith("numeric_unverified") for fl in f.flags)


def test_uncited_management_claim_blocked() -> None:
    res, _ = run(
        [
            finding(
                summary="Management expects margins to recover.",
                evidence_ids=["ev-met-revenue_growth"],
            )
        ]
    )
    assert res.findings[0].interpretation_status is InterpretationStatus.NOT_SUPPORTED
    assert "uncited_management_claim" in res.findings[0].flags


def test_one_finding_cites_multiple_evidence() -> None:
    res, _ = run([finding(evidence_ids=["ev-sig-risk", "ev-met-revenue_growth"])])
    assert res.findings[0].valid_evidence_ids == ("ev-sig-risk", "ev-met-revenue_growth")


def test_form_specific_tasks_and_bounded_inputs() -> None:
    for form, amend, needle in (
        ("10-Q", False, "quarterly"),
        ("8-K", False, "events"),
        ("10-K/A", True, "amendment"),
    ):
        _, provider = run([finding()], form=form, amendment=amend)
        assert needle in provider.prompts[0].user.lower()
    ev = build_evidence(signals=[RISK] * 100, max_items=10)
    assert len(ev) == 10


def test_llm_failure_keeps_deterministic_view() -> None:
    provider = FakeProvider(["garbage"])
    ev = build_evidence(signals=[RISK], metrics=METRICS)
    res = FilingChangeWorkflow(StructuredLLMClient(provider)).run(
        form="10-K", is_amendment=False, evidence=ev
    )
    assert res.status is CallStatus.FAILED and res.findings == ()
    assert set(res.deterministic_only) == {"ev-sig-risk", "ev-met-revenue_growth"}


def test_no_evidence_no_call() -> None:
    provider = FakeProvider(["{}"])
    res = FilingChangeWorkflow(StructuredLLMClient(provider)).run(
        form="10-K", is_amendment=False, evidence=[]
    )
    assert res.findings == () and provider.prompts == []
