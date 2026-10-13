"""8-K item and exhibit parser (``eight-k-parser/1``).

8-K filings are event filings: each ``Item X.YY`` is parsed into an event object carrying
its text, evidence locators and linked exhibits. Event categories are derived only from the
SEC item number; no conclusions are drawn from the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from filinglens.domain.package import DocumentRole, PackageManifest
from filinglens.parsing.normalizer import Block, BlockKind, NormalizedDocument, normalize_ws

PARSER_VERSION = "eight-k-parser/1"

EIGHT_K_ITEMS: dict[str, tuple[str, str]] = {
    "1.01": ("Entry into a Material Definitive Agreement", "material_agreement"),
    "1.02": ("Termination of a Material Definitive Agreement", "material_agreement"),
    "1.03": ("Bankruptcy or Receivership", "bankruptcy"),
    "1.04": ("Mine Safety", "other"),
    "1.05": ("Material Cybersecurity Incidents", "cybersecurity"),
    "2.01": ("Completion of Acquisition or Disposition of Assets", "acquisition_disposition"),
    "2.02": ("Results of Operations and Financial Condition", "earnings"),
    "2.03": ("Creation of a Direct Financial Obligation", "financing"),
    "2.04": ("Triggering Events That Accelerate a Financial Obligation", "financing"),
    "2.05": ("Costs Associated with Exit or Disposal Activities", "restructuring"),
    "2.06": ("Material Impairments", "impairment"),
    "3.01": ("Notice of Delisting or Failure to Satisfy a Listing Rule", "listing"),
    "3.02": ("Unregistered Sales of Equity Securities", "equity"),
    "3.03": ("Material Modification to Rights of Security Holders", "equity"),
    "4.01": ("Changes in Registrant's Certifying Accountant", "auditor_change"),
    "4.02": ("Non-Reliance on Previously Issued Financial Statements", "non_reliance"),
    "5.01": ("Changes in Control of Registrant", "control_change"),
    "5.02": ("Departure/Election of Directors or Officers", "leadership_change"),
    "5.03": ("Amendments to Articles of Incorporation or Bylaws", "governance"),
    "5.04": ("Temporary Suspension of Trading Under Employee Benefit Plans", "governance"),
    "5.05": ("Amendments to the Code of Ethics", "governance"),
    "5.06": ("Change in Shell Company Status", "other"),
    "5.07": ("Submission of Matters to a Vote of Security Holders", "governance"),
    "5.08": ("Shareholder Director Nominations", "governance"),
    "6.01": ("ABS Informational and Computational Material", "other"),
    "6.02": ("Change of Servicer or Trustee", "other"),
    "6.03": ("Change in Credit Enhancement", "other"),
    "6.04": ("Failure to Make a Required Distribution", "other"),
    "6.05": ("Securities Act Updating Disclosure", "other"),
    "7.01": ("Regulation FD Disclosure", "reg_fd"),
    "8.01": ("Other Events", "other"),
    "9.01": ("Financial Statements and Exhibits", "exhibits"),
}

_ITEM_RE = re.compile(r"^item\s*(\d{1,2})\s*\.\s*(\d{1,2})\b\s*[.:\-–—]?\s*(.*)$", re.I)
_SIGNATURE_RE = re.compile(r"^signatures?\.?$", re.I)
_EXHIBIT_NUM = re.compile(r"^(?:ex(?:hibit)?[\s.\-]*)?(\d{1,3}(?:\.\d{1,3})?)\b", re.I)
_EXHIBIT_REF = re.compile(r"\bexhibits?\s+(?:no\.\s*)?(\d{1,3}\.\d{1,3})\b", re.I)


class ExhibitLinkStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"


@dataclass(frozen=True)
class EvidenceLocator:
    filename: str | None
    block_id: str
    locator: str


@dataclass(frozen=True)
class ExhibitLink:
    exhibit_number: str
    description: str | None
    status: ExhibitLinkStatus
    filename: str | None = None
    doc_type: str | None = None
    referenced_in: tuple[str, ...] = ()  # item numbers


@dataclass(frozen=True)
class EightKEvent:
    item: str
    title: str
    category: str
    text: str
    evidence: tuple[EvidenceLocator, ...]
    exhibits: tuple[ExhibitLink, ...] = ()
    exhibit_evidence: tuple[EvidenceLocator, ...] = ()


@dataclass(frozen=True)
class EightKParseResult:
    events: tuple[EightKEvent, ...]
    exhibits: tuple[ExhibitLink, ...]
    diagnostics: tuple[str, ...] = ()
    parser_version: str = PARSER_VERSION

    def event(self, item: str) -> EightKEvent | None:
        return next((e for e in self.events if e.item == item), None)

    @property
    def items(self) -> tuple[str, ...]:
        return tuple(e.item for e in self.events)


@dataclass
class _Ref:
    desc: str | None = None
    items: set[str] = field(default_factory=set)


def _num_key(number: str) -> tuple[int, ...]:
    return tuple(int(p) for p in number.split("."))


def exhibit_number_from_type(doc_type: str | None) -> str | None:
    if not doc_type:
        return None
    m = re.match(r"^EX-(\d{1,3}(?:\.\d{1,3})?)", doc_type.strip(), re.I)
    return m.group(1) if m else None


def _item_headings(doc: NormalizedDocument) -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []
    for pos, b in enumerate(doc.blocks):
        if b.kind is BlockKind.TABLE or len(b.text) > 250:
            continue
        m = _ITEM_RE.match(normalize_ws(b.text))
        if m:
            item = f"{int(m.group(1))}.{int(m.group(2)):02d}"
            out.append((pos, item, m.group(3)))
    return out


def _exhibit_table(blocks: list[Block]) -> list[tuple[str, str | None]]:
    rows: list[tuple[str, str | None]] = []
    for b in blocks:
        if b.kind is not BlockKind.TABLE or not b.rows:
            continue
        for row in b.rows:
            m = _EXHIBIT_NUM.match(row[0])
            if m and "." in m.group(1):
                desc = " ".join(row[1:]) or None
                rows.append((m.group(1), desc))
    return rows


def parse_eight_k(
    doc: NormalizedDocument,
    manifest: PackageManifest | None = None,
    *,
    primary_filename: str | None = None,
    exhibit_docs: dict[str, NormalizedDocument] | None = None,
    reported_items: tuple[str, ...] | None = None,
) -> EightKParseResult:
    """Parse a normalized 8-K primary document.

    ``manifest`` resolves exhibit references to package documents; ``exhibit_docs`` maps
    exhibit filenames to their normalized content (e.g. EX-99.1 earnings releases);
    ``reported_items`` is the submissions ``items`` field, cross-checked in diagnostics.
    """
    diagnostics: list[str] = []
    heads = _item_headings(doc)
    sig = next(
        (p for p, b in enumerate(doc.blocks) if _SIGNATURE_RE.match(normalize_ws(b.text))),
        len(doc.blocks),
    )
    seen: set[str] = set()
    raw_events: list[tuple[str, str, list[Block]]] = []
    for i, (pos, item, heading_title) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(doc.blocks)
        end = min(end, sig) if sig > pos else end
        if item not in EIGHT_K_ITEMS:
            diagnostics.append(f"unknown 8-K item {item}")
        if item in seen:
            diagnostics.append(f"item {item} heading appears more than once")
            continue
        seen.add(item)
        raw_events.append((item, heading_title, list(doc.blocks[pos + 1 : end])))
        raw_events[-1][2].insert(0, doc.blocks[pos])

    # Exhibit references: 9.01 table rows + "Exhibit 99.1" mentions anywhere.
    refs: dict[str, _Ref] = {}
    for item, _title, blocks in raw_events:
        if item == "9.01":
            for number, desc in _exhibit_table(blocks):
                ref = refs.setdefault(number, _Ref())
                ref.desc = ref.desc or desc
                ref.items.add(item)
        for b in blocks:
            for number in _EXHIBIT_REF.findall(b.text):
                refs.setdefault(number, _Ref()).items.add(item)

    by_number: dict[tuple[int, ...], tuple[str, str | None]] = {}
    if manifest is not None:
        for d in manifest.documents:
            if d.role == DocumentRole.EXHIBIT.value:
                num = exhibit_number_from_type(d.doc_type)
                if num:
                    by_number[_num_key(num)] = (d.filename, d.doc_type)
    links: dict[str, ExhibitLink] = {}
    for number in sorted(refs, key=_num_key):
        items = tuple(sorted(refs[number].items))
        desc = refs[number].desc
        hit = by_number.get(_num_key(number))
        if number.startswith("104") or number.startswith("101"):
            continue  # cover-page iXBRL / XBRL, not an analyst exhibit document
        if hit:
            links[number] = ExhibitLink(
                number, desc, ExhibitLinkStatus.RESOLVED, hit[0], hit[1], items
            )
        else:
            links[number] = ExhibitLink(number, desc, ExhibitLinkStatus.MISSING, None, None, items)
            diagnostics.append(f"exhibit {number} referenced but not found in package")

    exhibit_docs = exhibit_docs or {}
    events: list[EightKEvent] = []
    for item, heading_title, blocks in raw_events:
        title, category = EIGHT_K_ITEMS.get(item, (heading_title or "Unknown item", "unknown"))
        ev_links = tuple(lk for lk in links.values() if item in lk.referenced_in)
        # Earnings (2.02) / Reg FD (7.01) items commonly furnish EX-99 content.
        if item in ("2.02", "7.01") and not ev_links:
            ev_links = tuple(lk for lk in links.values() if lk.exhibit_number.startswith("99"))
        ex_evidence: list[EvidenceLocator] = []
        for link in ev_links:
            if link.filename and link.filename in exhibit_docs:
                ex_evidence.extend(
                    EvidenceLocator(link.filename, b.block_id, b.locator)
                    for b in exhibit_docs[link.filename].blocks
                )
        events.append(
            EightKEvent(
                item=item,
                title=title,
                category=category,
                text="\n".join(b.text for b in blocks[1:]),
                evidence=tuple(
                    EvidenceLocator(primary_filename, b.block_id, b.locator) for b in blocks
                ),
                exhibits=ev_links,
                exhibit_evidence=tuple(ex_evidence),
            )
        )

    if not events:
        diagnostics.append("no 8-K item headings found")
    if reported_items is not None:
        parsed = {e.item for e in events}
        reported = {f"{int(a)}.{int(b):02d}" for a, b in (r.split(".") for r in reported_items)}
        if parsed != reported:
            diagnostics.append(
                f"parsed items {sorted(parsed)} differ from submissions items {sorted(reported)}"
            )
    return EightKParseResult(
        events=tuple(events), exhibits=tuple(links.values()), diagnostics=tuple(diagnostics)
    )


__all__ = ["EIGHT_K_ITEMS", "EightKEvent", "EightKParseResult", "ExhibitLink", "parse_eight_k"]
