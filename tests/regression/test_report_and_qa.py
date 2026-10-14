"""Task 5.6: cited filing intelligence report and bounded Q&A."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.llm.client import FakeProvider
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import parse_sections
from filinglens.reporting.qa import QAStatus, answer_question
from filinglens.reporting.report import (
    DISCLAIMER,
    CitedEvidence,
    FinalizationBlocked,
    ReportFinding,
    ReportInputs,
    ReportIntegrityError,
    ReportService,
    ReviewDecision,
    render_report,
)
from filinglens.retrieval.index import RetrievalFilter, RetrievalIndex

FOCAL = "0001234567-24-000031"
T = datetime(2026, 1, 2, tzinfo=UTC)


def inputs(**kw: object) -> ReportInputs:
    base: dict[str, object] = dict(
        run_id="run-1",
        issuer_name="Example Devices Inc.",
        cik=1234567,
        focal_accession=FOCAL,
        focal_form="10-K",
        comparator_accession="0001234567-23-000030",
        comparator_reason="prior fiscal-year 10-K",
        as_of="2026-01-01T00:00:00+00:00",
        findings=(
            ReportFinding(
                "new_risk_factor",
                "1A",
                "A new export-control risk factor was added.",
                "May restrict sales.",
                "high",
                88.0,
                ("ev-rf",),
                "risk_factors",
                ("Which products?",),
            ),
            ReportFinding(
                "liquidity_change",
                "7",
                "Cash supporting liquidity declined.",
                "Less flexibility.",
                "medium",
                60.0,
                ("ev-md", "ev-met"),
                "mdna_liquidity",
            ),
        ),
        evidence=(
            CitedEvidence(
                "ev-rf",
                "text_change",
                FOCAL,
                "main.htm",
                "1A",
                "/html/body/p[5]",
                "Export controls could restrict...",
            ),
            CitedEvidence(
                "ev-md",
                "text_change",
                FOCAL,
                "main.htm",
                "7",
                "/html/body/p[9]",
                "Liquidity is supported by cash of $6 billion.",
            ),
            CitedEvidence("ev-met", "xbrl_metric", None, None, None, None, "cash_change = -22 USD"),
        ),
        blocked_findings=2,
        eight_k_events=("8-K 2.02 earnings release furnished as EX-99.1 [ev-8k]",),
        lineage_notes=("10-K/A 0001234567-25-000010 amends this filing (scope to verify)",),
        xbrl_signals=("revenue_growth = 0.020220",),
        tensions=("liquidity: tension — narrative direction opposes cash_change",),
        component_versions={
            "parser": "section-parser/1",
            "diff": "diff/1",
            "llm_model": "fake-model-1",
        },
        snapshot_ids=("a" * 64,),
    )
    base.update(kw)
    return ReportInputs(**base)  # type: ignore[arg-type]


def test_report_sections_and_citations() -> None:
    md = render_report(inputs())
    for heading in (
        "Filing metadata",
        "Executive change summary",
        "Top ranked findings",
        "Risk Factors",
        "MD&A and liquidity",
        "8-K events and exhibits",
        "Amendment / restatement notes",
        "XBRL signals",
        "Narrative ↔ numeric tensions",
        "Open questions",
        "Methodology",
        "Evidence appendix",
    ):
        assert f"## {heading}" in md, heading
    assert DISCLAIMER in md and "2 model finding(s) were blocked" in md
    cited = set(re.findall(r"\[(ev-[\w-]+)\]", md.split("## Evidence appendix")[0]))
    appendix = set(re.findall(r"\*\*\[(ev-[\w-]+)\]\*\*", md))
    assert {"ev-rf", "ev-md", "ev-met"} <= cited
    assert cited - {"ev-8k"} <= appendix  # every finding citation resolves in the appendix


def test_uncited_or_dangling_claims_rejected() -> None:
    bad = inputs(
        findings=(ReportFinding("x", None, "Uncited claim.", "", "low", None, (), "other"),)
    )
    with pytest.raises(ReportIntegrityError):
        render_report(bad)
    dangling = inputs(
        findings=(ReportFinding("x", None, "Claim.", "", "low", None, ("ev-nope",), "other"),)
    )
    with pytest.raises(ReportIntegrityError):
        render_report(dangling)


def test_reviewer_can_block_finalization_and_versions_increment() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    svc = ReportService(repos.reports, repos.report_inputs)
    with pytest.raises(FinalizationBlocked):
        svc.finalize(inputs(), ReviewDecision("analyst@x", False, "needs MD&A check"), created_at=T)
    assert repos.reports.latest_for_run("run-1") is None
    r1 = svc.finalize(inputs(), ReviewDecision("analyst@x", True), created_at=T)
    r2 = svc.finalize(inputs(), ReviewDecision("analyst@x", True), created_at=T)
    assert (r1.version, r2.version) == (1, 2) and r1.body_markdown == r2.body_markdown


def test_report_reproducible_from_run_id() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    svc = ReportService(repos.reports, repos.report_inputs)
    staged = svc.stage(inputs())
    fresh = ReportService(repos.reports, repos.report_inputs)  # new process, same DB
    assert fresh.regenerate("run-1") == staged
    assert ReportInputs.from_json(inputs().to_json()) == inputs()


# ---- bounded Q&A -------------------------------------------------------------------------

FILLER = "This paragraph provides sufficient additional narrative context for the parser. " * 4
DOC = (
    "<html><body>"
    f"<div><b>Item 1. Business</b></div><p>{FILLER}</p>"
    "<div><b>Item 1A. Risk Factors</b></div><p>Export controls could restrict sales of advanced semiconductors in certain regions.</p>"
    f"<div><b>Item 3. Legal Proceedings</b></div><p>{FILLER}</p>"
    "<div><b>Item 7. Management's Discussion and Analysis</b></div><p>Services revenue grew 13% to $96.2 billion.</p>"
    f"<div><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></div><p>{FILLER}</p>"
    f"<div><b>Item 8. Financial Statements</b></div><p>{FILLER}</p></body></html>"
).encode()


@pytest.fixture
def qa_index() -> RetrievalIndex:
    idx = RetrievalIndex()
    doc = normalize_html(DOC)
    idx.add_document(
        doc,
        accession=FOCAL,
        filename="main.htm",
        available_at=datetime(2024, 11, 1, tzinfo=UTC),
        sections=parse_sections(doc, "10-K"),
    )
    return idx


FLT = RetrievalFilter(frozenset({FOCAL}), T)


def _qa_provider(
    index: RetrievalIndex, answer: str, cite_query: str, status: str = "answered"
) -> FakeProvider:
    cid = index.search(cite_query, FLT)[0].chunk.chunk_id
    return FakeProvider(
        [
            json.dumps(
                {
                    "schema_version": "qa-schema/1",
                    "status": status,
                    "answer": answer,
                    "evidence_ids": [cid],
                }
            )
        ]
    )


def test_qa_answers_with_citations(qa_index: RetrievalIndex) -> None:
    p = _qa_provider(qa_index, "Services revenue grew 13% to $96.2 billion.", "services revenue")
    ans = answer_question("How did services revenue change?", qa_index, FLT, p)
    assert ans.status is QAStatus.ANSWERED and ans.citations
    assert ans.citations[0].chunk.accession == FOCAL and ans.citations[0].chunk.section_key == "7"


def test_qa_not_supported_outside_evidence(qa_index: RetrievalIndex) -> None:
    p = FakeProvider(["{}"])
    ans = answer_question(
        "What is the dividend policy for preferred stock holders?", qa_index, FLT, p
    )
    assert ans.status is QAStatus.NOT_SUPPORTED and p.prompts == []  # no model call
    other = RetrievalFilter(frozenset({"0009999999-24-000001"}), T)
    assert answer_question("services revenue", qa_index, other, p).status is QAStatus.NOT_SUPPORTED


def test_qa_rejects_invented_numbers_and_foreign_citations(qa_index: RetrievalIndex) -> None:
    inv = _qa_provider(qa_index, "Services revenue grew 25% to $120 billion.", "services revenue")
    assert (
        answer_question("How did services revenue change?", qa_index, FLT, inv).status
        is QAStatus.NOT_SUPPORTED
    )
    foreign = FakeProvider(
        [
            json.dumps(
                {
                    "schema_version": "qa-schema/1",
                    "status": "answered",
                    "answer": "Revenue grew.",
                    "evidence_ids": ["chk-elsewhere"],
                }
            )
        ]
    )
    assert (
        answer_question("services revenue", qa_index, FLT, foreign).status is QAStatus.NOT_SUPPORTED
    )
    garbage = FakeProvider(["nope"])
    assert (
        answer_question("services revenue", qa_index, FLT, garbage).status is QAStatus.NOT_SUPPORTED
    )
