"""Task 7.5: backup, restore drill and runbook coverage."""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from filinglens.application.restore_drill import restore_drill
from filinglens.infrastructure.storage.backup import (
    BackupError,
    create_backup,
    prune_backups,
    restore_backup,
    verify_backup,
)
from filinglens.infrastructure.storage.raw_store import FileSystemRawStore
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.reporting.report import ReportService

ROOT = Path(__file__).resolve().parents[2]
T = datetime(2026, 1, 2, tzinfo=UTC)


def _e2e():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "e2e_backup", Path(__file__).with_name("test_end_to_end.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_backup"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def completed(tmp_path: Path):  # type: ignore[no-untyped-def]
    e2e = _e2e()
    env = e2e.Env(tmp_path / "live")
    res = env.pipeline.run(
        cik=1234567, focal_accession="0001234567-24-000031", as_of=e2e.NOW, review=e2e.APPROVE
    )
    assert res.run.state.value == "Completed"
    return env, res, tmp_path


def _drill(db: Path, raw: Path):  # type: ignore[no-untyped-def]
    repos = SqliteRepositories(Database(db))
    return restore_drill(
        repos.runs,
        repos.snapshots,
        repos.evidence,
        FileSystemRawStore(raw),
        ReportService(repos.reports, repos.report_inputs),
    )


def test_restore_drill_passes_and_completed_run_reopens(completed) -> None:  # type: ignore[no-untyped-def]
    env, res, tmp = completed
    manifest = create_backup(
        tmp / "live" / "fl.db", tmp / "live" / "raw", tmp / "backups" / "b1", created_at=T
    )
    assert (
        res.run.run_id in manifest.completed_runs
        and manifest.raw_objects
        and manifest.schema_version >= 6
    )
    restore_backup(tmp / "backups" / "b1", tmp / "restored" / "fl.db", tmp / "restored" / "raw")
    drill = _drill(tmp / "restored" / "fl.db", tmp / "restored" / "raw")
    assert drill.passed, drill.problems
    assert (
        drill.runs_checked == 1 and drill.snapshots_verified >= 3 and drill.reports_regenerated == 1
    )
    repos = SqliteRepositories(Database(tmp / "restored" / "fl.db"))
    report = repos.reports.latest_for_run(res.run.run_id)
    assert report is not None
    for f in repos.findings.list_for_run(res.run.run_id):
        for eid in f.evidence_ids:
            span = repos.evidence.get(eid)
            if span is not None:  # citations resolve to restored, hash-verified bytes
                snap = repos.snapshots.get(span.snapshot_id)
                assert snap is not None and snap.verify(
                    FileSystemRawStore(tmp / "restored" / "raw").get(snap.storage_key)
                )


def test_corrupt_backup_is_rejected(completed) -> None:  # type: ignore[no-untyped-def]
    _, _, tmp = completed
    m = create_backup(tmp / "live" / "fl.db", tmp / "live" / "raw", tmp / "b", created_at=T)
    victim = tmp / "b" / "raw" / next(iter(m.raw_objects))
    os.chmod(victim, 0o644)
    victim.write_bytes(b"tampered")
    with pytest.raises(BackupError):
        verify_backup(tmp / "b")
    with pytest.raises(BackupError):
        restore_backup(tmp / "b", tmp / "r" / "fl.db", tmp / "r" / "raw")


def test_drill_detects_missing_raw_evidence(completed) -> None:  # type: ignore[no-untyped-def]
    _, _, tmp = completed
    create_backup(tmp / "live" / "fl.db", tmp / "live" / "raw", tmp / "b", created_at=T)
    restore_backup(tmp / "b", tmp / "r" / "fl.db", tmp / "r" / "raw")
    obj = next(p for p in (tmp / "r" / "raw").rglob("*") if p.is_file())
    os.chmod(obj, 0o644)
    obj.unlink()
    drill = _drill(tmp / "r" / "fl.db", tmp / "r" / "raw")
    assert not drill.passed and any("unreadable" in p or "missing" in p for p in drill.problems)


def test_restore_never_overwrites_and_backup_target_must_be_empty(completed) -> None:  # type: ignore[no-untyped-def]
    _, _, tmp = completed
    create_backup(tmp / "live" / "fl.db", tmp / "live" / "raw", tmp / "b", created_at=T)
    with pytest.raises(BackupError):
        restore_backup(tmp / "b", tmp / "live" / "fl.db", tmp / "elsewhere")
    with pytest.raises(BackupError):
        create_backup(tmp / "live" / "fl.db", tmp / "live" / "raw", tmp / "b", created_at=T)


def test_backup_retention(completed) -> None:  # type: ignore[no-untyped-def]
    _, _, tmp = completed
    for i in range(4):
        create_backup(
            tmp / "live" / "fl.db",
            tmp / "live" / "raw",
            tmp / "all" / f"b{i}",
            created_at=T + timedelta(days=i),
        )
    removed = prune_backups(tmp / "all", keep=2)
    assert sorted(p.name for p in removed) == ["b0", "b1"]
    assert sorted(p.name for p in (tmp / "all").iterdir()) == ["b2", "b3"]


def test_runbook_covers_required_incidents() -> None:
    text = (ROOT / "docs" / "RUNBOOK.md").read_text(encoding="utf-8").lower()
    for topic in (
        "sec outage",
        "wrong issuer",
        "wrong comparator",
        "parser drift",
        "amendment lineage",
        "taxonomy",
        "llm",
        "evidence",
        "point-in-time",
        "backup",
        "restore drill",
    ):
        assert topic in text, topic
