"""Risk, MD&A, liquidity and exhibit change detectors (``detectors/1``).

Deterministic lexical features only. Detectors label *topics* and *language direction*;
they never assert materiality. When a classification is unsafe the direction is ``unknown``.
Formatting-only and year/number roll-forward edits are suppressed as non-substantive.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from filinglens.diff.text_diff import Change, ChangeKind, DiffResult, EvidenceRef
from filinglens.parsing.eight_k import EightKParseResult

DETECTOR_VERSION = "detectors/1"

MDNA_TOPICS: dict[str, tuple[str, ...]] = {
    "demand": ("demand", "orders", "backlog", "unit sales", "customer spending", "volume"),
    "pricing": ("pricing", "price increase", "prices", "average selling price", "discount"),
    "costs": ("cost of sales", "input costs", "inflation", "labor costs", "commodity"),
    "margins": ("gross margin", "operating margin", "margin"),
    "capex": ("capital expenditure", "capital spending", "capex", "property, plant"),
    "liquidity": ("liquidity", "cash and cash equivalents", "cash flows", "working capital"),
    "debt": ("debt", "credit facility", "notes due", "borrowings", "covenant", "refinanc"),
    "customer_concentration": (
        "largest customer",
        "significant customer",
        "% of net sales",
        "% of revenue",
        "customer concentration",
    ),
    "supplier_concentration": (
        "single-source",
        "sole source",
        "limited number of suppliers",
        "single source",
        "supplier concentration",
    ),
}
EXHIBIT_TOPICS: dict[str, tuple[str, ...]] = {
    "earnings": ("results", "net income", "earnings per share", "revenue", "net sales"),
    "guidance": ("outlook", "guidance", "expects", "forecast", "anticipates"),
    "financing": ("credit agreement", "indenture", "notes", "offering", "loan", "facility"),
    "acquisition_disposition": ("merger", "acquisition", "acquire", "divest", "disposition"),
    "material_agreement": ("agreement", "amendment no.", "contract"),
}
_INTENSIFIERS = re.compile(
    r"\b(materially|material|significant(?:ly)?|substantial(?:ly)?|severe(?:ly)?|adversely|"
    r"will|cannot|unable|depend(?:s|ent)? (?:heavily|substantially))\b",
    re.I,
)
_SOFTENERS = re.compile(r"\b(may|might|could|potentially|possibly|limited|modest(?:ly)?)\b", re.I)


class Direction(StrEnum):
    NEW = "new"
    REMOVED = "removed"
    STRENGTHENED = "strengthened"
    SOFTENED = "softened"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChangeSignal:
    signal_id: str
    detector: str  # risk_factor | mdna_topic | exhibit_topic
    section_key: str
    topics: tuple[str, ...]
    direction: Direction
    reason: str
    evidence: tuple[EvidenceRef, ...]
    source_change_id: str | None = None
    version: str = DETECTOR_VERSION


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_non_substantive(change: Change) -> str | None:
    """Return a reason when a modification is formatting-only or a pure roll-forward."""
    if change.kind is not ChangeKind.MODIFIED or not change.old or not change.new:
        return None
    if _alnum(change.old.text) == _alnum(change.new.text):
        return "formatting-only (punctuation/case/whitespace)"
    strip = re.compile(r"\d")
    if (
        change.block_kind != "table"
        and strip.sub("", _alnum(change.old.text)) == strip.sub("", _alnum(change.new.text))
        and re.sub(r"\b(19|20)\d{2}\b", "", change.old.text)
        == re.sub(r"\b(19|20)\d{2}\b", "", change.new.text)
    ):
        return "year roll-forward only"
    return None


def topics_in(text: str, lexicon: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    low = text.lower()
    return tuple(t for t, words in lexicon.items() if any(w in low for w in words))


def language_direction(old: str, new: str) -> tuple[Direction, str]:
    di = len(_INTENSIFIERS.findall(new)) - len(_INTENSIFIERS.findall(old))
    ds = len(_SOFTENERS.findall(new)) - len(_SOFTENERS.findall(old))
    if di > 0 and ds <= 0:
        return Direction.STRENGTHENED, f"intensifiers +{di}, softeners {ds:+d}"
    if (di < 0 and ds >= 0) or (di == 0 and ds > 0):
        return Direction.SOFTENED, f"intensifiers {di:+d}, softeners {ds:+d}"
    if di == 0 and ds == 0:
        return Direction.UNKNOWN, "no lexical intensity change"
    return Direction.UNKNOWN, f"mixed signals (intensifiers {di:+d}, softeners {ds:+d})"


def _sid(*parts: str) -> str:
    return "sig-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _changed_text(change: Change) -> tuple[str, str]:
    if change.sentence_edits:
        old = " ".join(e.text for e in change.sentence_edits if e.op == "removed")
        new = " ".join(e.text for e in change.sentence_edits if e.op == "added")
        return old, new
    return (change.old.text if change.old else ""), (change.new.text if change.new else "")


def _evidence(change: Change) -> tuple[EvidenceRef, ...]:
    return tuple(r for r in (change.old, change.new) if r is not None)


def detect_risk_factors(diff: DiffResult, section_key: str) -> list[ChangeSignal]:
    out: list[ChangeSignal] = []
    for c in diff.for_section(section_key):
        if is_non_substantive(c):
            continue
        if c.kind is ChangeKind.ADDED:
            direction, reason = Direction.NEW, "risk factor text added"
        elif c.kind is ChangeKind.REMOVED:
            direction, reason = Direction.REMOVED, "risk factor text removed"
        else:
            old, new = _changed_text(c)
            direction, reason = language_direction(old, new)
        old, new = _changed_text(c)
        topics = topics_in(f"{old} {new}", MDNA_TOPICS)
        out.append(
            ChangeSignal(
                _sid("rf", c.change_id),
                "risk_factor",
                section_key,
                topics,
                direction,
                reason,
                _evidence(c),
                c.change_id,
            )
        )
    return out


def detect_mdna_topics(diff: DiffResult, section_keys: Iterable[str]) -> list[ChangeSignal]:
    out: list[ChangeSignal] = []
    for key in section_keys:
        for c in diff.for_section(key):
            if is_non_substantive(c):
                continue
            old, new = _changed_text(c)
            topics = topics_in(f"{old} {new}", MDNA_TOPICS)
            if not topics:
                continue
            if c.kind is ChangeKind.ADDED:
                direction, reason = Direction.NEW, "topic text added"
            elif c.kind is ChangeKind.REMOVED:
                direction, reason = Direction.REMOVED, "topic text removed"
            elif c.block_kind == "table":
                direction, reason = Direction.UNKNOWN, "table values changed; see numeric engine"
            else:
                direction, reason = language_direction(old, new)
            out.append(
                ChangeSignal(
                    _sid("md", c.change_id),
                    "mdna_topic",
                    key,
                    topics,
                    direction,
                    reason,
                    _evidence(c),
                    c.change_id,
                )
            )
    return out


def detect_exhibit_topics(parsed: EightKParseResult) -> list[ChangeSignal]:
    """Topic flags for 8-K events and their exhibits; evidence is always attached."""
    out: list[ChangeSignal] = []
    for ev in parsed.events:
        if ev.category == "exhibits":
            continue
        topics = list(topics_in(ev.text, EXHIBIT_TOPICS))
        if ev.category in EXHIBIT_TOPICS and ev.category not in topics:
            topics.insert(0, ev.category)
        evidence = tuple(
            EvidenceRef(None, e.block_id, e.locator, "")
            for e in (*ev.evidence, *ev.exhibit_evidence)
        )
        if not topics:
            topics = ["unknown"]
        out.append(
            ChangeSignal(
                _sid("8k", ev.item, *(e.block_id for e in ev.evidence)),
                "exhibit_topic",
                f"8-K:{ev.item}",
                tuple(topics),
                Direction.NEW,
                f"8-K Item {ev.item} ({ev.category})",
                evidence,
            )
        )
    return out
