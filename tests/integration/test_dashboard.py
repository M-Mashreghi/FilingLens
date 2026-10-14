"""Task 6.2: Streamlit analyst dashboard (view models + headless AppTest)."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from filinglens.bootstrap import Container
from filinglens.domain.models import EvidenceSpan, Filing, Finding, Issuer, RunState
from filinglens.infrastructure.sec.reference import dataset_from_bytes
from filinglens.ui import viewmodels as vm

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "company_tickers_exchange.json"
APP = Path(__file__).resolve().parents[2] / "src" / "filinglens" / "ui" / "app.py"
K24 = "0001234567-24-000031"


@pytest.fixture
def container() -> Container:
    c = Container.in_memory()
    c.reference = dataset_from_bytes(
        FIX.read_bytes(), source="fx", retrieved_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    c.repos.issuers.upsert(Issuer(cik=320193, name="Apple Inc."))
    c.repos.filings.add(
        Filing(
            accession=K24.replace("1234567", "0320193"),
            cik=320193,
            form="10-K",
            filing_date=date(2024, 11, 1),
            accepted_at=datetime(2024, 10, 31, 22, tzinfo=UTC),
        )
    )
    c.repos.filings.add(
        Filing(
            accession="0000320193-24-000050",
            cik=320193,
            form="8-K",
            filing_date=date(2024, 10, 31),
            items=("2.02", "9.01"),
        )
    )
    return c


def _seed_run(c: Container) -> str:
    run = c.runs.create(
        cik=320193, as_of=datetime(2026, 1, 1, tzinfo=UTC), focal_accession="0000320193-24-000031"
    )
    c.repos.evidence.add(
        EvidenceSpan(
            evidence_id="e1",
            run_id=run.run_id,
            snapshot_id="a" * 64,
            accession="0000320193-24-000031",
            locator="/p[3]",
            text="Export controls could restrict sales.",
            section="1A",
        )
    )
    for fid, sev, ev in (
        ("f1", "high", ("e1",)),
        ("f2", "low", ("e1",)),
        ("f3", "medium", ("gone",)),
    ):
        c.repos.findings.add(
            Finding(
                finding_id=fid,
                run_id=run.run_id,
                finding_type=f"t{fid}",
                summary=f"s{fid}",
                severity=sev,
                confidence=0.7,
                evidence_ids=ev,
                interpretation_status="supported",
            )
        )
    return run.run_id


def test_search_and_ambiguity(container: Container) -> None:
    assert vm.search_issuers(container, "AAPL").status == "resolved"
    amb = vm.search_issuers(container, "SHR")
    assert amb.status == "ambiguous" and len(amb.choices) == 2 and "Choose" in amb.message
    assert vm.search_issuers(container, "ZZZZQ").status == "not_found"
    assert vm.search_issuers(Container.in_memory(), "AAPL").status == "unavailable"


def test_timeline_filters(container: Container) -> None:
    rows = vm.filing_timeline(container, 320193)
    assert [r.form for r in rows] == [
        "8-K",
        "10-K",
    ]  # newest public first (8-K: end-of-day ET fallback)
    assert [r.form for r in vm.filing_timeline(container, 320193, forms={"8-K"})] == ["8-K"]
    eightk = vm.filing_timeline(container, 320193, forms={"8-K"})[0]
    assert eightk.items == "2.02,9.01"
    assert vm.filing_timeline(container, 320193, as_of=datetime(2024, 1, 1, tzinfo=UTC)) == []


def test_failed_and_attention_states_visible(container: Container) -> None:
    rid = _seed_run(container)
    container.runs.transition(rid, RunState.RESOLVING)
    container.runs.transition(rid, RunState.BLOCKED_BY_SOURCE)
    other = container.runs.create(cik=320193, as_of=datetime(2026, 1, 1, tzinfo=UTC))
    container.runs.transition(other.run_id, RunState.FAILED, error="SEC returned 403")
    rows = {r.run_id: r for r in vm.run_rows(container, 320193)}
    assert rows[rid].needs_attention and "SEC source" in rows[rid].message
    assert rows[other.run_id].needs_attention and rows[other.run_id].message == "SEC returned 403"


def test_findings_drilldown_and_filters(container: Container) -> None:
    rid = _seed_run(container)
    views = vm.top_findings(container, rid)
    assert [v.finding_id for v in views] == ["f1", "f3", "f2"]  # severity order
    assert (
        views[0].evidence[0].text.startswith("Export controls")
        and views[0].evidence[0].locator == "/p[3]"
    )
    assert views[1].evidence[0].missing  # missing evidence surfaced, not hidden
    assert [v.finding_id for v in vm.top_findings(container, rid, min_severity="medium")] == [
        "f1",
        "f3",
    ]
    assert [v.finding_id for v in vm.top_findings(container, rid, sections={"1A"})] == ["f1", "f2"]
    assert [v.finding_id for v in vm.top_findings(container, rid, finding_types={"tf2"})] == ["f2"]


def test_streamlit_primary_flow_headless(container: Container) -> None:
    _seed_run(container)
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["container"] = container
    at.run()
    assert not at.exception
    at.text_input(key="issuer_query").input("AAPL").run()
    assert not at.exception
    assert any("Resolved to CIK 320193" in s.value for s in at.success)
    assert any("Filing timeline" in h.value for h in at.subheader)
    assert any("Top findings" in h.value for h in at.subheader)


def test_streamlit_ambiguity_flow_headless(container: Container) -> None:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["container"] = container
    at.run()
    at.text_input(key="issuer_query").input("SHR").run()
    assert not at.exception
    assert any("Ambiguous" in i.value for i in at.info)
    assert len(at.radio(key="issuer_pick").options) == 2


def test_ui_stays_thin() -> None:
    for path in (APP, APP.with_name("viewmodels.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        bad = [
            m
            for m in mods
            if m.startswith(
                (
                    "filinglens.parsing",
                    "filinglens.analytics",
                    "filinglens.xbrl",
                    "filinglens.infrastructure",
                    "httpx",
                    "requests",
                    "lxml",
                )
            )
        ]
        assert bad == [], (path.name, bad)
