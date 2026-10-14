"""Narrative ↔ numeric reconciliation engine (``reconciliation/1``).

Compares the direction of changed management narrative for a topic with the deterministic
direction of related canonical metrics. Classifications: ``aligned``, ``mixed``, ``tension``,
``insufficient_data``. Both evidence types are preserved; an LLM may later explain a result
but never creates or recalculates the numbers used here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.diff.detectors import ChangeSignal
from filinglens.diff.text_diff import EvidenceRef

RECONCILIATION_VERSION = "reconciliation/1"

# topic -> (metric, polarity): polarity -1 means "good narrative" corresponds to a falling metric.
TOPIC_METRICS: dict[str, tuple[tuple[str, int], ...]] = {
    "demand": (("revenue_growth", 1),),
    "pricing": (("gross_profit_margin_delta", 1),),
    "margins": (("gross_profit_margin_delta", 1), ("operating_income_margin_delta", 1)),
    "costs": (("gross_profit_margin_delta", -1),),
    "liquidity": (("cash_change", 1),),
    "debt": (("long_term_debt_change", 1),),
}
NEUTRAL_BAND: dict[str, Decimal] = {
    "revenue_growth": Decimal("0.005"),
    "gross_profit_margin_delta": Decimal("0.001"),
    "operating_income_margin_delta": Decimal("0.001"),
}

_UP = re.compile(
    r"\b(increas\w*|grew|grow\w*|higher|improv\w*|strong\w*|expand\w*|rose|rise|up)\b", re.I
)
_DOWN = re.compile(
    r"\b(decreas\w*|declin\w*|lower|weak\w*|reduc\w*|fell|fall\w*|pressure\w*|contract\w*|down)\b",
    re.I,
)


class Classification(StrEnum):
    ALIGNED = "aligned"
    MIXED = "mixed"
    TENSION = "tension"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class Reconciliation:
    topic: str
    classification: Classification
    reason: str
    narrative_sign: int
    numeric_signs: dict[str, int]
    narrative_evidence: tuple[EvidenceRef, ...]
    metrics: tuple[MetricResult, ...]
    version: str = RECONCILIATION_VERSION

    @property
    def driving_metrics(self) -> tuple[str, ...]:
        return tuple(sorted(self.numeric_signs))


def narrative_sign(text: str) -> int:
    up, down = len(_UP.findall(text)), len(_DOWN.findall(text))
    return (up > down) - (down > up)


def numeric_sign(m: MetricResult) -> int | None:
    if m.status is not MetricStatus.OK or m.value is None:
        return None
    band = NEUTRAL_BAND.get(m.name, Decimal(0))
    if abs(m.value) <= band:
        return 0
    return 1 if m.value > 0 else -1


def _new_text(sig: ChangeSignal) -> str:
    # The new side of the change carries the current narrative.
    return " ".join(e.text for e in sig.evidence[-1:]) if sig.evidence else ""


def reconcile(
    signals: Iterable[ChangeSignal], metrics: Mapping[str, MetricResult]
) -> list[Reconciliation]:
    by_topic: dict[str, list[ChangeSignal]] = {}
    for s in signals:
        for t in s.topics:
            by_topic.setdefault(t, []).append(s)
    out: list[Reconciliation] = []
    for topic in sorted(by_topic):
        sigs = by_topic[topic]
        evidence = tuple(e for s in sigs for e in s.evidence)
        n_sign = narrative_sign(" ".join(_new_text(s) for s in sigs))
        mapping = TOPIC_METRICS.get(topic)
        if not mapping:
            out.append(
                Reconciliation(
                    topic,
                    Classification.INSUFFICIENT_DATA,
                    f"no metric mapping for topic '{topic}'",
                    n_sign,
                    {},
                    evidence,
                    (),
                )
            )
            continue
        used = tuple(metrics[m] for m, _ in mapping if m in metrics)
        signs: dict[str, int] = {}
        missing: list[str] = []
        for name, polarity in mapping:
            m = metrics.get(name)
            num_sign = numeric_sign(m) if m is not None else None
            if num_sign is None:
                missing.append(name)
            else:
                signs[name] = num_sign * polarity
        if not signs:
            out.append(
                Reconciliation(
                    topic,
                    Classification.INSUFFICIENT_DATA,
                    f"metrics unavailable: {', '.join(missing)}",
                    n_sign,
                    {},
                    evidence,
                    used,
                )
            )
            continue
        if n_sign == 0:
            out.append(
                Reconciliation(
                    topic,
                    Classification.INSUFFICIENT_DATA,
                    "narrative direction not determinable",
                    n_sign,
                    signs,
                    evidence,
                    used,
                )
            )
            continue
        agree = [k for k, v in signs.items() if v == n_sign]
        oppose = [k for k, v in signs.items() if v == -n_sign]
        flat = [k for k, v in signs.items() if v == 0]
        if agree and not oppose and not flat:
            cls, why = Classification.ALIGNED, f"narrative and {', '.join(agree)} move together"
        elif oppose and not agree:
            cls, why = Classification.TENSION, f"narrative direction opposes {', '.join(oppose)}"
        else:
            cls = Classification.MIXED
            why = f"agree: {agree or '-'}; oppose: {oppose or '-'}; flat: {flat or '-'}"
        if missing:
            why += f" (unavailable: {', '.join(missing)})"
        out.append(Reconciliation(topic, cls, why, n_sign, signs, evidence, used))
    return out
