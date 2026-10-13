"""Task 3.6: research priority feature builder."""

from __future__ import annotations

import hashlib

from filinglens.diff.alignment import align_documents
from filinglens.diff.detectors import (
    ChangeSignal,
    Direction,
    detect_mdna_topics,
    detect_risk_factors,
)
from filinglens.diff.priority_features import (
    FEATURE_SPECS,
    FEATURE_VERSION,
    FeatureContext,
    build_features,
)
from filinglens.diff.text_diff import EvidenceRef, diff_documents
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections

FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4


def tenk(risk: list[str], mdna: list[str]) -> bytes:
    r = "".join(f"<p>{p}</p>" for p in risk)
    m = "".join(f"<p>{p}</p>" for p in mdna)
    return (
        "<html><body>"
        f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
        f"<div><b>Item 1A. Risk Factors</b></div>{r}"
        f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
        f"<div><b>Item 7. Management's Discussion and Analysis</b></div>{m}"
        f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
        f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p>"
        "</body></html>"
    ).encode()


OLD = tenk(
    ["We rely on a limited number of suppliers, and disruptions could affect us."],
    ["Liquidity is supported by cash and cash equivalents of $10 billion."],
)
NEW = tenk(
    [
        "We rely on a limited number of suppliers, and disruptions will materially and adversely affect us.",
        "New tariffs may increase our input costs and reduce gross margin.",
    ],
    [
        "Liquidity is supported by cash and cash equivalents of $6 billion and borrowings under our credit facility."
    ],
)


def _build(ctx: FeatureContext | None = None):  # type: ignore[no-untyped-def]
    od = normalize_html(OLD, snapshot_id=hashlib.sha256(OLD).hexdigest())
    nd = normalize_html(NEW, snapshot_id=hashlib.sha256(NEW).hexdigest())
    ps_o, ps_n = parse_sections(od, "10-K"), parse_sections(nd, "10-K")
    diff = diff_documents(od, nd, align_documents(od, ps_o, nd, ps_n))
    sigs = detect_risk_factors(diff, "1A") + detect_mdna_topics(diff, ["7"])
    ctx = ctx or FeatureContext(parser_confidence={s.key: s.confidence for s in ps_n.sections})
    return build_features(sigs, diff, ctx), sigs


def test_same_input_same_vector() -> None:
    a, _ = _build()
    b, _ = _build()
    assert a == b and len(a) == 3


def test_features_are_documented_bounded_and_versioned() -> None:
    vecs, _ = _build()
    spec = {s.name: s for s in FEATURE_SPECS}
    for v in vecs:
        assert v.version == FEATURE_VERSION
        assert set(v.values) == set(spec)
        for name, value in v.values.items():
            if value is not None:
                lo, hi = spec[name].range
                assert lo <= value <= hi, (name, value)
        assert v.explanations["direction_score"]


def test_feature_values_explainable() -> None:
    vecs, sigs = _build()
    by_id = {s.signal_id: s for s in sigs}
    new_rf = next(v for v in vecs if by_id[v.signal_id].direction is Direction.NEW)
    assert new_rf.values["novelty"] == 1.0 and new_rf.values["section_importance"] == 1.0
    assert new_rf.values["evidence_quality"] == 1.0
    assert new_rf.values["parser_confidence"] == 1.0
    stronger = next(v for v in vecs if by_id[v.signal_id].direction is Direction.STRENGTHENED)
    assert stronger.values["direction_score"] == 1.0
    assert stronger.values["exhibit_weight"] is None  # not an 8-K
    assert stronger.values["xbrl_signal"] is None  # missing is not zero


def test_context_flags_and_xbrl() -> None:
    ctx = FeatureContext(
        is_amendment=True, restatement_signal=True, xbrl_signal={"liquidity": 0.9, "debt": 0.4}
    )
    vecs, sigs = _build(ctx)
    by_id = {s.signal_id: s for s in sigs}
    mdna = next(v for v in vecs if by_id[v.signal_id].detector == "mdna_topic")
    assert mdna.values["amendment_flag"] == 1.0 and mdna.values["restatement_flag"] == 1.0
    assert mdna.values["xbrl_signal"] == 0.9


def test_eight_k_and_missing_evidence() -> None:
    sig = ChangeSignal(
        "sig-x",
        "exhibit_topic",
        "8-K:4.02",
        ("unknown",),
        Direction.NEW,
        "r",
        (EvidenceRef(None, "b1", "l", ""),),
    )
    (v,) = build_features([sig], None, FeatureContext())
    assert v.values["section_importance"] == 1.0 and v.values["restatement_flag"] == 1.0
    assert v.values["exhibit_weight"] == 1.0 and v.values["topic_breadth"] == 0.0
    assert v.values["evidence_quality"] == 0.0  # no snapshot id → weak evidence
    assert v.values["change_magnitude"] is None


def test_no_legal_materiality_label() -> None:
    vecs, _ = _build()
    for s in FEATURE_SPECS:
        assert "materiality" not in s.name and "materiality" not in s.meaning.lower()
    for v in vecs:
        assert not any("material" in k for k in v.values)
