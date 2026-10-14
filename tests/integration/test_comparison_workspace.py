"""Task 6.3: filing comparison workspace."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.diff.alignment import align_documents
from filinglens.diff.text_diff import diff_documents
from filinglens.domain.models import Filing, FilingDocument
from filinglens.domain.package import PackageManifest
from filinglens.lineage.amendments import LineageEngine
from filinglens.parsing.eight_k import parse_eight_k
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections
from filinglens.ui.workspace import WorkspaceInputs, build_workspace

PAGE = Path(__file__).resolve().parents[2] / "src" / "filinglens" / "ui" / "pages" / "comparison.py"
FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4
K23 = Filing(
    accession="0001234567-23-000030",
    cik=1234567,
    form="10-K",
    filing_date=date(2023, 11, 3),
    report_period=date(2023, 9, 30),
    accepted_at=datetime(2023, 11, 2, 22, tzinfo=UTC),
)
K24 = Filing(
    accession="0001234567-24-000031",
    cik=1234567,
    form="10-K",
    filing_date=date(2024, 11, 1),
    report_period=date(2024, 9, 28),
    accepted_at=datetime(2024, 10, 31, 22, tzinfo=UTC),
)
K24A = Filing(
    accession="0001234567-25-000010",
    cik=1234567,
    form="10-K/A",
    filing_date=date(2025, 2, 14),
    report_period=date(2024, 9, 28),
    accepted_at=datetime(2025, 2, 14, 21, tzinfo=UTC),
)


def tenk(risk: str, mdna: str, table: str) -> bytes:
    return (
        "<html><body>"
        f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
        f"<div><b>Item 1A. Risk Factors</b></div>{risk}"
        f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
        f"<div><b>Item 7. Management's Discussion and Analysis</b></div>{mdna}{table}"
        f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
        f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p></body></html>"
    ).encode()


OLD = tenk(
    "<p>Competition is intense and could reduce our margins over time.</p>",
    "<p>Liquidity is supported by cash of $10 billion.</p>",
    "<table><tr><td>Services</td><td>85,200</td></tr><tr><td>Total</td><td>383,285</td></tr></table>",
)
NEW = tenk(
    "<p>Competition is intense and could reduce our margins over time.</p>"
    "<p>New export controls may restrict sales of advanced products.</p>",
    "<p>Liquidity is supported by cash of $6 billion and borrowings.</p>",
    "<table><tr><td>Services</td><td>96,169</td></tr><tr><td>Total</td><td>391,035</td></tr></table>",
)
EIGHT_K = b"""<html><body><p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>Press release furnished as Exhibit 99.1.</p><p>Item 9.01 Financial Statements and Exhibits.</p>
<table><tr><td>99.1</td><td>Press release</td></tr><tr><td>99.2</td><td>Slides</td></tr></table></body></html>"""


def workspace():  # type: ignore[no-untyped-def]
    od = normalize_html(OLD, snapshot_id=hashlib.sha256(OLD).hexdigest())
    nd = normalize_html(NEW, snapshot_id=hashlib.sha256(NEW).hexdigest())
    diff = diff_documents(
        od, nd, align_documents(od, parse_sections(od, "10-K"), nd, parse_sections(nd, "10-K"))
    )
    added = next(c for c in diff.for_section("1A") if c.kind.value == "added")
    acc = "0001234567-24-000040"
    manifest = PackageManifest(
        accession=acc,
        cik=1234567,
        form="8-K",
        source_versions=(),
        documents=(
            FilingDocument(accession=acc, filename="ex991.htm", doc_type="EX-99.1", role="exhibit"),
        ),
    )
    metric = MetricResult(
        "revenue_growth", MetricStatus.OK, Decimal("0.020220"), "pure", "(r_t - r_t-1)/|r_t-1|", ()
    )
    lineage = LineageEngine([K23, K24, K24A]).period(
        K24.accession, datetime(2026, 1, 1, tzinfo=UTC)
    )
    inp = WorkspaceInputs(
        current=K24,
        comparator=K23,
        diff=diff,
        eight_k=parse_eight_k(normalize_html(EIGHT_K), manifest),
        lineage=lineage,
        metrics={"revenue_growth": metric},
        findings=[("new_risk_factor", "Export-control risk added.", ["ev-1"])],
        evidence_to_change={"ev-1": ("1A", added.change_id)},
    )
    return build_workspace(inp), added


def test_metadata_and_section_tabs() -> None:
    ws, _ = workspace()
    assert ws.current.accession == K24.accession and ws.comparator is not None
    assert ws.comparator.accession == K23.accession
    assert {t.key for t in ws.tabs} == {"1A", "7"}
    rf = ws.tab("1A")
    assert rf is not None and rf.title == "Risk Factors" and rf.added == 1


def test_risk_and_mdna_diff_usable_with_exact_locators() -> None:
    ws, _ = workspace()
    rf = ws.tab("1A")
    added = next(c for c in rf.changes if c.kind == "added")  # type: ignore[union-attr]
    assert added.new_text and "export controls" in added.new_text and added.new_locator
    md = ws.tab("7")
    assert md is not None
    para = next(c for c in md.changes if c.block_kind == "paragraph")
    assert para.old_locator and para.new_locator and para.sentence_edits
    table = next(c for c in md.changes if c.block_kind == "table")
    assert ("Services", 1, "85,200", "96,169") in table.table_cells


def test_eight_k_exhibits_and_lineage_visible() -> None:
    ws, _ = workspace()
    assert ws.eight_k is not None and ws.eight_k.items[0][0] == "2.02"
    statuses = {n: s for n, s, _ in ws.eight_k.exhibits}
    assert statuses == {"99.1": "resolved", "99.2": "missing"}
    assert ws.lineage is not None
    assert ws.lineage.original == K24.accession and ws.lineage.amendments == (K24A.accession,)
    assert ws.lineage.effective == K24A.accession


def test_metric_lineage_and_finding_navigation() -> None:
    ws, added = workspace()
    assert ws.metrics[0].name == "revenue_growth" and ws.metrics[0].formula
    link = ws.findings[0]
    assert link.targets == (("ev-1", "1A", added.change_id),)


def test_page_renders_headless() -> None:
    ws, _ = workspace()
    at = AppTest.from_file(str(PAGE), default_timeout=30)
    at.session_state["workspace"] = ws
    at.run()
    assert not at.exception
    assert any("Amendment lineage" in h.value for h in at.subheader)
    assert any("8-K items" in h.value for h in at.subheader)
    assert len(at.tabs) == 2
    empty = AppTest.from_file(str(PAGE), default_timeout=30)
    empty.run()
    assert not empty.exception and empty.info
