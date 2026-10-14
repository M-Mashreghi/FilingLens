"""Deterministic financial trend engine (``trend-engine/1``).

All arithmetic is Decimal-based Python; no LLM is involved. Every metric records its formula,
the canonical inputs it used (value, period, unit, source fact ids, accession) and the
transformation version. Missing inputs yield ``insufficient_data`` — never imputed zeros.

Sign / unit conventions:
- Monetary metrics use the reporting currency unit; inputs with mismatched units are refused.
- ``capex`` is the reported *payment* (positive outflow); ``FCF = operating cash flow - capex``.
- Growth rates are fractions ``(cur - prior) / |prior|`` (0.05 = +5%); undefined when prior=0.
- Margin deltas are differences of fractions (0.01 = +1 percentage point).
- Balance-sheet metrics use instants at the period end dates.

Point-in-time: only facts whose accession is in ``eligible_accessions`` (filings public at
``as_of``) are used when that set is provided.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from filinglens.domain.xbrl import XbrlFact
from filinglens.xbrl.normalize import FactSet, ResolutionStatus
from filinglens.xbrl.taxonomy import ConceptMapper

ENGINE_VERSION = "trend-engine/1"
_Q = Decimal("0.000001")


class MetricStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class CanonicalValue:
    metric: str
    period_start: date | None
    period_end: date
    value: Decimal
    unit: str | None
    fact_ids: tuple[str, ...]
    accession: str | None
    qname: str


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: MetricStatus
    value: Decimal | None
    unit: str | None
    formula: str
    inputs: tuple[CanonicalValue, ...]
    reason: str = ""
    version: str = ENGINE_VERSION

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(sorted({fid for i in self.inputs for fid in i.fact_ids}))

    @property
    def accessions(self) -> tuple[str, ...]:
        return tuple(sorted({i.accession for i in self.inputs if i.accession}))


@dataclass
class CanonicalStore:
    """Canonical metric values resolved from facts through versioned concept mappings."""

    facts: Iterable[XbrlFact]
    mapper: ConceptMapper = field(default_factory=ConceptMapper)
    eligible_accessions: set[str] | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        all_facts = list(self.facts)
        usable = [
            f
            for f in all_facts
            if self.eligible_accessions is None or f.accession in self.eligible_accessions
        ]
        self.excluded_not_public = len(all_facts) - len(usable)
        self.factset = FactSet(usable)
        self.mapping: dict[str, list[tuple[int, str]]] = {}
        for nf in self.factset.items:
            m = self.mapper.map_fact(nf.fact)
            if m.metric and m.priority is not None:
                self.mapping.setdefault(m.metric, []).append((m.priority, nf.key.qname))

    def get(self, metric: str, *, end: date, start: date | None = None) -> CanonicalValue | None:
        for _prio, qname in sorted(set(self.mapping.get(metric, []))):
            r = self.factset.lookup(qname, period_end=end, period_start=start)
            if r.status is ResolutionStatus.FOUND and r.value is not None:
                return CanonicalValue(
                    metric, start, end, r.value, r.unit, r.fact_ids, r.accession, qname
                )
            if r.status is ResolutionStatus.CONFLICT:
                self.notes.append(f"{metric} {start}..{end}: {r.reason}")
                return None
        return None


def _missing(
    name: str, formula: str, needed: list[tuple[str, CanonicalValue | None]]
) -> MetricResult:
    miss = [label for label, v in needed if v is None]
    return MetricResult(
        name,
        MetricStatus.INSUFFICIENT_DATA,
        None,
        None,
        formula,
        tuple(v for _, v in needed if v is not None),
        f"missing: {', '.join(miss)}",
    )


def _units_match(vals: list[CanonicalValue]) -> bool:
    return len({v.unit for v in vals}) == 1


def _ratio(
    name: str,
    formula: str,
    num: CanonicalValue | None,
    den: CanonicalValue | None,
    num_label: str,
    den_label: str,
) -> MetricResult:
    if num is None or den is None:
        return _missing(name, formula, [(num_label, num), (den_label, den)])
    if den.value == 0:
        return MetricResult(
            name,
            MetricStatus.INSUFFICIENT_DATA,
            None,
            None,
            formula,
            (num, den),
            "denominator is zero",
        )
    if not _units_match([num, den]):
        return MetricResult(
            name, MetricStatus.INSUFFICIENT_DATA, None, None, formula, (num, den), "unit mismatch"
        )
    return MetricResult(
        name, MetricStatus.OK, (num.value / den.value).quantize(_Q), "pure", formula, (num, den)
    )


def _growth(
    name: str, cur: CanonicalValue | None, prior: CanonicalValue | None, label: str
) -> MetricResult:
    formula = f"({label}_t - {label}_t-1) / |{label}_t-1|"
    if cur is None or prior is None:
        return _missing(name, formula, [(f"{label}_t", cur), (f"{label}_t-1", prior)])
    if prior.value == 0:
        return MetricResult(
            name,
            MetricStatus.INSUFFICIENT_DATA,
            None,
            None,
            formula,
            (cur, prior),
            "prior value is zero; growth undefined",
        )
    if not _units_match([cur, prior]):
        return MetricResult(
            name, MetricStatus.INSUFFICIENT_DATA, None, None, formula, (cur, prior), "unit mismatch"
        )
    value = ((cur.value - prior.value) / abs(prior.value)).quantize(_Q)
    return MetricResult(name, MetricStatus.OK, value, "pure", formula, (cur, prior))


def _diff(
    name: str, formula: str, a: CanonicalValue | None, b: CanonicalValue | None, la: str, lb: str
) -> MetricResult:
    if a is None or b is None:
        return _missing(name, formula, [(la, a), (lb, b)])
    if not _units_match([a, b]):
        return MetricResult(
            name, MetricStatus.INSUFFICIENT_DATA, None, None, formula, (a, b), "unit mismatch"
        )
    return MetricResult(name, MetricStatus.OK, a.value - b.value, a.unit, formula, (a, b))


@dataclass(frozen=True)
class PeriodPair:
    cur_start: date
    cur_end: date
    prior_start: date
    prior_end: date


class TrendEngine:
    def __init__(self, store: CanonicalStore) -> None:
        self.s = store

    def _flow(self, metric: str, start: date, end: date) -> CanonicalValue | None:
        return self.s.get(metric, start=start, end=end)

    def _stock(self, metric: str, end: date) -> CanonicalValue | None:
        return self.s.get(metric, end=end)

    def margin(self, numerator_metric: str, start: date, end: date) -> MetricResult:
        name = f"{numerator_metric}_margin"
        rev = self._flow("revenue", start, end)
        num = self._flow(numerator_metric, start, end)
        if num is None and numerator_metric == "gross_profit":
            cogs = self._flow("cost_of_revenue", start, end)
            if rev is not None and cogs is not None:
                gp = _diff(
                    "gross_profit",
                    "revenue - cost_of_revenue",
                    rev,
                    cogs,
                    "revenue",
                    "cost_of_revenue",
                )
                if gp.value is not None:
                    num = CanonicalValue(
                        "gross_profit",
                        start,
                        end,
                        gp.value,
                        gp.unit,
                        tuple(sorted(rev.fact_ids + cogs.fact_ids)),
                        rev.accession,
                        "derived:revenue-cost_of_revenue",
                    )
        return _ratio(name, f"{numerator_metric} / revenue", num, rev, numerator_metric, "revenue")

    def compute(self, p: PeriodPair) -> dict[str, MetricResult]:
        out: dict[str, MetricResult] = {}
        out["revenue_growth"] = _growth(
            "revenue_growth",
            self._flow("revenue", p.cur_start, p.cur_end),
            self._flow("revenue", p.prior_start, p.prior_end),
            "revenue",
        )
        for metric in ("gross_profit", "operating_income"):
            cur = self.margin(metric, p.cur_start, p.cur_end)
            prior = self.margin(metric, p.prior_start, p.prior_end)
            out[f"{metric}_margin"] = cur
            name = f"{metric}_margin_delta"
            formula = f"{metric}_margin_t - {metric}_margin_t-1"
            if cur.value is None or prior.value is None:
                out[name] = MetricResult(
                    name,
                    MetricStatus.INSUFFICIENT_DATA,
                    None,
                    None,
                    formula,
                    cur.inputs + prior.inputs,
                    "margin unavailable for a period",
                )
            else:
                out[name] = MetricResult(
                    name,
                    MetricStatus.OK,
                    cur.value - prior.value,
                    "pure",
                    formula,
                    cur.inputs + prior.inputs,
                )
        ocf = self._flow("operating_cash_flow", p.cur_start, p.cur_end)
        capex = self._flow("capex", p.cur_start, p.cur_end)
        out["operating_cash_flow"] = (
            MetricResult(
                "operating_cash_flow", MetricStatus.OK, ocf.value, ocf.unit, "reported", (ocf,)
            )
            if ocf
            else _missing("operating_cash_flow", "reported", [("operating_cash_flow", ocf)])
        )
        out["capex"] = (
            MetricResult(
                "capex", MetricStatus.OK, capex.value, capex.unit, "reported payments", (capex,)
            )
            if capex
            else _missing("capex", "reported payments", [("capex", capex)])
        )
        out["free_cash_flow"] = _diff(
            "free_cash_flow",
            "operating_cash_flow - capex",
            ocf,
            capex,
            "operating_cash_flow",
            "capex",
        )
        out["cash_change"] = _diff(
            "cash_change",
            "cash_t - cash_t-1",
            self._stock("cash", p.cur_end),
            self._stock("cash", p.prior_end),
            "cash_t",
            "cash_t-1",
        )
        out["long_term_debt_change"] = _diff(
            "long_term_debt_change",
            "long_term_debt_t - long_term_debt_t-1",
            self._stock("long_term_debt", p.cur_end),
            self._stock("long_term_debt", p.prior_end),
            "long_term_debt_t",
            "long_term_debt_t-1",
        )
        wc_cur = _diff(
            "working_capital",
            "current_assets - current_liabilities",
            self._stock("current_assets", p.cur_end),
            self._stock("current_liabilities", p.cur_end),
            "current_assets",
            "current_liabilities",
        )
        out["working_capital"] = wc_cur
        out["current_ratio"] = _ratio(
            "current_ratio",
            "current_assets / current_liabilities",
            self._stock("current_assets", p.cur_end),
            self._stock("current_liabilities", p.cur_end),
            "current_assets",
            "current_liabilities",
        )
        lt, cash = self._stock("long_term_debt", p.cur_end), self._stock("cash", p.cur_end)
        out["net_debt"] = _diff(
            "net_debt", "long_term_debt - cash", lt, cash, "long_term_debt", "cash"
        )
        out["receivables_growth"] = _growth(
            "receivables_growth",
            self._stock("receivables", p.cur_end),
            self._stock("receivables", p.prior_end),
            "receivables",
        )
        out["inventory_growth"] = _growth(
            "inventory_growth",
            self._stock("inventory", p.cur_end),
            self._stock("inventory", p.prior_end),
            "inventory",
        )
        out["diluted_share_change"] = _growth(
            "diluted_share_change",
            self._flow("diluted_shares", p.cur_start, p.cur_end),
            self._flow("diluted_shares", p.prior_start, p.prior_end),
            "diluted_shares",
        )
        out["net_income_growth"] = _growth(
            "net_income_growth",
            self._flow("net_income", p.cur_start, p.cur_end),
            self._flow("net_income", p.prior_start, p.prior_end),
            "net_income",
        )
        out["operating_cash_flow_growth"] = _growth(
            "operating_cash_flow_growth",
            ocf,
            self._flow("operating_cash_flow", p.prior_start, p.prior_end),
            "operating_cash_flow",
        )
        return out
