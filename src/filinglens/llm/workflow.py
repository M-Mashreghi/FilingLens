"""Filing change analyst workflow (``analyst-workflow/1``).

Combines deterministic text/exhibit changes, financial metrics, watch signals,
reconciliations and lineage notes into bounded, id-addressed evidence, asks the structured
LLM client for a first-pass analyst view and post-processes every finding:

- each finding records its *basis*: ``source_fact`` (filing text/exhibits), ``deterministic_
  calculation`` (Python metrics/signals) — the model text itself is ``model_interpretation``;
- findings citing no valid evidence become ``not_supported``;
- numbers in a finding must appear in the cited evidence, otherwise it is downgraded to
  ``uncertain`` with ``numeric_unverified`` (the model never introduces numbers);
- "management said/states/expects" claims require cited filing text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from filinglens.analytics.reconciliation import Reconciliation
from filinglens.analytics.trends import MetricResult, MetricStatus
from filinglens.analytics.watch_signals import WatchSignal, WatchStatus
from filinglens.diff.detectors import ChangeSignal
from filinglens.llm.client import (
    CallMetadata,
    CallStatus,
    EvidenceItem,
    FindingOutput,
    InterpretationStatus,
    StructuredLLMClient,
)

WORKFLOW_VERSION = "analyst-workflow/1"

TASKS = {
    "10-K": "Compare this annual report with its comparator. Identify research-relevant changes "
    "in risk factors, MD&A, liquidity and financial signals.",
    "10-Q": "Compare this quarterly report with the same quarter of the prior year. Identify "
    "research-relevant changes in MD&A, liquidity, risk factors and financial signals.",
    "8-K": "Summarize the reported events and furnished exhibits. Do not infer conclusions "
    "beyond the event text and exhibits.",
    "amendment": "Explain what this amendment changes relative to the original filing and "
    "whether restatement-related disclosures are present.",
}


class Basis(StrEnum):
    SOURCE_FACT = "source_fact"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"


_KIND_BASIS = {
    "text_change": Basis.SOURCE_FACT,
    "exhibit_text": Basis.SOURCE_FACT,
    "lineage": Basis.SOURCE_FACT,
    "xbrl_metric": Basis.DETERMINISTIC_CALCULATION,
    "watch_signal": Basis.DETERMINISTIC_CALCULATION,
    "reconciliation": Basis.DETERMINISTIC_CALCULATION,
}
_NUMBER = re.compile(
    r"(?<![\w.])[-$(]*\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|bps)?", re.I
)
_MGMT = re.compile(r"\bmanagement (said|says|stated|states|expects|believes|noted)\b", re.I)


@dataclass(frozen=True)
class AnalystFinding:
    output: FindingOutput
    valid_evidence_ids: tuple[str, ...]
    invalid_evidence_ids: tuple[str, ...]
    basis: tuple[Basis, ...]
    interpretation_status: InterpretationStatus
    flags: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowResult:
    status: CallStatus
    findings: tuple[AnalystFinding, ...]
    evidence: Mapping[str, EvidenceItem]
    metadata: CallMetadata | None
    error: str | None = None
    version: str = WORKFLOW_VERSION
    deterministic_only: tuple[str, ...] = field(default_factory=tuple)


def _norm_num(token: str) -> str:
    t = token.lower().replace(",", "").replace("$", "").replace("(", "").replace(" ", "")
    return t.replace("percent", "%")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUMBER.finditer(text):
        n = _norm_num(m.group(0)).lstrip("-")
        digits = re.sub(r"\D", "", n)
        if len(digits) >= 2 or "%" in n or "." in n:
            out.add(re.sub(r"(million|billion|bps)$", "", n))
    return out


def build_evidence(
    *,
    signals: Iterable[ChangeSignal] = (),
    metrics: Mapping[str, MetricResult] | None = None,
    watch: Iterable[WatchSignal] = (),
    reconciliations: Iterable[Reconciliation] = (),
    lineage_notes: Iterable[tuple[str, str]] = (),  # (accession, note)
    max_items: int = 60,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for s in signals:
        old = next((e.text for e in s.evidence[:-1]), "") if len(s.evidence) > 1 else ""
        new = s.evidence[-1].text if s.evidence else ""
        kind = "exhibit_text" if s.detector == "exhibit_topic" else "text_change"
        body = f"[{s.detector}; topics={','.join(s.topics)}; direction={s.direction}]"
        if old:
            body += f"\nOLD: {old}"
        body += f"\nNEW: {new}" if new else ""
        items.append(EvidenceItem(f"ev-{s.signal_id}", kind, body, None, s.section_key))
    for name, m in sorted((metrics or {}).items()):
        if m.status is MetricStatus.OK and m.value is not None:
            accs = ",".join(m.accessions)
            items.append(
                EvidenceItem(
                    f"ev-met-{name}",
                    "xbrl_metric",
                    f"{name} = {m.value} {m.unit or ''} "
                    f"(formula: {m.formula}; accessions: {accs}; {m.version})",
                )
            )
    for w in watch:
        if w.status is WatchStatus.FLAGGED:
            items.append(
                EvidenceItem(
                    f"ev-watch-{w.rule_id}",
                    "watch_signal",
                    f"{w.rule_id}: {w.description} (observed {w.observed})",
                )
            )
    for r in reconciliations:
        items.append(
            EvidenceItem(
                f"ev-rec-{r.topic}", "reconciliation", f"{r.topic}: {r.classification} — {r.reason}"
            )
        )
    for i, (acc, note) in enumerate(lineage_notes):
        items.append(EvidenceItem(f"ev-lin-{i}", "lineage", note, acc))
    return items[:max_items]


def review_finding(f: FindingOutput, evidence: Mapping[str, EvidenceItem]) -> AnalystFinding:
    valid = tuple(e for e in f.evidence_ids if e in evidence)
    invalid = tuple(e for e in f.evidence_ids if e not in evidence)
    basis = tuple(sorted({_KIND_BASIS[evidence[e].kind] for e in valid}))
    status = f.interpretation_status
    flags: list[str] = []
    if invalid:
        flags.append("cites_unknown_evidence")
    if not valid:
        status = InterpretationStatus.NOT_SUPPORTED
        flags.append("no_valid_evidence")
    cited_text = " ".join(evidence[e].content for e in valid)
    claimed = _numbers(f"{f.summary} {f.why_it_matters}")
    available = _numbers(cited_text)
    unverified = sorted(n for n in claimed if n not in available)
    if unverified and status is InterpretationStatus.SUPPORTED:
        status = InterpretationStatus.UNCERTAIN
    if unverified:
        flags.append(f"numeric_unverified:{','.join(unverified)}")
    if _MGMT.search(f.summary) and Basis.SOURCE_FACT not in basis:
        status = InterpretationStatus.NOT_SUPPORTED
        flags.append("uncited_management_claim")
    return AnalystFinding(f, valid, invalid, basis, status, tuple(flags), tuple(f.open_questions))


class FilingChangeWorkflow:
    def __init__(self, client: StructuredLLMClient) -> None:
        self.client = client

    def run(
        self, *, form: str, is_amendment: bool, evidence: Sequence[EvidenceItem]
    ) -> WorkflowResult:
        by_id = {e.evidence_id: e for e in evidence}
        det_only = tuple(sorted(by_id))
        if not evidence:
            return WorkflowResult(
                CallStatus.OK,
                (),
                by_id,
                None,
                "no evidence: nothing to interpret",
                deterministic_only=det_only,
            )
        key = "amendment" if is_amendment else form.upper().removesuffix("/A")
        task = TASKS.get(key, TASKS["8-K"])
        result = self.client.analyze(task, evidence)
        if result.status is CallStatus.FAILED:
            return WorkflowResult(
                CallStatus.FAILED,
                (),
                by_id,
                result.metadata,
                result.error,
                deterministic_only=det_only,
            )
        findings = tuple(review_finding(f, by_id) for f in result.findings)
        return WorkflowResult(
            CallStatus.OK, findings, by_id, result.metadata, None, deterministic_only=det_only
        )
