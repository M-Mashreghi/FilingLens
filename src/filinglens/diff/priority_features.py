"""Research Priority feature builder (``priority-features/1``).

Transparent, deterministic features for later ranking of analyst attention. These are
research-priority inputs only; they do not express legal materiality. A feature that cannot
be computed is ``None`` (missing is not zero).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from filinglens.diff.detectors import ChangeSignal, Direction
from filinglens.diff.text_diff import DiffResult

FEATURE_VERSION = "priority-features/1"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    range: tuple[float, float]
    meaning: str


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("section_importance", (0, 1), "Analyst weight of the SEC item/section."),
    FeatureSpec("novelty", (0, 1), "1 added, 0.8 removed, 1-similarity for modifications."),
    FeatureSpec("change_magnitude", (0, 1), "Word-level change magnitude of the section."),
    FeatureSpec("topic_breadth", (0, 1), "Number of tagged topics / 3, capped at 1."),
    FeatureSpec(
        "direction_score", (0, 1), "strengthened 1, new .8, removed .6, softened .4, unknown .2."
    ),
    FeatureSpec("amendment_flag", (0, 1), "1 when the focal filing is an amendment."),
    FeatureSpec("restatement_flag", (0, 1), "1 when a non-reliance (8-K 4.02) signal is present."),
    FeatureSpec(
        "exhibit_weight", (0, 1), "Analyst weight of the 8-K item/exhibit type; None for 10-K/10-Q."
    ),
    FeatureSpec("cross_section_recurrence", (0, 1), "Other sections sharing a topic / 3, capped."),
    FeatureSpec(
        "xbrl_signal",
        (0, 1),
        "Strength of a related deterministic XBRL signal; None if unavailable.",
    ),
    FeatureSpec("parser_confidence", (0, 1), "Section boundary confidence of the source section."),
    FeatureSpec("alignment_confidence", (0, 1), "Alignment confidence of the source change."),
    FeatureSpec("evidence_quality", (0, 1), "Share of evidence refs with snapshot id and locator."),
)

SECTION_WEIGHTS: Mapping[str, float] = {
    "1A": 1.0,
    "7": 1.0,
    "8": 0.8,
    "7A": 0.7,
    "3": 0.7,
    "1": 0.5,
    "9A": 0.6,
    "II-1A": 1.0,
    "I-2": 1.0,
    "I-1": 0.8,
    "I-3": 0.7,
    "II-1": 0.7,
    "I-4": 0.6,
}
EIGHT_K_WEIGHTS: Mapping[str, float] = {
    "4.02": 1.0,
    "4.01": 0.9,
    "2.02": 0.9,
    "1.01": 0.8,
    "2.01": 0.8,
    "2.03": 0.8,
    "2.06": 0.8,
    "1.03": 1.0,
    "5.02": 0.7,
    "1.05": 0.8,
    "7.01": 0.5,
    "8.01": 0.4,
}
_DIRECTION = {
    Direction.STRENGTHENED: 1.0,
    Direction.NEW: 0.8,
    Direction.REMOVED: 0.6,
    Direction.SOFTENED: 0.4,
    Direction.UNKNOWN: 0.2,
}


@dataclass(frozen=True)
class FeatureVector:
    signal_id: str
    values: dict[str, float | None]
    version: str = FEATURE_VERSION
    explanations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureContext:
    is_amendment: bool = False
    restatement_signal: bool = False
    parser_confidence: Mapping[str, float] = field(default_factory=dict)
    alignment_confidence: Mapping[str, float] = field(default_factory=dict)  # by change id
    novelty_similarity: Mapping[str, float] = field(default_factory=dict)  # by change id
    xbrl_signal: Mapping[str, float] = field(default_factory=dict)  # by topic


def _clip(x: float) -> float:
    return round(min(1.0, max(0.0, x)), 4)


def build_features(
    signals: Iterable[ChangeSignal], diff: DiffResult | None, ctx: FeatureContext
) -> list[FeatureVector]:
    sigs = list(signals)
    topic_sections: dict[str, set[str]] = {}
    for s in sigs:
        for t in s.topics:
            topic_sections.setdefault(t, set()).add(s.section_key)
    changes = {c.change_id: c for c in diff.changes} if diff else {}
    out: list[FeatureVector] = []
    for s in sorted(sigs, key=lambda x: x.signal_id):
        is_8k = s.section_key.startswith("8-K:")
        item = s.section_key.removeprefix("8-K:")
        v: dict[str, float | None] = {}
        expl: dict[str, str] = {}
        v["section_importance"] = (
            EIGHT_K_WEIGHTS.get(item, 0.3) if is_8k else SECTION_WEIGHTS.get(s.section_key, 0.3)
        )
        if s.direction is Direction.NEW:
            v["novelty"] = 1.0
        elif s.direction is Direction.REMOVED:
            v["novelty"] = 0.8
        elif (s.source_change_id or "") in ctx.novelty_similarity:
            v["novelty"] = _clip(1 - ctx.novelty_similarity[s.source_change_id or ""])
        else:
            v["novelty"] = None
        stat = diff.stat(s.section_key) if diff and not is_8k else None
        v["change_magnitude"] = _clip(stat.change_magnitude) if stat else None
        real_topics = [t for t in s.topics if t != "unknown"]
        v["topic_breadth"] = _clip(len(real_topics) / 3)
        v["direction_score"] = _DIRECTION[s.direction]
        v["amendment_flag"] = 1.0 if ctx.is_amendment else 0.0
        v["restatement_flag"] = 1.0 if ctx.restatement_signal or item == "4.02" else 0.0
        v["exhibit_weight"] = EIGHT_K_WEIGHTS.get(item, 0.3) if is_8k else None
        others = {sec for t in real_topics for sec in topic_sections[t]} - {s.section_key}
        v["cross_section_recurrence"] = _clip(len(others) / 3)
        xs = [ctx.xbrl_signal[t] for t in real_topics if t in ctx.xbrl_signal]
        v["xbrl_signal"] = _clip(max(xs)) if xs else None
        v["parser_confidence"] = ctx.parser_confidence.get(s.section_key)
        ch = changes.get(s.source_change_id or "")
        v["alignment_confidence"] = (
            ctx.alignment_confidence.get(s.source_change_id or "")
            if s.source_change_id in ctx.alignment_confidence
            else (ch.alignment_confidence if ch else None)
        )
        refs = list(s.evidence)
        v["evidence_quality"] = (
            _clip(sum(1 for r in refs if r.snapshot_id and r.locator) / len(refs)) if refs else 0.0
        )
        expl["direction_score"] = f"{s.direction.value}: {s.reason}"
        expl["topics"] = ",".join(s.topics)
        out.append(FeatureVector(s.signal_id, v, explanations=expl))
    return out
