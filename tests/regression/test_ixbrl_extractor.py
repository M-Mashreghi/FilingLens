"""Task 4.2: direct filing iXBRL fact extractor."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from filinglens.domain.xbrl import FactSource
from filinglens.xbrl.ixbrl import extract_ixbrl, parse_ix_number

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl" / "exdv-20240928.htm"
ACC = "0001234567-24-000031"
REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.fixture(scope="module")
def ext():  # type: ignore[no-untyped-def]
    return extract_ixbrl(FIX.read_bytes(), cik=1234567, accession=ACC)


def test_all_facts_extracted_without_errors(ext) -> None:  # type: ignore[no-untyped-def]
    assert ext.errors == ()
    assert len(ext.facts) == 13
    assert all(f.source is FactSource.IXBRL and f.accession == ACC for f in ext.facts)


def test_standard_fact_period_unit_scale(ext) -> None:  # type: ignore[no-untyped-def]
    fy24 = next(f for f in ext.facts if f.concept == REV and f.context_id == "FY2024")
    assert fy24.value == Decimal("391035000000") and fy24.unit == "USD"
    assert (fy24.period_start, fy24.period_end, fy24.is_instant) == (
        date(2023, 10, 1),
        date(2024, 9, 28),
        False,
    )
    assert fy24.decimals == "-6" and fy24.scale == 6 and not fy24.is_custom
    assert fy24.namespace == "http://fasb.org/us-gaap/2024"
    assert fy24.hidden is False and not fy24.is_dimensional


def test_custom_tag_fixture(ext) -> None:  # type: ignore[no-untyped-def]
    cust = next(f for f in ext.facts if f.concept == "ActiveSubscriptionCustomers")
    assert cust.prefix == "exdv" and cust.is_custom
    assert cust.namespace == "http://www.exampledevices.com/20240928"
    assert cust.unit == "Customer" and cust.value == Decimal("1050000000") and cust.is_instant
    text = next(f for f in ext.facts if f.concept == "SupplyChainPolicyTextBlock")
    assert (
        text.is_custom
        and text.value is None
        and text.text_value == "Our supply chain policy requires dual sourcing."
    )


def test_dimensional_fact_fixture(ext) -> None:  # type: ignore[no-untyped-def]
    revs = [f for f in ext.facts if f.concept == REV]
    entity_wide = [f for f in revs if not f.dimensions]
    assert len(entity_wide) == 2
    services = next(f for f in revs if f.context_id == "FY2024_Services")
    assert services.dimensions == (("srt:ProductOrServiceAxis", "us-gaap:ServiceMember"),)
    europe = next(f for f in revs if f.context_id == "FY2024_Europe_Services")
    assert europe.dimensions == (
        ("srt:ProductOrServiceAxis", "us-gaap:ServiceMember"),
        ("us-gaap:StatementBusinessSegmentsAxis", "exdv:EuropeSegmentMember"),
    )
    # dimensional facts are distinct facts, never merged into the entity-wide total
    assert len({f.fact_id for f in revs}) == 4


def test_hidden_fact_status_retained(ext) -> None:  # type: ignore[no-untyped-def]
    hidden = {f.concept for f in ext.facts if f.hidden}
    assert hidden == {
        "DocumentType",
        "AmendmentFlag",
        "IncomeTaxExaminationPenaltiesAndInterestAccrued",
    }
    pen = next(
        f for f in ext.facts if f.concept == "IncomeTaxExaminationPenaltiesAndInterestAccrued"
    )
    assert pen.value == Decimal("12000000")


def test_sign_nil_and_fixed_zero(ext) -> None:  # type: ignore[no-untyped-def]
    other = next(f for f in ext.facts if f.concept == "NonoperatingIncomeExpense")
    assert other.value == Decimal("-269000000") and other.sign_negated
    assert next(f for f in ext.facts if f.concept == "RestructuringCharges").value == Decimal(0)
    nil = next(f for f in ext.facts if f.concept == "GoodwillImpairmentLoss")
    assert nil.value is None  # nil is missing, not zero
    eps = next(f for f in ext.facts if f.concept == "EarningsPerShareDiluted")
    assert eps.unit == "USD/shares" and eps.value == Decimal("6.08")


def test_facts_trace_to_accession_and_locator(ext) -> None:  # type: ignore[no-untyped-def]
    tree = etree.fromstring(FIX.read_bytes()).getroottree()
    for f in ext.facts:
        el = tree.find(f.locator)
        assert el is not None
        assert el.get("name") == f.qname and el.get("contextRef") == f.context_id


def test_number_formats() -> None:
    assert parse_ix_number("1,234.5", "ixt:num-dot-decimal") == Decimal("1234.5")
    assert parse_ix_number("1.234,5", "ixt:num-comma-decimal") == Decimal("1234.5")
    assert parse_ix_number("—", None) == Decimal(0)
    with pytest.raises(ValueError):
        parse_ix_number("n/a", None)


def test_bad_context_and_number_reported_not_silent() -> None:
    raw = (
        FIX.read_bytes()
        .replace(b'contextRef="FY2023"', b'contextRef="NOPE"')
        .replace(b">6.08<", b">six<")
    )
    ext = extract_ixbrl(raw, cik=1234567, accession=ACC)
    assert len(ext.errors) == 2 and len(ext.facts) == 11
