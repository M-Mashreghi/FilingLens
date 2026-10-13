"""SEC ``companyfacts`` adapter (``sec-companyfacts/1``) and bulk ``companyfacts.zip`` reader.

``companyfacts`` aggregates facts that use standard (non-custom) taxonomies and apply to the
whole filing entity. It is NOT a complete representation of filing-specific XBRL: custom
concepts and dimensional facts must come from direct iXBRL extraction.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from filinglens.domain.identifiers import cik10
from filinglens.domain.xbrl import FactSource, XbrlFact

COMPANYFACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
BULK_COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
ADAPTER_VERSION = "sec-companyfacts/1"


class BytesFetcher(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


class LookupStatus(StrEnum):
    FOUND = "found"
    MISSING_CONCEPT = "missing_concept"
    MISSING_UNIT = "missing_unit"


@dataclass(frozen=True)
class ConceptLookup:
    status: LookupStatus
    facts: tuple[XbrlFact, ...]
    available_units: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyFacts:
    cik: int
    entity_name: str
    facts: tuple[XbrlFact, ...]
    source: str
    source_version: str

    def lookup(self, qname: str, unit: str | None = None) -> ConceptLookup:
        prefix, _, concept = qname.partition(":")
        matching = [f for f in self.facts if f.prefix == prefix and f.concept == concept]
        if not matching:
            return ConceptLookup(LookupStatus.MISSING_CONCEPT, ())
        units = tuple(sorted({f.unit or "" for f in matching}))
        if unit is not None:
            matching = [f for f in matching if f.unit == unit]
            if not matching:
                return ConceptLookup(LookupStatus.MISSING_UNIT, (), units)
        return ConceptLookup(LookupStatus.FOUND, tuple(matching), units)


def _d(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_companyfacts(raw: bytes, *, source: str) -> CompanyFacts:
    data = json.loads(raw)
    cik = int(data["cik"])
    facts: list[XbrlFact] = []
    for prefix, concepts in sorted(data.get("facts", {}).items()):
        for concept, body in sorted(concepts.items()):
            for unit, rows in sorted(body.get("units", {}).items()):
                for row in rows:
                    start = _d(row.get("start"))
                    val = row.get("val")
                    facts.append(
                        XbrlFact(
                            source=FactSource.COMPANYFACTS,
                            cik=cik,
                            accession=row.get("accn"),
                            concept=concept,
                            prefix=prefix,
                            unit=unit,
                            value=Decimal(str(val)) if isinstance(val, int | float) else None,
                            text_value=None if isinstance(val, int | float) else str(val),
                            period_start=start,
                            period_end=_d(row.get("end")),
                            is_instant=start is None,
                            fiscal_year=row.get("fy"),
                            fiscal_period=row.get("fp"),
                            form=row.get("form"),
                            filed=_d(row.get("filed")),
                            frame=row.get("frame"),
                            label=body.get("label"),
                        )
                    )
    version = "sha256:" + hashlib.sha256(raw).hexdigest()
    return CompanyFacts(cik, data.get("entityName", ""), tuple(facts), source, version)


class CompanyFactsAdapter:
    def __init__(self, client: BytesFetcher) -> None:
        self.client = client

    def fetch(self, cik: int) -> CompanyFacts:
        url = f"{COMPANYFACTS_BASE}/CIK{cik10(cik)}.json"
        return parse_companyfacts(self.client.get_bytes(url), source=url)


class BulkCompanyFactsArchive:
    """Reads a locally downloaded ``companyfacts.zip`` (download via the governed client)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def iter_companies(self, ciks: set[int] | None = None) -> Iterator[CompanyFacts]:
        with zipfile.ZipFile(self.path) as zf:
            for name in sorted(zf.namelist()):
                if not name.endswith(".json"):
                    continue
                cik = int(name.removeprefix("CIK").removesuffix(".json"))
                if ciks is not None and cik not in ciks:
                    continue
                yield parse_companyfacts(zf.read(name), source=f"{BULK_COMPANYFACTS_URL}#{name}")
