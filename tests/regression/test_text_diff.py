"""Task 3.4: deterministic text and table diff engine."""

from __future__ import annotations

import hashlib

from lxml import html as lxml_html

from filinglens.diff.alignment import align_documents
from filinglens.diff.text_diff import ChangeKind, diff_documents, split_sentences
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections

FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4


def tenk(risk: str, mdna: str, table: str) -> bytes:
    return (
        "<html><body>"
        f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
        f"<div><b>Item 1A. Risk Factors</b></div>{risk}"
        f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
        f"<div><b>Item 7. Management's Discussion and Analysis</b></div>{mdna}{table}"
        f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
        f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p>"
        "</body></html>"
    ).encode()


OLD_RISK = (
    "<p>We depend on a limited number of suppliers for key components. Any disruption could harm our results.</p>"
    "<p>Competition in our markets is intense and could reduce our margins over time.</p>"
    "<p>Our operations in Region X are subject to political uncertainty and possible sanctions.</p>"
)
NEW_RISK = (
    "<p>We depend on a limited number of suppliers for key components. Any disruption could materially harm our results. Several suppliers are single-source.</p>"
    "<p>Competition in our markets is intense and could reduce our margins over time.</p>"
    "<p>New export controls may restrict sales of our advanced products to certain customers.</p>"
)
MDNA = "<p>Net sales increased primarily due to higher services revenue across all segments.</p>"
OLD_TABLE = (
    "<table><tr><td></td><td>2023</td><td>2022</td></tr>"
    "<tr><td>Products</td><td>$</td><td>298,085</td><td>$</td><td>316,199</td></tr>"
    "<tr><td>Services</td><td>$</td><td>85,200</td><td>$</td><td>78,129</td></tr>"
    "<tr><td>Total net sales</td><td>$</td><td>383,285</td><td>$</td><td>394,328</td></tr></table>"
)
NEW_TABLE = (
    "<table><tr><td></td><td>2023</td><td>2022</td></tr>"
    "<tr><td>Products</td><td>$</td><td>298,085</td><td>$</td><td>316,199</td></tr>"
    "<tr><td>Services</td><td>$</td><td>96,169</td><td>$</td><td>78,129</td></tr>"
    "<tr><td>Wearables</td><td>$</td><td>37,005</td><td>$</td><td>39,845</td></tr>"
    "<tr><td>Total net sales</td><td>$</td><td>391,035</td><td>$</td><td>394,328</td></tr></table>"
)


def _run(old_raw: bytes, new_raw: bytes):  # type: ignore[no-untyped-def]
    so, sn = hashlib.sha256(old_raw).hexdigest(), hashlib.sha256(new_raw).hexdigest()
    od, nd = normalize_html(old_raw, snapshot_id=so), normalize_html(new_raw, snapshot_id=sn)
    al = align_documents(od, parse_sections(od, "10-K"), nd, parse_sections(nd, "10-K"))
    return diff_documents(od, nd, al), od, nd


OLD = tenk(OLD_RISK, MDNA, OLD_TABLE)
NEW = tenk(NEW_RISK, MDNA, NEW_TABLE)


def test_sentence_splitter() -> None:
    assert split_sentences("Apple Inc. reported results. Sales rose 5%. Margins fell.") == [
        "Apple Inc. reported results.",
        "Sales rose 5%.",
        "Margins fell.",
    ]


def test_paragraph_and_sentence_changes() -> None:
    res, *_ = _run(OLD, NEW)
    rf = res.for_section("1A")
    kinds = sorted(c.kind.value for c in rf)
    assert kinds == ["added", "modified", "removed"]
    mod = next(c for c in rf if c.kind is ChangeKind.MODIFIED)
    ops = [(e.op, e.text) for e in mod.sentence_edits]
    assert ("removed", "Any disruption could harm our results.") in ops
    assert ("added", "Any disruption could materially harm our results.") in ops
    assert ("added", "Several suppliers are single-source.") in ops
    assert all("We depend on" not in t for _, t in ops)  # unchanged sentence not reported
    added = next(c for c in rf if c.kind is ChangeKind.ADDED)
    assert added.old is None and added.new is not None and "export controls" in added.new.text
    removed = next(c for c in rf if c.kind is ChangeKind.REMOVED)
    assert removed.new is None and removed.old is not None and "Region X" in removed.old.text


def test_table_cell_and_row_diff() -> None:
    res, *_ = _run(OLD, NEW)
    tchg = next(c for c in res.for_section("7") if c.block_kind == "table")
    assert tchg.kind is ChangeKind.MODIFIED and tchg.table is not None and tchg.table.supported
    assert tchg.table.added_rows == ("Wearables",) and tchg.table.removed_rows == ()
    cells = {(c.row_label, c.column, c.old, c.new) for c in tchg.table.cell_changes}
    assert cells == {
        ("Services", 1, "$85,200", "$96,169"),
        ("Total net sales", 1, "$383,285", "$391,035"),
    }


def test_unsupported_table_shape_is_explicit() -> None:
    dup_old = OLD_TABLE.replace("Services", "Products")
    dup_new = NEW_TABLE.replace("Services", "Products")
    res, *_ = _run(tenk(OLD_RISK, MDNA, dup_old), tenk(OLD_RISK, MDNA, dup_new))
    t = next(c for c in res.for_section("7") if c.block_kind == "table")
    assert t.table is not None and not t.table.supported and "duplicate" in t.table.reason


def test_section_metrics() -> None:
    res, *_ = _run(OLD, NEW)
    rf = res.stat("1A")
    assert rf is not None and (rf.added, rf.removed, rf.modified) == (1, 1, 1)
    assert rf.length_delta == rf.new_chars - rf.old_chars
    assert 0 < rf.change_magnitude < 1
    biz = res.stat("1")
    assert biz is not None and biz.change_magnitude == 0.0 and res.for_section("1") == []


def test_deterministic_repeatability_and_stable_ids() -> None:
    a, *_ = _run(OLD, NEW)
    b, *_ = _run(OLD, NEW)
    assert a == b
    ids = [c.change_id for c in a.changes]
    assert len(ids) == len(set(ids)) and all(i.startswith("chg-") for i in ids)


def test_no_changes_for_identical_filings() -> None:
    res, *_ = _run(OLD, OLD)
    assert res.changes == () and all(s.change_magnitude == 0.0 for s in res.sections)


def test_evidence_links_resolve_to_both_snapshots() -> None:
    res, od, nd = _run(OLD, NEW)
    assert res.old_snapshot_id == hashlib.sha256(OLD).hexdigest()
    assert res.new_snapshot_id == hashlib.sha256(NEW).hexdigest()
    trees = {
        res.old_snapshot_id: lxml_html.fromstring(OLD).getroottree(),
        res.new_snapshot_id: lxml_html.fromstring(NEW).getroottree(),
    }
    docs = {res.old_snapshot_id: od, res.new_snapshot_id: nd}
    for c in res.changes:
        for ref in (c.old, c.new):
            if ref is None:
                continue
            block = docs[ref.snapshot_id].block(ref.block_id)  # type: ignore[index]
            assert block is not None and block.text == ref.text
            assert trees[ref.snapshot_id].xpath(ref.locator.split("#")[0])  # type: ignore[index]
