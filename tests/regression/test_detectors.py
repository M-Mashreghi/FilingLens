"""Task 3.5: risk, MD&A, liquidity and exhibit change detectors (labeled fixtures)."""

from __future__ import annotations

from filinglens.diff.alignment import align_documents
from filinglens.diff.detectors import (
    Direction,
    detect_exhibit_topics,
    detect_mdna_topics,
    detect_risk_factors,
    language_direction,
)
from filinglens.diff.text_diff import diff_documents
from filinglens.domain.models import FilingDocument
from filinglens.domain.package import PackageManifest
from filinglens.parsing.eight_k import parse_eight_k
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections

FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4


def tenk(risk: list[str], mdna: list[str]) -> bytes:
    r = "".join(f"<p>{p}</p>" for p in risk)
    m = "".join(f"<p>{p}</p>" for p in mdna)
    return (
        "<html><body>"
        f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
        f"<div><b>Item 1A. Risk Factors</b></div>{r}"
        f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
        f"<div><b>Item 7. Management's Discussion and Analysis</b></div>{m}"
        f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
        f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p>"
        "</body></html>"
    ).encode()


def _diff(old: bytes, new: bytes):  # type: ignore[no-untyped-def]
    od, nd = normalize_html(old), normalize_html(new)
    al = align_documents(od, parse_sections(od, "10-K"), nd, parse_sections(nd, "10-K"))
    return diff_documents(od, nd, al)


SUPPLY_OLD = (
    "We rely on a limited number of suppliers, and supply disruptions could affect our operations."
)
SUPPLY_NEW = "We rely on a limited number of suppliers, and supply disruptions will materially and adversely affect our operations."
CYBER_OLD = "A cybersecurity incident could disrupt our systems."
TARIFF_NEW = (
    "New tariffs on imported components may increase our input costs and pressure gross margin."
)
LIQ_OLD = "Our liquidity is supported by cash and cash equivalents of $10 billion."
LIQ_NEW = "Our liquidity is supported by cash and cash equivalents of $6 billion and borrowings under our credit facility."


def test_labeled_risk_factor_fixture() -> None:
    d = _diff(tenk([SUPPLY_OLD, CYBER_OLD], [LIQ_OLD]), tenk([SUPPLY_NEW, TARIFF_NEW], [LIQ_OLD]))
    sigs = detect_risk_factors(d, "1A")
    by_dir = {s.direction: s for s in sigs}
    assert set(by_dir) == {Direction.STRENGTHENED, Direction.NEW, Direction.REMOVED}
    assert "supplier_concentration" in by_dir[Direction.STRENGTHENED].topics
    assert set(by_dir[Direction.NEW].topics) >= {"costs", "margins"}
    assert "cybersecurity" in by_dir[Direction.REMOVED].evidence[0].text
    for s in sigs:
        assert s.evidence and s.source_change_id


def test_labeled_mdna_liquidity_fixture() -> None:
    d = _diff(tenk([SUPPLY_OLD], [LIQ_OLD]), tenk([SUPPLY_OLD], [LIQ_NEW]))
    sigs = detect_mdna_topics(d, ["7"])
    assert len(sigs) == 1
    assert set(sigs[0].topics) >= {"liquidity", "debt"}
    assert sigs[0].evidence[0].text == LIQ_OLD and sigs[0].evidence[1].text == LIQ_NEW


def test_formatting_only_changes_suppressed() -> None:
    fmt = SUPPLY_OLD.replace(",", "").upper()
    d = _diff(tenk([SUPPLY_OLD], [LIQ_OLD]), tenk([fmt], [LIQ_OLD]))
    assert len(d.for_section("1A")) == 1  # the diff engine still records it
    assert detect_risk_factors(d, "1A") == []  # detectors suppress it
    roll_old = "In fiscal 2023 our liquidity remained strong."
    roll_new = "In fiscal 2024 our liquidity remained strong."
    d2 = _diff(tenk([SUPPLY_OLD], [roll_old]), tenk([SUPPLY_OLD], [roll_new]))
    assert detect_mdna_topics(d2, ["7"]) == []


def test_unknown_path_explicit() -> None:
    assert (
        language_direction("We could face issues.", "We could face issues soon.")[0]
        is Direction.UNKNOWN
    )
    direction, reason = language_direction("risk", "materially might")
    assert direction is Direction.UNKNOWN and "mixed" in reason
    assert language_direction("will materially", "may")[0] is Direction.SOFTENED


EIGHT_K = b"""<html><body>
<p>Item 1.01 Entry into a Material Definitive Agreement.</p>
<p>The Company entered into a Credit Agreement providing a $2 billion revolving facility. See Exhibit 10.1.</p>
<p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>The Company issued a press release announcing results, furnished as Exhibit 99.1.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<table><tr><td>10.1</td><td>Credit Agreement</td></tr><tr><td>99.1</td><td>Press release</td></tr></table>
</body></html>"""
EX991 = b"<html><body><p>Revenue rose 8%. The Company expects fiscal 2025 revenue growth of 5% to 7% (outlook).</p></body></html>"


def test_exhibit_topics_with_evidence() -> None:
    acc = "0001234567-24-000040"
    manifest = PackageManifest(
        accession=acc,
        cik=1234567,
        form="8-K",
        source_versions=(),
        documents=(
            FilingDocument(accession=acc, filename="ex101.htm", doc_type="EX-10.1", role="exhibit"),
            FilingDocument(accession=acc, filename="ex991.htm", doc_type="EX-99.1", role="exhibit"),
        ),
    )
    parsed = parse_eight_k(
        normalize_html(EIGHT_K),
        manifest,
        primary_filename="p.htm",
        exhibit_docs={"ex991.htm": normalize_html(EX991)},
    )
    sigs = {s.section_key: s for s in detect_exhibit_topics(parsed)}
    assert set(sigs) == {"8-K:1.01", "8-K:2.02"}
    assert (
        "financing" in sigs["8-K:1.01"].topics and "material_agreement" in sigs["8-K:1.01"].topics
    )
    earn = sigs["8-K:2.02"]
    assert earn.topics[0] == "earnings"
    ex_blocks = {e.block_id for e in earn.evidence}
    ex_doc = normalize_html(EX991)
    assert {b.block_id for b in ex_doc.blocks} <= ex_blocks  # exhibit evidence retained


def test_detectors_deterministic() -> None:
    a = _diff(tenk([SUPPLY_OLD, CYBER_OLD], [LIQ_OLD]), tenk([SUPPLY_NEW, TARIFF_NEW], [LIQ_NEW]))
    b = _diff(tenk([SUPPLY_OLD, CYBER_OLD], [LIQ_OLD]), tenk([SUPPLY_NEW, TARIFF_NEW], [LIQ_NEW]))
    assert detect_risk_factors(a, "1A") == detect_risk_factors(b, "1A")
    assert detect_mdna_topics(a, ["7"]) == detect_mdna_topics(b, ["7"])
