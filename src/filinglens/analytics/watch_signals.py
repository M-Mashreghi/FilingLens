"""Accounting / disclosure watch signals (``watch-rules/1``).

Rule-based, neutral research flags for analyst follow-up. They are not accusations and do not
assert misstatement. Every rule documents its formula and threshold; insufficient data yields
``unknown`` (never ``not_flagged``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.diff.text_diff import Change, ChangeKind
from filinglens.lineage.amendments import RestatementSignal

RULES_VERSION = "watch-rules/1"


class WatchStatus(StrEnum):
    FLAGGED = "flagged"
    NOT_FLAGGED = "not_flagged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WatchRule:
    rule_id: str
    title: str
    formula: str
    threshold: str
    description: str


RULES: dict[str, WatchRule] = {
    r.rule_id: r
    for r in (
        WatchRule(
            "receivables_vs_revenue",
            "Receivables growth vs revenue growth",
            "receivables_growth - revenue_growth",
            "> 0.10",
            "Receivables grew faster than revenue; review collection terms and timing.",
        ),
        WatchRule(
            "inventory_vs_revenue",
            "Inventory growth vs revenue growth",
            "inventory_growth - revenue_growth",
            "> 0.10",
            "Inventory grew faster than revenue; review demand and valuation notes.",
        ),
        WatchRule(
            "cash_flow_vs_earnings",
            "Operating cash flow vs net income",
            "operating_cash_flow_growth - net_income_growth",
            "< -0.20",
            "Operating cash flow growth trailed net income growth; review accruals.",
        ),
        WatchRule(
            "liquidity_debt_shift",
            "Cash decline with higher long-term debt",
            "cash_change < 0 and long_term_debt_change > 0",
            "both conditions",
            "Cash declined while long-term debt increased; review liquidity notes.",
        ),
        WatchRule(
            "dilution",
            "Diluted share count increase",
            "diluted_share_change",
            "> 0.03",
            "Diluted share count rose more than 3%; review issuance and compensation.",
        ),
        WatchRule(
            "large_revenue_change",
            "Large revenue change",
            "|revenue_growth|",
            "> 0.50",
            "Revenue changed by more than 50%; review drivers and reporting changes.",
        ),
        WatchRule(
            "accounting_policy_text_change",
            "Accounting-policy text changed",
            "added/modified/removed blocks mentioning accounting policies or estimates",
            ">= 1",
            "Accounting-policy or critical-estimate language changed; review the exact text.",
        ),
        WatchRule(
            "restatement_or_amendment",
            "Restatement-related disclosure or amendment",
            "8-K Item 4.02 or 10-K/A / 10-Q/A filed",
            ">= 1",
            "A non-reliance disclosure or amendment was filed; review its scope.",
        ),
    )
}


@dataclass(frozen=True)
class WatchSignal:
    rule_id: str
    status: WatchStatus
    observed: Decimal | None
    description: str
    formula: str
    threshold: str
    metric_inputs: tuple[MetricResult, ...] = ()
    evidence_change_ids: tuple[str, ...] = ()
    related_accessions: tuple[str, ...] = ()
    reason: str = ""
    version: str = RULES_VERSION


def _sig(
    rule_id: str,
    status: WatchStatus,
    observed: Decimal | None,
    inputs: tuple[MetricResult, ...],
    reason: str = "",
    *,
    evidence_change_ids: tuple[str, ...] = (),
    related_accessions: tuple[str, ...] = (),
) -> WatchSignal:
    r = RULES[rule_id]
    return WatchSignal(
        rule_id,
        status,
        observed,
        r.description,
        r.formula,
        r.threshold,
        inputs,
        evidence_change_ids,
        related_accessions,
        reason,
    )


def _ok(*ms: MetricResult) -> bool:
    return all(m.status is MetricStatus.OK and m.value is not None for m in ms)


def _gap_rule(
    rule_id: str, a: MetricResult, b: MetricResult, threshold: Decimal, above: bool
) -> WatchSignal:
    if not _ok(a, b):
        missing = [m.name for m in (a, b) if not _ok(m)]
        return _sig(rule_id, WatchStatus.UNKNOWN, None, (a, b), f"insufficient data: {missing}")
    gap = a.value - b.value  # type: ignore[operator]
    hit = gap > threshold if above else gap < threshold
    return _sig(rule_id, WatchStatus.FLAGGED if hit else WatchStatus.NOT_FLAGGED, gap, (a, b))


_POLICY_TEXT = re.compile(
    r"\b(critical accounting (?:policies|estimates)|significant accounting policies|"
    r"change in accounting (?:principle|estimate)|revenue recognition|adopted asu)\b",
    re.I,
)


def evaluate(
    metrics: Mapping[str, MetricResult],
    *,
    changes: Iterable[Change] = (),
    restatements: Iterable[RestatementSignal] = (),
) -> list[WatchSignal]:
    m = metrics
    out = [
        _gap_rule(
            "receivables_vs_revenue",
            m["receivables_growth"],
            m["revenue_growth"],
            Decimal("0.10"),
            True,
        ),
        _gap_rule(
            "inventory_vs_revenue",
            m["inventory_growth"],
            m["revenue_growth"],
            Decimal("0.10"),
            True,
        ),
        _gap_rule(
            "cash_flow_vs_earnings",
            m["operating_cash_flow_growth"],
            m["net_income_growth"],
            Decimal("-0.20"),
            False,
        ),
    ]
    cash, debt = m["cash_change"], m["long_term_debt_change"]
    if _ok(cash, debt):
        hit = cash.value < 0 and debt.value > 0  # type: ignore[operator]
        out.append(
            _sig(
                "liquidity_debt_shift",
                WatchStatus.FLAGGED if hit else WatchStatus.NOT_FLAGGED,
                None,
                (cash, debt),
            )
        )
    else:
        out.append(
            _sig(
                "liquidity_debt_shift", WatchStatus.UNKNOWN, None, (cash, debt), "insufficient data"
            )
        )
    dil = m["diluted_share_change"]
    out.append(
        _sig(
            "dilution",
            WatchStatus.FLAGGED if dil.value > Decimal("0.03") else WatchStatus.NOT_FLAGGED,  # type: ignore[operator]
            dil.value,
            (dil,),
        )
        if _ok(dil)
        else _sig("dilution", WatchStatus.UNKNOWN, None, (dil,), "insufficient data")
    )
    rg = m["revenue_growth"]
    out.append(
        _sig(
            "large_revenue_change",
            WatchStatus.FLAGGED if abs(rg.value) > Decimal("0.50") else WatchStatus.NOT_FLAGGED,  # type: ignore[arg-type]
            rg.value,
            (rg,),
        )
        if _ok(rg)
        else _sig("large_revenue_change", WatchStatus.UNKNOWN, None, (rg,), "insufficient data")
    )
    policy_ids = tuple(
        c.change_id
        for c in changes
        if c.kind in (ChangeKind.ADDED, ChangeKind.MODIFIED, ChangeKind.REMOVED)
        and any(_POLICY_TEXT.search(r.text) for r in (c.old, c.new) if r is not None)
    )
    out.append(
        _sig(
            "accounting_policy_text_change",
            WatchStatus.FLAGGED if policy_ids else WatchStatus.NOT_FLAGGED,
            Decimal(len(policy_ids)),
            (),
            evidence_change_ids=policy_ids,
        )
    )
    rs = tuple(sorted({s.accession for s in restatements}))
    out.append(
        _sig(
            "restatement_or_amendment",
            WatchStatus.FLAGGED if rs else WatchStatus.NOT_FLAGGED,
            Decimal(len(rs)),
            (),
            related_accessions=rs,
        )
    )
    return out
