"""Watchlist new-filing workflow (``watchlist-poller/1``).

Each poll fetches submissions metadata (through the governed SEC adapter), merges it
idempotently, then for every unseen filing matching a watchlist:

1. atomically *reserves* ``(watchlist, accession)`` — concurrent pollers cannot both win;
2. creates exactly one analysis run with a deterministic run id;
3. marks the reservation ``enqueued`` (or ``failed`` with the error, retried next poll).

The accepted/public timestamp is kept on the reservation; failures never lose accession state.
"""

from __future__ import annotations

import builtins
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from filinglens.application.metadata import FilingMetadataService
from filinglens.application.runs import RunService
from filinglens.domain.errors import ConflictError
from filinglens.domain.models import ComparisonRun, Filing, Issuer, Watchlist
from filinglens.domain.time import utcnow

POLLER_VERSION = "watchlist-poller/1"


class SubmissionsBatchLike(Protocol):
    @property
    def issuer(self) -> Issuer: ...
    @property
    def filings(self) -> list[Filing]: ...
    @property
    def source(self) -> str: ...
    @property
    def source_versions(self) -> tuple[str, ...]: ...


class WatchlistStore(Protocol):
    def list(self) -> list[Watchlist]: ...
    def reserve(self, watchlist_id: str, filing: Filing, now: datetime) -> bool: ...
    def mark(
        self,
        watchlist_id: str,
        accession: str,
        status: str,
        now: datetime,
        *,
        run_id: str | None = None,
        error: str | None = None,
    ) -> None: ...
    def reservations(self, watchlist_id: str) -> builtins.list[dict[str, Any]]: ...


def run_id_for(watchlist_id: str, accession: str) -> str:
    return "wl-" + hashlib.sha256(f"{watchlist_id}|{accession}".encode()).hexdigest()[:20]


@dataclass
class PollResult:
    enqueued: list[str] = field(default_factory=list)  # run ids
    skipped_already_claimed: int = 0
    failures: list[str] = field(default_factory=list)


class WatchlistPoller:
    def __init__(
        self,
        fetch_submissions: Callable[[int], SubmissionsBatchLike],
        metadata: FilingMetadataService,
        watchlists: WatchlistStore,
        runs: RunService,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.fetch = fetch_submissions
        self.metadata = metadata
        self.watchlists = watchlists
        self.runs = runs
        self.clock = clock

    def poll_once(self) -> PollResult:
        result = PollResult()
        for wl in self.watchlists.list():
            if not wl.active:
                continue
            try:
                batch = self.fetch(wl.cik)
                self.metadata.ingest(
                    batch.issuer,
                    batch.filings,
                    source=batch.source,
                    source_versions=batch.source_versions,
                )
            except Exception as exc:  # source outage: retry on next poll, nothing reserved
                result.failures.append(f"{wl.watchlist_id}: fetch failed: {exc}")
                continue
            for filing in sorted(batch.filings, key=lambda f: (f.available_at, f.accession)):
                if not wl.wants(filing):
                    continue
                now = self.clock()
                if not self.watchlists.reserve(wl.watchlist_id, filing, now):
                    result.skipped_already_claimed += 1
                    continue
                rid = run_id_for(wl.watchlist_id, filing.accession)
                try:
                    run = self._create_run(rid, wl, filing, now)
                except Exception as exc:
                    self.watchlists.mark(
                        wl.watchlist_id, filing.accession, "failed", now, error=str(exc)
                    )
                    result.failures.append(f"{filing.accession}: {exc}")
                    continue
                self.watchlists.mark(
                    wl.watchlist_id, filing.accession, "enqueued", now, run_id=run.run_id
                )
                result.enqueued.append(run.run_id)
        return result

    def _create_run(self, rid: str, wl: Watchlist, filing: Filing, now: datetime) -> ComparisonRun:
        existing = self.runs.runs.get(rid)
        if existing is not None:  # retry after a crash between create and mark
            return existing
        factory = self.runs.id_factory
        self.runs.id_factory = lambda: rid
        try:
            return self.runs.create(
                cik=wl.cik, as_of=max(now, filing.available_at), focal_accession=filing.accession
            )
        except ConflictError:
            run = self.runs.runs.get(rid)
            assert run is not None
            return run
        finally:
            self.runs.id_factory = factory
