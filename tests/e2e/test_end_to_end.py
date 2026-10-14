"""Task 7.1: end-to-end filing intelligence suite (offline: fake SEC transport + scripted LLM)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from filinglens.application.alerts import AlertService
from filinglens.application.metadata import FilingMetadataService
from filinglens.application.packages import PackageService
from filinglens.application.pipeline import AnalysisPipeline, PipelineDeps
from filinglens.application.retrieval import DocumentRetriever
from filinglens.application.runs import RunService
from filinglens.domain.models import RunState
from filinglens.infrastructure.sec.client import RateGovernor, SecHttpClient
from filinglens.infrastructure.sec.package import PackageManifestAdapter
from filinglens.infrastructure.sec.submissions import SubmissionsAdapter
from filinglens.infrastructure.storage.raw_store import FileSystemRawStore
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.llm.client import StructuredLLMClient
from filinglens.reporting.report import ReportService, ReviewDecision

_spec = importlib.util.spec_from_file_location(
    "sec_world", Path(__file__).with_name("sec_world.py")
)
assert _spec and _spec.loader
W = importlib.util.module_from_spec(_spec)
sys.modules["sec_world"] = W
_spec.loader.exec_module(W)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
APPROVE = ReviewDecision("analyst@example.com", True)


def good_findings(ev: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text = [e for e, k in ev if k == "text_change"]
    exhibit = [e for e, k in ev if k == "exhibit_text"]
    metric = [e for e, _ in ev if e == "ev-met-revenue_growth"]
    lineage = [e for e, k in ev if k == "lineage"]
    if text:
        out.append(
            dict(
                finding_type="new_risk_factor",
                document_type="10-K",
                section_or_item=None,
                direction="new",
                severity="high",
                confidence=0.85,
                summary="New export-control risk language was added.",
                why_it_matters="Could restrict sales to certain customers.",
                evidence_ids=[text[0]],
                open_questions=["Which products are affected?"],
                interpretation_status="supported",
            )
        )
    if metric:
        out.append(
            dict(
                finding_type="revenue_trend",
                document_type="10-K",
                section_or_item=None,
                direction="increase",
                severity="low",
                confidence=0.7,
                summary="Revenue growth was 0.020220 year over year.",
                why_it_matters="Modest growth.",
                evidence_ids=metric,
                open_questions=[],
                interpretation_status="supported",
            )
        )
    if exhibit:
        out.append(
            dict(
                finding_type="earnings_event",
                document_type="8-K",
                section_or_item=None,
                direction="new",
                severity="medium",
                confidence=0.8,
                summary="Quarterly results were furnished in a press release.",
                why_it_matters="Earnings update.",
                evidence_ids=[exhibit[0]],
                open_questions=[],
                interpretation_status="supported",
            )
        )
    if lineage:
        out.append(
            dict(
                finding_type="amendment_notice",
                document_type="10-K/A",
                section_or_item=None,
                direction="changed",
                severity="high",
                confidence=0.8,
                summary="An amendment and a non-reliance disclosure relate to this filing.",
                why_it_matters="Prior statements may change.",
                evidence_ids=[lineage[0]],
                open_questions=[],
                interpretation_status="supported",
            )
        )
    return out


def unsupported_extra(ev: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out = good_findings(ev)
    out.append(
        dict(
            finding_type="invented_guidance",
            document_type="10-K",
            section_or_item=None,
            direction="increase",
            severity="high",
            confidence=0.9,
            summary="Management expects 25% growth next year.",
            why_it_matters="Guidance raised.",
            evidence_ids=["ev-met-revenue_growth"],
            open_questions=[],
            interpretation_status="supported",
        )
    )
    return out


class Env:
    def __init__(
        self, tmp: Path, *, llm_builder=good_findings, fail=lambda u: False, malformed: bool = False
    ) -> None:  # type: ignore[no-untyped-def]
        routes = W.build_routes(W.ALL)
        client = SecHttpClient(
            user_agent="FilingLens e2e test@example.com",
            live=False,
            transport=W.transport(routes, fail),
            governor=RateGovernor(1000),
            retry=None,
        )
        client.retry = type(client.retry)(max_retries=1, base_delay=0, max_delay=0)
        self.client = client
        self.repos = SqliteRepositories(Database(tmp / "fl.db"))
        self.store = FileSystemRawStore(tmp / "raw")
        if malformed:
            from filinglens.llm.client import FakeProvider

            provider: Any = FakeProvider(["this is not json"])
        else:
            provider = W.scripted_llm(llm_builder)
        self.provider = provider
        runs = RunService(self.repos.runs, self.repos.snapshots, self.repos.evidence)
        self.alerts = AlertService(self.repos.alerts, clock=lambda: NOW)
        self.pipeline = AnalysisPipeline(
            PipelineDeps(
                submissions=SubmissionsAdapter(client),
                packages=PackageManifestAdapter(client),
                retriever=DocumentRetriever(
                    client, self.store, self.repos.snapshots, attempts=self.repos.attempts
                ),
                raw=self.store,
                llm=StructuredLLMClient(provider),
                runs=runs,
                metadata=FilingMetadataService(self.repos.filings, self.repos.issuers),
                package_service=PackageService(self.repos.filings, self.repos.documents),
                reports=ReportService(self.repos.reports, self.repos.report_inputs),
                alerts=self.alerts,
                findings_repo=self.repos.findings,
                evidence_repo=self.repos.evidence,
                clock=lambda: NOW,
            )
        )


def test_happy_10k_path_with_provenance(tmp_path: Path) -> None:
    env = Env(tmp_path)
    res = env.pipeline.run(
        cik=W.CIK,
        focal_accession=W.K24.accession,
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        review=APPROVE,
    )
    assert res.run.state is RunState.COMPLETED, res.notes
    assert res.run.comparator_accession == W.K23.accession
    m = env.pipeline.d.runs.manifest(res.run.run_id)
    assert m.comparator_reason and "prior fiscal-year 10-K" in m.comparator_reason
    assert len(m.snapshot_ids) >= 3 and m.xbrl_fact_ids  # primary x2 + exhibit, fact lineage
    for sid in m.snapshot_ids:
        snap = env.repos.snapshots.get(sid)
        assert snap is not None and snap.verify(env.store.get(snap.storage_key))
    assert res.metrics["revenue_growth"].value is not None
    assert str(res.metrics["revenue_growth"].value) == "0.020220"
    report = env.repos.reports.latest_for_run(res.run.run_id)
    assert report is not None and "New export-control risk language" in report.body_markdown
    assert "Evidence appendix" in report.body_markdown and W.K24.accession in report.body_markdown
    findings = env.repos.findings.list_for_run(res.run.run_id)
    assert {f.finding_type for f in findings} == {"new_risk_factor", "revenue_trend"}
    for f in findings:
        for eid in f.evidence_ids:
            span = env.repos.evidence.get(eid)
            if span is not None:
                assert span.snapshot_id in m.snapshot_ids and span.accession == W.K24.accession
    assert env.pipeline.d.runs.verify_manifest(res.run.run_id) == []
    assert any(r.signal_id for r in res.signals)
    # SEC traffic stayed governed and bounded
    assert env.client.metrics.network_requests > 0 and env.client.metrics.forbidden_403 == 0


def test_eight_k_with_exhibit_path(tmp_path: Path) -> None:
    env = Env(tmp_path)
    res = env.pipeline.run(cik=W.CIK, focal_accession=W.E8K.accession, as_of=NOW, review=APPROVE)
    assert res.run.state is RunState.COMPLETED, res.notes
    assert res.report_inputs is not None
    assert any(
        "Item 2.02" in line and "99.1:resolved" in line for line in res.report_inputs.eight_k_events
    )
    assert {f.finding_type for f in res.report_inputs.findings} == {"earnings_event"}
    m = env.pipeline.d.runs.manifest(res.run.run_id)
    exhibit_snaps = [
        s
        for s in (env.repos.snapshots.get(x) for x in m.snapshot_ids)
        if s and s.filename == "exdv-ex991.htm"
    ]
    assert exhibit_snaps


def test_amendment_lineage_path(tmp_path: Path) -> None:
    env = Env(tmp_path)
    res = env.pipeline.run(cik=W.CIK, focal_accession=W.K24A.accession, as_of=NOW, review=APPROVE)
    assert res.run.state is RunState.COMPLETED, res.notes
    assert res.report_inputs is not None
    notes = " ".join(res.report_inputs.lineage_notes)
    assert "amendment" in notes.lower() and W.K24.accession in notes
    assert "fraud" not in notes.lower()
    assert any(a.kind == "restatement_signal" for a in env.repos.alerts.list())
    assert env.repos.filings.get(W.K24.accession) is not None  # original untouched and separate


def test_source_failure_blocks_run(tmp_path: Path) -> None:
    env = Env(tmp_path, fail=lambda u: u.endswith("exdv-20240928.htm"))
    res = env.pipeline.run(cik=W.CIK, focal_accession=W.K24.accession, as_of=NOW, review=APPROVE)
    assert res.run.state is RunState.BLOCKED_BY_SOURCE and res.run.error
    assert env.repos.reports.latest_for_run(res.run.run_id) is None
    assert any(
        a.kind == "source_failure" and a.run_id == res.run.run_id for a in env.repos.alerts.list()
    )
    env2 = Env(tmp_path / "b", fail=lambda u: "submissions" in u)
    res2 = env2.pipeline.run(cik=W.CIK, focal_accession=W.K24.accession, as_of=NOW)
    assert res2.run.state is RunState.BLOCKED_BY_SOURCE


def test_malformed_llm_output_fails_closed(tmp_path: Path) -> None:
    env = Env(tmp_path, malformed=True)
    res = env.pipeline.run(cik=W.CIK, focal_accession=W.K24.accession, as_of=NOW, review=APPROVE)
    assert res.run.state is RunState.NEEDS_INPUT and "model output rejected" in (
        res.run.error or ""
    )
    assert env.repos.findings.list_for_run(res.run.run_id) == []
    assert env.repos.reports.latest_for_run(res.run.run_id) is None
    assert any(a.kind == "model_failure" for a in env.repos.alerts.list())


def test_unsupported_finding_blocked(tmp_path: Path) -> None:
    env = Env(tmp_path, llm_builder=unsupported_extra)
    res = env.pipeline.run(cik=W.CIK, focal_accession=W.K24.accession, as_of=NOW, review=APPROVE)
    assert res.run.state is RunState.COMPLETED
    assert res.report_inputs is not None and res.report_inputs.blocked_findings >= 1
    assert "invented_guidance" not in {f.finding_type for f in res.report_inputs.findings}
    report = env.repos.reports.latest_for_run(res.run.run_id)
    assert report is not None and "25%" not in report.body_markdown


def test_point_in_time_future_source_excluded(tmp_path: Path) -> None:
    env = Env(tmp_path)
    # As of before the 10-K/A and the 8-K non-reliance were public.
    res = env.pipeline.run(
        cik=W.CIK,
        focal_accession=W.K24.accession,
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        review=APPROVE,
    )
    assert res.report_inputs is not None
    assert res.report_inputs.lineage_notes == ()
    assert W.K24A.accession not in (res.report_inputs.comparator_accession or "")
    prompt = env.provider.prompts[-1].user
    assert W.K24A.accession not in prompt and W.NR8K.accession not in prompt
    with_future = Env(tmp_path / "late")
    late = with_future.pipeline.run(
        cik=W.CIK, focal_accession=W.K24.accession, as_of=NOW, review=APPROVE
    )
    assert late.report_inputs is not None and late.report_inputs.lineage_notes  # now visible
    early = with_future.pipeline.run(
        cik=W.CIK, focal_accession=W.K24A.accession, as_of=datetime(2024, 12, 1, tzinfo=UTC)
    )
    assert early.run.state is RunState.FAILED and "not public" in (early.run.error or "")


@pytest.mark.parametrize("acc", [W.K24.accession, W.E8K.accession])
def test_reports_reproducible_from_run_id(tmp_path: Path, acc: str) -> None:
    env = Env(tmp_path)
    res = env.pipeline.run(cik=W.CIK, focal_accession=acc, as_of=NOW, review=APPROVE)
    assert env.pipeline.d.reports.regenerate(res.run.run_id) == res.report_markdown
