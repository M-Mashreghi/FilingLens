"""10-K / 10-Q item and section parser over normalized blocks.

Algorithm (``section-parser/1``):

1. Candidate headings: short non-table blocks starting with ``Item N[A-C]`` (or ``Part I..IV``
   markers, or ``Signatures``). Cross-references inside prose never qualify because the
   pattern is anchored and length-limited.
2. Table-of-contents entries are discarded: a candidate whose body before the next candidate
   is shorter than ``toc_max_chars`` is dropped when a later candidate for the same key exists.
3. If two candidates for one key both carry substantial bodies, the section is ``ambiguous``.
4. Every section ends at the next chosen boundary (any item, part marker or signatures), so an
   item cannot swallow later sections. Out-of-canonical-order sections are ``ambiguous``.
5. Required items that are not found are reported as ``missing``; nothing fails silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

from filinglens.parsing.normalizer import Block, BlockKind, NormalizedDocument, normalize_ws

PARSER_VERSION = "section-parser/1"

TEN_K_ITEMS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Reserved",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}
TEN_Q_ITEMS: dict[str, str] = {
    "I-1": "Financial Statements",
    "I-2": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "I-3": "Quantitative and Qualitative Disclosures About Market Risk",
    "I-4": "Controls and Procedures",
    "II-1": "Legal Proceedings",
    "II-1A": "Risk Factors",
    "II-2": "Unregistered Sales of Equity Securities and Use of Proceeds",
    "II-3": "Defaults Upon Senior Securities",
    "II-4": "Mine Safety Disclosures",
    "II-5": "Other Information",
    "II-6": "Exhibits",
}
REQUIRED = {
    "10-K": ("1", "1A", "3", "7", "7A", "8"),
    "10-Q": ("I-1", "I-2", "I-3", "II-1", "II-1A"),
}
ANALYST_KEYS = {
    "risk_factors": {"10-K": "1A", "10-Q": "II-1A"},
    "mdna": {"10-K": "7", "10-Q": "I-2"},
    "market_risk": {"10-K": "7A", "10-Q": "I-3"},
    "legal_proceedings": {"10-K": "3", "10-Q": "II-1"},
    "financial_statements": {"10-K": "8", "10-Q": "I-1"},
    "business": {"10-K": "1"},
}

_ITEM_RE = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*(?:[.:\-–—]\s*|\s+|$)(.*)$", re.IGNORECASE)
_PART_RE = re.compile(r"^part\s+(i{1,3}|iv)\b\.?\s*[\-–—:.]?\s*(.*)$", re.IGNORECASE)
_SIGNATURES_RE = re.compile(r"^signatures?\.?$", re.IGNORECASE)
_ROMAN = {"i": "I", "ii": "II", "iii": "III", "iv": "IV"}


class SectionStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


@dataclass(frozen=True)
class Section:
    key: str
    expected_title: str
    heading_text: str | None
    status: SectionStatus
    confidence: float
    block_ids: tuple[str, ...] = ()
    start_block_id: str | None = None
    diagnostics: tuple[str, ...] = ()
    text: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class SectionParseResult:
    form: str
    sections: tuple[Section, ...]
    parser_version: str = PARSER_VERSION
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def get(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    def analyst(self, name: str) -> Section | None:
        key = ANALYST_KEYS.get(name, {}).get(self.form)
        return self.get(key) if key else None


@dataclass(frozen=True)
class _Cand:
    pos: int  # index into doc.blocks
    kind: str  # item | part | signatures
    key: str
    heading: str
    inferred_part: bool = False


def _heading_like(b: Block) -> bool:
    return b.kind is not BlockKind.TABLE and len(b.text) <= 180


def _candidates(doc: NormalizedDocument, form: str) -> list[_Cand]:
    out: list[_Cand] = []
    part: str | None = None
    last_item_rank = -1
    explicit_parts = False
    for pos, b in enumerate(doc.blocks):
        if not _heading_like(b):
            continue
        text = normalize_ws(b.text)
        pm = _PART_RE.match(text)
        if pm and not _ITEM_RE.match(text):
            part = _ROMAN[pm.group(1).lower()]
            explicit_parts = True
            last_item_rank = -1
            out.append(_Cand(pos, "part", f"PART-{part}", text))
            # "Part I - Item 1. Financial Statements" on one line
            rest = pm.group(2)
            im = _ITEM_RE.match(rest) if rest else None
            if im:
                key = _item_key(form, part, im.group(1))
                if key:
                    out.append(_Cand(pos, "item", key, text))
            continue
        if _SIGNATURES_RE.match(text):
            out.append(_Cand(pos, "signatures", "SIGNATURES", text))
            continue
        im = _ITEM_RE.match(text)
        if not im:
            continue
        number = im.group(1).upper()
        inferred = False
        if form == "10-Q":
            rank = _rank(number)
            if not explicit_parts:
                # Infer Part II when item numbering restarts.
                if part is None:
                    part = "I"
                elif rank <= last_item_rank and part == "I":
                    part = "II"
                inferred = True
            last_item_rank = rank
        key = _item_key(form, part, number)
        if key:
            out.append(_Cand(pos, "item", key, text, inferred))
    return out


def _rank(number: str) -> int:
    m = re.match(r"(\d+)([A-C]?)", number)
    assert m
    return int(m.group(1)) * 10 + (ord(m.group(2)) - 64 if m.group(2) else 0)


def _item_key(form: str, part: str | None, number: str) -> str | None:
    number = number.upper()
    if form == "10-K":
        return number if number in TEN_K_ITEMS else None
    key = f"{part or 'I'}-{number}"
    return key if key in TEN_Q_ITEMS else None


def _body_chars(doc: NormalizedDocument, start: int, end: int) -> int:
    return sum(len(b.text) for b in doc.blocks[start + 1 : end])


def parse_sections(
    doc: NormalizedDocument,
    form: str,
    *,
    toc_max_chars: int = 150,
    substantial_chars: int = 400,
) -> SectionParseResult:
    base = form.upper().removesuffix("/A")
    if base not in REQUIRED:
        raise ValueError(f"section parser supports 10-K/10-Q, got {form!r}")
    catalog = TEN_K_ITEMS if base == "10-K" else TEN_Q_ITEMS
    cands = _candidates(doc, base)
    n = len(doc.blocks)
    next_pos = [n] * len(cands)
    for i in range(len(cands)):
        later = [c.pos for c in cands[i + 1 :] if c.pos > cands[i].pos]
        next_pos[i] = later[0] if later else n

    diagnostics: list[str] = []
    by_key: dict[str, list[tuple[_Cand, int]]] = {}
    for c_item, end in zip(cands, next_pos, strict=True):
        if c_item.kind == "item":
            by_key.setdefault(c_item.key, []).append((c_item, _body_chars(doc, c_item.pos, end)))

    chosen: dict[str, _Cand] = {}
    ambiguous: dict[str, list[str]] = {}
    for key, occ in by_key.items():
        kept = [
            (c, chars)
            for idx, (c, chars) in enumerate(occ)
            if not (chars < toc_max_chars and idx < len(occ) - 1)
        ]
        dropped = len(occ) - len(kept)
        if dropped:
            diagnostics.append(f"{key}: dropped {dropped} table-of-contents candidate(s)")
        substantial = [c for c, chars in kept if chars >= substantial_chars]
        if len(substantial) > 1:
            ambiguous.setdefault(key, []).append(
                f"{len(substantial)} competing headings with substantial bodies"
            )
            chosen[key] = substantial[0]
        else:
            chosen[key] = substantial[0] if substantial else kept[-1][0]

    # Boundaries: chosen items + part markers + signatures (after TOC filtering).
    toc_end = min((c.pos for c in chosen.values()), default=0)
    boundaries = sorted(
        {c.pos for c in chosen.values()}
        | {c.pos for c in cands if c.kind in ("part", "signatures") and c.pos >= toc_end}
    )

    order = list(catalog)
    starts = sorted(chosen.items(), key=lambda kv: kv[1].pos)
    seen_rank = -1
    for key, _cand in starts:
        rank = order.index(key)
        if rank < seen_rank:
            ambiguous.setdefault(key, []).append("appears out of canonical item order")
        seen_rank = max(seen_rank, rank)

    sections: list[Section] = []
    for key, expected in catalog.items():
        cand = chosen.get(key)
        if cand is None:
            if key in REQUIRED[base]:
                sections.append(
                    Section(
                        key=key,
                        expected_title=expected,
                        heading_text=None,
                        status=SectionStatus.MISSING,
                        confidence=0.0,
                        diagnostics=("required item heading not found",),
                    )
                )
            continue
        end = next((p for p in boundaries if p > cand.pos), n)
        blocks = doc.blocks[cand.pos + 1 : end]
        notes = list(ambiguous.get(key, []))
        confidence = 1.0
        title = _ITEM_RE.match(normalize_ws(doc.blocks[cand.pos].text))
        heading_title = title.group(2) if title else ""
        if heading_title:
            sim = SequenceMatcher(None, _simple(heading_title), _simple(expected)).ratio()
            if sim < 0.5 and not _simple(expected).startswith(_simple(heading_title)[:12]):
                confidence -= 0.2
                notes.append(f"heading title differs from expected ({sim:.2f})")
        if cand.inferred_part:
            confidence -= 0.1
            notes.append("10-Q part inferred from item numbering (no PART markers)")
        if not blocks:
            notes.append("section body is empty")
        status = SectionStatus.AMBIGUOUS if (key in ambiguous or not blocks) else SectionStatus.OK
        if status is SectionStatus.AMBIGUOUS:
            confidence = min(confidence, 0.4)
        sections.append(
            Section(
                key=key,
                expected_title=expected,
                heading_text=doc.blocks[cand.pos].text,
                status=status,
                confidence=round(max(confidence, 0.0), 2),
                block_ids=tuple(b.block_id for b in blocks),
                start_block_id=doc.blocks[cand.pos].block_id,
                diagnostics=tuple(notes),
                text="\n".join(b.text for b in blocks),
            )
        )
    return SectionParseResult(form=base, sections=tuple(sections), diagnostics=tuple(diagnostics))


def _simple(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower().replace("’", "'").replace("'", ""))
