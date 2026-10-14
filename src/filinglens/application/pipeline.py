"""End-to-end analysis pipeline (``pipeline/1``).

Issuer → SEC metadata → package → immutable snapshots → parse → lineage/comparator →
deterministic diff → XBRL → LLM interpretation → evidence validation → research priority →
report. Every external dependency is injected (governed SEC adapters, raw store, LLM
client), so mandatory tests run fully offline. The run lifecycle and reproducibility manifest
are updated at each stage; failures move the run to ``BlockedBySource``, ``NeedsInput`` or
``Failed`` with an operational alert instead of producing partial reports.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from filinglens.analytics.reconciliation import (
    RECONCILIATION_VERSION,
    Classification,
    Reconciliation,
    reconcile,
)
from filinglens.analytics.trends import (
    ENGINE_VERSION,
    CanonicalStore,
    MetricResult,
    PeriodPair,
    TrendEngine,
)
from filinglens.analytics.watch_signals import RULES_VERSION, WatchSignal, WatchStatus, evaluate
from filinglens.application.alerts import AlertService
from filinglens.application.comparison import record_comparator
from filinglens.application.metadata import FilingMetadataService
from filinglens.application.packages import PackageService
from filinglens.application.ports import EvidenceRepository, FindingRepository
from filinglens.application.retrieval import DocumentRetriever, PackageRetrieval
from filinglens.application.runs import RunService
from filinglens.application.watchlist import SubmissionsBatchLike
from filinglens.diff.alignment import ALIGNMENT_VERSION, align_documents
from filinglens.diff.detectors import (
    DETECTOR_VERSION,
    ChangeSignal,
    detect_exhibit_topics,
    detect_mdna_topics,
    detect_risk_factors,
)
from filinglens.diff.priority_features import FeatureContext, FeatureVector, build_features
from filinglens.diff.text_diff import DIFF_VERSION, diff_documents
from filinglens.domain.availability import AVAILABILITY_POLICY_VERSION
from filinglens.domain.errors import FilingLensError
from filinglens.domain.models import ComparisonRun, EvidenceSpan, Filing, Finding, RunState
from filinglens.domain.package import PackageManifest
from filinglens.domain.xbrl import XbrlFact
from filinglens.lineage.amendments import LineageEngine
from filinglens.lineage.comparator import POLICY_VERSION, ComparatorSelection, select_comparator
from filinglens.llm.client import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    CallStatus,
    EvidenceItem,
    StructuredLLMClient,
)
from filinglens.llm.validation import EvidenceBinding, RunScope, ValidatedFinding, Verdict, validate
from filinglens.llm.workflow import FilingChangeWorkflow, build_evidence
from filinglens.observability import Telemetry, log_context
from filinglens.parsing.eight_k import PARSER_VERSION as EIGHT_K_VERSION
from filinglens.parsing.eight_k import parse_eight_k
from filinglens.parsing.normalizer import NORMALIZER_VERSION, NormalizedDocument, normalize_html
from filinglens.parsing.sections import (
    PARSER_VERSION,
    SectionParseResult,
    SectionStatus,
    parse_sections,
)
from filinglens.reporting.report import (
    CitedEvidence,
    ReportFinding,
    ReportInputs,
    ReportService,
    ReviewDecision,
)
from filinglens.xbrl.ixbrl import EXTRACTOR_VERSION, extract_ixbrl
from filinglens.xbrl.normalize import PeriodClass, classify_period

PIPELINE_VERSION = "pipeline/1"
_LOG = logging.getLogger("filinglens.pipeline")
_ALERT_CATEGORY = {"source_failure": "source", "parser_failure": "parser", "model_failure": "model"}


class SubmissionsSource(Protocol):
    def fetch(self, cik: int) -> SubmissionsBatchLike: ...


class PackageSource(Protocol):
    def fetch(
        self, *, cik: int, accession: str, form: str | None, primary_document: str | None
    ) -> PackageManifest: ...


class RawReader(Protocol):
    def get(self, key: str) -> bytes: ...


@dataclass
class PipelineDeps:
    submissions: SubmissionsSource
    packages: PackageSource
    retriever: DocumentRetriever
    raw: RawReader
    llm: StructuredLLMClient
    runs: RunService
    metadata: FilingMetadataService
    package_service: PackageService
    reports: ReportService
    alerts: AlertService
    findings_repo: FindingRepository
    evidence_repo: EvidenceRepository
    clock: Callable[[], datetime]
    telemetry: Telemetry = field(default_factory=Telemetry)


@dataclass
class PipelineResult:
    run: ComparisonRun
    report_markdown: str | None = None
    validated: list[ValidatedFinding] = field(default_factory=list)
    metrics: dict[str, MetricResult] = field(default_factory=dict)
    signals: list[ChangeSignal] = field(default_factory=list)
    report_inputs: ReportInputs | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class _Loaded:
    filing: Filing
    retrieval: PackageRetrieval
    doc: NormalizedDocument
    raw: bytes
    sections: SectionParseResult | None = None
    facts: tuple[XbrlFact, ...] = ()
    exhibits: dict[str, NormalizedDocument] = field(default_factory=dict)


def _category(section: str | None, finding_type: str) -> str:
    s = (section or "").upper()
    if "1A" in s:
        return "risk_factors"
    if s.endswith(("7", "I-2", "2")) and "8-K" not in s:
        return "mdna_liquidity"
    if "8-K" in s or finding_type.startswith(("earnings", "event", "exhibit")):
        return "eight_k"
    if finding_type.startswith(("revenue", "margin", "cash", "xbrl")):
        return "xbrl"
    return "other"


class AnalysisPipeline:
    def __init__(self, deps: PipelineDeps) -> None:
        self.d = deps
        self._ctx: dict[str, str] = {}

    # -- helpers ------------------------------------------------------------------------------
    def _move(self, run_id: str, *states: RunState) -> None:
        for s in states:
            self.d.runs.transition(run_id, s)
            _LOG.info("run transition", extra={"state": s.value})

    def _stop(
        self, run: ComparisonRun, state: RunState, error: str, alert_kind: str
    ) -> PipelineResult:
        if state is RunState.FAILED:
            self.d.runs.transition(run.run_id, state, error=error)
        else:
            self.d.runs.transition(run.run_id, state)
            stored = self.d.runs.get(run.run_id)
            self.d.runs.runs.save(dataclasses.replace(stored, error=error))
        category = _ALERT_CATEGORY.get(alert_kind, "internal")
        self.d.telemetry.error(category)
        _LOG.error(
            "run stopped", extra={"state": state.value, "error_category": category, "error": error}
        )
        self.d.alerts.operational(alert_kind, error, run_id=run.run_id)
        return PipelineResult(self.d.runs.get(run.run_id), notes=[error])

    def _load(self, f: Filing) -> _Loaded:
        manifest = self.d.packages.fetch(
            cik=f.cik, accession=f.accession, form=f.form, primary_document=f.primary_document
        )
        self.d.package_service.register(manifest)
        retrieval = self.d.retriever.retrieve_package(manifest)
        if retrieval.primary_snapshot is None:
            reasons = "; ".join(o.reason for o in retrieval.outcomes if o.status.value == "failed")
            raise _SourceError(f"primary document unavailable for {f.accession}: {reasons}")
        raw = self.d.raw.get(retrieval.primary_snapshot.storage_key)
        doc = normalize_html(raw, snapshot_id=retrieval.primary_snapshot.snapshot_id)
        loaded = _Loaded(f, retrieval, doc, raw)
        for out in retrieval.outcomes:
            if out.role == "exhibit" and out.snapshot is not None:
                data = self.d.raw.get(out.snapshot.storage_key)
                loaded.exhibits[out.filename] = normalize_html(
                    data, snapshot_id=out.snapshot.snapshot_id
                )
        return loaded

    # -- main ---------------------------------------------------------------------------------
    def run(self, **kwargs: Any) -> PipelineResult:
        with log_context(
            accession=str(kwargs.get("focal_accession")), cik=str(kwargs.get("cik"))
        ) as ctx:
            self._ctx = ctx
            with self.d.telemetry.stage("pipeline.run"):
                result = self._run(**kwargs)
            _LOG.info("run finished", extra={"state": result.run.state.value})
            return result

    def _run(
        self,
        *,
        cik: int,
        focal_accession: str,
        as_of: datetime,
        review: ReviewDecision | None = None,
        fiscal_year_end: str | None = None,
    ) -> PipelineResult:
        run = self.d.runs.create(cik=cik, as_of=as_of, focal_accession=focal_accession)
        self._ctx["run_id"] = run.run_id
        self._move(run.run_id, RunState.RESOLVING)
        try:
            batch = self.d.submissions.fetch(cik)
            self.d.metadata.ingest(
                batch.issuer,
                batch.filings,
                source=batch.source,
                source_versions=batch.source_versions,
            )
        except FilingLensError as exc:
            return self._stop(
                run, RunState.BLOCKED_BY_SOURCE, f"submissions unavailable: {exc}", "source_failure"
            )
        fye = fiscal_year_end or batch.issuer.fiscal_year_end
        focal = self.d.metadata.filings.get(focal_accession)
        if focal is None:
            return self._stop(
                run, RunState.FAILED, f"focal filing {focal_accession} not found", "source_failure"
            )
        if focal.available_at > run.as_of:
            return self._stop(
                run,
                RunState.FAILED,
                f"focal filing {focal.accession} was not public at as_of",
                "source_failure",
            )
        history = self.d.metadata.filings.list_for_cik(cik)
        selection = select_comparator(focal, history, as_of=run.as_of, fiscal_year_end=fye)
        record_comparator(self.d.runs, run.run_id, selection)
        comparator = (
            self.d.metadata.filings.get(selection.comparator_accession)
            if selection.comparator_accession
            else None
        )

        self._move(run.run_id, RunState.COLLECTING)
        try:
            cur = self._load(focal)
            prior = self._load(comparator) if comparator and focal.base_form != "8-K" else None
        except (FilingLensError, _SourceError) as exc:
            return self._stop(run, RunState.BLOCKED_BY_SOURCE, str(exc), "source_failure")
        snaps = [s.snapshot_id for s in cur.retrieval.snapshots()]
        if prior:
            snaps += [s.snapshot_id for s in prior.retrieval.snapshots()]
        self.d.runs.update_manifest(run.run_id, snapshot_ids=snaps)

        self._move(run.run_id, RunState.PARSING)
        result = PipelineResult(run)
        signals: list[ChangeSignal] = []
        metrics: dict[str, MetricResult] = {}
        diff = None
        lineage_notes: list[tuple[str, str]] = []
        eight_k_lines: list[str] = []
        if focal.base_form == "8-K":
            primary_name = (
                cur.retrieval.primary_snapshot.filename if cur.retrieval.primary_snapshot else None
            )
            parsed = parse_eight_k(
                cur.doc,
                self._manifest(focal),
                primary_filename=primary_name,
                exhibit_docs=cur.exhibits,
                reported_items=focal.items,
            )
            if not parsed.events:
                return self._stop(
                    run, RunState.NEEDS_INPUT, "8-K items could not be parsed", "parser_failure"
                )
            signals = detect_exhibit_topics(parsed)
            eight_k_lines = [
                f"Item {e.item} {e.title} ({e.category}); exhibits: "
                + (", ".join(f"{x.exhibit_number}:{x.status.value}" for x in e.exhibits) or "none")
                for e in parsed.events
            ]
            self._move(run.run_id, RunState.ALIGNING, RunState.ANALYZING)
        else:
            cur.sections = parse_sections(cur.doc, focal.form)
            if prior is None or comparator is None:
                return self._stop(
                    run,
                    RunState.NEEDS_INPUT,
                    "no comparable prior filing available",
                    "parser_failure",
                )
            prior.sections = parse_sections(prior.doc, comparator.form)
            ok = [s for s in cur.sections.sections if s.status is SectionStatus.OK]
            if not ok:
                return self._stop(
                    run,
                    RunState.NEEDS_INPUT,
                    "no reliable item boundaries in focal filing",
                    "parser_failure",
                )
            cur.facts = extract_ixbrl(cur.raw, cik=cik, accession=focal.accession).facts
            prior.facts = extract_ixbrl(prior.raw, cik=cik, accession=comparator.accession).facts
            self._move(run.run_id, RunState.ALIGNING)
            alignment = align_documents(prior.doc, prior.sections, cur.doc, cur.sections)
            diff = diff_documents(prior.doc, cur.doc, alignment)
            self._move(run.run_id, RunState.ANALYZING)
            rf_key = cur.sections.analyst("risk_factors")
            md_key = cur.sections.analyst("mdna")
            if rf_key:
                signals += detect_risk_factors(diff, rf_key.key)
            if md_key:
                signals += detect_mdna_topics(diff, [md_key.key])
            pair = self._period_pair(cur.facts, prior.facts, focal, comparator)
            if pair is not None:
                store = CanonicalStore(
                    [*cur.facts, *prior.facts],
                    eligible_accessions={focal.accession, comparator.accession},
                )
                metrics = TrendEngine(store).compute(pair)
            else:
                result.notes.append("could not determine comparable XBRL periods")
        lineage = LineageEngine(history)
        restatements = lineage.restatement_signals(run.as_of)
        for s in restatements:
            if s.accession == focal.accession or s.related_accession == focal.accession:
                lineage_notes.append((s.accession, s.description))
        if focal.is_amendment:
            link = lineage.links.get(focal.accession)
            lineage_notes.append(
                (
                    focal.accession,
                    f"Focal filing is an amendment; base filing: "
                    f"{link.base_accession if link else 'unknown'}",
                )
            )
        watch = (
            evaluate(metrics, changes=diff.changes if diff else (), restatements=restatements)
            if metrics
            else []
        )
        recon = (
            reconcile([s for s in signals if s.detector == "mdna_topic"], metrics)
            if metrics
            else []
        )
        features = build_features(
            signals,
            diff,
            FeatureContext(
                is_amendment=focal.is_amendment,
                restatement_signal=bool(lineage_notes),
                parser_confidence={s.key: s.confidence for s in cur.sections.sections}
                if cur.sections
                else {},
            ),
        )
        evidence = build_evidence(
            signals=signals,
            metrics=metrics,
            watch=[w for w in watch if w.status is WatchStatus.FLAGGED],
            reconciliations=recon,
            lineage_notes=lineage_notes,
        )
        wf = FilingChangeWorkflow(self.d.llm).run(
            form=focal.form, is_amendment=focal.is_amendment, evidence=evidence
        )
        meta = wf.metadata
        versions = {
            "parser": "+".join(
                (NORMALIZER_VERSION, PARSER_VERSION, EIGHT_K_VERSION, EXTRACTOR_VERSION)
            ),
            "diff": f"{ALIGNMENT_VERSION}+{DIFF_VERSION}+{DETECTOR_VERSION}",
            "calculation": f"{ENGINE_VERSION}+{RULES_VERSION}+{RECONCILIATION_VERSION}",
            "comparator_policy": POLICY_VERSION,
            "availability_policy": AVAILABILITY_POLICY_VERSION,
            "pipeline": PIPELINE_VERSION,
            "llm_provider": meta.provider if meta else "none",
            "llm_model": meta.model if meta else "none",
            "prompt": PROMPT_VERSION,
            "output_schema": SCHEMA_VERSION,
        }
        fact_ids = sorted({fid for m in metrics.values() for fid in m.fact_ids})
        self.d.runs.update_manifest(
            run.run_id, component_versions=versions, xbrl_fact_ids=fact_ids, uses_llm=True
        )
        result.signals, result.metrics = signals, metrics
        if wf.status is CallStatus.FAILED:
            return self._stop(
                run, RunState.NEEDS_INPUT, f"model output rejected: {wf.error}", "model_failure"
            )

        self._move(run.run_id, RunState.REVIEWING)
        snap_acc = {s.snapshot_id: s.accession for s in cur.retrieval.snapshots()}
        if prior:
            snap_acc.update({s.snapshot_id: s.accession for s in prior.retrieval.snapshots()})
        bindings, texts, feats = self._bindings(
            evidence, signals, metrics, watch, recon, features, cur, focal
        )
        scope = RunScope(
            frozenset(snap_acc),
            frozenset(
                {focal.accession} | ({comparator.accession} if comparator and prior else set())
            ),
            frozenset(fact_ids),
        )
        validated = validate(list(wf.findings), bindings, scope, features_by_evidence=feats)
        result.validated = validated
        inputs = self._report_inputs(
            run.run_id,
            batch.issuer.name,
            focal,
            selection,
            validated,
            evidence,
            bindings,
            signals,
            versions,
            snaps,
            lineage_notes,
            eight_k_lines,
            metrics,
            recon,
        )
        self._persist(run.run_id, validated, bindings, signals)
        result.report_inputs = inputs
        result.report_markdown = self.d.reports.stage(inputs)
        for i, vf in enumerate(validated):
            self.d.alerts.high_priority_finding(run.run_id, f"{run.run_id}-f{i}", vf)
        for s in restatements:
            if s.accession == focal.accession or s.related_accession == focal.accession:
                self.d.alerts.restatement(run.run_id, s)
        if review is not None:
            self.d.reports.finalize(inputs, review, created_at=self.d.clock())
            if review.approved:
                self.d.runs.transition(run.run_id, RunState.COMPLETED)
        result.run = self.d.runs.get(run.run_id)
        return result

    # -- internals ----------------------------------------------------------------------------
    def _manifest(self, f: Filing) -> PackageManifest:
        return self.d.package_service.load(f.accession)

    @staticmethod
    def _period_pair(
        cur: Sequence[XbrlFact], prior: Sequence[XbrlFact], focal: Filing, comparator: Filing
    ) -> PeriodPair | None:
        wanted = {PeriodClass.ANNUAL} if focal.base_form == "10-K" else {PeriodClass.QUARTER}

        def find(facts: Sequence[XbrlFact], end: object) -> tuple[object, object] | None:
            for f in facts:
                if f.period_end == end and not f.dimensions and classify_period(f) in wanted:
                    return f.period_start, f.period_end
            return None

        c = find(cur, focal.report_period)
        p = find([*cur, *prior], comparator.report_period)
        if c is None or p is None:
            return None
        return PeriodPair(c[0], c[1], p[0], p[1])  # type: ignore[arg-type]

    def _bindings(
        self,
        evidence: Sequence[EvidenceItem],
        signals: Sequence[ChangeSignal],
        metrics: dict[str, MetricResult],
        watch: Sequence[WatchSignal],
        recon: Sequence[Reconciliation],
        features: Sequence[FeatureVector],
        cur: _Loaded,
        focal: Filing,
    ) -> tuple[dict[str, EvidenceBinding], dict[str, str], dict[str, list[FeatureVector]]]:
        sig_by_ev = {f"ev-{s.signal_id}": s for s in signals}
        assert cur.retrieval.primary_snapshot is not None
        primary = cur.retrieval.primary_snapshot.snapshot_id
        bindings: dict[str, EvidenceBinding] = {}
        texts: dict[str, str] = {}
        feats: dict[str, list[FeatureVector]] = {}
        fv_by_sig = {v.signal_id: v for v in features}
        snap_acc = {s.snapshot_id: s.accession for s in cur.retrieval.snapshots()}
        for item in evidence:
            texts[item.evidence_id] = item.content
            if item.evidence_id in sig_by_ev:
                s = sig_by_ev[item.evidence_id]
                ref = s.evidence[-1] if s.evidence else None
                snap = ref.snapshot_id if ref and ref.snapshot_id else primary
                bindings[item.evidence_id] = EvidenceBinding(
                    item.evidence_id, snap, snap_acc.get(snap, focal.accession), None, s.section_key
                )
                if s.signal_id in fv_by_sig:
                    feats[item.evidence_id] = [fv_by_sig[s.signal_id]]
            elif item.evidence_id.startswith("ev-met-"):
                m = metrics[item.evidence_id.removeprefix("ev-met-")]
                bindings[item.evidence_id] = EvidenceBinding(
                    item.evidence_id, None, None, None, None, m.fact_ids
                )
            elif item.evidence_id.startswith("ev-watch-"):
                w = next(x for x in watch if f"ev-watch-{x.rule_id}" == item.evidence_id)
                ids = tuple(fid for m in w.metric_inputs for fid in m.fact_ids)
                bindings[item.evidence_id] = (
                    EvidenceBinding(item.evidence_id, None, None, None, None, ids)
                    if ids
                    else EvidenceBinding(item.evidence_id, primary, focal.accession, None, None)
                )
            elif item.evidence_id.startswith("ev-rec-"):
                r = next(x for x in recon if f"ev-rec-{x.topic}" == item.evidence_id)
                ids = tuple(fid for m in r.metrics for fid in m.fact_ids)
                bindings[item.evidence_id] = EvidenceBinding(
                    item.evidence_id, None, None, None, None, ids
                )
            else:  # lineage notes reference the focal filing's primary snapshot
                bindings[item.evidence_id] = EvidenceBinding(
                    item.evidence_id, primary, focal.accession, None, None
                )
        return bindings, texts, feats

    def _report_inputs(
        self,
        run_id: str,
        issuer_name: str,
        focal: Filing,
        selection: ComparatorSelection,
        validated: Sequence[ValidatedFinding],
        evidence: Sequence[EvidenceItem],
        bindings: dict[str, EvidenceBinding],
        signals: Sequence[ChangeSignal],
        versions: dict[str, str],
        snaps: Sequence[str],
        lineage_notes: Sequence[tuple[str, str]],
        eight_k_lines: Sequence[str],
        metrics: dict[str, MetricResult],
        recon: Sequence[Reconciliation],
    ) -> ReportInputs:
        ev_by_id = {e.evidence_id: e for e in evidence}
        sig_by_ev = {f"ev-{s.signal_id}": s for s in signals}
        accepted = [v for v in validated if v.verdict is Verdict.ACCEPTED]
        cited: dict[str, CitedEvidence] = {}
        findings = []
        for v in accepted:
            out = v.finding.output
            for eid in v.finding.valid_evidence_ids:
                b = bindings[eid]
                s = sig_by_ev.get(eid)
                ref = s.evidence[-1] if s and s.evidence else None
                cited[eid] = CitedEvidence(
                    eid,
                    ev_by_id[eid].kind,
                    b.accession,
                    None,
                    b.section,
                    ref.locator if ref else None,
                    (ref.text if ref and ref.text else ev_by_id[eid].content)[:300],
                )
            first = sig_by_ev.get(v.finding.valid_evidence_ids[0])
            section = out.section_or_item or (first.section_key if first else None)
            findings.append(
                ReportFinding(
                    out.finding_type,
                    out.section_or_item,
                    out.summary,
                    out.why_it_matters,
                    out.severity.value,
                    v.priority_score,
                    v.finding.valid_evidence_ids,
                    _category(section, out.finding_type),
                    tuple(out.open_questions),
                )
            )
        xbrl_lines = tuple(
            f"{k} = {m.value} ({m.formula})"
            for k, m in sorted(metrics.items())
            if m.value is not None and isinstance(m.value, Decimal)
        )
        tensions = tuple(
            f"{r.topic}: {r.classification} — {r.reason}"
            for r in recon
            if r.classification in (Classification.TENSION, Classification.MIXED)
        )
        return ReportInputs(
            run_id=run_id,
            issuer_name=issuer_name,
            cik=focal.cik,
            focal_accession=focal.accession,
            focal_form=focal.form,
            comparator_accession=selection.comparator_accession,
            comparator_reason=selection.reason,
            as_of=self.d.runs.get(run_id).as_of.isoformat(),
            findings=tuple(findings),
            evidence=tuple(cited.values()),
            blocked_findings=sum(1 for v in validated if v.verdict is Verdict.BLOCKED),
            eight_k_events=tuple(eight_k_lines),
            lineage_notes=tuple(n for _, n in lineage_notes),
            xbrl_signals=xbrl_lines,
            tensions=tensions,
            component_versions=dict(versions),
            snapshot_ids=tuple(sorted(set(snaps))),
        )

    def _persist(
        self,
        run_id: str,
        validated: Sequence[ValidatedFinding],
        bindings: dict[str, EvidenceBinding],
        signals: Sequence[ChangeSignal],
    ) -> None:
        sig_by_ev = {f"ev-{s.signal_id}": s for s in signals}
        stored: set[str] = set()
        for i, v in enumerate(validated):
            if v.verdict is not Verdict.ACCEPTED:
                continue
            out = v.finding.output
            for eid in v.finding.valid_evidence_ids:
                b = bindings[eid]
                s = sig_by_ev.get(eid)
                if eid in stored or b.snapshot_id is None or b.accession is None:
                    continue
                ref = s.evidence[-1] if s and s.evidence else None
                self.d.evidence_repo.add(
                    EvidenceSpan(
                        evidence_id=f"{run_id}:{eid}",
                        run_id=run_id,
                        snapshot_id=b.snapshot_id,
                        accession=b.accession,
                        locator=(ref.locator if ref else "document"),
                        text=(ref.text if ref else "")[:2000],
                        section=b.section,
                    )
                )
                stored.add(eid)
            self.d.findings_repo.add(
                Finding(
                    finding_id=f"{run_id}-f{i}",
                    run_id=run_id,
                    finding_type=out.finding_type,
                    summary=out.summary,
                    severity=out.severity.value,
                    confidence=out.confidence,
                    evidence_ids=tuple(
                        f"{run_id}:{e}" if e in stored else e for e in v.finding.valid_evidence_ids
                    ),
                    interpretation_status=v.finding.interpretation_status.value,
                    direction=out.direction,
                    why_it_matters=out.why_it_matters,
                    open_questions=tuple(out.open_questions),
                )
            )


class _SourceError(Exception):
    pass


__all__ = ["AnalysisPipeline", "PipelineDeps", "PipelineResult"]
