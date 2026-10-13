"""Comparable filing selection policy (``comparator-policy/1``).

- 10-K → prior comparable 10-K (latest earlier fiscal year; ~1-year gap expected).
- 10-Q → same fiscal quarter of the prior fiscal year (default) or, optionally, the
  immediately preceding quarter ("sequential" lens; Q1 has no quarterly sequential comparator
  because fiscal Q4 is reported in the 10-K).
- 8-K → only for earnings events (Item 2.02): the previous earnings 8-K. Otherwise no
  baseline is meaningful and none is returned.

Only filings publicly available at ``as_of`` are eligible (``available_at <= as_of``).

Amendment participation: candidates are grouped by fiscal period. With
``AmendmentPolicy.ORIGINAL`` (default) the original filing of the chosen period is the
comparator; with ``LATEST_AVAILABLE`` the most recent amendment of that period that was
public by ``as_of`` is used (flagged in the reason because amendments may be partial).
A focal amendment is compared against the prior period like its base form; the
amendment-vs-original lens belongs to the lineage engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from filinglens.domain.errors import FilingLensError
from filinglens.domain.models import Filing
from filinglens.domain.time import ensure_utc

POLICY_VERSION = "comparator-policy/1"


class ComparatorMode(StrEnum):
    DEFAULT = "default"
    SEQUENTIAL = "sequential"


class AmendmentPolicy(StrEnum):
    ORIGINAL = "original"
    LATEST_AVAILABLE = "latest_available"


class ComparatorError(FilingLensError):
    pass


@dataclass(frozen=True)
class ComparatorSelection:
    focal_accession: str
    comparator_accession: str | None
    mode: ComparatorMode
    reason: str
    confidence: float
    excluded: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION
    amendment_policy: AmendmentPolicy = AmendmentPolicy.ORIGINAL


def fiscal_quarter(period_end: date, fiscal_year_end: str | None) -> int | None:
    """Fiscal quarter (1-4) of a period end given the issuer FYE ``MMDD``.

    52/53-week calendars end within a week of a month end; dates in the first 7 days of a
    month are attributed to the previous month.
    """
    if not fiscal_year_end or len(fiscal_year_end) != 4:
        return None
    fye_month = int(fiscal_year_end[:2])
    month = period_end.month
    if period_end.day <= 7:
        month = 12 if month == 1 else month - 1
    offset = (month - fye_month) % 12  # 0 == fiscal year end month
    if offset % 3 != 0:
        return None  # not on a quarter boundary: irregular period
    return 4 if offset == 0 else offset // 3


def _days(a: date, b: date) -> int:
    return (a - b).days


@dataclass
class _Ctx:
    excluded: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def select_comparator(
    focal: Filing,
    history: Iterable[Filing],
    *,
    as_of: datetime,
    fiscal_year_end: str | None = None,
    mode: ComparatorMode = ComparatorMode.DEFAULT,
    amendment_policy: AmendmentPolicy = AmendmentPolicy.ORIGINAL,
) -> ComparatorSelection:
    cutoff = ensure_utc(as_of)
    if focal.available_at > cutoff:
        raise ComparatorError(f"focal filing {focal.accession} was not public at as_of")
    ctx = _Ctx()
    eligible: list[Filing] = []
    for f in history:
        if f.accession == focal.accession:
            continue
        if f.available_at > cutoff:
            ctx.excluded.append((f.accession, "not publicly available at as_of"))
            continue
        if f.available_at > focal.available_at and f.report_period == focal.report_period:
            ctx.excluded.append((f.accession, "later filing for the focal period"))
            continue
        eligible.append(f)

    base = focal.base_form
    if base == "10-K":
        result = _annual(focal, eligible, ctx, amendment_policy)
    elif base == "10-Q":
        result = _quarterly(focal, eligible, ctx, fiscal_year_end, mode, amendment_policy)
    elif base == "8-K":
        result = _event(focal, eligible, ctx)
    else:
        raise ComparatorError(f"no comparator policy for form {focal.form}")
    comp, reason, confidence = result
    if focal.is_amendment:
        ctx.notes.append("focal is an amendment; compared to prior period like its base form")
    return ComparatorSelection(
        focal_accession=focal.accession,
        comparator_accession=comp.accession if comp else None,
        mode=mode,
        reason=reason,
        confidence=confidence,
        excluded=tuple(ctx.excluded),
        notes=tuple(ctx.notes),
        amendment_policy=amendment_policy,
    )


def _pick_version(period_filings: list[Filing], policy: AmendmentPolicy, ctx: _Ctx) -> Filing:
    originals = [f for f in period_filings if not f.is_amendment]
    amendments = sorted((f for f in period_filings if f.is_amendment), key=lambda f: f.available_at)
    if policy is AmendmentPolicy.LATEST_AVAILABLE and amendments:
        ctx.notes.append(
            f"using amendment {amendments[-1].accession} (may be partial; verify scope)"
        )
        return amendments[-1]
    if originals:
        if amendments:
            ctx.notes.append(
                f"comparator period also has amendment(s): {[a.accession for a in amendments]}"
            )
        return sorted(originals, key=lambda f: f.available_at)[0]
    ctx.notes.append("only an amendment is available for the comparator period")
    return amendments[-1]


def _by_period(filings: list[Filing]) -> dict[date, list[Filing]]:
    out: dict[date, list[Filing]] = {}
    for f in filings:
        if f.report_period is not None:
            out.setdefault(f.report_period, []).append(f)
    return out


def _annual(
    focal: Filing, eligible: list[Filing], ctx: _Ctx, policy: AmendmentPolicy
) -> tuple[Filing | None, str, float]:
    if focal.report_period is None:
        return None, "focal 10-K has no report period", 0.0
    groups = _by_period([f for f in eligible if f.base_form == "10-K"])
    earlier = sorted(p for p in groups if p < focal.report_period)
    if not earlier:
        return None, "no earlier 10-K available at as_of", 0.0
    period = earlier[-1]
    comp = _pick_version(groups[period], policy, ctx)
    gap = _days(focal.report_period, period)
    if 350 <= gap <= 380:
        return comp, f"prior fiscal-year 10-K (period {period}, gap {gap} days)", 1.0
    if 300 <= gap <= 430:
        ctx.notes.append("fiscal-year length irregular (52/53-week or transition period)")
        return comp, f"prior 10-K with irregular gap of {gap} days", 0.8
    ctx.notes.append("prior fiscal year 10-K missing or fiscal year changed")
    return comp, f"most recent earlier 10-K is {gap} days earlier (not one year)", 0.5


def _quarterly(
    focal: Filing,
    eligible: list[Filing],
    ctx: _Ctx,
    fye: str | None,
    mode: ComparatorMode,
    policy: AmendmentPolicy,
) -> tuple[Filing | None, str, float]:
    if focal.report_period is None:
        return None, "focal 10-Q has no report period", 0.0
    fq = fiscal_quarter(focal.report_period, fye)
    groups = _by_period([f for f in eligible if f.base_form == "10-Q"])
    if mode is ComparatorMode.SEQUENTIAL:
        if fq == 1:
            return (
                None,
                "sequential prior quarter is fiscal Q4, reported only in the 10-K",
                0.0,
            )
        cands = [p for p in groups if 75 <= _days(focal.report_period, p) <= 105]
        if not cands:
            return None, "no immediately preceding 10-Q available at as_of", 0.0
        period = max(cands)
        comp = _pick_version(groups[period], policy, ctx)
        return comp, f"sequential prior quarter 10-Q (period {period})", 0.9
    cands = [p for p in groups if 350 <= _days(focal.report_period, p) <= 380]
    if fq is not None:
        same_q = [p for p in cands if fiscal_quarter(p, fye) == fq]
        if cands and not same_q:
            ctx.notes.append("prior-year candidates are in a different fiscal quarter")
        cands = same_q
    else:
        ctx.notes.append("fiscal quarter unknown (no fiscal year end); matched by ~1-year gap")
    if not cands:
        return None, "same fiscal quarter of prior year not available at as_of", 0.0
    period = max(cands)
    comp = _pick_version(groups[period], policy, ctx)
    q = f"Q{fq}" if fq else "quarter"
    confidence = 1.0 if fq is not None else 0.7
    return comp, f"same fiscal {q} prior year 10-Q (period {period})", confidence


def _event(focal: Filing, eligible: list[Filing], ctx: _Ctx) -> tuple[Filing | None, str, float]:
    items = set(focal.items or ())
    if "2.02" not in items:
        return None, "8-K event has no meaningful prior baseline (non-earnings item)", 1.0
    prior = [
        f
        for f in eligible
        if f.base_form == "8-K"
        and "2.02" in set(f.items or ())
        and f.available_at < focal.available_at
    ]
    if not prior:
        return None, "no earlier earnings 8-K available at as_of", 0.0
    comp = max(prior, key=lambda f: f.available_at)
    return comp, "previous earnings release 8-K (Item 2.02)", 0.9
