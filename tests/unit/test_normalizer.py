"""Task 2.5: filing HTML / inline-XBRL normalizer."""

from __future__ import annotations

import re

from lxml import html as lxml_html

from filinglens.parsing.normalizer import BlockKind, normalize_html

DOC = b"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head><title>exdv-20240930</title><style>.x{color:red}</style>
<script>var tracking = "do not keep";</script></head>
<body>
<div style="display:none"><ix:header><ix:hidden>
  <ix:nonNumeric name="dei:DocumentType" contextRef="c1">10-K</ix:nonNumeric>
  <ix:nonNumeric name="dei:AmendmentFlag" contextRef="c1">false</ix:nonNumeric>
</ix:hidden><ix:resources>context junk</ix:resources></ix:header></div>
<div><a href="#toc">Table of Contents</a></div>
<div style="text-align:center"><span style="font-weight:700">PART I</span></div>
<div><span style="font-weight:bold">Item 1A.&#160;&#160;Risk Factors</span></div>
<p>Our business   depends on&#160;<b>key</b> suppliers.<br/>Supply may be disrupted.</p>
<div>Revenue was $<ix:nonFraction name="us-gaap:Revenues" contextRef="c2" unitRef="usd" scale="6" decimals="-6">391,035</ix:nonFraction> million in fiscal 2024.</div>
<ul><li>Demand risk</li><li>Pricing risk</li></ul>
<div>Loose text before a block<p>Nested paragraph.</p>loose tail text</div>
<p style="display:none">Hidden paragraph that must not appear.</p>
<table>
 <tr><td></td><td>2024</td><td></td><td>2023</td></tr>
 <tr><td>Net sales</td><td>$</td><td>391,035</td><td>$</td><td>383,285</td></tr>
 <tr><td>Change</td><td>2</td><td>%</td><td>(3</td><td>)%</td></tr>
</table>
<p>12</p>
<h2>Item 7. Management&#8217;s Discussion</h2>
<!-- comment should vanish -->
</body></html>
"""


def _texts(kind: BlockKind | None = None) -> list[str]:
    doc = normalize_html(DOC)
    return [b.text for b in doc.blocks if kind is None or b.kind is kind]


def test_deterministic_and_stable_ids() -> None:
    a, b = normalize_html(DOC), normalize_html(DOC)
    assert a == b
    assert [x.block_id for x in a.blocks] == [x.block_id for x in b.blocks]
    assert len({x.block_id for x in a.blocks}) == len(a.blocks)
    assert all(re.fullmatch(r"blk-\d{5}-[0-9a-f]{8}", x.block_id) for x in a.blocks)


def test_noise_removed() -> None:
    text = normalize_html(DOC).text
    assert "tracking" not in text and "color:red" not in text and "comment" not in text
    assert "Table of Contents" not in text
    assert "12" not in _texts()  # page-number artifact
    assert "exdv-20240930" not in text  # <title>


def test_visible_text_preserved_and_whitespace_normalized() -> None:
    texts = _texts()
    assert "Our business depends on key suppliers. Supply may be disrupted." in texts
    assert "Revenue was $391,035 million in fiscal 2024." in texts  # iXBRL wrapper unwrapped
    assert "Loose text before a block" in texts
    assert "Nested paragraph." in texts
    assert "loose tail text" in texts
    # every visible word in the source body appears in the normalized output
    root = lxml_html.fromstring(DOC)
    body_words = set()
    for el in root.iter("p", "li", "td", "h2"):
        if "display:none" in (el.get("style") or ""):
            continue
        body_words.update(re.findall(r"[A-Za-z]{4,}", el.text_content()))
    out = normalize_html(DOC).text
    assert {w for w in body_words if w not in out} == set()


def test_structure_headings_lists_tables() -> None:
    doc = normalize_html(DOC)
    headings = [b.text for b in doc.blocks if b.kind is BlockKind.HEADING]
    assert headings == ["PART I", "Item 1A. Risk Factors", "Item 7. Management’s Discussion"]
    assert next(b for b in doc.blocks if b.text.startswith("Item 7")).heading_level == 2
    assert _texts(BlockKind.LIST_ITEM) == ["Demand risk", "Pricing risk"]
    table = next(b for b in doc.blocks if b.kind is BlockKind.TABLE)
    assert table.rows == (
        ("2024", "2023"),
        ("Net sales", "$391,035", "$383,285"),
        ("Change", "2%", "(3)%"),
    )


def test_hidden_ixbrl_is_separate_from_narrative() -> None:
    doc = normalize_html(DOC)
    assert "Hidden paragraph" not in doc.text
    assert "AmendmentFlag" not in doc.text and "context junk" not in doc.text
    names = {(f.name, f.text) for f in doc.hidden_facts}
    assert names == {("dei:DocumentType", "10-K"), ("dei:AmendmentFlag", "false")}
    assert all(f.context_ref == "c1" for f in doc.hidden_facts)
    # the visible tagged revenue is NOT reported as hidden
    assert all("Revenues" not in f.name for f in doc.hidden_facts)


def test_locators_survive_and_resolve() -> None:
    doc = normalize_html(DOC)
    root = lxml_html.fromstring(DOC)
    tree = root.getroottree()
    for b in doc.blocks:
        path = b.locator.split("#", 1)[0]
        found = tree.xpath(path)
        assert found, b.locator
        if "#" not in b.locator and b.kind is not BlockKind.TABLE:
            assert b.text.split()[0] in found[0].text_content().replace("\xa0", " ")
    assert all(b.source_line for b in doc.blocks)
    # original text references raw content before whitespace normalization
    para = next(b for b in doc.blocks if b.text.startswith("Our business"))
    assert "   " in para.original_text


def test_legacy_html_and_encoding() -> None:
    legacy = "<HTML><BODY><FONT SIZE=2><P>Caf\xe9 sales rose.</P></FONT></BODY></HTML>".encode(
        "cp1252"
    )
    doc = normalize_html(legacy)
    assert [b.text for b in doc.blocks] == ["Caf\xe9 sales rose."]


def test_empty_document_warns() -> None:
    doc = normalize_html(b"<html><body><div style='display:none'>x</div></body></html>")
    assert doc.blocks == () and doc.warnings
