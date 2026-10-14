"""XBRL context, unit, dimension and period normalizer (``xbrl-normalizer/1``).

Duplicate-fact resolution policy (``DUPLICATE_POLICY``):

- Facts with the same key (concept, normalized unit, period, dimensions) are duplicates.
- If all numeric values agree after rounding each to the *coarsest* declared ``decimals``,
  the duplicate set is ``consistent``; the most precise fact (highest decimals, ``INF``
  highest; ties by fact id) is the representative.
- If values disagree beyond that tolerance the set is ``conflict`` and no value is chosen.
- Missing facts are reported as ``missing`` with ``value=None`` — never zero.
- Dimensional facts are keyed by their full (axis, member) set and never collapse into
  entity-wide values.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from filinglens.domain.xbrl import XbrlFact
from filinglens.lineage.comparator import fiscal_quarter

NORMALIZER_VERSION = "xbrl-normalizer/1"
DUPLICATE_POLICY = "duplicates/consistent-within-coarsest-decimals"

_UNIT_ALIASES = {"usd": "USD", "iso4217:usd": "USD", "xbrli:shares": "shares", "share": "shares"}


class PeriodClass(StrEnum):
    INSTANT = "instant"
    QUARTER = "quarter"
    HALF_YEAR = "half_year_ytd"
    NINE_MONTHS = "nine_months_ytd"
    ANNUAL = "annual"
    OTHER = "other_duration"


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    parts = []
    for side in unit.split("/"):
        names = []
        for raw in side.split("*"):
            name = _UNIT_ALIASES.get(raw.strip().lower(), raw.strip().split(":")[-1])
            # ISO 4217 currency codes are canonical upper-case three-letter codes.
            names.append(name.upper() if len(name) == 3 and name.isalpha() else name)
        parts.append("*".join(names))
    return "/".join(parts)


def classify_period(fact: XbrlFact) -> PeriodClass:
    if fact.is_instant:
        return PeriodClass.INSTANT
    days = fact.duration_days
    if days is None:
        return PeriodClass.OTHER
    if 80 <= days <= 100:
        return PeriodClass.QUARTER
    if 170 <= days <= 190:
        return PeriodClass.HALF_YEAR
    if 260 <= days <= 285:
        return PeriodClass.NINE_MONTHS
    if 350 <= days <= 380:
        return PeriodClass.ANNUAL
    return PeriodClass.OTHER


@dataclass(frozen=True)
class FactKey:
    qname: str
    unit: str | None
    period_start: date | None
    period_end: date | None
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class NormalizedFact:
    fact: XbrlFact
    key: FactKey
    unit: str | None
    original_unit: str | None
    period_class: PeriodClass
    fiscal_quarter: int | None


class ResolutionStatus(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ResolvedValue:
    key: FactKey | None
    status: ResolutionStatus
    value: Decimal | None
    representative_fact_id: str | None
    fact_ids: tuple[str, ...]
    duplicates: int
    reason: str
    accession: str | None = None
    unit: str | None = None


def _decimals_rank(d: str | None) -> float:
    if d is None:
        return float("-inf")
    if d.upper() == "INF":
        return float("inf")
    try:
        return float(int(d))
    except ValueError:
        return float("-inf")


def _round(v: Decimal, decimals: float) -> Decimal:
    if decimals in (float("inf"), float("-inf")):
        return v
    q = Decimal(1).scaleb(-int(decimals))
    return (v / q).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * q


class FactSet:
    def __init__(self, facts: Iterable[XbrlFact], *, fiscal_year_end: str | None = None) -> None:
        self.items: list[NormalizedFact] = []
        for f in facts:
            unit = normalize_unit(f.unit)
            key = FactKey(f.qname, unit, f.period_start, f.period_end, f.dimensions)
            fq = fiscal_quarter(f.period_end, fiscal_year_end) if f.period_end else None
            self.items.append(NormalizedFact(f, key, unit, f.unit, classify_period(f), fq))
        self.by_key: dict[FactKey, list[NormalizedFact]] = {}
        for nf in self.items:
            self.by_key.setdefault(nf.key, []).append(nf)

    def resolve_key(self, key: FactKey) -> ResolvedValue:
        group = [nf for nf in self.by_key.get(key, []) if nf.fact.value is not None]
        ids = tuple(sorted(nf.fact.fact_id for nf in self.by_key.get(key, [])))
        if not group:
            return ResolvedValue(
                key,
                ResolutionStatus.MISSING,
                None,
                None,
                ids,
                0,
                "no numeric fact for key (nil or absent)",
            )
        coarsest = min(_decimals_rank(nf.fact.decimals) for nf in group)
        rounded = {_round(nf.fact.value, coarsest) for nf in group if nf.fact.value is not None}
        if len(rounded) > 1:
            return ResolvedValue(
                key,
                ResolutionStatus.CONFLICT,
                None,
                None,
                ids,
                len(group),
                f"duplicate facts disagree: {sorted(map(str, rounded))}",
            )
        rep = sorted(group, key=lambda nf: (-_decimals_rank(nf.fact.decimals), nf.fact.fact_id))[0]
        return ResolvedValue(
            key,
            ResolutionStatus.FOUND,
            rep.fact.value,
            rep.fact.fact_id,
            ids,
            len(group),
            "single fact" if len(group) == 1 else f"{len(group)} consistent duplicates",
            rep.fact.accession,
            rep.unit,
        )

    def lookup(
        self,
        qname: str,
        *,
        period_end: date,
        period_start: date | None = None,
        unit: str | None = None,
        dimensions: tuple[tuple[str, str], ...] = (),
    ) -> ResolvedValue:
        dims = tuple(sorted(dimensions))
        cands = [
            k
            for k in self.by_key
            if k.qname == qname
            and k.period_end == period_end
            and k.period_start == period_start
            and k.dimensions == dims
            and (unit is None or k.unit == normalize_unit(unit))
        ]
        if not cands:
            return ResolvedValue(
                None,
                ResolutionStatus.MISSING,
                None,
                None,
                (),
                0,
                "no fact for concept/period/dimensions",
            )
        if len(cands) > 1:
            return ResolvedValue(
                None,
                ResolutionStatus.CONFLICT,
                None,
                None,
                (),
                0,
                f"multiple units for key: {sorted(str(k.unit) for k in cands)}",
            )
        return self.resolve_key(cands[0])

    def dimensional(self, qname: str) -> list[NormalizedFact]:
        return [nf for nf in self.items if nf.key.qname == qname and nf.key.dimensions]
