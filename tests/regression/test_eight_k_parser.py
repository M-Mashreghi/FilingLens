"""Task 2.7: 8-K item and exhibit parser."""

from __future__ import annotations

from filinglens.domain.models import FilingDocument
from filinglens.domain.package import PackageManifest
from filinglens.parsing.eight_k import ExhibitLinkStatus, parse_eight_k
from filinglens.parsing.normalizer import normalize_html

ACC = "0001234567-24-000040"

EIGHT_K = b"""<html><body>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p>FORM 8-K</p>
<div><b>Item 1.01 Entry into a Material Definitive Agreement.</b></div>
<p>On May 1, 2024, the Company entered into a Credit Agreement, a copy of which is filed as Exhibit 10.1 hereto.</p>
<div><b>Item 2.02 Results of Operations and Financial Condition.</b></div>
<p>On May 2, 2024, the Company issued a press release announcing results for its fiscal second quarter. The press release is furnished as Exhibit 99.1.</p>
<div><b>Item 5.02 Departure of Directors or Certain Officers.</b></div>
<p>The Chief Financial Officer informed the Company of her intent to retire.</p>
<div><b>Item 9.01 Financial Statements and Exhibits.</b></div>
<p>(d) Exhibits.</p>
<table>
<tr><td>Exhibit No.</td><td>Description</td></tr>
<tr><td>10.1</td><td>Credit Agreement dated May 1, 2024</td></tr>
<tr><td>99.1</td><td>Press release dated May 2, 2024</td></tr>
<tr><td>99.2</td><td>Investor presentation</td></tr>
<tr><td>104</td><td>Cover Page Interactive Data File</td></tr>
</table>
<p>SIGNATURES</p>
<p>Pursuant to the requirements of the Securities Exchange Act of 1934 ...</p>
</body></html>"""

EX991 = b"""<html><body><p>Example Devices Reports Second Quarter Results</p>
<p>Revenue was $1.2 billion, up 8% year over year.</p></body></html>"""


def _manifest(include_992: bool = False) -> PackageManifest:
    docs = [
        FilingDocument(
            accession=ACC, filename="form8k.htm", sequence=1, doc_type="8-K", role="primary"
        ),
        FilingDocument(
            accession=ACC, filename="ex101.htm", sequence=2, doc_type="EX-10.1", role="exhibit"
        ),
        FilingDocument(
            accession=ACC, filename="ex991.htm", sequence=3, doc_type="EX-99.1", role="exhibit"
        ),
    ]
    if include_992:
        docs.append(
            FilingDocument(
                accession=ACC, filename="ex992.htm", sequence=4, doc_type="EX-99.02", role="exhibit"
            )
        )
    return PackageManifest(
        accession=ACC, cik=1234567, form="8-K", documents=tuple(docs), source_versions=()
    )


def _parse(**kw: object):  # type: ignore[no-untyped-def]
    return parse_eight_k(normalize_html(EIGHT_K), _manifest(), primary_filename="form8k.htm", **kw)  # type: ignore[arg-type]


def test_multi_item_8k_parses() -> None:
    res = _parse()
    assert res.items == ("1.01", "2.02", "5.02", "9.01")
    cats = {e.item: e.category for e in res.events}
    assert cats == {
        "1.01": "material_agreement",
        "2.02": "earnings",
        "5.02": "leadership_change",
        "9.01": "exhibits",
    }
    earn = res.event("2.02")
    assert earn is not None and "fiscal second quarter" in earn.text
    assert "Chief Financial Officer" not in earn.text  # no swallowing of later items
    nine = res.event("9.01")
    assert (
        nine is not None and "Pursuant to the requirements" not in nine.text
    )  # stops at SIGNATURES


def test_exhibit_references_resolve_to_manifest() -> None:
    res = _parse()
    links = {lk.exhibit_number: lk for lk in res.exhibits}
    assert (
        links["10.1"].status is ExhibitLinkStatus.RESOLVED and links["10.1"].filename == "ex101.htm"
    )
    assert (
        links["99.1"].filename == "ex991.htm"
        and links["99.1"].description == "Press release dated May 2, 2024"
    )
    assert set(links["99.1"].referenced_in) == {"2.02", "9.01"}
    assert "104" not in links  # cover page iXBRL is not an analyst exhibit
    agreement = res.event("1.01")
    assert agreement is not None and [lk.exhibit_number for lk in agreement.exhibits] == ["10.1"]


def test_missing_exhibit_is_explicit() -> None:
    res = _parse()
    missing = next(lk for lk in res.exhibits if lk.exhibit_number == "99.2")
    assert missing.status is ExhibitLinkStatus.MISSING and missing.filename is None
    assert any("99.2" in d for d in res.diagnostics)


def test_exhibit_number_formats_match() -> None:
    res = parse_eight_k(normalize_html(EIGHT_K), _manifest(include_992=True))
    l992 = next(lk for lk in res.exhibits if lk.exhibit_number == "99.2")
    assert l992.status is ExhibitLinkStatus.RESOLVED and l992.filename == "ex992.htm"


def test_earnings_exhibit_evidence_is_attached() -> None:
    ex = normalize_html(EX991)
    res = _parse(exhibit_docs={"ex991.htm": ex})
    earn = res.event("2.02")
    assert earn is not None
    assert {e.filename for e in earn.exhibit_evidence} == {"ex991.htm"}
    assert [e.block_id for e in earn.exhibit_evidence] == [b.block_id for b in ex.blocks]


def test_evidence_locators_preserved() -> None:
    doc = normalize_html(EIGHT_K)
    res = parse_eight_k(doc, _manifest(), primary_filename="form8k.htm")
    for event in res.events:
        assert event.evidence and all(e.filename == "form8k.htm" for e in event.evidence)
        for ev in event.evidence:
            block = doc.block(ev.block_id)
            assert block is not None and block.locator == ev.locator
        assert event.evidence[0].block_id == next(
            b.block_id for b in doc.blocks if b.text.startswith(f"Item {event.item}")
        )


def test_submissions_items_cross_check() -> None:
    assert not any(
        "differ" in d for d in _parse(reported_items=("1.01", "2.02", "5.02", "9.01")).diagnostics
    )
    res = _parse(reported_items=("2.02", "9.01"))
    assert any("differ from submissions items" in d for d in res.diagnostics)


def test_no_items_is_reported_not_silent() -> None:
    res = parse_eight_k(normalize_html(b"<html><body><p>Nothing here.</p></body></html>"))
    assert res.events == () and "no 8-K item headings found" in res.diagnostics


def test_non_reliance_item_category() -> None:
    raw = b"<html><body><p>Item 4.02 Non-Reliance on Previously Issued Financial Statements.</p><p>The Audit Committee concluded that the previously issued financial statements should no longer be relied upon.</p></body></html>"
    res = parse_eight_k(normalize_html(raw))
    ev = res.event("4.02")
    assert ev is not None and ev.category == "non_reliance"
    assert "fraud" not in ev.title.lower()
