"""Normalized XBRL fact model shared by the aggregate (companyfacts) and filing iXBRL paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from filinglens.domain.accession import normalize_accession

STANDARD_PREFIXES = frozenset(
    {
        "us-gaap",
        "dei",
        "ifrs-full",
        "srt",
        "country",
        "currency",
        "exch",
        "naics",
        "sic",
        "stpr",
        "invest",
        "ecd",
        "cyd",
        "snj",
    }
)


class FactSource(StrEnum):
    COMPANYFACTS = "companyfacts"  # SEC aggregate, standard taxonomies, entity-wide only
    IXBRL = "ixbrl"  # direct filing extraction


@dataclass(frozen=True, kw_only=True)
class XbrlFact:
    source: FactSource
    cik: int
    accession: str | None
    concept: str  # local name, e.g. "Revenues"
    prefix: str  # taxonomy prefix, e.g. "us-gaap" or issuer prefix "aapl"
    namespace: str | None = None  # full namespace URI incl. taxonomy version when known
    unit: str | None
    value: Decimal | None  # numeric facts; None for text or nil
    text_value: str | None = None
    period_start: date | None = None
    period_end: date | None = None  # instant facts: the instant date
    is_instant: bool = False
    decimals: str | None = None
    scale: int | None = None
    sign_negated: bool = False
    dimensions: tuple[tuple[str, str], ...] = ()  # (axis, member), sorted
    context_id: str | None = None
    hidden: bool | None = None
    locator: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    form: str | None = None
    filed: date | None = None
    frame: str | None = None
    label: str | None = None
    fact_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.accession is not None:
            object.__setattr__(self, "accession", normalize_accession(self.accession))
        object.__setattr__(self, "dimensions", tuple(sorted(self.dimensions)))
        if self.is_instant and self.period_start is not None:
            raise ValueError("instant facts have no period start")
        key = "|".join(
            str(x)
            for x in (
                self.source.value,
                self.cik,
                self.accession,
                self.prefix,
                self.concept,
                self.unit,
                self.period_start,
                self.period_end,
                self.is_instant,
                self.dimensions,
                self.context_id,
                self.value,
                self.text_value,
                self.locator,
            )
        )
        object.__setattr__(self, "fact_id", "fact-" + hashlib.sha256(key.encode()).hexdigest()[:20])

    @property
    def qname(self) -> str:
        return f"{self.prefix}:{self.concept}"

    @property
    def is_custom(self) -> bool:
        return self.prefix.lower() not in STANDARD_PREFIXES

    @property
    def is_dimensional(self) -> bool:
        return bool(self.dimensions)

    @property
    def duration_days(self) -> int | None:
        if self.is_instant or self.period_start is None or self.period_end is None:
            return None
        return (self.period_end - self.period_start).days
