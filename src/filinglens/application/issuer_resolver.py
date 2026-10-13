"""Resolve ticker / CIK / exchange+ticker / company name / alias into a canonical SEC filer.

The resolver never silently guesses: fuzzy or multi-CIK matches are returned as ``ambiguous``
with ranked candidates. A ticker is only an alias observed in a specific reference-data
version; the durable identity is always the CIK.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum

from filinglens.domain.identifiers import looks_like_cik, parse_cik
from filinglens.domain.time import ensure_utc, utcnow


@dataclass(frozen=True, kw_only=True)
class TickerRecord:
    cik: int
    name: str
    ticker: str
    exchange: str | None = None


@dataclass(frozen=True, kw_only=True)
class ReferenceDataset:
    """A versioned snapshot of SEC ticker/exchange reference data."""

    records: tuple[TickerRecord, ...]
    source: str
    source_version: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "retrieved_at", ensure_utc(self.retrieved_at))


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, kw_only=True)
class Candidate:
    cik: int
    name: str
    tickers: tuple[str, ...]
    score: float
    match_type: str  # cik | ticker | exchange_ticker | name_exact | alias | name_fuzzy


@dataclass(frozen=True, kw_only=True)
class Resolution:
    query: str
    status: ResolutionStatus
    candidates: tuple[Candidate, ...]
    source: str
    source_version: str
    retrieved_at: datetime
    reason: str

    @property
    def cik(self) -> int | None:
        if self.status is ResolutionStatus.RESOLVED:
            return self.candidates[0].cik
        return None


_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "sa",
    "nv",
    "ag",
    "the",
}


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.casefold().replace("&", " and ")
    # Collapse dotted abbreviations ("s.a." -> "sa", "u.s." -> "us").
    text = re.sub(r"\b(?:[a-z]\.){2,}", lambda m: m.group(0).replace(".", ""), text)
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    return " ".join(tokens)


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


_EXCHANGE_TICKER = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:\s*([A-Za-z0-9.\-]+)\s*$")


@dataclass
class IssuerResolver:
    dataset: ReferenceDataset
    aliases: Mapping[str, int] = field(default_factory=dict)
    fuzzy_threshold: float = 0.6
    max_candidates: int = 5

    def __post_init__(self) -> None:
        self._by_cik: dict[int, list[TickerRecord]] = {}
        self._by_ticker: dict[str, list[TickerRecord]] = {}
        self._by_name: dict[str, set[int]] = {}
        for r in self.dataset.records:
            self._by_cik.setdefault(r.cik, []).append(r)
            self._by_ticker.setdefault(normalize_ticker(r.ticker), []).append(r)
            self._by_name.setdefault(normalize_name(r.name), set()).add(r.cik)
        self._aliases: dict[str, set[int]] = {}
        for alias, cik in self.aliases.items():
            self._aliases.setdefault(normalize_name(alias), set()).add(cik)

    def resolve(self, query: str) -> Resolution:
        q = query.strip()
        if not q:
            return self._result(query, [], "empty query")
        if looks_like_cik(q):
            return self._resolve_cik(query, parse_cik(q))
        m = _EXCHANGE_TICKER.match(q)
        if m:
            return self._resolve_exchange_ticker(query, m.group(1), m.group(2))
        ticker_hits = self._by_ticker.get(normalize_ticker(q), [])
        if ticker_hits:
            ciks = sorted({r.cik for r in ticker_hits})
            return self._result(
                query,
                [self._candidate(c, 1.0, "ticker") for c in ciks],
                "ticker match" if len(ciks) == 1 else "ticker maps to multiple CIKs",
            )
        return self._resolve_name(query, q)

    def _resolve_cik(self, query: str, cik: int) -> Resolution:
        if cik in self._by_cik:
            return self._result(query, [self._candidate(cik, 1.0, "cik")], "CIK match")
        # A valid CIK may have no listed ticker (e.g. delisted / debt-only filers).
        cand = Candidate(cik=cik, name="", tickers=(), score=1.0, match_type="cik")
        return self._result(query, [cand], "CIK not present in ticker reference data")

    def _resolve_exchange_ticker(self, query: str, exchange: str, ticker: str) -> Resolution:
        hits = [
            r
            for r in self._by_ticker.get(normalize_ticker(ticker), [])
            if (r.exchange or "").casefold() == exchange.strip().casefold()
        ]
        ciks = sorted({r.cik for r in hits})
        return self._result(
            query,
            [self._candidate(c, 1.0, "exchange_ticker") for c in ciks],
            "exchange+ticker match" if hits else "no listing for exchange+ticker",
        )

    def _resolve_name(self, query: str, q: str) -> Resolution:
        norm = normalize_name(q)
        exact = self._by_name.get(norm, set())
        alias = self._aliases.get(norm, set())
        if exact or alias:
            cands = [self._candidate(c, 1.0, "name_exact") for c in sorted(exact)]
            cands += [self._candidate(c, 0.95, "alias") for c in sorted(alias - exact)]
            reason = "exact normalized name" if exact else "alias match"
            if len({c.cik for c in cands}) > 1:
                reason = "name/alias maps to multiple CIKs"
            return self._result(query, cands, reason)
        scored: list[tuple[float, int]] = []
        for name, ciks in list(self._by_name.items()) + list(self._aliases.items()):
            score = _similarity(norm, name)
            if score >= self.fuzzy_threshold:
                scored.extend((score, c) for c in ciks)
        best: dict[int, float] = {}
        for score, cik in scored:
            best[cik] = max(score, best.get(cik, 0.0))
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[: self.max_candidates]
        cands = [self._candidate(c, round(s, 4), "name_fuzzy") for c, s in ranked]
        # Fuzzy matches are never auto-resolved, even when only one candidate exists.
        return self._result(query, cands, "fuzzy name candidates", force_ambiguous=True)

    def _candidate(self, cik: int, score: float, match_type: str) -> Candidate:
        records = self._by_cik.get(cik, [])
        name = records[0].name if records else ""
        tickers = tuple(sorted({r.ticker.upper() for r in records}))
        return Candidate(cik=cik, name=name, tickers=tickers, score=score, match_type=match_type)

    def _result(
        self,
        query: str,
        candidates: Iterable[Candidate],
        reason: str,
        *,
        force_ambiguous: bool = False,
    ) -> Resolution:
        cands = tuple(sorted(candidates, key=lambda c: (-c.score, c.cik)))
        if not cands:
            status = ResolutionStatus.NOT_FOUND
        elif len({c.cik for c in cands}) == 1 and not force_ambiguous:
            status = ResolutionStatus.RESOLVED
        else:
            status = ResolutionStatus.AMBIGUOUS
        return Resolution(
            query=query,
            status=status,
            candidates=cands,
            source=self.dataset.source,
            source_version=self.dataset.source_version,
            retrieved_at=self.dataset.retrieved_at,
            reason=reason,
        )


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb)
    prefix = 1.0 if b.startswith(a) or a.startswith(b) else 0.0
    return max(ratio, jaccard, 0.85 * prefix)


class ReferenceDataCache:
    """Caches the latest reference dataset with its retrieval timestamp/version.

    ``loader`` is typically an SEC reference adapter routed through the governed SEC client.
    """

    def __init__(
        self,
        loader: Callable[[], ReferenceDataset],
        ttl: timedelta = timedelta(hours=24),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._loader = loader
        self._ttl = ttl
        self._clock = clock
        self._dataset: ReferenceDataset | None = None
        self.history: list[tuple[str, datetime]] = []

    def get(self, *, force_refresh: bool = False) -> ReferenceDataset:
        now = self._clock()
        if force_refresh or self._dataset is None or now - self._dataset.retrieved_at >= self._ttl:
            self._dataset = self._loader()
            self.history.append((self._dataset.source_version, self._dataset.retrieved_at))
        return self._dataset
