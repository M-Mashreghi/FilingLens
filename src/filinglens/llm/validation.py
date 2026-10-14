"""Evidence validator, contradiction checker and Research Priority scorer.

Versions: ``evidence-validator/1`` and ``research-priority/1``.

A finding is *blocked* when any cited evidence does not bind to the run (snapshot outside the
run manifest, wrong accession, wrong document/section), when it has no valid evidence, when
deterministic claim checks fail, or when an optional reviewer model rejects it. Contradictory
findings are flagged. The Research Priority Score ranks analyst attention only — it is not a
legal materiality determination.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from filinglens.diff.priority_features import FeatureVector
from filinglens.llm.client import InterpretationStatus
from filinglens.llm.workflow import AnalystFinding

VALIDATOR_VERSION = "evidence-validator/1"
SCORE_VERSION = "research-priority/1"
SCORE_LABEL = "Research Priority Score (analyst attention ranking; not legal materiality)"


@dataclass(frozen=True)
class EvidenceBinding:
    evidence_id: str
    snapshot_id: str | None  # None for deterministic calculations (bound via fact ids)
    accession: str | None
    filename: str | None
    section: str | None
    fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunScope:
    snapshot_ids: frozenset[str]
    accessions: frozenset[str]
    fact_ids: frozenset[str] = frozenset()


class Verdict(StrEnum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class Reviewer(Protocol):
    def review(self, finding: AnalystFinding, evidence_texts: Sequence[str]) -> bool:
        """Return True when the claim is supported by the evidence."""
        ...


@dataclass(frozen=True)
class ValidatedFinding:
    finding: AnalystFinding
    verdict: Verdict
    block_reasons: tuple[str, ...]
    contradicts: tuple[int, ...]
    priority_score: float | None
    reason_codes: tuple[str, ...]
    validator_version: str = VALIDATOR_VERSION
    score_version: str = SCORE_VERSION
    score_label: str = SCORE_LABEL


def _norm_section(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(?:item\s*)?((?:i{1,2}-)?\d{1,2}[a-c]?(?:\.\d{2})?)\s*$", s.strip(), re.I)
    return m.group(1).upper() if m else s.strip().upper()


def binding_problems(
    f: AnalystFinding, bindings: Mapping[str, EvidenceBinding], scope: RunScope
) -> list[str]:
    problems: list[str] = []
    if not f.valid_evidence_ids:
        problems.append("no valid evidence cited")
    claimed_section = _norm_section(f.output.section_or_item)
    sections_cited: set[str] = set()
    for eid in f.valid_evidence_ids:
        b = bindings.get(eid)
        if b is None:
            problems.append(f"{eid}: evidence has no provenance binding")
            continue
        if b.snapshot_id is not None and b.snapshot_id not in scope.snapshot_ids:
            problems.append(f"{eid}: snapshot not part of this run")
        if b.accession is not None and b.accession not in scope.accessions:
            problems.append(f"{eid}: wrong accession {b.accession}")
        if b.snapshot_id is None and not b.fact_ids:
            problems.append(f"{eid}: calculation without source fact ids")
        if b.fact_ids and scope.fact_ids and not set(b.fact_ids) <= scope.fact_ids:
            problems.append(f"{eid}: fact ids outside run manifest")
        if b.section:
            norm = _norm_section(b.section)
            if norm:
                sections_cited.add(norm)
    if claimed_section and sections_cited and claimed_section not in sections_cited:
        problems.append(
            f"finding section {claimed_section} not among cited sections {sorted(sections_cited)}"
        )
    return problems


_OPPOSITE = {
    ("increase", "decrease"),
    ("decrease", "increase"),
    ("new", "removed"),
    ("removed", "new"),
}


def contradictions(findings: Sequence[AnalystFinding]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for i, a in enumerate(findings):
        for j, b in enumerate(findings):
            if i >= j:
                continue
            same_subject = a.output.finding_type == b.output.finding_type and _norm_section(
                a.output.section_or_item
            ) == _norm_section(b.output.section_or_item)
            if same_subject and (a.output.direction, b.output.direction) in _OPPOSITE:
                out.setdefault(i, []).append(j)
                out.setdefault(j, []).append(i)
    return out


WEIGHTS: dict[str, float] = {
    "section_importance": 0.20,
    "novelty": 0.15,
    "change_magnitude": 0.10,
    "topic_breadth": 0.05,
    "direction_score": 0.10,
    "restatement_flag": 0.10,
    "amendment_flag": 0.05,
    "cross_section_recurrence": 0.05,
    "xbrl_signal": 0.10,
    "evidence_quality": 0.10,
}


def priority_score(
    vectors: Sequence[FeatureVector], confidence: float
) -> tuple[float | None, tuple[str, ...]]:
    """Weighted mean of available features (missing features renormalized away), scaled by
    model confidence; returns score in [0, 100] and reason codes of top contributors."""
    if not vectors:
        return None, ("no_deterministic_features",)
    contrib: dict[str, float] = {}
    total_w = 0.0
    for name, w in WEIGHTS.items():
        vals = [v.values.get(name) for v in vectors if v.values.get(name) is not None]
        if not vals:
            continue
        val = max(float(x) for x in vals if x is not None)
        contrib[name] = w * val
        total_w += w
    if total_w == 0:
        return None, ("no_available_features",)
    base = sum(contrib.values()) / total_w
    score = round(100 * base * (0.5 + 0.5 * confidence), 2)
    top = sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return score, tuple(f"{k}:{v / total_w:.3f}" for k, v in top)


def validate(
    findings: Sequence[AnalystFinding],
    bindings: Mapping[str, EvidenceBinding],
    scope: RunScope,
    *,
    features_by_evidence: Mapping[str, Sequence[FeatureVector]] | None = None,
    evidence_texts: Mapping[str, str] | None = None,
    reviewer: Reviewer | None = None,
) -> list[ValidatedFinding]:
    contra = contradictions(findings)
    out: list[ValidatedFinding] = []
    for i, f in enumerate(findings):
        reasons = binding_problems(f, bindings, scope)
        if f.interpretation_status is InterpretationStatus.NOT_SUPPORTED:
            reasons.append("interpretation not supported by evidence")
        reasons.extend(
            fl for fl in f.flags if fl.startswith(("numeric_unverified", "uncited_management"))
        )
        if reviewer is not None and not reasons:
            texts = [(evidence_texts or {}).get(e, "") for e in f.valid_evidence_ids]
            if not reviewer.review(f, texts):
                reasons.append("reviewer model rejected claim")
        vecs = [v for e in f.valid_evidence_ids for v in (features_by_evidence or {}).get(e, ())]
        score, codes = priority_score(vecs, f.output.confidence)
        if i in contra:
            codes = (*codes, "contradiction_flagged")
        verdict = Verdict.BLOCKED if reasons else Verdict.ACCEPTED
        out.append(
            ValidatedFinding(
                f,
                verdict,
                tuple(reasons),
                tuple(contra.get(i, [])),
                score if verdict is Verdict.ACCEPTED else None,
                codes,
            )
        )
    return out


def accepted(results: Iterable[ValidatedFinding]) -> list[ValidatedFinding]:
    ranked = [r for r in results if r.verdict is Verdict.ACCEPTED]
    return sorted(ranked, key=lambda r: (-(r.priority_score or 0.0), r.finding.output.finding_type))
