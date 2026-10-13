"""Task 2.6: 10-K / 10-Q item and section parser (boundary regression fixtures)."""

from __future__ import annotations

import pytest

from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import SectionStatus, parse_sections


def body(tag: str, n: int = 3) -> str:
    sentence = f"The {tag} discussion explains operating conditions in detail for the period. "
    return "".join(f"<p>{sentence * 3}({tag} paragraph {i})</p>" for i in range(n))


def tenk_modern() -> bytes:
    toc = (
        "<table>"
        + "".join(
            f"<tr><td>Item {k}.</td><td>{t}</td><td>{p}</td></tr>"
            for k, t, p in [("1", "Business", 3), ("1A", "Risk Factors", 9), ("7", "MD&amp;A", 30)]
        )
        + "</table>"
    )
    parts = [
        "<html><body>",
        toc,
        "<div><b>PART I</b></div>",
        "<div><b>Item 1. Business</b></div>",
        body("business"),
        "<div><b>Item 1A. Risk Factors</b></div>",
        body("risk"),
        '<p>See also Item 7, "Management’s Discussion and Analysis," for liquidity matters and other items of interest that are discussed elsewhere.</p>',
        "<div><b>Item 1B. Unresolved Staff Comments</b></div><p>None.</p>",
        "<div><b>Item 2. Properties</b></div>",
        body("properties", 1),
        "<div><b>Item 3. Legal Proceedings</b></div>",
        body("legal", 1),
        "<div><b>PART II</b></div>",
        "<div><b>Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations</b></div>",
        body("mdna", 4),
        "<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div>",
        body("market", 1),
        "<div><b>Item 8. Financial Statements and Supplementary Data</b></div>",
        body("financials", 2),
        "<div><b>Item 9A. Controls and Procedures</b></div>",
        body("controls", 1),
        "<div><b>SIGNATURES</b></div><p>Pursuant to the requirements...</p>",
        "</body></html>",
    ]
    return "".join(parts).encode()


def tenk_legacy_text_toc() -> bytes:
    # Legacy: TOC as plain paragraphs, uppercase headings, no styling.
    toc = "".join(
        f"<p>ITEM {k}. {t.upper()}</p>"
        for k, t in [
            ("1", "Business"),
            ("1A", "Risk Factors"),
            ("3", "Legal Proceedings"),
            ("7", "Management's Discussion and Analysis"),
            ("7A", "Market Risk"),
            ("8", "Financial Statements"),
        ]
    )
    parts = ["<html><body><p>TABLE OF CONTENTS</p>", toc]
    for k, t, tag in [
        ("1", "BUSINESS", "business"),
        ("1A", "RISK FACTORS", "risk"),
        ("3", "LEGAL PROCEEDINGS", "legal"),
        (
            "7",
            "MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS",
            "mdna",
        ),
        ("7A", "QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK", "market"),
        ("8", "FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA", "financials"),
    ]:
        parts.append(f"<p>ITEM {k}. {t}</p>{body(tag)}")
    parts.append("</body></html>")
    return "".join(parts).encode()


def tenq(with_parts: bool = True) -> bytes:
    p1 = "<div><b>PART I — FINANCIAL INFORMATION</b></div>" if with_parts else ""
    p2 = "<div><b>PART II — OTHER INFORMATION</b></div>" if with_parts else ""
    return (
        "<html><body>"
        + p1
        + "<div><b>Item 1. Financial Statements</b></div>"
        + body("fs")
        + "<div><b>Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations</b></div>"
        + body("mdna")
        + "<div><b>Item 3. Quantitative and Qualitative Disclosures About Market Risk</b></div>"
        + body("market", 1)
        + "<div><b>Item 4. Controls and Procedures</b></div>"
        + body("controls", 1)
        + p2
        + "<div><b>Item 1. Legal Proceedings</b></div>"
        + body("legal", 1)
        + "<div><b>Item 1A. Risk Factors</b></div><p>There have been no material changes to the risk factors disclosed in the 2024 Form 10-K.</p>"
        + "<div><b>Item 6. Exhibits</b></div><p>See exhibit index.</p>"
        + "</body></html>"
    ).encode()


def _parse(raw: bytes, form: str):  # type: ignore[no-untyped-def]
    return parse_sections(normalize_html(raw), form)


def test_modern_10k_boundaries() -> None:
    res = _parse(tenk_modern(), "10-K")
    rf = res.analyst("risk_factors")
    assert rf is not None and rf.status is SectionStatus.OK
    assert "risk paragraph 0" in rf.text and "risk paragraph 2" in rf.text
    assert "business paragraph" not in rf.text
    assert "Unresolved" not in rf.text and "properties" not in rf.text  # no swallowing
    assert "See also Item 7" in rf.text  # cross-reference stays inside Risk Factors
    mdna = res.analyst("mdna")
    assert mdna is not None and mdna.status is SectionStatus.OK and mdna.confidence == 1.0
    assert "mdna paragraph 3" in mdna.text
    assert "market paragraph" not in mdna.text and "financials" not in mdna.text
    fs = res.get("8")
    assert fs is not None and "controls" not in fs.text
    controls = res.get("9A")
    assert controls is not None and "Pursuant" not in controls.text  # stops at SIGNATURES
    assert res.get("1B").text == "None."  # type: ignore[union-attr]


def test_toc_table_and_text_toc_are_not_sections() -> None:
    res = _parse(tenk_legacy_text_toc(), "10-K")
    for key in ("1", "1A", "3", "7", "7A", "8"):
        sec = res.get(key)
        assert sec is not None and sec.status is SectionStatus.OK, (key, sec)
        assert len(sec.text) > 400
    assert any("table-of-contents" in d for d in res.diagnostics)
    assert "risk paragraph 2" in res.get("1A").text  # type: ignore[union-attr]
    assert "legal" not in res.get("1A").text  # type: ignore[union-attr]


def test_missing_required_item_is_explicit() -> None:
    raw = tenk_modern().replace(b"Item 1A. Risk Factors", b"Risk Discussion")
    res = _parse(raw, "10-K")
    rf = res.get("1A")
    assert rf is not None and rf.status is SectionStatus.MISSING and rf.confidence == 0
    assert rf.text == ""
    biz = res.get("1")
    assert biz is not None and "risk paragraph" in biz.text  # absorbed, but 1A is not faked


def test_duplicate_substantial_headings_are_ambiguous() -> None:
    raw = tenk_modern().replace(
        b"<div><b>Item 7A.",
        b"<div><b>Item 7. MD&amp;A (continued)</b></div>"
        + body("dup", 3).encode()
        + b"<div><b>Item 7A.",
    )
    res = _parse(raw, "10-K")
    mdna = res.get("7")
    assert mdna is not None and mdna.status is SectionStatus.AMBIGUOUS
    assert mdna.confidence <= 0.4 and any("competing" in d for d in mdna.diagnostics)


def test_out_of_order_item_is_ambiguous() -> None:
    raw = (
        tenk_modern()
        .replace(b"<div><b>Item 3. Legal Proceedings</b></div>", b"")
        .replace(
            b"<div><b>SIGNATURES",
            b"<div><b>Item 3. Legal Proceedings</b></div>"
            + body("legal").encode()
            + b"<div><b>SIGNATURES",
        )
    )
    res = _parse(raw, "10-K")
    legal = res.get("3")
    assert legal is not None and legal.status is SectionStatus.AMBIGUOUS


def test_empty_section_is_ambiguous_not_success() -> None:
    raw = tenk_modern().replace(body("market", 1).encode(), b"")
    res = _parse(raw, "10-K")
    mr = res.get("7A")
    assert mr is not None and mr.status is SectionStatus.AMBIGUOUS


@pytest.mark.parametrize("with_parts", [True, False])
def test_10q_parts_and_items(with_parts: bool) -> None:
    res = _parse(tenq(with_parts), "10-Q")
    mdna = res.analyst("mdna")
    assert mdna is not None and mdna.status is SectionStatus.OK
    assert "mdna paragraph 2" in mdna.text and "market" not in mdna.text
    legal = res.get("II-1")
    assert legal is not None and "legal paragraph 0" in legal.text
    fs = res.get("I-1")
    assert fs is not None and "legal" not in fs.text
    rf = res.analyst("risk_factors")
    assert rf is not None and rf.status is SectionStatus.OK
    assert rf.text.startswith("There have been no material changes")
    if not with_parts:
        assert legal.confidence < 1.0 and any("inferred" in d for d in legal.diagnostics)
    else:
        assert legal.confidence == 1.0
    controls = res.get("I-4")
    assert controls is not None and "OTHER INFORMATION" not in controls.text


def test_amendment_form_uses_base_catalog() -> None:
    res = _parse(tenk_modern(), "10-K/A")
    assert res.form == "10-K" and res.get("1A") is not None


def test_unsupported_form_rejected() -> None:
    with pytest.raises(ValueError):
        _parse(tenk_modern(), "8-K")


def test_deterministic() -> None:
    assert _parse(tenk_modern(), "10-K") == _parse(tenk_modern(), "10-K")


def test_corpus_required_section_success_rate_meets_target() -> None:
    """EVALUATION.md target: >95% required-section extraction success on the fixture corpus."""
    from filinglens.parsing.sections import REQUIRED

    corpus = [
        (tenk_modern(), "10-K"),
        (tenk_legacy_text_toc(), "10-K"),
        (tenq(True), "10-Q"),
        (tenq(False), "10-Q"),
    ]
    total = ok = 0
    for raw, form in corpus:
        res = _parse(raw, form)
        for key in REQUIRED[form]:
            total += 1
            sec = res.get(key)
            ok += bool(sec and sec.status is SectionStatus.OK)
    assert ok / total > 0.95, (ok, total)
