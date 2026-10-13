"""Task 1.2: domain round-trip, idempotent accession identity and migrations."""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from filinglens.domain.errors import ConflictError
from filinglens.domain.models import (
    Alert,
    ComparisonRun,
    EvidenceSpan,
    Filing,
    FilingDocument,
    Finding,
    Issuer,
    Report,
    RunState,
    SecurityIdentifier,
    SourceSnapshot,
)
from filinglens.infrastructure.storage.sqlite import MIGRATIONS, Database, SqliteRepositories

T0 = datetime(2024, 11, 1, 18, 1, 36, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[2] / "src" / "filinglens"


@pytest.fixture
def repos() -> SqliteRepositories:
    return SqliteRepositories(Database(":memory:"))


def _filing(**kw: object) -> Filing:
    base: dict[str, object] = {
        "accession": "0000320193-24-000123",
        "cik": 320193,
        "form": "10-K",
        "filing_date": date(2024, 11, 1),
        "report_period": date(2024, 9, 28),
        "accepted_at": T0,
        "primary_document": "aapl-20240928.htm",
        "source": "submissions",
    }
    base.update(kw)
    return Filing(**base)  # type: ignore[arg-type]


def test_issuer_and_identifier_round_trip(repos: SqliteRepositories) -> None:
    issuer = Issuer(
        cik=320193, name="Apple Inc.", former_names=("APPLE COMPUTER INC",), created_at=T0
    )
    repos.issuers.upsert(issuer)
    assert repos.issuers.get(320193) == issuer
    sid = SecurityIdentifier(
        cik=320193,
        ticker="aapl",
        exchange="Nasdaq",
        source="sec_tickers",
        source_version="v1",
        retrieved_at=T0,
    )
    repos.identifiers.add(sid)
    repos.identifiers.add(sid)  # idempotent
    assert repos.identifiers.find_by_ticker("AAPL") == [sid]
    assert repos.identifiers.list_for_cik(320193)[0].ticker == "AAPL"


def test_filing_round_trip_and_idempotent_duplicate(repos: SqliteRepositories) -> None:
    f = _filing()
    assert repos.filings.add(f) == f
    assert repos.filings.add(f) == f
    assert repos.filings.get(f.accession) == f
    assert repos.filings.list_for_cik(320193) == [f]
    assert repos.db.query("SELECT COUNT(*) FROM filings")[0][0] == 1


def test_duplicate_accession_with_conflicting_content_raises(repos: SqliteRepositories) -> None:
    repos.filings.add(_filing())
    with pytest.raises(ConflictError):
        repos.filings.add(_filing(form="10-Q"))
    assert repos.filings.get("0000320193-24-000123").form == "10-K"  # type: ignore[union-attr]


def test_duplicate_accession_fills_missing_fields(repos: SqliteRepositories) -> None:
    repos.filings.add(_filing(accepted_at=None))
    merged = repos.filings.add(_filing())
    assert merged.accepted_at == T0
    assert repos.filings.get(merged.accession) == merged


def test_co_registrant_is_linked_not_duplicated(repos: SqliteRepositories) -> None:
    repos.filings.add(_filing())
    repos.filings.add(_filing(cik=1234567))
    assert repos.filings.ciks_for("0000320193-24-000123") == [320193, 1234567]
    assert repos.filings.get("0000320193-24-000123").cik == 320193  # type: ignore[union-attr]
    assert len(repos.filings.list_for_cik(1234567)) == 1


def test_amendment_is_a_separate_filing(repos: SqliteRepositories) -> None:
    repos.filings.add(_filing())
    amend = repos.filings.add(
        _filing(
            accession="0000320193-25-000001", form="10-K/A", amends_accession="0000320193-24-000123"
        )
    )
    assert amend.is_amendment
    assert len(repos.filings.list_for_cik(320193)) == 2
    assert repos.filings.get("0000320193-24-000123").form == "10-K"  # type: ignore[union-attr]


def test_documents_round_trip(repos: SqliteRepositories) -> None:
    d1 = FilingDocument(
        accession="0000000001-24-000001", filename="ex99.htm", sequence=2, doc_type="EX-99.1"
    )
    d0 = FilingDocument(
        accession="0000000001-24-000001",
        filename="main.htm",
        sequence=1,
        doc_type="10-K",
        role="primary",
    )
    repos.documents.add(d1)
    repos.documents.add(d0)
    assert repos.documents.list_for_accession("0000000001-24-000001") == [d0, d1]
    assert repos.documents.add(d0) == d0


def test_snapshot_is_never_overwritten(repos: SqliteRepositories) -> None:
    digest = hashlib.sha256(b"raw").hexdigest()
    s1 = SourceSnapshot(
        snapshot_id=digest,
        source_url="https://www.sec.gov/a",
        retrieved_at=T0,
        content_type="text/html",
        size_bytes=3,
        adapter="sec",
        adapter_version="1",
        storage_key=f"sha256/{digest}",
        accession="0000000001-24-000001",
    )
    s2 = SourceSnapshot(
        snapshot_id=digest,
        source_url="https://www.sec.gov/b",
        retrieved_at=T0 + timedelta(days=1),
        content_type="text/html",
        size_bytes=3,
        adapter="sec",
        adapter_version="2",
        storage_key=f"sha256/{digest}",
        accession="0000000001-24-000001",
    )
    repos.snapshots.add(s1)
    assert repos.snapshots.add(s2) == s1
    assert repos.snapshots.get(digest) == s1
    assert repos.snapshots.list_for_accession("0000000001-24-000001") == [s1]


def test_run_finding_evidence_alert_report_round_trip(repos: SqliteRepositories) -> None:
    run = ComparisonRun(
        run_id="r1",
        cik=320193,
        focal_accession="0000000001-24-000002",
        comparator_accession="0000000001-24-000001",
        as_of=T0,
    )
    repos.runs.add(run)
    with pytest.raises(ConflictError):
        repos.runs.add(run)
    assert repos.runs.get("r1") == run
    moved = repos.runs.save(run.__class__(**{**run.__dict__, "state": RunState.COLLECTING}))
    assert repos.runs.get("r1").state is RunState.COLLECTING  # type: ignore[union-attr]
    assert moved.state is RunState.COLLECTING

    ev = EvidenceSpan(
        evidence_id="e1",
        run_id="r1",
        snapshot_id="0" * 64,
        accession="0000000001-24-000002",
        locator="block:12",
        text="Revenue increased",
        section="Item 7",
    )
    repos.evidence.add(ev)
    finding = Finding(
        finding_id="f1",
        run_id="r1",
        finding_type="mdna_change",
        summary="s",
        severity="medium",
        confidence=0.7,
        evidence_ids=("e1",),
        interpretation_status="supported",
        open_questions=("q?",),
        created_at=T0,
    )
    repos.findings.add(finding)
    repos.findings.add(finding)  # identical content is idempotent
    with pytest.raises(ConflictError):
        repos.findings.add(Finding(**{**finding.__dict__, "summary": "different"}))
    assert repos.findings.list_for_run("r1") == [finding]
    assert repos.evidence.list_for_run("r1") == [ev]

    alert = Alert(
        alert_id="al1",
        kind="new_filing",
        priority="high",
        reason="r",
        dedupe_key="k1",
        created_at=T0,
    )
    repos.alerts.add(alert)
    dup = Alert(
        alert_id="al2",
        kind="new_filing",
        priority="high",
        reason="r",
        dedupe_key="k1",
        created_at=T0,
    )
    assert repos.alerts.add(dup) == alert
    assert repos.alerts.acknowledge("al1").acknowledged
    assert repos.alerts.list(acknowledged=False) == []

    repos.reports.add(
        Report(report_id="rep1", run_id="r1", version=1, body_markdown="# v1", created_at=T0)
    )
    repos.reports.add(
        Report(report_id="rep2", run_id="r1", version=2, body_markdown="# v2", created_at=T0)
    )
    with pytest.raises(ConflictError):
        repos.reports.add(Report(report_id="rep3", run_id="r1", version=2, body_markdown="x"))
    assert repos.reports.latest_for_run("r1").version == 2  # type: ignore[union-attr]


def test_timestamps_are_utc_and_naive_rejected() -> None:
    est = timezone(timedelta(hours=-5))
    f = _filing(accepted_at=datetime(2024, 11, 1, 13, 1, 36, tzinfo=est))
    assert f.accepted_at == T0 and f.accepted_at.tzinfo is UTC  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        _filing(accepted_at=datetime(2024, 11, 1, 18, 0))


def test_explicit_nullable_fields_round_trip(repos: SqliteRepositories) -> None:
    f = _filing(report_period=None, accepted_at=None, primary_document=None)
    repos.filings.add(f)
    got = repos.filings.get(f.accession)
    assert got is not None and got.report_period is None and got.accepted_at is None


def test_file_database_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "fl.db"
    db = Database(path)
    SqliteRepositories(db).filings.add(_filing())
    assert db.schema_version == MIGRATIONS[-1][0]
    db.close()
    db2 = Database(path)
    assert db2.schema_version == MIGRATIONS[-1][0]
    assert SqliteRepositories(db2).filings.get("0000320193-24-000123") is not None
    db2.close()


def test_migrations_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        Database(":memory:", migrations=[(2, "SELECT 1"), (1, "SELECT 1")])


FORBIDDEN = {"fastapi", "streamlit", "sqlite3", "httpx", "starlette", "uvicorn"}


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_inner_layers_do_not_import_frameworks(layer: str) -> None:
    for path in (SRC / layer).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert root not in FORBIDDEN, f"{path.name} imports {name}"
                assert not name.startswith("filinglens.infrastructure"), f"{path.name} -> {name}"
                assert not name.startswith(("filinglens.api", "filinglens.ui")), path.name
