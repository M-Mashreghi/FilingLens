"""Restore drill: prove completed runs reopen with their evidence after a restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from filinglens.application.ports import EvidenceRepository, RunRepository, SnapshotRepository
from filinglens.application.runs import RunService
from filinglens.domain.models import RunState
from filinglens.reporting.report import ReportService


class RawReader(Protocol):
    def get(self, key: str) -> bytes: ...


@dataclass
class DrillResult:
    runs_checked: int = 0
    snapshots_verified: int = 0
    reports_regenerated: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.runs_checked > 0 and not self.problems


def restore_drill(
    runs: RunRepository,
    snapshots: SnapshotRepository,
    evidence: EvidenceRepository,
    raw: RawReader,
    reports: ReportService,
) -> DrillResult:
    service = RunService(runs, snapshots, evidence)
    result = DrillResult()
    for run in runs.list():
        if run.state is not RunState.COMPLETED:
            continue
        result.runs_checked += 1
        result.problems += [f"{run.run_id}: {p}" for p in service.verify_manifest(run.run_id)]
        manifest = service.manifest(run.run_id)
        for sid in manifest.snapshot_ids:
            snap = snapshots.get(sid)
            if snap is None:
                result.problems.append(f"{run.run_id}: snapshot {sid[:12]} missing")
                continue
            try:
                ok = snap.verify(raw.get(snap.storage_key))
            except Exception as exc:
                result.problems.append(
                    f"{run.run_id}: snapshot {sid[:12]} unreadable ({type(exc).__name__})"
                )
                continue
            if ok:
                result.snapshots_verified += 1
            else:
                result.problems.append(f"{run.run_id}: snapshot {sid[:12]} hash mismatch")
        for span in evidence.list_for_run(run.run_id):
            snap = snapshots.get(span.snapshot_id)
            if snap is None or span.snapshot_id not in manifest.snapshot_ids:
                result.problems.append(
                    f"{run.run_id}: evidence {span.evidence_id} cites unavailable snapshot"
                )
        stored = reports.reports.latest_for_run(run.run_id)
        if stored is not None:
            try:
                regenerated = reports.regenerate(run.run_id)
            except Exception as exc:
                result.problems.append(f"{run.run_id}: report inputs unavailable ({exc})")
            else:
                if regenerated != stored.body_markdown:
                    result.problems.append(
                        f"{run.run_id}: regenerated report differs from stored report"
                    )
                else:
                    result.reports_regenerated += 1
    return result
