"""Task 5.1: section-aware retrieval index."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections
from filinglens.retrieval.index import RetrievalError, RetrievalFilter, RetrievalIndex

FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4
A24 = "0001234567-24-000031"
A23 = "0001234567-23-000030"
T24 = datetime(2024, 10, 31, 22, 1, tzinfo=UTC)
T23 = datetime(2023, 11, 2, 22, 8, tzinfo=UTC)


def tenk(risk: str, mdna: str) -> bytes:
    return (
        "<html><body>"
        f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
        f"<div><b>Item 1A. Risk Factors</b></div><p>{risk}</p>"
        f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
        f"<div><b>Item 7. Management's Discussion and Analysis</b></div><p>{mdna}</p>"
        f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
        f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p>"
        "</body></html>"
    ).encode()


RISK = "Export controls could restrict sales of advanced semiconductors to customers in certain regions."
MDNA24 = "Net sales of services increased due to higher subscription revenue and advertising."
MDNA23 = "Net sales of services increased due to higher subscription revenue and advertising."  # identical text


@pytest.fixture
def index() -> RetrievalIndex:
    idx = RetrievalIndex()
    for acc, t, mdna in ((A24, T24, MDNA24), (A23, T23, MDNA23)):
        raw = tenk(RISK, mdna)
        doc = normalize_html(raw, snapshot_id=("a" if acc == A24 else "b") * 64)
        idx.add_document(
            doc,
            accession=acc,
            filename="main.htm",
            available_at=t,
            doc_type="10-K",
            sections=parse_sections(doc, "10-K"),
        )
    ex = normalize_html(
        b"<html><body><p>Press release: services revenue reached a record.</p></body></html>",
        snapshot_id="c" * 64,
    )
    idx.add_document(ex, accession=A24, filename="ex991.htm", available_at=T24, doc_type="EX-99.1")
    return idx


LATE = datetime(2026, 1, 1, tzinfo=UTC)


def test_retrieval_cannot_cross_accession_filter(index: RetrievalIndex) -> None:
    hits = index.search(
        "services subscription revenue", RetrievalFilter(frozenset({A23}), LATE), k=10
    )
    assert hits and all(h.chunk.accession == A23 for h in hits)
    hits24 = index.search(
        "services subscription revenue", RetrievalFilter(frozenset({A24}), LATE), k=10
    )
    assert hits24 and all(h.chunk.accession == A24 for h in hits24)


def test_unbounded_query_rejected() -> None:
    with pytest.raises(RetrievalError):
        RetrievalFilter(frozenset(), LATE)


def test_as_of_filter(index: RetrievalIndex) -> None:
    before = T24 - timedelta(seconds=1)
    assert index.search("export controls", RetrievalFilter(frozenset({A24}), before)) == []
    assert (
        index.search("export controls", RetrievalFilter(frozenset({A24, A23}), before))[
            0
        ].chunk.accession
        == A23
    )


def test_item_and_document_metadata_preserved(index: RetrievalIndex) -> None:
    (hit,) = index.search(
        "export controls semiconductors",
        RetrievalFilter(frozenset({A24}), LATE, sections=frozenset({"1A"})),
    )
    c = hit.chunk
    assert (c.section_key, c.filename, c.doc_type, c.snapshot_id) == (
        "1A",
        "main.htm",
        "10-K",
        "a" * 64,
    )
    assert c.block_ids and c.locators and len(c.block_ids) == len(c.locators)
    ex = index.search(
        "record", RetrievalFilter(frozenset({A24}), LATE, filenames=frozenset({"ex991.htm"}))
    )
    assert ex[0].chunk.filename == "ex991.htm" and ex[0].chunk.section_key is None


def test_section_and_doc_type_filters(index: RetrievalIndex) -> None:
    f = RetrievalFilter(frozenset({A24}), LATE, sections=frozenset({"7"}))
    assert all(h.chunk.section_key == "7" for h in index.search("services", f))
    only_ex = RetrievalFilter(frozenset({A24}), LATE, doc_types=frozenset({"EX-99.1"}))
    assert {h.chunk.filename for h in index.search("services revenue", only_ex)} == {"ex991.htm"}


def test_chunks_never_span_sections() -> None:
    doc = normalize_html(tenk(RISK, MDNA24))
    secs = parse_sections(doc, "10-K")
    section_of = {bid: s.key for s in secs.sections for bid in s.block_ids}
    idx = RetrievalIndex(max_chunk_chars=10_000)
    chunks = idx.add_document(
        doc, accession=A24, filename="main.htm", available_at=T24, sections=secs
    )
    for c in chunks:
        assert {section_of.get(b) for b in c.block_ids} == {c.section_key}
    assert {c.section_key for c in chunks} >= {"1", "1A", "3", "7", "7A", "8"}


def test_deterministic_fixture_queries(index: RetrievalIndex) -> None:
    f = RetrievalFilter(frozenset({A24}), LATE)
    top = index.search("export controls regions", f)[0]
    assert top.chunk.section_key == "1A" and top.rank == 1
    assert index.search("export controls regions", f) == index.search("export controls regions", f)
    assert index.search("zzzz unmatched", f) == []
