"""Cited filing intelligence report (``report/1``) with reviewer-gated finalization.

The report is a deterministic rendering of stored ``ReportInputs``: re-rendering the inputs
stored for a run reproduces the identical Markdown (``inputs_hash`` is printed in the
header). Only *accepted* findings appear as claims and every claim carries citations that
resolve in the evidence appendix. A reviewer must approve before a report is finalized.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol

from filinglens.application.ports import ReportRepository
from filinglens.domain.errors import FilingLensError
from filinglens.domain.models import Report

REPORT_VERSION = "report/1"
DISCLAIMER = (
    "Research Priority Scores rank analyst attention only; they are not legal materiality "
    "determinations. Findings are research observations, not allegations."
)


@dataclass(frozen=True)
class CitedEvidence:
    evidence_id: str
    kind: str
    accession: str | None
    document: str | None
    section: str | None
    locator: str | None
    excerpt: str


@dataclass(frozen=True)
class ReportFinding:
    finding_type: str
    section: str | None
    summary: str
    why_it_matters: str
    severity: str
    priority_score: float | None
    citations: tuple[str, ...]
    category: str  # risk_factors | mdna_liquidity | eight_k | xbrl | other
    open_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportInputs:
    run_id: str
    issuer_name: str
    cik: int
    focal_accession: str
    focal_form: str
    comparator_accession: str | None
    comparator_reason: str | None
    as_of: str
    findings: tuple[ReportFinding, ...]
    evidence: tuple[CitedEvidence, ...]
    blocked_findings: int = 0
    eight_k_events: tuple[str, ...] = ()
    lineage_notes: tuple[str, ...] = ()
    xbrl_signals: tuple[str, ...] = ()
    tensions: tuple[str, ...] = ()
    component_versions: dict[str, str] = field(default_factory=dict)
    snapshot_ids: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> ReportInputs:
        d = json.loads(text)
        d["findings"] = tuple(
            ReportFinding(
                **{
                    **f,
                    "citations": tuple(f["citations"]),
                    "open_questions": tuple(f["open_questions"]),
                }
            )
            for f in d["findings"]
        )
        d["evidence"] = tuple(CitedEvidence(**e) for e in d["evidence"])
        for k in ("eight_k_events", "lineage_notes", "xbrl_signals", "tensions", "snapshot_ids"):
            d[k] = tuple(d[k])
        return cls(**d)

    @property
    def inputs_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()


class ReportIntegrityError(FilingLensError):
    pass


def integrity_problems(inputs: ReportInputs) -> list[str]:
    ids = {e.evidence_id for e in inputs.evidence}
    problems = []
    for f in inputs.findings:
        if not f.citations:
            problems.append(f"finding '{f.finding_type}' has no citations")
        missing = [c for c in f.citations if c not in ids]
        if missing:
            problems.append(f"finding '{f.finding_type}' cites unknown evidence {missing}")
    return problems


def _cite(ids: Sequence[str]) -> str:
    return " ".join(f"[{i}]" for i in ids)


def _finding_lines(fs: Sequence[ReportFinding]) -> list[str]:
    lines = []
    for f in fs:
        score = f"{f.priority_score:.1f}" if f.priority_score is not None else "n/a"
        lines.append(
            f"- **{f.finding_type}** ({f.severity}; priority {score}): "
            f"{f.summary} {_cite(f.citations)}"
        )
        if f.why_it_matters:
            lines.append(f"  - Why it matters: {f.why_it_matters}")
    return lines or ["- No accepted findings in this category."]


def render_report(inputs: ReportInputs) -> str:
    problems = integrity_problems(inputs)
    if problems:
        raise ReportIntegrityError("; ".join(problems))
    ranked = sorted(inputs.findings, key=lambda f: (-(f.priority_score or 0.0), f.finding_type))
    by_cat = {
        c: [f for f in ranked if f.category == c]
        for c in ("risk_factors", "mdna_liquidity", "eight_k", "xbrl", "other")
    }
    out: list[str] = [
        f"# FilingLens Report — {inputs.issuer_name} ({inputs.focal_form})",
        "",
        f"`{REPORT_VERSION}` · run `{inputs.run_id}` · inputs `{inputs.inputs_hash[:16]}`",
        "",
        "## Filing metadata",
        f"- CIK: {inputs.cik}",
        f"- Focal accession: {inputs.focal_accession} ({inputs.focal_form})",
        f"- Comparator: {inputs.comparator_accession or 'none'} — "
        f"{inputs.comparator_reason or 'n/a'}",
        f"- As of: {inputs.as_of}",
        "",
        "## Executive change summary",
    ]
    out += [f"- {f.summary} {_cite(f.citations)}" for f in ranked[:3]] or [
        "- No accepted findings."
    ]
    out += ["", "## Top ranked findings", *_finding_lines(ranked[:10])]
    out += ["", "## Risk Factors", *_finding_lines(by_cat["risk_factors"])]
    out += ["", "## MD&A and liquidity", *_finding_lines(by_cat["mdna_liquidity"])]
    out += ["", "## 8-K events and exhibits", *_finding_lines(by_cat["eight_k"])]
    out += [f"- {e}" for e in inputs.eight_k_events]
    out += ["", "## Amendment / restatement notes"]
    out += [f"- {n}" for n in inputs.lineage_notes] or ["- None identified."]
    out += ["", "## XBRL signals", *_finding_lines(by_cat["xbrl"])]
    out += [f"- {s}" for s in inputs.xbrl_signals]
    out += ["", "## Narrative ↔ numeric tensions"]
    out += [f"- {t}" for t in inputs.tensions] or ["- None identified."]
    out += ["", "## Other findings", *_finding_lines(by_cat["other"])]
    out += ["", "## Open questions"]
    qs = [q for f in ranked for q in f.open_questions]
    out += [f"- {q}" for q in qs] or ["- None."]
    out += [
        "",
        "## Methodology",
        f"- {DISCLAIMER}",
        f"- {inputs.blocked_findings} model finding(s) were blocked by evidence validation "
        "and are not shown.",
        "- Component versions:",
    ]
    out += [f"  - {k}: {v}" for k, v in sorted(inputs.component_versions.items())]
    out += [f"- Source snapshots: {', '.join(s[:12] for s in inputs.snapshot_ids) or 'n/a'}"]
    out += ["", "## Evidence appendix"]
    for e in sorted(inputs.evidence, key=lambda e: e.evidence_id):
        where = " · ".join(x for x in (e.accession, e.document, e.section, e.locator) if x)
        out.append(f"- **[{e.evidence_id}]** ({e.kind}) {where}: {e.excerpt}")
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class ReviewDecision:
    reviewer: str
    approved: bool
    notes: str = ""


class FinalizationBlocked(FilingLensError):
    pass


class ReportInputsStore(Protocol):
    def save(self, run_id: str, inputs_json: str, inputs_hash: str) -> None: ...
    def get(self, run_id: str) -> str | None: ...


class ReportService:
    """Persists report inputs per run and finalizes reviewer-approved reports."""

    def __init__(self, reports: ReportRepository, inputs_store: ReportInputsStore) -> None:
        self.reports = reports
        self.inputs_store = inputs_store

    def stage(self, inputs: ReportInputs) -> str:
        body = render_report(inputs)
        self.inputs_store.save(inputs.run_id, inputs.to_json(), inputs.inputs_hash)
        return body

    def regenerate(self, run_id: str) -> str:
        """Reproduce the report for a run purely from its stored inputs."""
        raw = self.inputs_store.get(run_id)
        if raw is None:
            raise FilingLensError(f"no stored report inputs for run {run_id}")
        return render_report(ReportInputs.from_json(raw))

    def finalize(
        self, inputs: ReportInputs, decision: ReviewDecision, *, created_at: datetime
    ) -> Report:
        if not decision.approved:
            raise FinalizationBlocked(
                f"reviewer {decision.reviewer} blocked finalization: {decision.notes}"
            )
        problems = integrity_problems(inputs)
        if problems:
            raise FinalizationBlocked("; ".join(problems))
        body = render_report(inputs)
        latest = self.reports.latest_for_run(inputs.run_id)
        version = (latest.version + 1) if latest else 1
        report = Report(
            report_id=f"rep-{inputs.run_id}-v{version}",
            run_id=inputs.run_id,
            version=version,
            body_markdown=body,
            created_at=created_at,
        )
        self.inputs_store.save(inputs.run_id, inputs.to_json(), inputs.inputs_hash)
        return self.reports.add(report)
