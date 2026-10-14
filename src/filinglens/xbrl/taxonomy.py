"""Taxonomy / concept mapping and version-drift layer (``concept-map/1``).

Standard concepts map to internal canonical metrics through versioned, auditable rules that
may be restricted to a range of taxonomy versions. Issuer extension (custom) concepts are
first-class facts and are mapped only by an explicit issuer-scoped rule with evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from filinglens.domain.xbrl import XbrlFact

MAPPING_VERSION = "concept-map/1"
_NS_VERSION = re.compile(
    r"/(?P<prefix>us-gaap|dei|srt|ifrs-full|ifrs)/(?P<year>\d{4})(?:-\d{2}-\d{2})?/?$"
)


def taxonomy_version(namespace: str | None) -> tuple[str, str] | None:
    """``http://fasb.org/us-gaap/2024`` → ``("us-gaap", "2024")``."""
    if not namespace:
        return None
    m = _NS_VERSION.search(namespace)
    return (m.group("prefix"), m.group("year")) if m else None


@dataclass(frozen=True)
class MappingRule:
    rule_id: str
    metric: str
    qname: str
    priority: int = 0  # lower wins when several concepts are reported for one metric
    min_version: int | None = None
    max_version: int | None = None
    note: str = ""

    def applies(self, version: int | None) -> bool:
        if version is None:
            return True
        if self.min_version is not None and version < self.min_version:
            return False
        return not (self.max_version is not None and version > self.max_version)


@dataclass(frozen=True)
class CustomMappingRule:
    """Explicit, evidence-backed mapping of an issuer extension concept."""

    rule_id: str
    cik: int
    qname: str
    metric: str
    evidence: str

    def __post_init__(self) -> None:
        if not self.evidence.strip():
            raise ValueError("custom concept mappings require evidence")


def _r(rid: str, metric: str, qname: str, prio: int = 0, **kw: int) -> MappingRule:
    return MappingRule(rid, metric, qname, prio, **kw)  # type: ignore[arg-type]


STANDARD_RULES: tuple[MappingRule, ...] = (
    _r(
        "rev-asc606",
        "revenue",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        0,
        min_version=2017,
    ),
    _r("rev-total", "revenue", "us-gaap:Revenues", 1),
    _r("rev-salesnet", "revenue", "us-gaap:SalesRevenueNet", 2, max_version=2018),
    _r("cogs-goods-services", "cost_of_revenue", "us-gaap:CostOfGoodsAndServicesSold", 0),
    _r("cogs-revenue", "cost_of_revenue", "us-gaap:CostOfRevenue", 1),
    _r("gross-profit", "gross_profit", "us-gaap:GrossProfit"),
    _r("op-income", "operating_income", "us-gaap:OperatingIncomeLoss"),
    _r("net-income", "net_income", "us-gaap:NetIncomeLoss"),
    _r("ocf", "operating_cash_flow", "us-gaap:NetCashProvidedByUsedInOperatingActivities"),
    _r("capex-ppe", "capex", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"),
    _r("cash", "cash", "us-gaap:CashAndCashEquivalentsAtCarryingValue"),
    _r("debt-lt-noncurrent", "long_term_debt", "us-gaap:LongTermDebtNoncurrent", 0),
    _r("debt-lt", "long_term_debt", "us-gaap:LongTermDebt", 1),
    _r("debt-current", "current_debt", "us-gaap:LongTermDebtCurrent", 0),
    _r("cp", "commercial_paper", "us-gaap:CommercialPaper"),
    _r("receivables", "receivables", "us-gaap:AccountsReceivableNetCurrent"),
    _r("inventory", "inventory", "us-gaap:InventoryNet"),
    _r("assets-current", "current_assets", "us-gaap:AssetsCurrent"),
    _r("liabilities-current", "current_liabilities", "us-gaap:LiabilitiesCurrent"),
    _r(
        "shares-diluted",
        "diluted_shares",
        "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
    _r("shares-outstanding", "shares_outstanding", "dei:EntityCommonStockSharesOutstanding"),
    _r("eps-diluted", "eps_diluted", "us-gaap:EarningsPerShareDiluted"),
)


@dataclass(frozen=True)
class MappingResult:
    fact_id: str
    qname: str
    metric: str | None
    rule_id: str | None
    priority: int | None
    taxonomy_version: tuple[str, str] | None
    mapping_version: str
    reason: str


@dataclass
class ConceptMapper:
    rules: tuple[MappingRule, ...] = STANDARD_RULES
    custom_rules: tuple[CustomMappingRule, ...] = ()
    version: str = MAPPING_VERSION
    _by_qname: dict[str, list[MappingRule]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        for r in self.rules:
            self._by_qname.setdefault(r.qname, []).append(r)

    def map_fact(self, fact: XbrlFact) -> MappingResult:
        tv = taxonomy_version(fact.namespace)
        if fact.is_custom:
            for cr in self.custom_rules:
                if cr.cik == fact.cik and cr.qname == fact.qname:
                    return MappingResult(
                        fact.fact_id,
                        fact.qname,
                        cr.metric,
                        cr.rule_id,
                        None,
                        tv,
                        self.version,
                        f"explicit custom rule: {cr.evidence}",
                    )
            return MappingResult(
                fact.fact_id,
                fact.qname,
                None,
                None,
                None,
                tv,
                self.version,
                "custom extension concept; no explicit mapping rule",
            )
        if fact.is_dimensional:
            return MappingResult(
                fact.fact_id,
                fact.qname,
                None,
                None,
                None,
                tv,
                self.version,
                "dimensional fact; canonical metrics are entity-wide only",
            )
        year = int(tv[1]) if tv else None
        for rule in sorted(self._by_qname.get(fact.qname, []), key=lambda r: r.priority):
            if rule.applies(year):
                return MappingResult(
                    fact.fact_id,
                    fact.qname,
                    rule.metric,
                    rule.rule_id,
                    rule.priority,
                    tv,
                    self.version,
                    f"standard rule {rule.rule_id}",
                )
            return MappingResult(
                fact.fact_id,
                fact.qname,
                None,
                rule.rule_id,
                None,
                tv,
                self.version,
                f"rule {rule.rule_id} not valid for taxonomy {year}",
            )
        return MappingResult(
            fact.fact_id,
            fact.qname,
            None,
            None,
            None,
            tv,
            self.version,
            "no mapping rule for concept",
        )


@dataclass(frozen=True)
class DriftReport:
    version_changes: dict[str, tuple[str | None, str | None]]
    metric_concept_changes: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]

    @property
    def has_drift(self) -> bool:
        return bool(self.version_changes or self.metric_concept_changes)


def _versions(facts: Iterable[XbrlFact]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in facts:
        tv = taxonomy_version(f.namespace)
        if tv:
            out[tv[0]] = tv[1]
    return out


def detect_drift(
    old: Iterable[XbrlFact], new: Iterable[XbrlFact], mapper: ConceptMapper
) -> DriftReport:
    old_l, new_l = list(old), list(new)
    vo, vn = _versions(old_l), _versions(new_l)
    version_changes = {
        p: (vo.get(p), vn.get(p)) for p in sorted(set(vo) | set(vn)) if vo.get(p) != vn.get(p)
    }

    def concepts(facts: list[XbrlFact]) -> dict[str, tuple[str, ...]]:
        out: dict[str, set[str]] = {}
        for f in facts:
            m = mapper.map_fact(f)
            if m.metric:
                out.setdefault(m.metric, set()).add(f.qname)
        return {k: tuple(sorted(v)) for k, v in out.items()}

    co, cn = concepts(old_l), concepts(new_l)
    metric_changes = {
        m: (co.get(m, ()), cn.get(m, ())) for m in sorted(set(co) & set(cn)) if co[m] != cn[m]
    }
    return DriftReport(version_changes, metric_changes)
