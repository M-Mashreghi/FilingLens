"""Task 6.1: core FastAPI endpoints."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from filinglens.api.app import create_app
from filinglens.bootstrap import Container
from filinglens.config import Settings
from filinglens.domain.models import EvidenceSpan, Filing, FilingDocument, Finding, Issuer, Report
from filinglens.infrastructure.sec.reference import dataset_from_bytes

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "company_tickers_exchange.json"
SRC = Path(__file__).resolve().parents[2] / "src" / "filinglens"
K24 = "0001234567-24-000031"
K23 = "0001234567-23-000030"


@pytest.fixture
def env() -> tuple[TestClient, Container]:
    c = Container.in_memory()
    c.reference = dataset_from_bytes(
        FIX.read_bytes(), source="fixture", retrieved_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    c.repos.issuers.upsert(
        Issuer(cik=1234567, name="Example Devices Inc.", former_names=("Example Computer Corp",))
    )
    for acc, d, acc_at in (
        (K23, date(2023, 11, 3), datetime(2023, 11, 2, 22, tzinfo=UTC)),
        (K24, date(2024, 11, 1), datetime(2024, 10, 31, 22, tzinfo=UTC)),
    ):
        c.repos.filings.add(
            Filing(
                accession=acc,
                cik=1234567,
                form="10-K",
                filing_date=d,
                accepted_at=acc_at,
                primary_document="main.htm",
            )
        )
    c.repos.documents.add(
        FilingDocument(
            accession=K24, filename="main.htm", sequence=1, doc_type="10-K", role="primary"
        )
    )
    return TestClient(create_app(Settings(env="test"), container=c)), c


def test_health(env: tuple[TestClient, Container]) -> None:
    client, _ = env
    assert client.get("/healthz").json()["api"] == "api/1"


def test_issuer_endpoints(env: tuple[TestClient, Container]) -> None:
    client, _ = env
    r = client.get("/issuers/resolve", params={"q": "AAPL"}).json()
    assert (
        r["status"] == "resolved"
        and r["cik"] == 320193
        and r["source_version"].startswith("sha256:")
    )
    amb = client.get("/issuers/resolve", params={"q": "SHR"}).json()
    assert amb["status"] == "ambiguous" and amb["cik"] is None and len(amb["candidates"]) == 2
    alias = client.get("/issuers/resolve", params={"q": "Example Computer Corp"}).json()
    assert alias["cik"] == 1234567
    assert client.get("/issuers/1234567").json()["former_names"] == ["Example Computer Corp"]


def test_filings_pagination_and_as_of(env: tuple[TestClient, Container]) -> None:
    client, _ = env
    page = client.get("/issuers/1234567/filings", params={"limit": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["accession"] == K24
    page2 = client.get("/issuers/1234567/filings", params={"limit": 1, "offset": 1}).json()
    assert page2["items"][0]["accession"] == K23
    pit = client.get("/issuers/1234567/filings", params={"as_of": "2024-01-01T00:00:00Z"}).json()
    assert [f["accession"] for f in pit["items"]] == [K23]
    f = client.get(f"/filings/{K24.replace('-', '')}").json()
    assert f["accession"] == K24 and f["availability_basis"] == "accepted_timestamp"
    docs = client.get(f"/filings/{K24}/documents").json()
    assert docs[0]["role"] == "primary"


def test_run_lifecycle_and_errors(env: tuple[TestClient, Container]) -> None:
    client, _ = env
    created = client.post(
        "/runs", json={"cik": 1234567, "as_of": "2026-01-01T00:00:00Z", "focal_accession": K24}
    )
    assert created.status_code == 201
    rid = created.json()["run_id"]
    assert client.get(f"/runs/{rid}").json()["state"] == "Created"
    ok = client.post(f"/runs/{rid}/transitions", json={"target": "Resolving"})
    assert ok.json()["state"] == "Resolving"
    bad = client.post(f"/runs/{rid}/transitions", json={"target": "Completed"})
    assert bad.status_code == 409 and bad.headers["content-type"] == "application/problem+json"
    assert bad.json()["title"] == "Invalid State Transition"
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs", params={"state": "Resolving"}).json()["total"] == 1


def test_validation_error_contract(env: tuple[TestClient, Container]) -> None:
    client, _ = env
    r = client.post("/runs", json={"cik": -5, "as_of": "not-a-date"})
    assert r.status_code == 422 and r.json()["title"] == "Validation Error"
    naive = client.post("/runs", json={"cik": 1234567, "as_of": "2026-01-01T00:00:00"})
    assert naive.status_code == 422  # naive timestamps rejected by the domain
    assert client.get("/issuers/1234567/filings", params={"limit": 500}).status_code == 422


def test_findings_evidence_report(env: tuple[TestClient, Container]) -> None:
    client, c = env
    run = c.runs.create(cik=1234567, as_of=datetime(2026, 1, 1, tzinfo=UTC), focal_accession=K24)
    c.repos.evidence.add(
        EvidenceSpan(
            evidence_id="e1",
            run_id=run.run_id,
            snapshot_id="a" * 64,
            accession=K24,
            locator="/p[1]",
            text="Export controls",
            section="1A",
        )
    )
    c.repos.findings.add(
        Finding(
            finding_id="f1",
            run_id=run.run_id,
            finding_type="new_risk_factor",
            summary="s",
            severity="high",
            confidence=0.8,
            evidence_ids=("e1",),
            interpretation_status="supported",
        )
    )
    page = client.get(
        f"/runs/{run.run_id}/findings", params={"finding_type": "new_risk_factor"}
    ).json()
    assert page["total"] == 1 and page["items"][0]["evidence_ids"] == ["e1"]
    assert client.get(f"/runs/{run.run_id}/evidence/e1").json()["section"] == "1A"
    assert client.get("/runs/other/evidence/e1").status_code == 404
    assert client.get(f"/runs/{run.run_id}/report").status_code == 404
    c.repos.reports.add(Report(report_id="r1", run_id=run.run_id, version=1, body_markdown="# r"))
    assert client.get(f"/runs/{run.run_id}/report").json()["version"] == 1


def test_controllers_have_no_business_logic_imports() -> None:
    tree = ast.parse((SRC / "api" / "app.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    forbidden = (
        "filinglens.parsing",
        "filinglens.analytics",
        "filinglens.diff",
        "filinglens.xbrl",
        "filinglens.infrastructure.sec",
        "httpx",
        "sqlite3",
    )
    assert not [m for m in mods if m.startswith(forbidden)]
