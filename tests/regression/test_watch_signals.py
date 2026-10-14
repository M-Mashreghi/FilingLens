"""Task 4.6: accounting / disclosure watch signals."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.analytics.watch_signals import RULES, WatchStatus, evaluate
from filinglens.diff.text_diff import Change, ChangeKind, EvidenceRef
from filinglens.lineage.amendments import RestatementSignal

NAMES = [
    "receivables_growth",
    "revenue_growth",
    "inventory_growth",
    "operating_cash_flow_growth",
    "net_income_growth",
    "cash_change",
    "long_term_debt_change",
    "diluted_share_change",
]


def mr(name: str, value: str | None) -> MetricResult:
    if value is None:
        return MetricResult(name, MetricStatus.INSUFFICIENT_DATA, None, None, "f", (), "missing: x")
    return MetricResult(name, MetricStatus.OK, Decimal(value), "pure", "f", ())


def metrics(**vals: str | None) -> dict[str, MetricResult]:
    base = {
        "receivables_growth": "0.05",
        "revenue_growth": "0.04",
        "inventory_growth": "0.03",
        "operating_cash_flow_growth": "0.06",
        "net_income_growth": "0.05",
        "cash_change": "10",
        "long_term_debt_change": "-5",
        "diluted_share_change": "-0.01",
    }
    base.update(vals)
    return {k: mr(k, v) for k, v in base.items()}


def by_rule(sigs):  # type: ignore[no-untyped-def]
    return {s.rule_id: s for s in sigs}


def test_quiet_baseline_flags_nothing() -> None:
    sigs = by_rule(evaluate(metrics()))
    assert set(sigs) == set(RULES)
    assert all(s.status is WatchStatus.NOT_FLAGGED for s in sigs.values())


@pytest.mark.parametrize(
    "overrides,rule",
    [
        ({"receivables_growth": "0.30"}, "receivables_vs_revenue"),
        ({"inventory_growth": "0.25"}, "inventory_vs_revenue"),
        (
            {"operating_cash_flow_growth": "-0.20", "net_income_growth": "0.10"},
            "cash_flow_vs_earnings",
        ),
        ({"cash_change": "-100", "long_term_debt_change": "500"}, "liquidity_debt_shift"),
        ({"diluted_share_change": "0.05"}, "dilution"),
        ({"revenue_growth": "-0.60"}, "large_revenue_change"),
    ],
)
def test_each_rule_flags_on_threshold(overrides: dict[str, str], rule: str) -> None:
    s = by_rule(evaluate(metrics(**overrides)))[rule]
    assert s.status is WatchStatus.FLAGGED and s.formula and s.threshold


def test_threshold_boundaries() -> None:
    at = by_rule(evaluate(metrics(receivables_growth="0.14", revenue_growth="0.04")))[
        "receivables_vs_revenue"
    ]
    assert at.status is WatchStatus.NOT_FLAGGED and at.observed == Decimal("0.10")


def test_insufficient_data_returns_unknown() -> None:
    sigs = by_rule(
        evaluate(
            metrics(
                inventory_growth=None,
                cash_change=None,
                diluted_share_change=None,
                revenue_growth=None,
            )
        )
    )
    for rule in (
        "inventory_vs_revenue",
        "liquidity_debt_shift",
        "dilution",
        "large_revenue_change",
        "receivables_vs_revenue",
    ):
        assert sigs[rule].status is WatchStatus.UNKNOWN, rule
        assert sigs[rule].observed is None


def test_text_and_lineage_rules_carry_evidence() -> None:
    chg = Change(
        "chg-1",
        "7",
        ChangeKind.MODIFIED,
        "paragraph",
        EvidenceRef(
            "s1", "b1", "/x", "Our critical accounting estimates include warranty reserves."
        ),
        EvidenceRef(
            "s2",
            "b2",
            "/y",
            "Our critical accounting estimates include warranty and returns reserves.",
        ),
    )
    other = Change(
        "chg-2",
        "7",
        ChangeKind.ADDED,
        "paragraph",
        None,
        EvidenceRef("s2", "b3", "/z", "Sales rose."),
    )
    rs = RestatementSignal(
        "0001234567-25-000008", "non_reliance_8k", datetime(2025, 2, 10, tzinfo=UTC), "d"
    )
    sigs = by_rule(evaluate(metrics(), changes=[chg, other], restatements=[rs]))
    assert sigs["accounting_policy_text_change"].evidence_change_ids == ("chg-1",)
    assert sigs["restatement_or_amendment"].status is WatchStatus.FLAGGED
    assert sigs["restatement_or_amendment"].related_accessions == ("0001234567-25-000008",)


def test_every_rule_documented_and_neutral() -> None:
    banned = ("fraud", "manipulat", "misconduct", "illegal", "cooking", "aggressive", "misstat")
    for rule in RULES.values():
        text = f"{rule.title} {rule.description}".lower()
        assert rule.formula and rule.threshold and rule.description
        assert not any(b in text for b in banned), rule.rule_id
