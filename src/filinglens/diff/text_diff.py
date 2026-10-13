"""Deterministic text and table diff engine (``diff/1``). No LLM dependency.

Consumes a :class:`DocumentAlignment` and emits change objects with stable ids, exact
old/new evidence (snapshot id + block id + locator + text), sentence-level edits for
modified paragraphs, cell/row edits for supported tables and per-section change metrics.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from filinglens.diff.alignment import AlignedPair, DocumentAlignment, PairStatus
from filinglens.parsing.normalizer import Block, BlockKind, NormalizedDocument

DIFF_VERSION = "diff/1"

_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\"“$])")
_ABBREV_END = re.compile(r"\b(?:inc|corp|co|ltd|no|u\.s|vs|e\.g|i\.e|approx|mr|ms|dr)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text.strip())
    out: list[str] = []
    for part in parts:
        if out and _ABBREV_END.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [p for p in out if p]


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass(frozen=True)
class EvidenceRef:
    snapshot_id: str | None
    block_id: str
    locator: str
    text: str


@dataclass(frozen=True)
class SentenceEdit:
    op: str  # added | removed
    text: str


@dataclass(frozen=True)
class CellChange:
    row_label: str
    column: int
    old: str | None
    new: str | None


@dataclass(frozen=True)
class TableDiff:
    supported: bool
    added_rows: tuple[str, ...] = ()
    removed_rows: tuple[str, ...] = ()
    cell_changes: tuple[CellChange, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class Change:
    change_id: str
    section_key: str
    kind: ChangeKind
    block_kind: str
    old: EvidenceRef | None
    new: EvidenceRef | None
    sentence_edits: tuple[SentenceEdit, ...] = ()
    table: TableDiff | None = None
    moved: bool = False
    alignment_confidence: float = 1.0


@dataclass(frozen=True)
class SectionStat:
    section_key: str
    old_chars: int
    new_chars: int
    length_delta: int
    change_magnitude: float  # 0 = identical, 1 = completely different (word level)
    added: int
    removed: int
    modified: int


@dataclass(frozen=True)
class DiffResult:
    changes: tuple[Change, ...]
    sections: tuple[SectionStat, ...]
    old_snapshot_id: str | None
    new_snapshot_id: str | None
    version: str = DIFF_VERSION

    def for_section(self, key: str) -> list[Change]:
        return [c for c in self.changes if c.section_key == key]

    def stat(self, key: str) -> SectionStat | None:
        return next((s for s in self.sections if s.section_key == key), None)


def sentence_edits(old: str, new: str) -> tuple[SentenceEdit, ...]:
    a, b = split_sentences(old), split_sentences(new)
    edits: list[SentenceEdit] = []
    sm = SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        edits.extend(SentenceEdit("removed", s) for s in a[i1:i2])
        edits.extend(SentenceEdit("added", s) for s in b[j1:j2])
    return tuple(edits)


def _numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"[$(]*-?[\d,.]+\)?%?|—|-|–", cell.strip()))


def table_diff(old: Block, new: Block) -> TableDiff:
    if not old.rows or not new.rows:
        return TableDiff(False, reason="table has no rows")

    def keyed(rows: tuple[tuple[str, ...], ...]) -> dict[str, tuple[str, ...]] | None:
        out: dict[str, tuple[str, ...]] = {}
        for i, row in enumerate(rows):
            label = row[0] if not _numeric(row[0]) else f"<header:{i}>"
            if label in out:
                return None
            out[label] = row[1:]
        return out

    ko, kn = keyed(old.rows), keyed(new.rows)
    if ko is None or kn is None:
        return TableDiff(False, reason="duplicate row labels; cell diff unsupported")
    added = tuple(k for k in kn if k not in ko)
    removed = tuple(k for k in ko if k not in kn)
    cells: list[CellChange] = []
    for label in (k for k in kn if k in ko):
        o, n = ko[label], kn[label]
        for col in range(max(len(o), len(n))):
            ov = o[col] if col < len(o) else None
            nv = n[col] if col < len(n) else None
            if ov != nv:
                cells.append(CellChange(label, col + 1, ov, nv))
    return TableDiff(True, added, removed, tuple(cells))


def _ref(doc: NormalizedDocument, block: Block | None) -> EvidenceRef | None:
    if block is None:
        return None
    return EvidenceRef(doc.snapshot_id, block.block_id, block.locator, block.text)


def _change_id(section: str, pair: AlignedPair, old: Block | None, new: Block | None) -> str:
    raw = "|".join(
        [
            section,
            pair.status.value,
            old.fingerprint if old else "-",
            new.fingerprint if new else "-",
            str(old.index if old else -1),
            str(new.index if new else -1),
        ]
    )
    return "chg-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _words(text: str) -> list[str]:
    return text.split()


def diff_documents(
    old_doc: NormalizedDocument, new_doc: NormalizedDocument, alignment: DocumentAlignment
) -> DiffResult:
    old_blocks = {b.block_id: b for b in old_doc.blocks}
    new_blocks = {b.block_id: b for b in new_doc.blocks}
    changes: list[Change] = []
    stats: list[SectionStat] = []
    for sec in alignment.sections:
        old_text: list[str] = []
        new_text: list[str] = []
        counts = {"added": 0, "removed": 0, "modified": 0}
        for pair in sec.pairs:
            ob = old_blocks.get(pair.old_block_id) if pair.old_block_id else None
            nb = new_blocks.get(pair.new_block_id) if pair.new_block_id else None
            if ob:
                old_text.append(ob.text)
            if nb:
                new_text.append(nb.text)
            if pair.status is PairStatus.IDENTICAL:
                continue
            kind = {
                PairStatus.ADDED: ChangeKind.ADDED,
                PairStatus.REMOVED: ChangeKind.REMOVED,
                PairStatus.MODIFIED: ChangeKind.MODIFIED,
            }[pair.status]
            block = nb or ob
            assert block is not None
            edits: tuple[SentenceEdit, ...] = ()
            tdiff: TableDiff | None = None
            if kind is ChangeKind.MODIFIED and ob and nb:
                if ob.kind is BlockKind.TABLE:
                    tdiff = table_diff(ob, nb)
                else:
                    edits = sentence_edits(ob.text, nb.text)
            counts[kind.value] += 1
            changes.append(
                Change(
                    change_id=_change_id(sec.section_key, pair, ob, nb),
                    section_key=sec.section_key,
                    kind=kind,
                    block_kind=block.kind.value,
                    old=_ref(old_doc, ob),
                    new=_ref(new_doc, nb),
                    sentence_edits=edits,
                    table=tdiff,
                    moved=pair.moved,
                    alignment_confidence=pair.confidence,
                )
            )
        o_chars = sum(len(t) for t in old_text)
        n_chars = sum(len(t) for t in new_text)
        ow, nw = _words(" ".join(old_text)), _words(" ".join(new_text))
        if not ow and not nw:
            magnitude = 0.0
        else:
            magnitude = round(1.0 - SequenceMatcher(None, ow, nw, autojunk=False).ratio(), 4)
        stats.append(
            SectionStat(
                sec.section_key,
                o_chars,
                n_chars,
                n_chars - o_chars,
                magnitude,
                counts["added"],
                counts["removed"],
                counts["modified"],
            )
        )
    return DiffResult(tuple(changes), tuple(stats), old_doc.snapshot_id, new_doc.snapshot_id)
