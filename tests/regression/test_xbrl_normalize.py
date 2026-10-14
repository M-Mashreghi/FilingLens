"""Task 4.3: XBRL context, unit, dimension and period normalizer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from filinglens.domain.xbrl import FactSource, XbrlFact
from filinglens.xbrl.ixbrl import extract_ixbrl
from filinglens.xbrl.normalize import (
    FactSet,
    PeriodClass,
    ResolutionStatus,
    classify_period,
    normalize_unit,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl" / "exdv-20240928.htm"
ACC = "0001234567-24-000031"
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def fact(
    concept: str = "Revenues",
    value: str | None = "100",
    *,
    start: date | None = date(2023, 10, 1),
    end: date = date(2024, 9, 28),
    unit: str | None = "USD",
    decimals: str | None = "-6",
    dims: tuple[tuple[str, str], ...] = (),
    locator: str = "a",
) -> XbrlFact:
    return XbrlFact(
        source=FactSource.IXBRL,
        cik=1,
        accession=ACC,
        concept=concept,
        prefix="us-gaap",
        unit=unit,
        value=Decimal(value) if value is not None else None,
        period_start=start,
        period_end=end,
        is_instant=start is None,
        decimals=decimals,
        dimensions=dims,
        locator=locator,
    )


def test_period_classes() -> None:
    assert classify_period(fact(start=None)) is PeriodClass.INSTANT
    assert classify_period(fact(start=date(2024, 6, 30))) is PeriodClass.QUARTER
    assert classify_period(fact(start=date(2024, 3, 31))) is PeriodClass.HALF_YEAR
    assert classify_period(fact(start=date(2023, 12, 31))) is PeriodClass.NINE_MONTHS
    assert classify_period(fact()) is PeriodClass.ANNUAL
    assert classify_period(fact(start=date(2024, 8, 1))) is PeriodClass.OTHER


def test_unit_normalization_keeps_original() -> None:
    assert normalize_unit("iso4217:USD") == "USD" and normalize_unit("usd") == "USD"
    assert (
        normalize_unit("USD/shares") == "USD/shares"
        and normalize_unit("iso4217:EUR/xbrli:shares") == "EUR/shares"
    )
    assert normalize_unit("Customer") == "Customer" and normalize_unit(None) is None
    fs = FactSet([fact(unit="iso4217:USD")])
    (nf,) = fs.items
    assert nf.unit == "USD" and nf.original_unit == "iso4217:USD"


def test_fiscal_metadata() -> None:
    fs = FactSet(
        [fact(), fact(start=date(2023, 10, 1), end=date(2023, 12, 30))], fiscal_year_end="0928"
    )
    assert [nf.fiscal_quarter for nf in fs.items] == [4, 1]


def test_fixture_dimensions_never_collapse() -> None:
    ext = extract_ixbrl(FIX.read_bytes(), cik=1234567, accession=ACC)
    fs = FactSet(ext.facts, fiscal_year_end="0928")
    total = fs.lookup(REV, period_start=date(2023, 10, 1), period_end=date(2024, 9, 28))
    assert total.status is ResolutionStatus.FOUND and total.value == Decimal("391035000000")
    services = fs.lookup(
        REV,
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
        dimensions=(("srt:ProductOrServiceAxis", "us-gaap:ServiceMember"),),
    )
    assert services.value == Decimal("96169000000")
    assert services.representative_fact_id != total.representative_fact_id
    assert len(fs.dimensional(REV)) == 2
    eps = fs.lookup(
        "us-gaap:EarningsPerShareDiluted",
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
    )
    assert eps.unit == "USD/shares" and eps.value == Decimal("6.08")


def test_duplicate_context_policy_consistent() -> None:
    a = fact(value="391035000000", decimals="-6", locator="a")
    b = fact(value="391035000000", decimals="-6", locator="b")
    c = fact(value="391000000000", decimals="-9", locator="c")  # rounded presentation, same value
    fs = FactSet([a, b, c])
    r = fs.lookup("us-gaap:Revenues", period_start=date(2023, 10, 1), period_end=date(2024, 9, 28))
    assert r.status is ResolutionStatus.FOUND and r.duplicates == 3
    assert r.value == Decimal("391035000000")  # most precise representative
    assert r.representative_fact_id in (a.fact_id, b.fact_id)


def test_duplicate_context_policy_conflict() -> None:
    fs = FactSet([fact(value="100000000", locator="a"), fact(value="105000000", locator="b")])
    r = fs.lookup("us-gaap:Revenues", period_start=date(2023, 10, 1), period_end=date(2024, 9, 28))
    assert r.status is ResolutionStatus.CONFLICT and r.value is None and "disagree" in r.reason


def test_missing_is_not_zero() -> None:
    fs = FactSet([fact(concept="GoodwillImpairmentLoss", value=None)])
    nil = fs.lookup(
        "us-gaap:GoodwillImpairmentLoss",
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
    )
    assert nil.status is ResolutionStatus.MISSING and nil.value is None
    absent = fs.lookup("us-gaap:InventoryNet", period_end=date(2024, 9, 28))
    assert absent.status is ResolutionStatus.MISSING and absent.value is None


def test_multiple_units_are_ambiguous() -> None:
    fs = FactSet([fact(unit="USD"), fact(unit="EUR", locator="x")])
    r = fs.lookup("us-gaap:Revenues", period_start=date(2023, 10, 1), period_end=date(2024, 9, 28))
    assert r.status is ResolutionStatus.CONFLICT
    assert (
        fs.lookup(
            "us-gaap:Revenues",
            period_start=date(2023, 10, 1),
            period_end=date(2024, 9, 28),
            unit="eur",
        ).status
        is ResolutionStatus.FOUND
    )
