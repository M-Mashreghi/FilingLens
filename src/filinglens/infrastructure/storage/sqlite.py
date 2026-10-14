"""SQLite persistence for core domain entities.

Each table stores indexed identity/filter columns plus the full entity as a JSON ``payload``.

Migration policy: migrations are forward-only, numbered and append-only. Applied versions
are recorded in ``schema_migrations``; an existing migration is never edited after release.
"""

from __future__ import annotations

import builtins
import dataclasses
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from filinglens.domain.accession import normalize_accession as _acc
from filinglens.domain.errors import ConflictError, ImmutabilityError, NotFoundError
from filinglens.domain.models import (
    Alert,
    ComparisonRun,
    EvidenceSpan,
    Filing,
    FilingDocument,
    Finding,
    Issuer,
    Report,
    RetrievalAttempt,
    RunState,
    SecurityIdentifier,
    SourceSnapshot,
    Watchlist,
)
from filinglens.domain.run_lifecycle import RunManifest
from filinglens.domain.time import utcnow
from filinglens.infrastructure.storage.codec import dumps, loads

T = TypeVar("T")

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE issuers (cik INTEGER PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE security_identifiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cik INTEGER NOT NULL, ticker TEXT NOT NULL, exchange TEXT,
            source_version TEXT NOT NULL, payload TEXT NOT NULL,
            UNIQUE (cik, ticker, exchange, source_version)
        );
        CREATE INDEX ix_secid_ticker ON security_identifiers (ticker);
        CREATE TABLE filings (
            accession TEXT PRIMARY KEY, cik INTEGER NOT NULL, form TEXT NOT NULL,
            filing_date TEXT NOT NULL, accepted_at TEXT, payload TEXT NOT NULL
        );
        CREATE INDEX ix_filings_cik ON filings (cik, filing_date);
        CREATE TABLE filing_issuers (
            accession TEXT NOT NULL REFERENCES filings(accession),
            cik INTEGER NOT NULL, PRIMARY KEY (accession, cik)
        );
        CREATE TABLE filing_documents (
            accession TEXT NOT NULL, filename TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY (accession, filename)
        );
        CREATE TABLE source_snapshots (
            snapshot_id TEXT PRIMARY KEY, accession TEXT, payload TEXT NOT NULL
        );
        CREATE INDEX ix_snapshots_accession ON source_snapshots (accession);
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, cik INTEGER NOT NULL, state TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE findings (
            finding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX ix_findings_run ON findings (run_id);
        CREATE TABLE evidence_spans (
            evidence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX ix_evidence_run ON evidence_spans (run_id);
        CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
            acknowledged INTEGER NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE reports (
            report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, version INTEGER NOT NULL,
            payload TEXT NOT NULL, UNIQUE (run_id, version)
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE run_manifests (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE filing_observations (
            accession TEXT NOT NULL REFERENCES filings(accession),
            source TEXT NOT NULL, source_version TEXT NOT NULL, observed_at TEXT NOT NULL,
            PRIMARY KEY (accession, source, source_version)
        );
        """,
    ),
    (
        4,
        """
        CREATE TABLE retrieval_attempts (
            attempt_id TEXT PRIMARY KEY, accession TEXT, filename TEXT,
            attempted_at TEXT NOT NULL, snapshot_id TEXT, payload TEXT NOT NULL
        );
        CREATE INDEX ix_attempts_doc ON retrieval_attempts (accession, filename, attempted_at);
        """,
    ),
    (
        5,
        """
        CREATE TABLE report_inputs (
            run_id TEXT PRIMARY KEY, inputs_hash TEXT NOT NULL, inputs_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        6,
        """
        CREATE TABLE watchlists (
            watchlist_id TEXT PRIMARY KEY, cik INTEGER NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE filing_reservations (
            watchlist_id TEXT NOT NULL, accession TEXT NOT NULL, cik INTEGER NOT NULL,
            status TEXT NOT NULL, run_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, available_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (watchlist_id, accession)
        );
        """,
    ),
]


class Database:
    """Owns a SQLite connection and applies migrations on open."""

    def __init__(
        self, path: str | Path = ":memory:", migrations: Sequence[tuple[int, str]] = MIGRATIONS
    ) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=30
        )
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate(migrations)

    def _migrate(self, migrations: Sequence[tuple[int, str]]) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")}
        versions = [v for v, _ in migrations]
        if versions != sorted(versions) or len(set(versions)) != len(versions):
            raise ValueError("migrations must be strictly increasing")
        for version, sql in migrations:
            if version in applied:
                continue
            with self.transaction() as cur:
                for statement in (s for s in sql.split(";") if s.strip()):
                    cur.execute(statement)
                cur.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)",
                    (version, utcnow().isoformat()),
                )

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")
            finally:
                cur.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self.conn.close()


def _rows(db: Database, cls: type[T], sql: str, params: Sequence[Any] = ()) -> list[T]:
    return [loads(cls, r[0]) for r in db.query(sql, params)]


def _one(db: Database, cls: type[T], sql: str, params: Sequence[Any] = ()) -> T | None:
    rows = _rows(db, cls, sql, params)
    return rows[0] if rows else None


class SqliteIssuerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, issuer: Issuer) -> Issuer:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO issuers (cik, payload) VALUES (?, ?) "
                "ON CONFLICT(cik) DO UPDATE SET payload = excluded.payload",
                (issuer.cik, dumps(issuer)),
            )
        return issuer

    def get(self, cik: int) -> Issuer | None:
        return _one(self.db, Issuer, "SELECT payload FROM issuers WHERE cik = ?", (cik,))

    def list(self) -> list[Issuer]:
        return _rows(self.db, Issuer, "SELECT payload FROM issuers ORDER BY cik")


class SqliteSecurityIdentifierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, identifier: SecurityIdentifier) -> SecurityIdentifier:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO security_identifiers "
                "(cik, ticker, exchange, source_version, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    identifier.cik,
                    identifier.ticker,
                    identifier.exchange,
                    identifier.source_version,
                    dumps(identifier),
                ),
            )
        return identifier

    def list_for_cik(self, cik: int) -> list[SecurityIdentifier]:
        return _rows(
            self.db,
            SecurityIdentifier,
            "SELECT payload FROM security_identifiers WHERE cik = ? ORDER BY id",
            (cik,),
        )

    def find_by_ticker(self, ticker: str) -> list[SecurityIdentifier]:
        return _rows(
            self.db,
            SecurityIdentifier,
            "SELECT payload FROM security_identifiers WHERE ticker = ? ORDER BY id",
            (ticker.upper(),),
        )


def merge_filled(existing: T, incoming: T, identity: str) -> T:
    """Merge ``incoming`` into ``existing``: ``None`` fields may be filled, but any
    conflicting non-null value raises :class:`ConflictError`. Returns the merged entity."""
    updates: dict[str, Any] = {}
    for f in dataclasses.fields(existing):  # type: ignore[arg-type]
        if not f.init:  # derived fields are recomputed by __post_init__
            continue
        old, new = getattr(existing, f.name), getattr(incoming, f.name)
        if old == new or new is None:
            continue
        if old is None:
            updates[f.name] = new
            continue
        raise ConflictError(f"{identity}: field {f.name!r} conflicts ({old!r} != {new!r})")
    return dataclasses.replace(existing, **updates) if updates else existing  # type: ignore[type-var]


class SqliteFilingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, filing: Filing) -> Filing:
        with self.db.transaction() as cur:
            row = cur.execute(
                "SELECT payload FROM filings WHERE accession = ?", (filing.accession,)
            ).fetchone()
            if row is None:
                merged = filing
                cur.execute(
                    "INSERT INTO filings (accession, cik, form, filing_date, accepted_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        filing.accession,
                        filing.cik,
                        filing.form,
                        filing.filing_date.isoformat(),
                        filing.accepted_at.isoformat() if filing.accepted_at else None,
                        dumps(filing),
                    ),
                )
            else:
                existing = loads(Filing, row[0])
                merged = _merge_filing(existing, filing)
                if merged != existing:
                    cur.execute(
                        "UPDATE filings SET accepted_at = ?, payload = ? WHERE accession = ?",
                        (
                            merged.accepted_at.isoformat() if merged.accepted_at else None,
                            dumps(merged),
                            merged.accession,
                        ),
                    )
            cur.execute(
                "INSERT OR IGNORE INTO filing_issuers (accession, cik) VALUES (?, ?)",
                (filing.accession, filing.cik),
            )
        return merged

    def get(self, accession: str) -> Filing | None:
        return _one(
            self.db, Filing, "SELECT payload FROM filings WHERE accession = ?", (_acc(accession),)
        )

    def list_for_cik(self, cik: int) -> list[Filing]:
        return _rows(
            self.db,
            Filing,
            "SELECT f.payload FROM filings f JOIN filing_issuers fi ON f.accession = fi.accession "
            "WHERE fi.cik = ? ORDER BY f.filing_date, f.accession",
            (cik,),
        )

    def link_issuer(self, accession: str, cik: int) -> None:
        accession = _acc(accession)
        if self.get(accession) is None:
            raise NotFoundError(accession)
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO filing_issuers (accession, cik) VALUES (?, ?)",
                (accession, cik),
            )

    def record_observation(
        self, accession: str, source: str, source_version: str, observed_at: datetime
    ) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO filing_observations VALUES (?, ?, ?, ?)",
                (_acc(accession), source, source_version, observed_at.isoformat()),
            )

    def observations(self, accession: str) -> list[tuple[str, str, datetime]]:
        return [
            (r[0], r[1], datetime.fromisoformat(r[2]))
            for r in self.db.query(
                "SELECT source, source_version, observed_at FROM filing_observations "
                "WHERE accession = ? ORDER BY observed_at, source",
                (_acc(accession),),
            )
        ]

    def ciks_for(self, accession: str) -> list[int]:
        return [
            int(r[0])
            for r in self.db.query(
                "SELECT cik FROM filing_issuers WHERE accession = ? ORDER BY cik",
                (_acc(accession),),
            )
        ]


def _merge_filing(existing: Filing, incoming: Filing) -> Filing:
    # A filing discovered under a co-registrant CIK is the same accession; keep the first
    # filer as primary and record the other via filing_issuers.
    if incoming.cik != existing.cik:
        incoming = dataclasses.replace(incoming, cik=existing.cik)
    if incoming.source != existing.source:
        incoming = dataclasses.replace(incoming, source=existing.source)
    return merge_filled(existing, incoming, f"filing {existing.accession}")


class SqliteFilingDocumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, document: FilingDocument) -> FilingDocument:
        with self.db.transaction() as cur:
            row = cur.execute(
                "SELECT payload FROM filing_documents WHERE accession = ? AND filename = ?",
                (document.accession, document.filename),
            ).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO filing_documents (accession, filename, payload) VALUES (?, ?, ?)",
                    (document.accession, document.filename, dumps(document)),
                )
                return document
            existing = loads(FilingDocument, row[0])
            if existing.role == "unknown" and document.role != "unknown":
                existing = dataclasses.replace(existing, role=document.role)
            incoming = dataclasses.replace(document, role=existing.role)
            merged = merge_filled(
                existing, incoming, f"document {document.accession}/{document.filename}"
            )
            cur.execute(
                "UPDATE filing_documents SET payload = ? WHERE accession = ? AND filename = ?",
                (dumps(merged), merged.accession, merged.filename),
            )
            return merged

    def get(self, accession: str, filename: str) -> FilingDocument | None:
        return _one(
            self.db,
            FilingDocument,
            "SELECT payload FROM filing_documents WHERE accession = ? AND filename = ?",
            (_acc(accession), filename),
        )

    def list_for_accession(self, accession: str) -> list[FilingDocument]:
        docs = _rows(
            self.db,
            FilingDocument,
            "SELECT payload FROM filing_documents WHERE accession = ?",
            (_acc(accession),),
        )
        return sorted(docs, key=lambda d: (d.sequence is None, d.sequence or 0, d.filename))


class SqliteSnapshotRepository:
    """Snapshots are content-addressed; an existing id is never overwritten."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        with self.db.transaction() as cur:
            row = cur.execute(
                "SELECT payload FROM source_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if row is not None:
                return loads(SourceSnapshot, row[0])
            cur.execute(
                "INSERT INTO source_snapshots (snapshot_id, accession, payload) VALUES (?, ?, ?)",
                (snapshot.snapshot_id, snapshot.accession, dumps(snapshot)),
            )
        return snapshot

    def get(self, snapshot_id: str) -> SourceSnapshot | None:
        return _one(
            self.db,
            SourceSnapshot,
            "SELECT payload FROM source_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )

    def list_for_accession(self, accession: str) -> list[SourceSnapshot]:
        return _rows(
            self.db,
            SourceSnapshot,
            "SELECT payload FROM source_snapshots WHERE accession = ? ORDER BY snapshot_id",
            (_acc(accession),),
        )


class SqliteRunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, run: ComparisonRun) -> ComparisonRun:
        with self.db.transaction() as cur:
            try:
                cur.execute(
                    "INSERT INTO runs (run_id, cik, state, payload) VALUES (?, ?, ?, ?)",
                    (run.run_id, run.cik, run.state.value, dumps(run)),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"run {run.run_id} already exists") from exc
        return run

    def get(self, run_id: str) -> ComparisonRun | None:
        return _one(self.db, ComparisonRun, "SELECT payload FROM runs WHERE run_id = ?", (run_id,))

    def save(self, run: ComparisonRun) -> ComparisonRun:
        """Persist a changed run. Completed runs are immutable."""
        with self.db.transaction() as cur:
            self._assert_mutable(cur, run.run_id)
            cur.execute(
                "UPDATE runs SET state = ?, payload = ? WHERE run_id = ?",
                (run.state.value, dumps(run), run.run_id),
            )
        return run

    def save_manifest(self, manifest: RunManifest) -> RunManifest:
        with self.db.transaction() as cur:
            self._assert_mutable(cur, manifest.run_id)
            cur.execute(
                "INSERT INTO run_manifests (run_id, payload) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload",
                (manifest.run_id, dumps(manifest)),
            )
        return manifest

    def get_manifest(self, run_id: str) -> RunManifest | None:
        return _one(
            self.db, RunManifest, "SELECT payload FROM run_manifests WHERE run_id = ?", (run_id,)
        )

    @staticmethod
    def _assert_mutable(cur: sqlite3.Cursor, run_id: str) -> None:
        row = cur.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(run_id)
        if row[0] == RunState.COMPLETED.value:
            raise ImmutabilityError(f"run {run_id} is completed and immutable")

    def list(self) -> list[ComparisonRun]:
        return _rows(self.db, ComparisonRun, "SELECT payload FROM runs ORDER BY run_id")


class SqliteFindingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, finding: Finding) -> Finding:
        _insert_unique(
            self.db, "findings", "finding_id", finding.finding_id, finding.run_id, finding
        )
        return finding

    def get(self, finding_id: str) -> Finding | None:
        return _one(
            self.db, Finding, "SELECT payload FROM findings WHERE finding_id = ?", (finding_id,)
        )

    def list_for_run(self, run_id: str) -> list[Finding]:
        return _rows(
            self.db,
            Finding,
            "SELECT payload FROM findings WHERE run_id = ? ORDER BY finding_id",
            (run_id,),
        )


class SqliteEvidenceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, evidence: EvidenceSpan) -> EvidenceSpan:
        _insert_unique(
            self.db,
            "evidence_spans",
            "evidence_id",
            evidence.evidence_id,
            evidence.run_id,
            evidence,
        )
        return evidence

    def get(self, evidence_id: str) -> EvidenceSpan | None:
        return _one(
            self.db,
            EvidenceSpan,
            "SELECT payload FROM evidence_spans WHERE evidence_id = ?",
            (evidence_id,),
        )

    def list_for_run(self, run_id: str) -> list[EvidenceSpan]:
        return _rows(
            self.db,
            EvidenceSpan,
            "SELECT payload FROM evidence_spans WHERE run_id = ? ORDER BY evidence_id",
            (run_id,),
        )


def _insert_unique(
    db: Database, table: str, key: str, key_value: str, run_id: str, obj: Any
) -> None:
    payload = dumps(obj)
    with db.transaction() as cur:
        row = cur.execute(f"SELECT payload FROM {table} WHERE {key} = ?", (key_value,)).fetchone()
        if row is not None:
            if row[0] != payload:
                raise ConflictError(f"{table}.{key_value} already exists with different content")
            return
        cur.execute(
            f"INSERT INTO {table} ({key}, run_id, payload) VALUES (?, ?, ?)",
            (key_value, run_id, payload),
        )


class SqliteAlertRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, alert: Alert) -> Alert:
        with self.db.transaction() as cur:
            row = cur.execute(
                "SELECT payload FROM alerts WHERE dedupe_key = ?", (alert.dedupe_key,)
            ).fetchone()
            if row is not None:
                return loads(Alert, row[0])
            cur.execute(
                "INSERT INTO alerts (alert_id, dedupe_key, acknowledged, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    alert.alert_id,
                    alert.dedupe_key,
                    int(alert.acknowledged),
                    alert.created_at.isoformat(),
                    dumps(alert),
                ),
            )
        return alert

    def get(self, alert_id: str) -> Alert | None:
        return _one(self.db, Alert, "SELECT payload FROM alerts WHERE alert_id = ?", (alert_id,))

    def list(self, *, acknowledged: bool | None = None) -> list[Alert]:
        if acknowledged is None:
            return _rows(self.db, Alert, "SELECT payload FROM alerts ORDER BY created_at")
        return _rows(
            self.db,
            Alert,
            "SELECT payload FROM alerts WHERE acknowledged = ? ORDER BY created_at",
            (int(acknowledged),),
        )

    def acknowledge(self, alert_id: str) -> Alert:
        alert = self.get(alert_id)
        if alert is None:
            raise NotFoundError(alert_id)
        acked = dataclasses.replace(alert, acknowledged=True)
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE alerts SET acknowledged = 1, payload = ? WHERE alert_id = ?",
                (dumps(acked), alert_id),
            )
        return acked


class SqliteReportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, report: Report) -> Report:
        with self.db.transaction() as cur:
            try:
                cur.execute(
                    "INSERT INTO reports (report_id, run_id, version, payload) VALUES (?, ?, ?, ?)",
                    (report.report_id, report.run_id, report.version, dumps(report)),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    f"report {report.report_id} / run {report.run_id} v{report.version} exists"
                ) from exc
        return report

    def get(self, report_id: str) -> Report | None:
        return _one(
            self.db, Report, "SELECT payload FROM reports WHERE report_id = ?", (report_id,)
        )

    def latest_for_run(self, run_id: str) -> Report | None:
        return _one(
            self.db,
            Report,
            "SELECT payload FROM reports WHERE run_id = ? ORDER BY version DESC LIMIT 1",
            (run_id,),
        )


class SqliteRetrievalAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, attempt: RetrievalAttempt) -> RetrievalAttempt:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO retrieval_attempts "
                "(attempt_id, accession, filename, attempted_at, snapshot_id, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt.attempt_id,
                    attempt.accession,
                    attempt.filename,
                    attempt.attempted_at.isoformat(),
                    attempt.snapshot_id,
                    dumps(attempt),
                ),
            )
        return attempt

    def list_for_document(self, accession: str, filename: str) -> list[RetrievalAttempt]:
        return _rows(
            self.db,
            RetrievalAttempt,
            "SELECT payload FROM retrieval_attempts WHERE accession = ? AND filename = ? "
            "ORDER BY attempted_at, attempt_id",
            (_acc(accession), filename),
        )

    def snapshot_versions(self, accession: str, filename: str) -> list[str]:
        seen: list[str] = []
        for a in self.list_for_document(accession, filename):
            if a.snapshot_id and a.snapshot_id not in seen:
                seen.append(a.snapshot_id)
        return seen

    def referenced_snapshot_ids(self) -> set[str]:
        rows = self.db.query(
            "SELECT DISTINCT snapshot_id FROM retrieval_attempts WHERE snapshot_id IS NOT NULL"
        )
        return {str(r[0]) for r in rows}


class SqliteReportInputsStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, run_id: str, inputs_json: str, inputs_hash: str) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO report_inputs (run_id, inputs_hash, inputs_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET "
                "inputs_hash = excluded.inputs_hash, inputs_json = excluded.inputs_json, "
                "updated_at = excluded.updated_at",
                (run_id, inputs_hash, inputs_json, utcnow().isoformat()),
            )

    def get(self, run_id: str) -> str | None:
        rows = self.db.query("SELECT inputs_json FROM report_inputs WHERE run_id = ?", (run_id,))
        return str(rows[0][0]) if rows else None


class SqliteWatchlistRepository:
    """Watchlists plus concurrency-safe per-accession reservations."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, watchlist: Watchlist) -> Watchlist:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO watchlists (watchlist_id, cik, payload) VALUES (?, ?, ?)",
                (watchlist.watchlist_id, watchlist.cik, dumps(watchlist)),
            )
        return watchlist

    def list(self) -> list[Watchlist]:
        return _rows(self.db, Watchlist, "SELECT payload FROM watchlists ORDER BY watchlist_id")

    def reserve(self, watchlist_id: str, filing: Filing, now: datetime) -> bool:
        """Atomically claim an accession for a watchlist. True only for the first claimant,
        or when re-claiming a previously failed reservation for retry."""
        with self.db.transaction() as cur:
            inserted = cur.execute(
                "INSERT OR IGNORE INTO filing_reservations (watchlist_id, accession, cik, status, "
                "attempts, available_at, updated_at) VALUES (?, ?, ?, 'reserved', 1, ?, ?)",
                (
                    watchlist_id,
                    filing.accession,
                    filing.cik,
                    filing.available_at.isoformat(),
                    now.isoformat(),
                ),
            ).rowcount
            if inserted:
                return True
            retried = cur.execute(
                "UPDATE filing_reservations SET status = 'reserved', attempts = attempts + 1, "
                "updated_at = ? WHERE watchlist_id = ? AND accession = ? AND status = 'failed'",
                (now.isoformat(), watchlist_id, filing.accession),
            ).rowcount
            return bool(retried)

    def mark(
        self,
        watchlist_id: str,
        accession: str,
        status: str,
        now: datetime,
        *,
        run_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE filing_reservations SET status = ?, run_id = COALESCE(?, run_id), "
                "last_error = ?, updated_at = ? WHERE watchlist_id = ? AND accession = ?",
                (status, run_id, error, now.isoformat(), watchlist_id, _acc(accession)),
            )

    def reservations(self, watchlist_id: str) -> builtins.list[dict[str, Any]]:
        cols = ("accession", "status", "run_id", "attempts", "last_error", "available_at")
        rows = self.db.query(
            f"SELECT {', '.join(cols)} FROM filing_reservations WHERE watchlist_id = ? "
            "ORDER BY available_at, accession",
            (watchlist_id,),
        )
        return [dict(zip(cols, r, strict=True)) for r in rows]


@dataclasses.dataclass
class SqliteRepositories:
    """Convenience bundle wiring every repository to one database."""

    db: Database
    issuers: SqliteIssuerRepository = dataclasses.field(init=False)
    identifiers: SqliteSecurityIdentifierRepository = dataclasses.field(init=False)
    filings: SqliteFilingRepository = dataclasses.field(init=False)
    documents: SqliteFilingDocumentRepository = dataclasses.field(init=False)
    snapshots: SqliteSnapshotRepository = dataclasses.field(init=False)
    runs: SqliteRunRepository = dataclasses.field(init=False)
    findings: SqliteFindingRepository = dataclasses.field(init=False)
    evidence: SqliteEvidenceRepository = dataclasses.field(init=False)
    alerts: SqliteAlertRepository = dataclasses.field(init=False)
    reports: SqliteReportRepository = dataclasses.field(init=False)
    attempts: SqliteRetrievalAttemptRepository = dataclasses.field(init=False)
    report_inputs: SqliteReportInputsStore = dataclasses.field(init=False)
    watchlists: SqliteWatchlistRepository = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.issuers = SqliteIssuerRepository(self.db)
        self.identifiers = SqliteSecurityIdentifierRepository(self.db)
        self.filings = SqliteFilingRepository(self.db)
        self.documents = SqliteFilingDocumentRepository(self.db)
        self.snapshots = SqliteSnapshotRepository(self.db)
        self.runs = SqliteRunRepository(self.db)
        self.findings = SqliteFindingRepository(self.db)
        self.evidence = SqliteEvidenceRepository(self.db)
        self.alerts = SqliteAlertRepository(self.db)
        self.reports = SqliteReportRepository(self.db)
        self.attempts = SqliteRetrievalAttemptRepository(self.db)
        self.report_inputs = SqliteReportInputsStore(self.db)
        self.watchlists = SqliteWatchlistRepository(self.db)
