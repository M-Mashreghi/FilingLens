"""Task 5.4: evidence validator, contradiction checker and priority scorer."""

from __future__ import annotations

from filinglens.diff.priority_features import FeatureVector
from filinglens.llm.client import FindingOutput, InterpretationStatus
from filinglens.llm.validation import (
    SCORE_LABEL,
    EvidenceBinding,
    RunScope,
    Verdict,
    accepted,
    validate,
)
from filinglens.llm.workflow import AnalystFinding, Basis

FOCAL = "0001234567-24-000031"
COMP = "0001234567-23-000030"
OTHER = "0009999999-24-000001"
SCOPE = RunScope(
    frozenset({"s-new", "s-old"}), frozenset({FOCAL, COMP}), frozenset({"fact-1", "fact-2"})
)
BINDINGS = {
    "ev-rf": EvidenceBinding("ev-rf", "s-new", FOCAL, "main.htm", "1A"),
    "ev-md": EvidenceBinding("ev-md", "s-new", FOCAL, "main.htm", "7"),
    "ev-met": EvidenceBinding("ev-met", None, None, None, None, ("fact-1", "fact-2")),
    "ev-wrong-acc": EvidenceBinding("ev-wrong-acc", "s-new", OTHER, "main.htm", "1A"),
    "ev-wrong-snap": EvidenceBinding("ev-wrong-snap", "s-foreign", FOCAL, "main.htm", "1A"),
    "ev-calc-nofacts": EvidenceBinding("ev-calc-nofacts", None, None, None, None),
}


def af(
    ev: tuple[str, ...],
    *,
    ftype: str = "new_risk_factor",
    section: str = "Item 1A",
    direction: str = "new",
    status: InterpretationStatus = InterpretationStatus.SUPPORTED,
    flags: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> AnalystFinding:
    out = FindingOutput(
        finding_type=ftype,
        document_type="10-K",
        section_or_item=section,
        direction=direction,  # type: ignore[arg-type]
        severity="high",
        confidence=confidence,
        summary="s",
        why_it_matters="w",
        evidence_ids=list(ev),  # type: ignore[arg-type]
        open_questions=[],
        interpretation_status=status,
    )
    return AnalystFinding(out, ev, (), (Basis.SOURCE_FACT,), status, flags)


def fv(**values: float | None) -> FeatureVector:
    return FeatureVector("sig", dict(values))


def test_valid_finding_accepted_and_scored() -> None:
    feats = {
        "ev-rf": [fv(section_importance=1.0, novelty=1.0, evidence_quality=1.0, xbrl_signal=None)]
    }
    (r,) = validate([af(("ev-rf",))], BINDINGS, SCOPE, features_by_evidence=feats)
    assert r.verdict is Verdict.ACCEPTED and r.block_reasons == ()
    assert r.priority_score == 90.0  # 100 * 1.0 * (0.5 + 0.5*0.8)
    assert r.reason_codes[0].startswith("section_importance")
    assert "not legal materiality" in r.score_label and r.score_label == SCORE_LABEL


def test_wrong_accession_or_document_citation_blocked() -> None:
    res = validate(
        [af(("ev-wrong-acc",)), af(("ev-wrong-snap",)), af(("ev-md",), section="Item 1A")],
        BINDINGS,
        SCOPE,
    )
    assert [r.verdict for r in res] == [Verdict.BLOCKED] * 3
    assert "wrong accession" in res[0].block_reasons[0]
    assert "snapshot not part of this run" in res[1].block_reasons[0]
    assert "not among cited sections" in res[2].block_reasons[0]
    assert all(r.priority_score is None for r in res)


def test_unsupported_claims_blocked() -> None:
    res = validate(
        [
            af(()),
            af(("ev-rf",), status=InterpretationStatus.NOT_SUPPORTED),
            af(
                ("ev-rf",), flags=("numeric_unverified:12%",), status=InterpretationStatus.UNCERTAIN
            ),
            af(("ev-calc-nofacts",), section=""),
            af(("ev-unbound",)),
        ],
        BINDINGS,
        SCOPE,
    )
    assert all(r.verdict is Verdict.BLOCKED for r in res)
    assert any("calculation without source fact ids" in x for x in res[3].block_reasons)
    assert any("no provenance binding" in x for x in res[4].block_reasons)


def test_calculation_evidence_binds_through_fact_ids() -> None:
    (ok,) = validate(
        [af(("ev-met",), ftype="revenue_trend", section="", direction="increase")], BINDINGS, SCOPE
    )
    assert ok.verdict is Verdict.ACCEPTED
    bad_scope = RunScope(SCOPE.snapshot_ids, SCOPE.accessions, frozenset({"fact-9"}))
    (bad,) = validate([af(("ev-met",), section="", direction="increase")], BINDINGS, bad_scope)
    assert bad.verdict is Verdict.BLOCKED


def test_contradictory_output_flagged() -> None:
    up = af(("ev-md",), ftype="liquidity_change", section="Item 7", direction="increase")
    down = af(("ev-md",), ftype="liquidity_change", section="7", direction="decrease")
    other = af(("ev-rf",))
    res = validate([up, down, other], BINDINGS, SCOPE)
    assert res[0].contradicts == (1,) and res[1].contradicts == (0,) and res[2].contradicts == ()
    assert "contradiction_flagged" in res[0].reason_codes


def test_reviewer_model_can_block() -> None:
    class Strict:
        def review(self, finding, texts):  # type: ignore[no-untyped-def]
            return False

    (r,) = validate(
        [af(("ev-rf",))], BINDINGS, SCOPE, reviewer=Strict(), evidence_texts={"ev-rf": "text"}
    )
    assert r.verdict is Verdict.BLOCKED and "reviewer model rejected claim" in r.block_reasons


def test_score_reproducible_and_missing_features_renormalized() -> None:
    feats = {"ev-rf": [fv(section_importance=0.5, novelty=None, xbrl_signal=None)]}
    a = validate([af(("ev-rf",), confidence=1.0)], BINDINGS, SCOPE, features_by_evidence=feats)
    b = validate([af(("ev-rf",), confidence=1.0)], BINDINGS, SCOPE, features_by_evidence=feats)
    assert a == b and a[0].priority_score == 50.0
    (none,) = validate([af(("ev-rf",))], BINDINGS, SCOPE)
    assert none.priority_score is None and none.reason_codes == ("no_deterministic_features",)


def test_accepted_ranking() -> None:
    feats = {"ev-rf": [fv(section_importance=1.0)], "ev-md": [fv(section_importance=0.2)]}
    res = validate(
        [af(("ev-md",), ftype="m", section="Item 7"), af(("ev-rf",))],
        BINDINGS,
        SCOPE,
        features_by_evidence=feats,
    )
    assert [r.finding.output.finding_type for r in accepted(res)] == ["new_risk_factor", "m"]
