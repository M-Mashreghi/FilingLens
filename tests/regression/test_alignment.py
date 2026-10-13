"""Task 3.3: section alignment engine."""

from __future__ import annotations

from filinglens.diff.alignment import PairStatus, align_blocks, align_documents, boilerplate_key
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


R1 = "We depend on a limited number of suppliers for key components and any disruption could harm us."
R2 = (
    "Competition in our markets is intense and could reduce our margins and market share over time."
)
R3 = "Changes in foreign exchange rates could adversely affect our reported results of operations."
R_NEW = (
    "Recently enacted export controls may restrict our ability to sell products in certain regions."
)
M1 = "Net sales increased during fiscal 2024 compared to fiscal 2023 driven by higher services revenue."
M2 = "Liquidity remains strong and we believe cash on hand will satisfy our needs for the next 12 months."


def _align(old: bytes, new: bytes):  # type: ignore[no-untyped-def]
    od, nd = normalize_html(old), normalize_html(new)
    return align_documents(od, parse_sections(od, "10-K"), nd, parse_sections(nd, "10-K")), od, nd


def test_reorder_fixture() -> None:
    al, od, nd = _align(tenk([R1, R2, R3], [M1]), tenk([R3, R1, R2], [M1]))
    rf = al.section("1A")
    assert rf is not None
    assert [p.status for p in rf.pairs] == [PairStatus.IDENTICAL] * 3
    assert sum(p.moved for p in rf.pairs) == 1  # only R3 moved
    assert not rf.by_status(PairStatus.ADDED) and not rf.by_status(PairStatus.REMOVED)


def test_boilerplate_year_rollforward_fixture() -> None:
    m1_next = M1.replace("2024", "2025").replace("2023", "2024")
    al, *_ = _align(tenk([R1], [M1, M2]), tenk([R1], [m1_next, M2]))
    mdna = al.section("7")
    assert mdna is not None
    statuses = [(p.status, p.similarity) for p in mdna.pairs]
    assert statuses == [(PairStatus.MODIFIED, 1.0), (PairStatus.IDENTICAL, 1.0)]
    assert "normalization" in mdna.pairs[0].reason
    assert boilerplate_key("In fiscal 2024 sales were $1,234.") == boilerplate_key(
        "In fiscal 2025 sales were $9,876."
    )


def test_modified_added_removed() -> None:
    r2_edit = R2.replace("intense", "intense and increasing")
    al, *_ = _align(tenk([R1, R2, R3], [M1]), tenk([R1, r2_edit, R_NEW], [M1]))
    rf = al.section("1A")
    assert rf is not None
    kinds = [p.status for p in rf.pairs]
    assert kinds.count(PairStatus.IDENTICAL) == 1
    assert kinds.count(PairStatus.MODIFIED) == 1
    assert kinds.count(PairStatus.ADDED) == 1 and kinds.count(PairStatus.REMOVED) == 1
    mod = rf.by_status(PairStatus.MODIFIED)[0]
    assert 0.55 <= mod.similarity < 1.0


def test_cross_section_false_match_rejected() -> None:
    # R3 moves from Risk Factors to MD&A: must NOT be aligned across items.
    al, *_ = _align(tenk([R1, R3], [M1]), tenk([R1], [M1, R3]))
    rf, mdna = al.section("1A"), al.section("7")
    assert rf is not None and mdna is not None
    assert [p.status for p in rf.by_status(PairStatus.REMOVED)] == [PairStatus.REMOVED]
    assert [p.status for p in mdna.by_status(PairStatus.ADDED)] == [PairStatus.ADDED]
    for sec in al.sections:
        for p in sec.pairs:
            assert p.section_key == sec.section_key


def test_ambiguous_alignment_exposed() -> None:
    doc_old = normalize_html(
        b"<html><body><p>Our revenue depends on customer demand in the Americas region.</p></body></html>"
    )
    doc_new = normalize_html(
        b"<html><body><p>Our revenue depends on customer demand in the Europe region.</p>"
        b"<p>Our revenue depends on customer demand in the Greater region.</p></body></html>"
    )
    pairs = align_blocks("x", doc_old.blocks, doc_new.blocks)
    matched = [p for p in pairs if p.status is PairStatus.MODIFIED]
    assert len(matched) == 1 and matched[0].ambiguous and matched[0].confidence <= 0.5


def test_unrelated_text_not_matched() -> None:
    old = normalize_html(f"<html><body><p>{R1}</p></body></html>".encode())
    new = normalize_html(f"<html><body><p>{M2}</p></body></html>".encode())
    statuses = {p.status for p in align_blocks("x", old.blocks, new.blocks)}
    assert statuses == {PairStatus.ADDED, PairStatus.REMOVED}


def test_missing_section_noted_and_deterministic() -> None:
    old = tenk([R1], [M1]).replace(b"Item 1A. Risk Factors", b"Risk overview")
    al, *_ = _align(old, tenk([R1], [M1]))
    rf = al.section("1A")
    assert rf is not None and "section absent in old filing" in rf.notes
    assert all(p.status is PairStatus.ADDED for p in rf.pairs)
    assert _align(old, tenk([R1], [M1]))[0] == al
