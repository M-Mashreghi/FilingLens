"""Task 6.4: watchlist and new-filing workflow."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from filinglens.application.metadata import FilingMetadataService
from filinglens.application.runs import RunService
from filinglens.application.watchlist import WatchlistPoller, run_id_for
from filinglens.domain.models import Filing, Issuer, Watchlist
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

ISSUER = Issuer(cik=1234567, name="Example Devices Inc.")
SINCE = datetime(2024, 12, 1, tzinfo=UTC)
NOW = datetime(2025, 3, 1, tzinfo=UTC)


def filing(acc: str, form: str, accepted: datetime) -> Filing:
    return Filing(
        accession=acc,
        cik=1234567,
        form=form,
        filing_date=accepted.date(),
        accepted_at=accepted,
        report_period=date(2024, 12, 28),
    )


OLD_K = filing("0001234567-24-000031", "10-K", datetime(2024, 10, 31, 22, tzinfo=UTC))
NEW_Q = filing("0001234567-25-000004", "10-Q", datetime(2025, 1, 30, 21, tzinfo=UTC))
NEW_8K = filing("0001234567-25-000006", "8-K", datetime(2025, 2, 1, 21, tzinfo=UTC))


@dataclass
class Batch:
    issuer: Issuer
    filings: list[Filing]
    source: str = "sec-submissions"
    source_versions: tuple[str, ...] = ("sha256:x",)


class Feed:
    def __init__(self, filings: list[Filing]) -> None:
        self.filings = filings
        self.fail = False

    def __call__(self, cik: int) -> Batch:
        if self.fail:
            raise RuntimeError("SEC 503")
        return Batch(ISSUER, list(self.filings))


def poller(repos: SqliteRepositories, feed: Feed) -> WatchlistPoller:
    return WatchlistPoller(
        feed,
        FilingMetadataService(repos.filings, repos.issuers),
        repos.watchlists,
        RunService(repos.runs, repos.snapshots, repos.evidence),
        clock=lambda: NOW,
    )


def setup(forms: tuple[str, ...] = ("10-Q", "10-K")) -> tuple[SqliteRepositories, Feed]:
    repos = SqliteRepositories(Database(":memory:"))
    repos.watchlists.add(Watchlist(watchlist_id="wl1", cik=1234567, forms=forms, since=SINCE))
    return repos, Feed([OLD_K, NEW_Q, NEW_8K])


def test_unseen_filing_creates_exactly_one_workflow() -> None:
    repos, feed = setup()
    res = poller(repos, feed).poll_once()
    assert res.enqueued == [
        run_id_for("wl1", NEW_Q.accession)
    ]  # old 10-K before `since`, 8-K filtered
    run = repos.runs.get(res.enqueued[0])
    assert run is not None and run.focal_accession == NEW_Q.accession
    (r,) = repos.watchlists.reservations("wl1")
    assert r["status"] == "enqueued" and r["available_at"] == NEW_Q.available_at.isoformat()


def test_repeated_polling_does_not_duplicate() -> None:
    repos, feed = setup()
    p = poller(repos, feed)
    p.poll_once()
    second = p.poll_once()
    assert second.enqueued == [] and second.skipped_already_claimed == 1
    assert len(repos.runs.list()) == 1


def test_concurrent_pollers_do_not_double_enqueue(tmp_path: Path) -> None:
    path = tmp_path / "fl.db"
    base = SqliteRepositories(Database(path))
    base.watchlists.add(
        Watchlist(watchlist_id="wl1", cik=1234567, forms=("10-Q", "8-K"), since=SINCE)
    )
    results = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        repos = SqliteRepositories(Database(path))  # separate connection per poller
        barrier.wait()
        results.append(poller(repos, Feed([OLD_K, NEW_Q, NEW_8K])).poll_once())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    enqueued = [rid for r in results for rid in r.enqueued]
    assert sorted(enqueued) == sorted(
        {run_id_for("wl1", NEW_Q.accession), run_id_for("wl1", NEW_8K.accession)}
    )
    assert len(SqliteRepositories(Database(path)).runs.list()) == 2


def test_failures_retry_without_losing_accession_state() -> None:
    repos, feed = setup()
    p = poller(repos, feed)
    original = p.runs.create

    def broken(**kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("db hiccup")

    p.runs.create = broken  # type: ignore[method-assign]
    first = p.poll_once()
    assert first.enqueued == [] and first.failures
    (r,) = repos.watchlists.reservations("wl1")
    assert r["status"] == "failed" and r["last_error"] == "db hiccup" and r["attempts"] == 1
    p.runs.create = original  # type: ignore[method-assign]
    second = p.poll_once()
    assert second.enqueued == [run_id_for("wl1", NEW_Q.accession)]
    (r,) = repos.watchlists.reservations("wl1")
    assert r["status"] == "enqueued" and r["attempts"] == 2


def test_source_outage_reserves_nothing() -> None:
    repos, feed = setup()
    feed.fail = True
    res = poller(repos, feed).poll_once()
    assert res.failures and repos.watchlists.reservations("wl1") == []
    feed.fail = False
    assert len(poller(repos, feed).poll_once().enqueued) == 1


def test_crash_between_create_and_mark_is_idempotent() -> None:
    repos, feed = setup()
    p = poller(repos, feed)
    rid = run_id_for("wl1", NEW_Q.accession)
    p.runs.id_factory = lambda: rid
    p.runs.create(
        cik=1234567, as_of=NOW, focal_accession=NEW_Q.accession
    )  # run exists, no reservation
    res = p.poll_once()
    assert res.enqueued == [rid] and len(repos.runs.list()) == 1


def test_watchlist_form_filter() -> None:
    wl = Watchlist(watchlist_id="w", cik=1234567, forms=("8-k",), since=SINCE)
    assert wl.wants(NEW_8K) and not wl.wants(NEW_Q) and not wl.wants(OLD_K)
