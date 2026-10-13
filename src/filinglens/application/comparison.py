"""Persist comparator selection into a run manifest."""

from __future__ import annotations

from filinglens.application.runs import RunService
from filinglens.domain.run_lifecycle import RunManifest
from filinglens.lineage.comparator import ComparatorSelection


def record_comparator(
    service: RunService, run_id: str, selection: ComparatorSelection
) -> RunManifest:
    reason = f"[{selection.mode}/{selection.amendment_policy}] {selection.reason}"
    if selection.notes:
        reason += " | " + "; ".join(selection.notes)
    return service.update_manifest(
        run_id,
        focal_accession=selection.focal_accession,
        comparator_accession=selection.comparator_accession,
        comparator_reason=reason,
        component_versions={"comparator_policy": selection.policy_version},
    )
