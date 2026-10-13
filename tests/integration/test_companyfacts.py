"""Task 4.1: SEC Company Facts adapter."""

from __future__ import annotations

import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from filinglens.domain.xbrl import FactSource
from filinglens.infrastructure.sec.client import RateGovernor, SecHttpClient
from filinglens.infrastructure.sec.companyfacts import (
    BulkCompanyFactsArchive,
    CompanyFactsAdapter,
    LookupStatus,
    parse_companyfacts,
)

FIX = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "companyfacts" / "CIK0001234567.json"
)


def _client(seen: list[httpx.Request]) -> SecHttpClient:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=FIX.read_bytes())

    return SecHttpClient(
        user_agent="t t@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        governor=RateGovernor(1000),
    )


def test_fixture_parses_with_metadata() -> None:
    seen: list[httpx.Request] = []
    cf = CompanyFactsAdapter(_client(seen)).fetch(1234567)
    assert str(seen[0].url) == "https://data.sec.gov/api/xbrl/companyfacts/CIK0001234567.json"
    assert "authorization" not in {k.lower() for k in seen[0].headers}  # no API key on public path
    assert cf.entity_name == "Example Devices Inc." and len(cf.facts) == 8
    rev = cf.lookup("us-gaap:Revenues", "USD")
    assert rev.status is LookupStatus.FOUND and len(rev.facts) == 4
    fy24 = next(f for f in rev.facts if f.frame == "CY2024")
    assert fy24.value == Decimal("391035000000") and fy24.unit == "USD"
    assert (fy24.period_start, fy24.period_end) == (date(2023, 10, 1), date(2024, 9, 28))
    assert fy24.accession == "0001234567-24-000031" and fy24.form == "10-K"
    assert (
        fy24.fiscal_year == 2024 and fy24.fiscal_period == "FY" and fy24.filed == date(2024, 11, 1)
    )
    assert fy24.source is FactSource.COMPANYFACTS and not fy24.is_custom and not fy24.is_dimensional
    cash = cf.lookup("us-gaap:CashAndCashEquivalentsAtCarryingValue").facts[0]
    assert cash.is_instant and cash.period_start is None
    eps = cf.lookup("us-gaap:EarningsPerShareDiluted").facts[0]
    assert eps.unit == "USD/shares" and eps.value == Decimal("6.08")


def test_missing_concept_and_unit_explicit() -> None:
    cf = parse_companyfacts(FIX.read_bytes(), source="fixture")
    missing = cf.lookup("us-gaap:InventoryNet")
    assert missing.status is LookupStatus.MISSING_CONCEPT and missing.facts == ()
    wrong_unit = cf.lookup("us-gaap:Revenues", "EUR")
    assert wrong_unit.status is LookupStatus.MISSING_UNIT and wrong_unit.available_units == ("USD",)


def test_bulk_and_per_company_reconcile(tmp_path: Path) -> None:
    zpath = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(FIX, "CIK0001234567.json")
        zf.writestr("CIK0000000001.json", '{"cik":1,"entityName":"Other","facts":{}}')
    api = parse_companyfacts(FIX.read_bytes(), source="api")
    (bulk,) = list(BulkCompanyFactsArchive(zpath).iter_companies({1234567}))
    assert {f.fact_id for f in bulk.facts} == {f.fact_id for f in api.facts}
    assert bulk.facts == api.facts and bulk.source_version == api.source_version
    assert len(list(BulkCompanyFactsArchive(zpath).iter_companies())) == 2


def test_fact_ids_deterministic_and_distinct() -> None:
    a = parse_companyfacts(FIX.read_bytes(), source="x")
    b = parse_companyfacts(FIX.read_bytes(), source="y")
    assert [f.fact_id for f in a.facts] == [f.fact_id for f in b.facts]
    assert len({f.fact_id for f in a.facts}) == len(
        a.facts
    )  # same value re-reported in a later accession is a distinct fact
