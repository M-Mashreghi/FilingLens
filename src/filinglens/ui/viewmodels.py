"""View models for the Streamlit dashboard (``ui/1``).

Pure functions over the composition root that shape data for display. They contain no
parsing, SEC access or financial calculations — those live in the application/analytics
layers. Keeping them free of Streamlit makes the UI behaviour unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from filinglens.bootstrap import Container
from filinglens.domain.models import RunState

UI_VERSION = "ui/1"
ATTENTION_STATES = {RunState.FAILED, RunState.NEEDS_INPUT, RunState.BLOCKED_BY_SOURCE}


@dataclass(frozen=True)
class IssuerChoice:
    cik: int
    label: str
    match_type: str
    score: float


@dataclass(frozen=True)
class SearchView:
    status: str  # resolved | ambiguous | not_found | unavailable
    message: str
    choices: tuple[IssuerChoice, ...]


def search_issuers(c: Container, query: str) -> SearchView:
    resolver = c.resolver
    if resolver is None:
        return SearchView("unavailable", "Issuer reference data is not loaded.", ())
    r = resolver.resolve(query)
    choices = tuple(
        IssuerChoice(
            x.cik,
            f"{x.name or 'CIK ' + str(x.cik)} ({', '.join(x.tickers) or 'no ticker'}) "
            f"— CIK {x.cik}",
            x.match_type,
            x.score,
        )
        for x in r.candidates
    )
    if r.status.value == "resolved":
        msg = f"Resolved to CIK {r.cik} ({r.reason})."
    elif r.status.value == "ambiguous":
        msg = f"Ambiguous: {len(choices)} candidates ({r.reason}). Choose the intended filer."
    else:
        msg = "No matching SEC filer found."
    return SearchView(r.status.value, msg, choices)


@dataclass(frozen=True)
class TimelineRow:
    accession: str
    form: str
    filing_date: str
    report_period: str | None
    available_at: str
    amendment: bool
    items: str


def filing_timeline(
    c: Container, cik: int, *, forms: set[str] | None = None, as_of: datetime | None = None
) -> list[TimelineRow]:
    rows = (
        c.metadata.filings_as_of(cik, as_of, forms=forms)
        if as_of
        else c.repos.filings.list_for_cik(cik)
    )
    if forms and not as_of:
        wanted = {f.upper() for f in forms}
        rows = [f for f in rows if f.form in wanted]
    rows = sorted(rows, key=lambda f: (f.available_at, f.accession), reverse=True)
    return [
        TimelineRow(
            f.accession,
            f.form,
            f.filing_date.isoformat(),
            f.report_period.isoformat() if f.report_period else None,
            f.available_at.isoformat(),
            f.is_amendment,
            ",".join(f.items or ()),
        )
        for f in rows
    ]


@dataclass(frozen=True)
class RunRow:
    run_id: str
    state: str
    needs_attention: bool
    message: str
    focal: str | None
    comparator: str | None
    as_of: str


def run_rows(c: Container, cik: int | None = None) -> list[RunRow]:
    runs = [r for r in c.repos.runs.list() if cik is None or r.cik == cik]
    runs.sort(key=lambda r: r.created_at, reverse=True)
    out = []
    for r in runs:
        attention = r.state in ATTENTION_STATES
        msg = r.error or ("Waiting for analyst input." if r.state is RunState.NEEDS_INPUT else "")
        if r.state is RunState.BLOCKED_BY_SOURCE and not msg:
            msg = "Blocked by an SEC source problem."
        out.append(
            RunRow(
                r.run_id,
                r.state.value,
                attention,
                msg,
                r.focal_accession,
                r.comparator_accession,
                r.as_of.isoformat(),
            )
        )
    return out


@dataclass(frozen=True)
class EvidenceView:
    evidence_id: str
    accession: str
    section: str | None
    locator: str
    text: str
    missing: bool = False


@dataclass(frozen=True)
class FindingView:
    finding_id: str
    finding_type: str
    severity: str
    confidence: float
    status: str
    summary: str
    why_it_matters: str | None
    evidence: tuple[EvidenceView, ...]


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def top_findings(
    c: Container,
    run_id: str,
    *,
    finding_types: set[str] | None = None,
    sections: set[str] | None = None,
    min_severity: str | None = None,
    limit: int = 20,
) -> list[FindingView]:
    views: list[FindingView] = []
    for f in c.repos.findings.list_for_run(run_id):
        if finding_types and f.finding_type not in finding_types:
            continue
        if min_severity and _SEVERITY_ORDER.get(f.severity, 9) > _SEVERITY_ORDER[min_severity]:
            continue
        ev: list[EvidenceView] = []
        for eid in f.evidence_ids:
            span = c.repos.evidence.get(eid)
            if span is None or span.run_id != run_id:
                ev.append(EvidenceView(eid, "", None, "", "", missing=True))
            else:
                ev.append(EvidenceView(eid, span.accession, span.section, span.locator, span.text))
        if sections and not any(e.section in sections for e in ev):
            continue
        views.append(
            FindingView(
                f.finding_id,
                f.finding_type,
                f.severity,
                f.confidence,
                f.interpretation_status,
                f.summary,
                f.why_it_matters,
                tuple(ev),
            )
        )
    views.sort(key=lambda v: (_SEVERITY_ORDER.get(v.severity, 9), -v.confidence, v.finding_id))
    return views[:limit]
