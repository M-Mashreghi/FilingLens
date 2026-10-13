"""Raw snapshot retention (``retention-policy/1``).

Raw bytes referenced by a completed run (its manifest snapshots and evidence snapshots) are
*pinned* and can never be purged. Other raw bytes may be purged to save space; their
snapshot metadata rows remain, so a later refetch with identical bytes re-attaches to the
same snapshot id. Network caches are independent of this policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from filinglens.application.ports import EvidenceRepository, RunRepository, SnapshotRepository
from filinglens.domain.errors import ImmutabilityError, NotFoundError
from filinglens.domain.models import RunState

POLICY_VERSION = "retention-policy/1"


class DeletableRawStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class PurgeResult:
    purged: tuple[str, ...]
    already_absent: tuple[str, ...]


class SnapshotRetentionService:
    def __init__(
        self,
        runs: RunRepository,
        evidence: EvidenceRepository,
        snapshots: SnapshotRepository,
        raw_store: DeletableRawStore,
    ) -> None:
        self.runs = runs
        self.evidence = evidence
        self.snapshots = snapshots
        self.raw_store = raw_store

    def pinned_snapshot_ids(self) -> set[str]:
        pinned: set[str] = set()
        for run in self.runs.list():
            if run.state is not RunState.COMPLETED:
                continue
            manifest = self.runs.get_manifest(run.run_id)
            if manifest is not None:
                pinned.update(manifest.snapshot_ids)
            pinned.update(e.snapshot_id for e in self.evidence.list_for_run(run.run_id))
        return pinned

    def missing_pinned(self) -> list[str]:
        """Pinned snapshots whose raw bytes are not present (a retention violation)."""
        missing = []
        for sid in sorted(self.pinned_snapshot_ids()):
            snap = self.snapshots.get(sid)
            if snap is None or not self.raw_store.exists(snap.storage_key):
                missing.append(sid)
        return missing

    def purge(self, snapshot_ids: list[str]) -> PurgeResult:
        pinned = self.pinned_snapshot_ids()
        blocked = sorted(set(snapshot_ids) & pinned)
        if blocked:
            raise ImmutabilityError(
                f"snapshots required by completed runs cannot be purged: {blocked}"
            )
        purged, absent = [], []
        for sid in snapshot_ids:
            snap = self.snapshots.get(sid)
            if snap is None:
                raise NotFoundError(sid)
            if self.raw_store.exists(snap.storage_key):
                self.raw_store.delete(snap.storage_key)
                purged.append(sid)
            else:
                absent.append(sid)
        return PurgeResult(tuple(purged), tuple(absent))
