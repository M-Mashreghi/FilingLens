"""Task 4.4: taxonomy / concept mapping and version drift."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from filinglens.domain.xbrl import FactSource, XbrlFact
from filinglens.xbrl.ixbrl import extract_ixbrl
from filinglens.xbrl.taxonomy import (
    MAPPING_VERSION,
    STANDARD_RULES,
    ConceptMapper,
    CustomMappingRule,
    detect_drift,
    taxonomy_version,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl" / "exdv-20240928.htm"


def fact(
    qname: str, ns_year: str, *, dims: tuple[tuple[str, str], ...] = (), cik: int = 1234567
) -> XbrlFact:
    prefix, concept = qname.split(":")
    ns = {
        "us-gaap": f"http://fasb.org/us-gaap/{ns_year}",
        "dei": f"http://xbrl.sec.gov/dei/{ns_year}",
    }.get(prefix, f"http://www.example.com/{ns_year}0930")
    return XbrlFact(
        source=FactSource.IXBRL,
        cik=cik,
        accession="0001234567-24-000031",
        concept=concept,
        prefix=prefix,
        namespace=ns,
        unit="USD",
        value=Decimal(1),
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
        dimensions=dims,
    )


def test_taxonomy_version_parsing() -> None:
    assert taxonomy_version("http://fasb.org/us-gaap/2024") == ("us-gaap", "2024")
    assert taxonomy_version("http://xbrl.sec.gov/dei/2019-01-31") == ("dei", "2019")
    assert taxonomy_version("http://www.exampledevices.com/20240928") is None
    assert taxonomy_version(None) is None


def test_mappings_are_versioned_and_auditable() -> None:
    mapper = ConceptMapper()
    r = mapper.map_fact(fact("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "2024"))
    assert (r.metric, r.rule_id, r.mapping_version) == ("revenue", "rev-asc606", MAPPING_VERSION)
    assert r.taxonomy_version == ("us-gaap", "2024")
    assert len({rule.rule_id for rule in STANDARD_RULES}) == len(STANDARD_RULES)


def test_version_drift_fixture_two_taxonomy_versions() -> None:
    mapper = ConceptMapper()
    old = [
        fact("us-gaap:SalesRevenueNet", "2017"),
        fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", "2017"),
    ]
    new = [
        fact("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "2024"),
        fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", "2024"),
    ]
    assert mapper.map_fact(old[0]).metric == "revenue"  # deprecated concept valid in its era
    late = mapper.map_fact(fact("us-gaap:SalesRevenueNet", "2024"))
    assert late.metric is None and "not valid for taxonomy 2024" in late.reason
    drift = detect_drift(old, new, mapper)
    assert drift.has_drift
    assert drift.version_changes == {"us-gaap": ("2017", "2024")}
    assert drift.metric_concept_changes == {
        "revenue": (
            ("us-gaap:SalesRevenueNet",),
            ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",),
        )
    }
    assert not detect_drift(new, new, mapper).has_drift


def test_custom_concept_never_silently_mapped() -> None:
    ext = extract_ixbrl(FIX.read_bytes(), cik=1234567, accession="0001234567-24-000031")
    mapper = ConceptMapper()
    customs = [f for f in ext.facts if f.is_custom]
    assert customs
    for f in customs:
        r = mapper.map_fact(f)
        assert r.metric is None and "custom extension" in r.reason


def test_explicit_custom_rule_requires_evidence_and_issuer_scope() -> None:
    with pytest.raises(ValueError):
        CustomMappingRule("c1", 1234567, "exdv:NetSalesTotal", "revenue", evidence=" ")
    rule = CustomMappingRule(
        "c1",
        1234567,
        "exdv:NetSalesTotal",
        "revenue",
        evidence="label 'Total net sales' ties to income statement; reviewed 2026-10-14",
    )
    mapper = ConceptMapper(custom_rules=(rule,))
    mine = mapper.map_fact(fact("exdv:NetSalesTotal", "2024"))
    assert mine.metric == "revenue" and mine.rule_id == "c1" and "reviewed" in mine.reason
    other_issuer = mapper.map_fact(fact("exdv:NetSalesTotal", "2024", cik=999))
    assert other_issuer.metric is None


def test_dimensional_and_unknown_not_mapped() -> None:
    mapper = ConceptMapper()
    dim = fact(
        "us-gaap:Revenues", "2024", dims=(("srt:ProductOrServiceAxis", "us-gaap:ServiceMember"),)
    )
    assert mapper.map_fact(dim).metric is None
    assert (
        mapper.map_fact(fact("us-gaap:SomethingElse", "2024")).reason
        == "no mapping rule for concept"
    )
