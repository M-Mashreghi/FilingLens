"""Deterministic offline SEC world for end-to-end tests (synthetic issuer, minimized filings)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

CIK = 1234567
FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4


@dataclass
class FilingSpec:
    accession: str
    form: str
    filing_date: str
    report_date: str
    accepted: str
    primary: str
    body: str
    items: str = ""
    exhibits: dict[str, tuple[str, str]] = field(default_factory=dict)  # filename -> (type, html)


def ixbrl_10k(
    *,
    fy: tuple[str, str],
    py: tuple[str, str],
    values: dict[str, tuple[str, str]],
    risk: list[str],
    mdna: list[str],
) -> str:
    ctx = "".join(
        f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">'
        f"{CIK:010d}</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
        for cid, period in (
            (
                "CUR",
                f"<xbrli:startDate>{fy[0]}</xbrli:startDate><xbrli:endDate>{fy[1]}</xbrli:endDate>",
            ),
            (
                "PRI",
                f"<xbrli:startDate>{py[0]}</xbrli:startDate><xbrli:endDate>{py[1]}</xbrli:endDate>",
            ),
            ("CURI", f"<xbrli:instant>{fy[1]}</xbrli:instant>"),
            ("PRII", f"<xbrli:instant>{py[1]}</xbrli:instant>"),
        )
    )
    facts = []
    for concept, (cur, pri) in values.items():
        instant = concept in ("CashAndCashEquivalentsAtCarryingValue", "LongTermDebtNoncurrent")
        for cref, val in (
            (("CURI" if instant else "CUR"), cur),
            (("PRII" if instant else "PRI"), pri),
        ):
            facts.append(
                f'<p>{concept}: <ix:nonFraction name="us-gaap:{concept}" contextRef="{cref}" '
                f'unitRef="usd" decimals="-6" scale="6">{val}</ix:nonFraction></p>'
            )
    risk_html = "".join(f"<p>{r}</p>" for r in risk)
    mdna_html = "".join(f"<p>{m}</p>" for m in mdna)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:us-gaap="http://fasb.org/us-gaap/2024" xmlns:dei="http://xbrl.sec.gov/dei/2024">
<head><title>10-K</title></head><body>
<div style="display:none"><ix:header><ix:hidden>
<ix:nonNumeric name="dei:DocumentType" contextRef="CUR">10-K</ix:nonNumeric></ix:hidden>
<ix:resources>{ctx}<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources>
</ix:header></div>
<div><b>Item 1. Business</b></div><p>{FILLER}</p>
<div><b>Item 1A. Risk Factors</b></div>{risk_html}
<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>
<div><b>Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations</b></div>
{mdna_html}
<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>
<div><b>Item 8. Financial Statements and Supplementary Data</b></div><p>{FILLER}</p>{"".join(facts)}
<div><b>SIGNATURES</b></div><p>Signed.</p></body></html>"""


VALUES_24 = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("391,035", "383,285"),
    "CostOfGoodsAndServicesSold": ("210,352", "214,137"),
    "OperatingIncomeLoss": ("123,216", "114,301"),
    "NetIncomeLoss": ("93,736", "96,995"),
    "NetCashProvidedByUsedInOperatingActivities": ("118,254", "110,543"),
    "PaymentsToAcquirePropertyPlantAndEquipment": ("9,447", "10,959"),
    "CashAndCashEquivalentsAtCarryingValue": ("6,000", "10,000"),
    "LongTermDebtNoncurrent": ("95,000", "85,000"),
}
VALUES_23 = {k: (p, p) for k, (_, p) in VALUES_24.items()}

RISK_OLD = ["Competition is intense and could reduce our margins over time."]
RISK_NEW = [
    *RISK_OLD,
    "New export controls will materially restrict sales of advanced products to certain customers.",
]
MDNA_OLD = ["Liquidity is supported by cash and cash equivalents of $10 billion."]
MDNA_NEW = [
    "Liquidity is strong and improved, supported by cash and cash equivalents of $6 billion and borrowings under our credit facility."
]

K23 = FilingSpec(
    "0001234567-23-000030",
    "10-K",
    "2023-11-03",
    "2023-09-30",
    "2023-11-02T18:08:27.000Z",
    "exdv-20230930.htm",
    ixbrl_10k(
        fy=("2022-09-25", "2023-09-30"),
        py=("2021-09-26", "2022-09-24"),
        values=VALUES_23,
        risk=RISK_OLD,
        mdna=MDNA_OLD,
    ),
)
K24 = FilingSpec(
    "0001234567-24-000031",
    "10-K",
    "2024-11-01",
    "2024-09-28",
    "2024-10-31T18:01:36.000Z",
    "exdv-20240928.htm",
    ixbrl_10k(
        fy=("2023-10-01", "2024-09-28"),
        py=("2022-09-25", "2023-09-30"),
        values=VALUES_24,
        risk=RISK_NEW,
        mdna=MDNA_NEW,
    ),
    exhibits={
        "exdv-ex101.htm": (
            "EX-10.1",
            "<html><body><p>Credit Agreement dated 2024.</p></body></html>",
        )
    },
)
K24A = FilingSpec(
    "0001234567-25-000010",
    "10-K/A",
    "2025-02-14",
    "2024-09-28",
    "2025-02-14T16:30:05.000Z",
    "exdv-20240928a.htm",
    ixbrl_10k(
        fy=("2023-10-01", "2024-09-28"),
        py=("2022-09-25", "2023-09-30"),
        values=VALUES_24,
        risk=[*RISK_NEW, "Restated: inventory reserve methodology corrected."],
        mdna=MDNA_NEW,
    ),
)
E8K = FilingSpec(
    "0001234567-25-000002",
    "8-K",
    "2025-01-30",
    "",
    "2025-01-30T16:30:00.000Z",
    "exdv-8k.htm",
    """<html><body><p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>The Company issued a press release announcing first quarter results, furnished as Exhibit 99.1.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<table><tr><td>99.1</td><td>Press release dated January 30, 2025</td></tr></table>
<p>SIGNATURES</p></body></html>""",
    items="2.02,9.01",
    exhibits={
        "exdv-ex991.htm": (
            "EX-99.1",
            "<html><body><p>Revenue rose 4% and the Company "
            "expects continued growth (outlook).</p></body></html>",
        )
    },
)
NR8K = FilingSpec(
    "0001234567-25-000008",
    "8-K",
    "2025-02-10",
    "",
    "2025-02-10T16:00:00.000Z",
    "exdv-8k-nr.htm",
    "<html><body><p>Item 4.02 Non-Reliance on Previously Issued Financial Statements.</p>"
    "<p>The Audit Committee concluded prior statements should no longer be relied upon.</p></body></html>",
    items="4.02",
)
ALL = [K23, K24, K24A, E8K, NR8K]


def _nodash(acc: str) -> str:
    return acc.replace("-", "")


def build_routes(specs: list[FilingSpec]) -> dict[str, bytes]:
    cols: dict[str, list[object]] = {
        k: []
        for k in (
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
            "items",
            "isXBRL",
            "isInlineXBRL",
            "primaryDocDescription",
        )
    }
    routes: dict[str, bytes] = {}
    for s in specs:
        for k, v in (
            ("accessionNumber", s.accession),
            ("filingDate", s.filing_date),
            ("reportDate", s.report_date),
            ("acceptanceDateTime", s.accepted),
            ("form", s.form),
            ("primaryDocument", s.primary),
            ("items", s.items),
            ("isXBRL", 1),
            ("isInlineXBRL", 1),
            ("primaryDocDescription", s.form),
        ):
            cols[k].append(v)
        base = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{_nodash(s.accession)}/"
        files = {s.primary: (s.form, s.body), **s.exhibits}
        items = [{"name": n, "type": "text.gif", "size": str(len(b[1]))} for n, b in files.items()]
        items.append({"name": f"{s.accession}-index.htm", "type": "text.gif", "size": ""})
        routes[base + "index.json"] = json.dumps({"directory": {"item": items}}).encode()
        rows = "".join(
            f'<tr><td>{i + 1}</td><td>{t}</td><td><a href="{base}{n}">{n}</a></td><td>{t}</td><td>{len(h)}</td></tr>'
            for i, (n, (t, h)) in enumerate(files.items())
        )
        routes[base + f"{s.accession}-index.htm"] = (
            '<html><body><table class="tableFile"><tr><th>Seq</th><th>Description</th><th>Document</th>'
            f"<th>Type</th><th>Size</th></tr>{rows}</table></body></html>"
        ).encode()
        for n, (_, h) in files.items():
            routes[base + n] = h.encode()
    sub = {
        "cik": str(CIK),
        "name": "Example Devices Inc.",
        "fiscalYearEnd": "0928",
        "formerNames": [],
        "filings": {"recent": cols, "files": []},
    }
    routes[f"https://data.sec.gov/submissions/CIK{CIK:010d}.json"] = json.dumps(sub).encode()
    return routes


def transport(
    routes: dict[str, bytes], fail: Callable[[str], bool] = lambda u: False
) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if fail(url):
            return httpx.Response(503)
        if url not in routes:
            return httpx.Response(404)
        ctype = "application/json" if url.endswith(".json") else "text/html"
        return httpx.Response(200, content=routes[url], headers={"Content-Type": ctype})

    return httpx.MockTransport(handler)


def scripted_llm(builder: Callable[[list[tuple[str, str]]], list[dict[str, object]]]):  # type: ignore[no-untyped-def]
    """Provider whose response is built from the evidence ids/kinds in the prompt."""
    from filinglens.llm.client import Prompt, ProviderResponse

    class Scripted:
        name = "scripted"

        def __init__(self) -> None:
            self.prompts: list[Prompt] = []

        def complete(self, prompt: Prompt) -> ProviderResponse:
            self.prompts.append(prompt)
            ev = re.findall(r'<evidence id="([^"]+)" kind="([^"]+)"', prompt.user)
            body = {"schema_version": "finding-schema/1", "findings": builder(ev)}
            return ProviderResponse(
                json.dumps(body), "scripted", "scripted-model-1", "req-1", 10, 10, 0.0
            )

    return Scripted()
