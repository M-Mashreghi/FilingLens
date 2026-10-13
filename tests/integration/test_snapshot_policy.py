"""Task 2.8: snapshot deduplication, cache and source-version policy."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from filinglens.application.retention import SnapshotRetentionService
from filinglens.application.retrieval import DocumentRetriever, RetrievalStatus
from filinglens.application.runs import RunService
from filinglens.domain.errors import ImmutabilityError
from filinglens.domain.models import EvidenceSpan, FilingDocument, RunState
from filinglens.infrastructure.sec.client import InMemoryHttpCache, RateGovernor, SecHttpClient
from filinglens.infrastructure.storage.raw_store import FileSystemRawStore
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

ACC = "0001234567-24-000031"
URL = "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000031/main.htm"
DOC = FilingDocument(
    accession=ACC, filename="main.htm", sequence=1, doc_type="10-K", role="primary", url=URL
)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Env:
    def __init__(self, tmp_path: Path, ttl: timedelta = timedelta(0)) -> None:
        self.body = b"<html>version one</html>"
        self.now = T0
        self.network_calls = 0

        def handler(req: httpx.Request) -> httpx.Response:
            self.network_calls += 1
            return httpx.Response(200, content=self.body, headers={"Content-Type": "text/html"})

        self.client = SecHttpClient(
            user_agent="t t@example.com",
            live=False,
            transport=httpx.MockTransport(handler),
            governor=RateGovernor(1000),
            cache=InMemoryHttpCache(),
            cache_ttl=ttl,
            now=lambda: self.now,
        )
        self.repos = SqliteRepositories(Database(":memory:"))
        self.store = FileSystemRawStore(tmp_path / "raw")
        self.retriever = DocumentRetriever(
            self.client,
            self.store,
            self.repos.snapshots,
            attempts=self.repos.attempts,
            clock=lambda: self.now,
        )

    def fetch(self):  # type: ignore[no-untyped-def]
        self.now += timedelta(hours=1)
        return self.retriever.retrieve(DOC)


def test_repeat_retrieval_is_idempotent(tmp_path: Path) -> None:
    env = Env(tmp_path)
    a, b = env.fetch(), env.fetch()
    assert a.snapshot == b.snapshot  # same bytes -> same snapshot (first capture kept)
    assert env.repos.db.query("SELECT COUNT(*) FROM source_snapshots")[0][0] == 1
    attempts = env.repos.attempts.list_for_document(ACC, "main.htm")
    assert len(attempts) == 2  # retrieval events are recorded separately
    assert [x.attempted_at for x in attempts] == [T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    assert env.repos.attempts.snapshot_versions(ACC, "main.htm") == [a.snapshot.snapshot_id]  # type: ignore[union-attr]


def test_changed_bytes_create_new_snapshot_without_overwriting(tmp_path: Path) -> None:
    env = Env(tmp_path)
    first = env.fetch().snapshot
    env.body = b"<html>version two (re-published)</html>"
    second = env.fetch().snapshot
    assert first is not None and second is not None
    assert first.snapshot_id != second.snapshot_id
    assert env.repos.snapshots.get(first.snapshot_id) == first
    assert first.verify(env.store.get(first.storage_key))  # original bytes intact
    assert env.repos.attempts.snapshot_versions(ACC, "main.htm") == [
        first.snapshot_id,
        second.snapshot_id,
    ]


def test_cache_affects_network_only_not_provenance(tmp_path: Path) -> None:
    env = Env(tmp_path, ttl=timedelta(days=1))
    a = env.fetch()
    env.body = b"<html>changed upstream but cache still fresh</html>"
    b = env.fetch()
    assert env.network_calls == 1
    assert b.snapshot == a.snapshot  # bytes identical -> identity identical
    attempts = env.repos.attempts.list_for_document(ACC, "main.htm")
    assert [x.from_cache for x in attempts] == [False, True]
    # Snapshot provenance keeps the original capture time, not the cache read.
    assert b.snapshot is not None and b.snapshot.retrieved_at == a.snapshot.retrieved_at  # type: ignore[union-attr]


def test_failed_attempt_logged_without_snapshot(tmp_path: Path) -> None:
    env = Env(tmp_path)
    bad = FilingDocument(accession=ACC, filename="nourl.htm", role="exhibit")
    out = env.retriever.retrieve(bad)
    assert out.status is RetrievalStatus.FAILED
    (attempt,) = env.repos.attempts.list_for_document(ACC, "nourl.htm")
    assert attempt.status == "failed" and attempt.snapshot_id is None


def _completed_run(env: Env, snapshot_id: str) -> str:
    counter = itertools.count()
    svc = RunService(
        env.repos.runs,
        env.repos.snapshots,
        env.repos.evidence,
        id_factory=lambda: f"r{next(counter)}",
    )
    run = svc.create(cik=1234567, as_of=T0 + timedelta(days=1), focal_accession=ACC)
    svc.update_manifest(
        run.run_id,
        snapshot_ids=[snapshot_id],
        component_versions={
            "parser": "p",
            "diff": "d",
            "calculation": "c",
            "comparator_policy": "cp",
            "availability_policy": "a",
        },
    )
    env.repos.evidence.add(
        EvidenceSpan(
            evidence_id="e1",
            run_id=run.run_id,
            snapshot_id=snapshot_id,
            accession=ACC,
            locator="b:1",
            text="x",
        )
    )
    for state in (
        RunState.RESOLVING,
        RunState.COLLECTING,
        RunState.PARSING,
        RunState.ALIGNING,
        RunState.ANALYZING,
        RunState.REVIEWING,
        RunState.COMPLETED,
    ):
        svc.transition(run.run_id, state)
    return run.run_id


def test_completed_report_never_loses_required_snapshot(tmp_path: Path) -> None:
    env = Env(tmp_path)
    used = env.fetch().snapshot
    env.body = b"<html>unused newer version</html>"
    unused = env.fetch().snapshot
    assert used is not None and unused is not None
    _completed_run(env, used.snapshot_id)
    retention = SnapshotRetentionService(
        env.repos.runs, env.repos.evidence, env.repos.snapshots, env.store
    )
    assert retention.pinned_snapshot_ids() == {used.snapshot_id}
    with pytest.raises(ImmutabilityError):
        retention.purge([used.snapshot_id, unused.snapshot_id])
    assert env.store.exists(used.storage_key) and env.store.exists(unused.storage_key)
    result = retention.purge([unused.snapshot_id])
    assert result.purged == (unused.snapshot_id,)
    assert not env.store.exists(unused.storage_key)
    assert retention.missing_pinned() == []
    assert used.verify(env.store.get(used.storage_key))


def test_purged_bytes_reattach_on_identical_refetch(tmp_path: Path) -> None:
    env = Env(tmp_path)
    snap = env.fetch().snapshot
    assert snap is not None
    retention = SnapshotRetentionService(
        env.repos.runs, env.repos.evidence, env.repos.snapshots, env.store
    )
    retention.purge([snap.snapshot_id])
    again = env.fetch().snapshot
    assert again == snap and env.store.exists(snap.storage_key)
