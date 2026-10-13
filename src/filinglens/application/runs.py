"""Analysis-run lifecycle service."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from filinglens.application.ports import EvidenceRepository, RunRepository, SnapshotRepository
from filinglens.domain.errors import ImmutabilityError, InvalidTransitionError, NotFoundError
from filinglens.domain.models import ComparisonRun, RunState
from filinglens.domain.run_lifecycle import TERMINAL_STATES, RunManifest, check_transition
from filinglens.domain.time import utcnow


class RunService:
    def __init__(
        self,
        runs: RunRepository,
        snapshots: SnapshotRepository,
        evidence: EvidenceRepository,
        *,
        clock: Callable[[], datetime] = utcnow,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.runs = runs
        self.snapshots = snapshots
        self.evidence = evidence
        self.clock = clock
        self.id_factory = id_factory

    def create(
        self,
        *,
        cik: int,
        as_of: datetime,
        focal_accession: str | None = None,
        comparator_accession: str | None = None,
        supersedes_run_id: str | None = None,
    ) -> ComparisonRun:
        now = self.clock()
        run = ComparisonRun(
            run_id=self.id_factory(),
            cik=cik,
            focal_accession=focal_accession,
            comparator_accession=comparator_accession,
            as_of=as_of,
            created_at=now,
            updated_at=now,
            supersedes_run_id=supersedes_run_id,
        )
        self.runs.add(run)
        self.runs.save_manifest(
            RunManifest(
                run_id=run.run_id,
                as_of=run.as_of,
                cik=cik,
                focal_accession=run.focal_accession,
                comparator_accession=run.comparator_accession,
            )
        )
        return run

    def get(self, run_id: str) -> ComparisonRun:
        run = self.runs.get(run_id)
        if run is None:
            raise NotFoundError(run_id)
        return run

    def manifest(self, run_id: str) -> RunManifest:
        manifest = self.runs.get_manifest(run_id)
        if manifest is None:
            raise NotFoundError(f"manifest for {run_id}")
        return manifest

    def update_manifest(self, run_id: str, **changes: Any) -> RunManifest:
        run = self.get(run_id)
        self._assert_not_completed(run)
        current = self.manifest(run_id)
        if "snapshot_ids" in changes:
            changes["snapshot_ids"] = tuple(current.snapshot_ids) + tuple(changes["snapshot_ids"])
        if "xbrl_fact_ids" in changes:
            changes["xbrl_fact_ids"] = tuple(current.xbrl_fact_ids) + tuple(
                changes["xbrl_fact_ids"]
            )
        if "component_versions" in changes:
            changes["component_versions"] = {
                **current.component_versions,
                **changes["component_versions"],
            }
        updated = dataclasses.replace(current, **changes)
        self.runs.save_manifest(updated)
        if (updated.focal_accession, updated.comparator_accession) != (
            run.focal_accession,
            run.comparator_accession,
        ):
            self.runs.save(
                dataclasses.replace(
                    run,
                    focal_accession=updated.focal_accession,
                    comparator_accession=updated.comparator_accession,
                    updated_at=self.clock(),
                )
            )
        return updated

    def transition(
        self, run_id: str, target: RunState, *, error: str | None = None
    ) -> ComparisonRun:
        run = self.get(run_id)
        self._assert_not_completed(run)
        check_transition(run.state, target)
        if target is RunState.COMPLETED:
            problems = self.verify_manifest(run_id)
            if problems:
                raise InvalidTransitionError("manifest is not reproducible: " + "; ".join(problems))
        if target is RunState.FAILED and not error:
            raise InvalidTransitionError("a failed run must record an error")
        updated = dataclasses.replace(
            run, state=target, updated_at=self.clock(), error=error if error else run.error
        )
        return self.runs.save(updated)

    def verify_manifest(self, run_id: str) -> list[str]:
        """Return reasons the manifest cannot reproduce the run (empty when sufficient)."""
        run = self.get(run_id)
        m = self.manifest(run_id)
        problems: list[str] = []
        if m.focal_accession is None:
            problems.append("focal accession not recorded")
        if (m.focal_accession, m.comparator_accession) != (
            run.focal_accession,
            run.comparator_accession,
        ):
            problems.append("manifest accessions differ from run")
        if m.as_of != run.as_of:
            problems.append("manifest as_of differs from run")
        missing = m.missing_versions()
        if missing:
            problems.append("missing component versions: " + ", ".join(missing))
        if not m.snapshot_ids:
            problems.append("no source snapshots recorded")
        for sid in m.snapshot_ids:
            if self.snapshots.get(sid) is None:
                problems.append(f"snapshot {sid[:12]} not in snapshot store")
        recorded = set(m.snapshot_ids)
        for ev in self.evidence.list_for_run(run_id):
            if ev.snapshot_id not in recorded:
                problems.append(f"evidence {ev.evidence_id} cites unrecorded snapshot")
        return problems

    def supersede(self, run_id: str) -> ComparisonRun:
        """Corrections never mutate a finished run; they create a linked new run."""
        prior = self.get(run_id)
        if prior.state not in TERMINAL_STATES:
            raise InvalidTransitionError("only completed or failed runs can be superseded")
        return self.create(
            cik=prior.cik,
            as_of=prior.as_of,
            focal_accession=prior.focal_accession,
            comparator_accession=prior.comparator_accession,
            supersedes_run_id=prior.run_id,
        )

    @staticmethod
    def _assert_not_completed(run: ComparisonRun) -> None:
        if run.state is RunState.COMPLETED:
            raise ImmutabilityError(f"run {run.run_id} is completed and immutable")
