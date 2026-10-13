"""Analysis-run state machine and reproducibility manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from filinglens.domain.errors import InvalidTransitionError
from filinglens.domain.models import RunState
from filinglens.domain.time import ensure_utc

S = RunState

_WORK_STATES = (S.RESOLVING, S.COLLECTING, S.PARSING, S.ALIGNING, S.ANALYZING, S.REVIEWING)

TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    S.CREATED: frozenset({S.RESOLVING, S.FAILED}),
    S.RESOLVING: frozenset({S.COLLECTING, S.NEEDS_INPUT, S.BLOCKED_BY_SOURCE, S.FAILED}),
    S.COLLECTING: frozenset({S.PARSING, S.NEEDS_INPUT, S.BLOCKED_BY_SOURCE, S.FAILED}),
    S.PARSING: frozenset({S.ALIGNING, S.NEEDS_INPUT, S.BLOCKED_BY_SOURCE, S.FAILED}),
    S.ALIGNING: frozenset({S.ANALYZING, S.NEEDS_INPUT, S.FAILED}),
    S.ANALYZING: frozenset({S.REVIEWING, S.NEEDS_INPUT, S.FAILED}),
    # A reviewer may send work back for re-analysis.
    S.REVIEWING: frozenset({S.COMPLETED, S.ANALYZING, S.NEEDS_INPUT, S.FAILED}),
    # Resuming after input re-enters any work state.
    S.NEEDS_INPUT: frozenset({*_WORK_STATES, S.FAILED}),
    # Source recovered: resume collection; otherwise fail.
    S.BLOCKED_BY_SOURCE: frozenset({S.RESOLVING, S.COLLECTING, S.FAILED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL_STATES = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


def check_transition(current: RunState, target: RunState) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidTransitionError(f"illegal run transition {current.value} -> {target.value}")


REQUIRED_COMPONENT_VERSIONS = (
    "parser",
    "diff",
    "calculation",
    "comparator_policy",
    "availability_policy",
)
LLM_COMPONENT_VERSIONS = ("llm_provider", "llm_model", "prompt", "output_schema")


@dataclass(frozen=True, kw_only=True)
class RunManifest:
    """Everything needed to reproduce a run's evidence and calculations."""

    run_id: str
    as_of: datetime
    cik: int
    focal_accession: str | None
    comparator_accession: str | None
    comparator_reason: str | None = None
    snapshot_ids: tuple[str, ...] = ()
    xbrl_fact_ids: tuple[str, ...] = ()
    component_versions: dict[str, str] = field(default_factory=dict)
    config_fingerprint: str | None = None
    uses_llm: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", ensure_utc(self.as_of))
        object.__setattr__(self, "snapshot_ids", tuple(sorted(set(self.snapshot_ids))))
        object.__setattr__(self, "xbrl_fact_ids", tuple(sorted(set(self.xbrl_fact_ids))))

    def missing_versions(self) -> list[str]:
        required = list(REQUIRED_COMPONENT_VERSIONS)
        if self.uses_llm:
            required += LLM_COMPONENT_VERSIONS
        return [k for k in required if not self.component_versions.get(k)]
