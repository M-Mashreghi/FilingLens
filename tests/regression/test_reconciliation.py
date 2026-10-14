"""Task 4.7: narrative ↔ numeric reconciliation."""

from __future__ import annotations

from decimal import Decimal

from filinglens.analytics.reconciliation import Classification, narrative_sign, reconcile
from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.diff.detectors import ChangeSignal, Direction
from filinglens.diff.text_diff import EvidenceRef


def sig(topic: str, new_text: str, sid: str = "s1") -> ChangeSignal:
    return ChangeSignal(
        sid,
        "mdna_topic",
        "7",
        (topic,),
        Direction.NEW,
        "r",
        (EvidenceRef("old", "b0", "/o", "previous text"), EvidenceRef("new", "b1", "/n", new_text)),
        "chg-1",
    )


def mr(name: str, value: str | None) -> MetricResult:
    if value is None:
        return MetricResult(name, MetricStatus.INSUFFICIENT_DATA, None, None, "f", (), "missing")
    return MetricResult(name, MetricStatus.OK, Decimal(value), "pure", "f", ())


M = {
    "revenue_growth": mr("revenue_growth", "0.08"),
    "gross_profit_margin_delta": mr("gross_profit_margin_delta", "-0.02"),
    "operating_income_margin_delta": mr("operating_income_margin_delta", "0.01"),
    "cash_change": mr("cash_change", "-500"),
    "long_term_debt_change": mr("long_term_debt_change", None),
}


def one(signal: ChangeSignal, metrics=M):  # type: ignore[no-untyped-def]
    (r,) = reconcile([signal], metrics)
    return r


def test_aligned() -> None:
    r = one(sig("demand", "Demand increased strongly across regions."))
    assert r.classification is Classification.ALIGNED
    assert r.driving_metrics == ("revenue_growth",) and "revenue_growth" in r.reason


def test_tension() -> None:
    r = one(sig("liquidity", "Our liquidity position improved and remains strong."))
    assert r.classification is Classification.TENSION and "cash_change" in r.reason
    assert r.narrative_evidence[-1].text.startswith("Our liquidity")
    assert r.metrics[0].value == Decimal("-500")  # numeric lineage preserved, not recalculated


def test_mixed() -> None:
    r = one(sig("margins", "Margins expanded due to higher services mix."))
    assert r.classification is Classification.MIXED
    assert r.numeric_signs == {"gross_profit_margin_delta": -1, "operating_income_margin_delta": 1}


def test_cost_polarity() -> None:
    # Higher costs (narrative up) with a falling gross margin is consistent.
    r = one(sig("costs", "Input costs increased due to higher component prices."))
    assert r.classification is Classification.ALIGNED


def test_insufficient_data_paths() -> None:
    assert (
        one(sig("debt", "Borrowings increased.")).classification is Classification.INSUFFICIENT_DATA
    )
    assert "metrics unavailable" in one(sig("debt", "Borrowings increased.")).reason
    unmapped = one(sig("customer_concentration", "Our largest customer grew."))
    assert (
        unmapped.classification is Classification.INSUFFICIENT_DATA
        and "no metric mapping" in unmapped.reason
    )
    flat_text = one(sig("demand", "Demand was discussed in the investor letter."))
    assert flat_text.classification is Classification.INSUFFICIENT_DATA


def test_neutral_band() -> None:
    m = {**M, "revenue_growth": mr("revenue_growth", "0.003")}
    r = one(sig("demand", "Demand increased."), m)
    assert r.classification is Classification.MIXED and r.numeric_signs == {"revenue_growth": 0}


def test_narrative_sign() -> None:
    assert narrative_sign("Sales increased and margins improved") == 1
    assert narrative_sign("Sales declined under pricing pressure") == -1
    assert narrative_sign("Sales increased while costs declined") == 0
