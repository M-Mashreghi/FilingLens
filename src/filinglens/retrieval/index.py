"""Section-aware retrieval index (``retrieval/1``).

Chunks follow filing document → SEC item/section → block boundaries; a chunk never spans two
sections or two documents. Every chunk carries accession, document, section, block ids,
locators, snapshot hash and public-availability time.

Hard filters (accession set, optional section/document/doc-type, ``as_of``) are applied
*before* scoring, so a bounded query can never return evidence from outside its filter.
The default scorer is a deterministic BM25 lexical baseline; embedding scorers plug in
through the :class:`Scorer` protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from filinglens.domain.errors import FilingLensError
from filinglens.domain.time import ensure_utc
from filinglens.parsing.normalizer import NormalizedDocument
from filinglens.parsing.sections import SectionParseResult

INDEX_VERSION = "retrieval/1"
_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "we",
    ]
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    accession: str
    filename: str
    doc_type: str | None
    section_key: str | None
    block_ids: tuple[str, ...]
    locators: tuple[str, ...]
    snapshot_id: str | None
    available_at: datetime
    text: str


@dataclass(frozen=True)
class RetrievalFilter:
    accessions: frozenset[str]
    as_of: datetime
    sections: frozenset[str] | None = None
    filenames: frozenset[str] | None = None
    doc_types: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.accessions:
            raise RetrievalError("bounded retrieval requires at least one accession")
        object.__setattr__(self, "as_of", ensure_utc(self.as_of))

    def allows(self, c: Chunk) -> bool:
        return (
            c.accession in self.accessions
            and c.available_at <= self.as_of
            and (self.sections is None or c.section_key in self.sections)
            and (self.filenames is None or c.filename in self.filenames)
            and (self.doc_types is None or (c.doc_type or "") in self.doc_types)
        )


class RetrievalError(FilingLensError):
    pass


class Scorer(Protocol):
    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]: ...


class BM25Scorer:
    """Okapi BM25 over the candidate set only (statistics never leak across filters)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b

    def score(self, query: str, chunks: Sequence[Chunk]) -> list[float]:
        docs = [tokenize(c.text) for c in chunks]
        if not docs:
            return []
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / n or 1.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        q = tokenize(query)
        scores: list[float] = []
        for d in docs:
            tf = Counter(d)
            s = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                f = tf[term]
                s += (
                    idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / avgdl))
                )
            scores.append(round(s, 6))
        return scores


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int


@dataclass
class RetrievalIndex:
    scorer: Scorer = field(default_factory=BM25Scorer)
    max_chunk_chars: int = 1500
    chunks: list[Chunk] = field(default_factory=list)
    version: str = INDEX_VERSION

    def add_document(
        self,
        doc: NormalizedDocument,
        *,
        accession: str,
        filename: str,
        available_at: datetime,
        doc_type: str | None = None,
        sections: SectionParseResult | None = None,
    ) -> list[Chunk]:
        section_of: dict[str, str] = {}
        if sections is not None:
            for s in sections.sections:
                for bid in s.block_ids:
                    section_of[bid] = s.key
        added: list[Chunk] = []
        buf: list[tuple[str, str, str]] = []  # (block_id, locator, text)
        cur_section: str | None = None

        def flush() -> None:
            if not buf:
                return
            text = "\n".join(t for _, _, t in buf)
            ids = tuple(b for b, _, _ in buf)
            cid = (
                "chk-"
                + hashlib.sha256(f"{accession}|{filename}|{'|'.join(ids)}".encode()).hexdigest()[
                    :16
                ]
            )
            chunk = Chunk(
                cid,
                accession,
                filename,
                doc_type,
                cur_section,
                ids,
                tuple(loc for _, loc, _ in buf),
                doc.snapshot_id,
                ensure_utc(available_at),
                text,
            )
            added.append(chunk)
            buf.clear()

        for b in doc.blocks:
            sec = section_of.get(b.block_id) if sections is not None else None
            size = sum(len(t) for _, _, t in buf)
            if buf and (sec != cur_section or size + len(b.text) > self.max_chunk_chars):
                flush()
            cur_section = sec
            buf.append((b.block_id, b.locator, b.text))
        flush()
        self.chunks.extend(added)
        return added

    def search(self, query: str, flt: RetrievalFilter, *, k: int = 5) -> list[Hit]:
        candidates = [c for c in self.chunks if flt.allows(c)]
        if not candidates:
            return []
        scores = self.scorer.score(query, candidates)
        ranked = sorted(
            zip(candidates, scores, strict=True), key=lambda cs: (-cs[1], cs[0].chunk_id)
        )
        hits = [Hit(c, s, i + 1) for i, (c, s) in enumerate(ranked) if s > 0][:k]
        for h in hits:  # defence in depth: never return out-of-filter evidence
            if not flt.allows(h.chunk):
                raise RetrievalError(f"chunk {h.chunk.chunk_id} escaped retrieval filter")
        return hits

    def by_accession(self, accessions: Iterable[str]) -> list[Chunk]:
        wanted = set(accessions)
        return [c for c in self.chunks if c.accession in wanted]
