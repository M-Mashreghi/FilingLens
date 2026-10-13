"""Task 1.5: run state machine, immutability and reproducibility manifest."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from filinglens.application.runs import RunService
from filinglens.domain.errors import ImmutabilityError, InvalidTransitionError
from filinglens.domain.models import EvidenceSpan, RunState, SourceSnapshot
from filinglens.domain.run_lifecycle import TERMINAL_STATES, TRANSITIONS, check_transition
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

S = RunState
AS_OF = datetime(2025, 3, 1, tzinfo=UTC)
FOCAL = "0000320193-24-000123"
COMP = "0000320193-23-000106"
VERSIONS = {
    "parser": "p1",
    "diff": "d1",
    "calculation": "c1",
    "comparator_policy": "cp1",
    "availability_policy": "availability/1",
}
HAPPY = [S.RESOLVING, S.COLLECTING, S.PARSING, S.ALIGNING, S.ANALYZING, S.REVIEWING]

LEGAL = {
    (S.CREATED, S.RESOLVING),
    (S.CREATED, S.FAILED),
    (S.RESOLVING, S.COLLECTING),
    (S.RESOLVING, S.NEEDS_INPUT),
    (S.RESOLVING, S.BLOCKED_BY_SOURCE),
    (S.RESOLVING, S.FAILED),
    (S.COLLECTING, S.PARSING),
    (S.COLLECTING, S.NEEDS_INPUT),
    (S.COLLECTING, S.BLOCKED_BY_SOURCE),
    (S.COLLECTING, S.FAILED),
    (S.PARSING, S.ALIGNING),
    (S.PARSING, S.NEEDS_INPUT),
    (S.PARSING, S.BLOCKED_BY_SOURCE),
    (S.PARSING, S.FAILED),
    (S.ALIGNING, S.ANALYZING),
    (S.ALIGNING, S.NEEDS_INPUT),
    (S.ALIGNING, S.FAILED),
    (S.ANALYZING, S.REVIEWING),
    (S.ANALYZING, S.NEEDS_INPUT),
    (S.ANALYZING, S.FAILED),
    (S.REVIEWING, S.COMPLETED),
    (S.REVIEWING, S.ANALYZING),
    (S.REVIEWING, S.NEEDS_INPUT),
    (S.REVIEWING, S.FAILED),
    *((S.NEEDS_INPUT, t) for t in (*HAPPY, S.FAILED)),
    (S.BLOCKED_BY_SOURCE, S.RESOLVING),
    (S.BLOCKED_BY_SOURCE, S.COLLECTING),
    (S.BLOCKED_BY_SOURCE, S.FAILED),
}


@pytest.mark.parametrize("src,dst", list(itertools.product(list(S), list(S))))
def test_transition_matrix(src: RunState, dst: RunState) -> None:
    if (src, dst) in LEGAL:
        check_transition(src, dst)
    else:
        with pytest.raises(InvalidTransitionError):
            check_transition(src, dst)


def test_matrix_definition_matches_spec() -> None:
    assert {(a, b) for a, nxt in TRANSITIONS.items() for b in nxt} == LEGAL
    assert {S.COMPLETED, S.FAILED} == TERMINAL_STATES


@pytest.fixture
def env() -> tuple[RunService, SqliteRepositories]:
    repos = SqliteRepositories(Database(":memory:"))
    counter = itertools.count(1)
    svc = RunService(
        repos.runs, repos.snapshots, repos.evidence, id_factory=lambda: f"run-{next(counter)}"
    )
    return svc, repos


def _snapshot(repos: SqliteRepositories, data: bytes) -> SourceSnapshot:
    snap = SourceSnapshot.from_bytes(
        data,
        source_url="https://www.sec.gov/x",
        retrieved_at=AS_OF,
        content_type="text/html",
        adapter="test",
        adapter_version="1",
        accession=FOCAL,
    )
    return repos.snapshots.add(snap)


def _complete(svc: RunService, repos: SqliteRepositories) -> str:
    run = svc.create(cik=320193, as_of=AS_OF, focal_accession=FOCAL, comparator_accession=COMP)
    snap = _snapshot(repos, b"focal")
    svc.update_manifest(run.run_id, snapshot_ids=[snap.snapshot_id], component_versions=VERSIONS)
    for state in HAPPY:
        svc.transition(run.run_id, state)
    svc.transition(run.run_id, S.COMPLETED)
    return run.run_id


def test_happy_path_completes_and_persists(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, repos = env
    rid = _complete(svc, repos)
    assert repos.runs.get(rid).state is S.COMPLETED  # type: ignore[union-attr]
    m = svc.manifest(rid)
    assert m.as_of == AS_OF and m.focal_accession == FOCAL and m.comparator_accession == COMP
    assert m.component_versions["availability_policy"] == "availability/1"


def test_illegal_transition_rejected(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, _ = env
    run = svc.create(cik=320193, as_of=AS_OF, focal_accession=FOCAL)
    with pytest.raises(InvalidTransitionError):
        svc.transition(run.run_id, S.ANALYZING)
    assert svc.get(run.run_id).state is S.CREATED


def test_completed_run_mutation_blocked(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, repos = env
    rid = _complete(svc, repos)
    with pytest.raises(ImmutabilityError):
        svc.transition(rid, S.ANALYZING)
    with pytest.raises(ImmutabilityError):
        svc.update_manifest(rid, comparator_reason="changed")
    run = repos.runs.get(rid)
    assert run is not None
    with pytest.raises(ImmutabilityError):  # repository-level guard too
        repos.runs.save(run.__class__(**{**{k: v for k, v in run.__dict__.items()}, "error": "x"}))
    with pytest.raises(ImmutabilityError):
        repos.runs.save_manifest(svc.manifest(rid))


def test_completion_requires_reproducible_manifest(
    env: tuple[RunService, SqliteRepositories],
) -> None:
    svc, repos = env
    run = svc.create(cik=320193, as_of=AS_OF, focal_accession=FOCAL)
    for state in HAPPY:
        svc.transition(run.run_id, state)
    problems = svc.verify_manifest(run.run_id)
    assert any("missing component versions" in p for p in problems)
    assert any("no source snapshots" in p for p in problems)
    with pytest.raises(InvalidTransitionError):
        svc.transition(run.run_id, S.COMPLETED)

    missing_sid = "f" * 64
    svc.update_manifest(run.run_id, snapshot_ids=[missing_sid], component_versions=VERSIONS)
    assert any("not in snapshot store" in p for p in svc.verify_manifest(run.run_id))

    snap = _snapshot(repos, b"real")
    repos.evidence.add(
        EvidenceSpan(
            evidence_id="e1",
            run_id=run.run_id,
            snapshot_id=snap.snapshot_id,
            accession=FOCAL,
            locator="b:1",
            text="t",
        )
    )
    assert any("unrecorded snapshot" in p for p in svc.verify_manifest(run.run_id))


def test_llm_runs_require_model_versions(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, repos = env
    run = svc.create(cik=320193, as_of=AS_OF, focal_accession=FOCAL)
    snap = _snapshot(repos, b"x")
    svc.update_manifest(
        run.run_id, snapshot_ids=[snap.snapshot_id], component_versions=VERSIONS, uses_llm=True
    )
    assert any("llm_model" in p for p in svc.verify_manifest(run.run_id))
    svc.update_manifest(
        run.run_id,
        component_versions={
            "llm_provider": "fake",
            "llm_model": "m",
            "prompt": "v1",
            "output_schema": "s1",
        },
    )
    assert svc.verify_manifest(run.run_id) == []


def test_correction_creates_linked_run(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, repos = env
    rid = _complete(svc, repos)
    new = svc.supersede(rid)
    assert new.supersedes_run_id == rid and new.run_id != rid
    assert new.state is S.CREATED and new.as_of == AS_OF and new.focal_accession == FOCAL
    assert repos.runs.get(rid).state is S.COMPLETED  # type: ignore[union-attr]
    open_run = svc.create(cik=320193, as_of=AS_OF)
    with pytest.raises(InvalidTransitionError):
        svc.supersede(open_run.run_id)


def test_failure_requires_error_and_is_terminal(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, _ = env
    run = svc.create(cik=320193, as_of=AS_OF)
    with pytest.raises(InvalidTransitionError):
        svc.transition(run.run_id, S.FAILED)
    failed = svc.transition(run.run_id, S.FAILED, error="source unavailable")
    assert failed.error == "source unavailable"
    with pytest.raises(InvalidTransitionError):
        svc.transition(run.run_id, S.RESOLVING)


def test_needs_input_and_blocked_resume(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, _ = env
    run = svc.create(cik=320193, as_of=AS_OF)
    svc.transition(run.run_id, S.RESOLVING)
    svc.transition(run.run_id, S.NEEDS_INPUT)  # e.g. ambiguous issuer
    svc.transition(run.run_id, S.RESOLVING)
    svc.transition(run.run_id, S.COLLECTING)
    svc.transition(run.run_id, S.BLOCKED_BY_SOURCE)
    assert svc.transition(run.run_id, S.COLLECTING).state is S.COLLECTING


def test_manifest_round_trips_through_storage(env: tuple[RunService, SqliteRepositories]) -> None:
    svc, repos = env
    run = svc.create(cik=320193, as_of=AS_OF, focal_accession=FOCAL)
    svc.update_manifest(
        run.run_id,
        xbrl_fact_ids=["f2", "f1", "f1"],
        component_versions={"parser": "p"},
        config_fingerprint="abc",
        comparator_reason="prior 10-K",
    )
    m = repos.runs.get_manifest(run.run_id)
    assert m is not None and m.xbrl_fact_ids == ("f1", "f2")
    assert m.component_versions == {"parser": "p"} and m.config_fingerprint == "abc"
