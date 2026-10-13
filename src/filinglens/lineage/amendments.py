"""Amendment, restatement and filing lineage engine (``lineage/1``).

Originals and amendments are always separate filings. This module links amendments to their
likely base filing, exposes original / amended / effective views as of a timestamp, isolates
amendment-only content and surfaces 8-K non-reliance (Item 4.02) events. Terminology is
neutral: "amendment", "restatement-related", "non-reliance" — never allegations.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from filinglens.domain.models import Filing
from filinglens.domain.time import ensure_utc
from filinglens.parsing.normalizer import Block, NormalizedDocument

LINEAGE_VERSION = "lineage/1"


@dataclass(frozen=True)
class AmendmentLink:
    amendment_accession: str
    base_accession: str | None
    confidence: float
    reason: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestatementSignal:
    accession: str
    signal: str  # "non_reliance_8k" | "amendment_filed"
    available_at: datetime
    description: str
    related_accession: str | None = None


@dataclass(frozen=True)
class PeriodLineage:
    """All versions of one fiscal-period report, as visible at ``as_of``."""

    original: Filing | None
    amendments: tuple[Filing, ...]
    as_of: datetime

    @property
    def effective(self) -> Filing | None:
        """Latest public version (amendment if any, else original). Amendments may be
        partial; consumers must not treat an amendment as a full replacement blindly."""
        return self.amendments[-1] if self.amendments else self.original

    @property
    def is_amended(self) -> bool:
        return bool(self.amendments)


def link_amendment(amendment: Filing, history: Iterable[Filing]) -> AmendmentLink:
    if not amendment.is_amendment:
        raise ValueError(f"{amendment.accession} is not an amendment")
    hist = list(history)
    if amendment.amends_accession:
        known = {f.accession for f in hist}
        conf = 1.0 if amendment.amends_accession in known else 0.9
        return AmendmentLink(
            amendment.accession, amendment.amends_accession, conf, "explicit amends_accession"
        )
    base_form = amendment.base_form
    originals = [
        f
        for f in hist
        if f.cik == amendment.cik
        and f.form == base_form
        and not f.is_amendment
        and f.available_at <= amendment.available_at
    ]
    same_period = [
        f
        for f in originals
        if amendment.report_period is not None and f.report_period == amendment.report_period
    ]
    if len(same_period) == 1:
        return AmendmentLink(
            amendment.accession,
            same_period[0].accession,
            0.95,
            "unique original with the same form and report period",
        )
    if len(same_period) > 1:
        ranked = sorted(same_period, key=lambda f: f.available_at, reverse=True)
        return AmendmentLink(
            amendment.accession,
            None,
            0.3,
            "multiple originals share the report period; not linked automatically",
            tuple(f.accession for f in ranked),
        )
    if amendment.report_period is None and originals:
        latest = max(originals, key=lambda f: f.available_at)
        return AmendmentLink(
            amendment.accession,
            latest.accession,
            0.5,
            "amendment has no report period; linked to latest prior original of same form",
            (latest.accession,),
        )
    return AmendmentLink(amendment.accession, None, 0.0, "no matching original filing found")


class LineageEngine:
    def __init__(self, filings: Iterable[Filing]) -> None:
        self.filings = sorted(filings, key=lambda f: (f.available_at, f.accession))
        self.links = {
            f.accession: link_amendment(f, self.filings) for f in self.filings if f.is_amendment
        }

    def visible(self, as_of: datetime) -> list[Filing]:
        cutoff = ensure_utc(as_of)
        return [f for f in self.filings if f.available_at <= cutoff]

    def period(self, base_accession: str, as_of: datetime) -> PeriodLineage:
        """Original + amendments of the period of ``base_accession``, as public at ``as_of``."""
        cutoff = ensure_utc(as_of)
        by_acc = {f.accession: f for f in self.filings}
        original = by_acc.get(base_accession)
        amends = tuple(
            by_acc[a]
            for a, link in self.links.items()
            if link.base_accession == base_accession and by_acc[a].available_at <= cutoff
        )
        visible_original = original if original and original.available_at <= cutoff else None
        return PeriodLineage(
            original=visible_original,
            amendments=tuple(sorted(amends, key=lambda f: f.available_at)),
            as_of=cutoff,
        )

    def restatement_signals(self, as_of: datetime) -> list[RestatementSignal]:
        signals: list[RestatementSignal] = []
        for f in self.visible(as_of):
            if f.base_form == "8-K" and "4.02" in (f.items or ()):
                signals.append(
                    RestatementSignal(
                        f.accession,
                        "non_reliance_8k",
                        f.available_at,
                        "8-K Item 4.02: previously issued financial statements should no "
                        "longer be relied upon (restatement-related disclosure)",
                    )
                )
            elif f.is_amendment and f.base_form in ("10-K", "10-Q"):
                link = self.links[f.accession]
                signals.append(
                    RestatementSignal(
                        f.accession,
                        "amendment_filed",
                        f.available_at,
                        f"{f.form} filed (amendment; scope must be verified)",
                        link.base_accession,
                    )
                )
        return signals


@dataclass(frozen=True)
class AmendmentDelta:
    added: tuple[Block, ...]
    removed: tuple[Block, ...]
    unchanged_count: int

    @property
    def amendment_only_text(self) -> str:
        return "\n".join(b.text for b in self.added)


def amendment_only_changes(
    base: NormalizedDocument, amendment: NormalizedDocument
) -> AmendmentDelta:
    """Blocks present only in the amendment (and only in the base), by content fingerprint."""
    base_fp = {b.fingerprint for b in base.blocks}
    amend_fp = {b.fingerprint for b in amendment.blocks}
    added = tuple(b for b in amendment.blocks if b.fingerprint not in base_fp)
    removed = tuple(b for b in base.blocks if b.fingerprint not in amend_fp)
    unchanged = sum(1 for b in amendment.blocks if b.fingerprint in base_fp)
    return AmendmentDelta(added, removed, unchanged)
