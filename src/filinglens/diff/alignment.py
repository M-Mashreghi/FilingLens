"""Section alignment engine (``alignment/1``).

Sections are aligned by SEC item key first; blocks are only ever matched within the same
section key (never across unrelated items, however similar). Within a section:

1. identical blocks (by fingerprint) are matched in order;
2. blocks identical after boilerplate normalization (years, dates, numbers, case) are
   matched as ``modified`` with similarity 1.0 on the normalized form;
3. remaining blocks are matched greedily by text similarity above ``threshold``; a match
   whose runner-up is within ``ambiguity_margin`` is flagged ``ambiguous``;
4. leftovers are ``added`` / ``removed``.

Matched pairs whose relative order changed are flagged ``moved``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from filinglens.parsing.normalizer import Block, NormalizedDocument
from filinglens.parsing.sections import SectionParseResult, SectionStatus

ALIGNMENT_VERSION = "alignment/1"

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_NUM = re.compile(r"\$?\(?\d[\d,]*(\.\d+)?\)?%?")
_MONTH = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2},?",
    re.I,
)
_WS = re.compile(r"\s+")


def boilerplate_key(text: str) -> str:
    t = text.lower()
    t = _MONTH.sub("<date>", t)
    t = _YEAR.sub("<year>", t)
    t = _NUM.sub("<num>", t)
    return _WS.sub(" ", t).strip()


def similarity(a: str, b: str) -> float:
    ka, kb = boilerplate_key(a), boilerplate_key(b)
    if not ka or not kb:
        return 0.0
    ta, tb = set(ka.split()), set(kb.split())
    jacc = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, ka, kb, autojunk=False).ratio()
    return round(max(jacc, seq), 4)


class PairStatus(StrEnum):
    IDENTICAL = "identical"
    MODIFIED = "modified"
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True)
class AlignedPair:
    section_key: str
    old_block_id: str | None
    new_block_id: str | None
    status: PairStatus
    similarity: float
    confidence: float
    reason: str
    moved: bool = False
    ambiguous: bool = False


@dataclass(frozen=True)
class SectionAlignment:
    section_key: str
    pairs: tuple[AlignedPair, ...]
    old_status: str | None
    new_status: str | None
    notes: tuple[str, ...] = ()

    def by_status(self, status: PairStatus) -> list[AlignedPair]:
        return [p for p in self.pairs if p.status is status]

    @property
    def ambiguous(self) -> bool:
        return any(p.ambiguous for p in self.pairs) or "ambiguous" in (
            self.old_status,
            self.new_status,
        )


@dataclass(frozen=True)
class DocumentAlignment:
    sections: tuple[SectionAlignment, ...]
    version: str = ALIGNMENT_VERSION

    def section(self, key: str) -> SectionAlignment | None:
        return next((s for s in self.sections if s.section_key == key), None)


def align_blocks(
    key: str,
    old: Sequence[Block],
    new: Sequence[Block],
    *,
    threshold: float = 0.55,
    ambiguity_margin: float = 0.03,
) -> tuple[AlignedPair, ...]:
    matched_old: dict[int, tuple[int, PairStatus, float, str, bool]] = {}
    used_new: set[int] = set()

    # 1. identical fingerprints, in order
    by_fp: dict[str, list[int]] = {}
    for j, b in enumerate(new):
        by_fp.setdefault(b.fingerprint, []).append(j)
    for i, b in enumerate(old):
        js = [j for j in by_fp.get(b.fingerprint, []) if j not in used_new]
        if js:
            matched_old[i] = (js[0], PairStatus.IDENTICAL, 1.0, "identical text", False)
            used_new.add(js[0])

    # 2. identical after boilerplate normalization
    by_key: dict[str, list[int]] = {}
    for j, b in enumerate(new):
        if j not in used_new:
            by_key.setdefault(boilerplate_key(b.text), []).append(j)
    for i, b in enumerate(old):
        if i in matched_old:
            continue
        js = [j for j in by_key.get(boilerplate_key(b.text), []) if j not in used_new]
        if js:
            matched_old[i] = (
                js[0],
                PairStatus.MODIFIED,
                1.0,
                "identical after year/date/number normalization",
                False,
            )
            used_new.add(js[0])

    # 3. greedy similarity matching (same block kind only)
    scored: list[tuple[float, int, int]] = []
    for i, ob in enumerate(old):
        if i in matched_old:
            continue
        for j, nb in enumerate(new):
            if j in used_new or nb.kind != ob.kind:
                continue
            s = similarity(ob.text, nb.text)
            if s >= threshold:
                scored.append((s, i, j))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    for s, i, j in scored:
        if i in matched_old or j in used_new:
            continue
        rivals = [
            s2
            for s2, i2, j2 in scored
            if (i2 == i and j2 != j and j2 not in used_new)
            or (j2 == j and i2 != i and i2 not in matched_old)
        ]
        amb = bool(rivals) and s - max(rivals) <= ambiguity_margin
        matched_old[i] = (j, PairStatus.MODIFIED, s, f"text similarity {s:.2f}", amb)
        used_new.add(j)

    # moved detection: longest increasing subsequence of new indices over old order
    order = sorted(matched_old.items())
    in_place = _lis_members([m[0] for _, m in order])
    pairs: list[tuple[int, AlignedPair]] = []
    for pos, (i, (j, status, s, reason, amb)) in enumerate(order):
        moved = pos not in in_place
        conf = s if not amb else min(s, 0.5)
        pairs.append(
            (
                j,
                AlignedPair(
                    key,
                    old[i].block_id,
                    new[j].block_id,
                    status,
                    s,
                    round(conf, 4),
                    reason + ("; moved" if moved else ""),
                    moved,
                    amb,
                ),
            )
        )
    for j, b in enumerate(new):
        if j not in used_new:
            pairs.append(
                (j, AlignedPair(key, None, b.block_id, PairStatus.ADDED, 0.0, 1.0, "no match"))
            )
    out = [p for _, p in sorted(pairs, key=lambda t: t[0])]
    old_pos = {b.block_id: i for i, b in enumerate(old)}
    for i, b in enumerate(old):
        if i not in matched_old:
            # place removed blocks after the pair of their predecessor in old order
            out_idx = len(out)
            for k, p in enumerate(out):
                if p.old_block_id is not None and old_pos[p.old_block_id] > i:
                    out_idx = k
                    break
            out.insert(
                out_idx,
                AlignedPair(key, b.block_id, None, PairStatus.REMOVED, 0.0, 1.0, "no match"),
            )
    return tuple(out)


def _lis_members(seq: list[int]) -> set[int]:
    """Positions forming one longest increasing subsequence (deterministic)."""
    n = len(seq)
    if n == 0:
        return set()
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for k in range(i):
            if seq[k] < seq[i] and best[k] + 1 > best[i]:
                best[i], prev[i] = best[k] + 1, k
    end = max(range(n), key=lambda i: (best[i], -i))
    members = set()
    while end != -1:
        members.add(end)
        end = prev[end]
    return members


def align_documents(
    old_doc: NormalizedDocument,
    old_sections: SectionParseResult,
    new_doc: NormalizedDocument,
    new_sections: SectionParseResult,
    **kwargs: float,
) -> DocumentAlignment:
    old_blocks = {b.block_id: b for b in old_doc.blocks}
    new_blocks = {b.block_id: b for b in new_doc.blocks}
    keys = [s.key for s in new_sections.sections]
    keys += [s.key for s in old_sections.sections if s.key not in keys]
    out: list[SectionAlignment] = []
    for key in keys:
        o, n = old_sections.get(key), new_sections.get(key)
        notes: list[str] = []
        ob = [old_blocks[i] for i in (o.block_ids if o else ())]
        nb = [new_blocks[i] for i in (n.block_ids if n else ())]
        if o is None or o.status is SectionStatus.MISSING:
            notes.append("section absent in old filing")
        if n is None or n.status is SectionStatus.MISSING:
            notes.append("section absent in new filing")
        out.append(
            SectionAlignment(
                section_key=key,
                pairs=align_blocks(key, ob, nb, **kwargs),
                old_status=o.status.value if o else None,
                new_status=n.status.value if n else None,
                notes=tuple(notes),
            )
        )
    return DocumentAlignment(tuple(out))
