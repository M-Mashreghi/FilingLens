"""Filing comparison workspace view model (``workspace/1``).

Shapes already-computed deterministic artifacts (diff, 8-K parse, lineage, metrics, findings)
into panels an analyst can inspect: exact old/new text with locators, table cell changes,
8-K items and exhibit links, amendment lineage, XBRL metric lineage and finding → evidence
navigation. Nothing is recomputed here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from filinglens.analytics.trends import MetricResult
from filinglens.diff.text_diff import Change, DiffResult
from filinglens.domain.models import Filing
from filinglens.lineage.amendments import PeriodLineage
from filinglens.parsing.eight_k import EightKParseResult

SECTION_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "3": "Legal Proceedings",
    "7": "MD&A",
    "7A": "Market Risk",
    "8": "Financial Statements",
    "I-1": "Financial Statements",
    "I-2": "MD&A",
    "I-3": "Market Risk",
    "II-1": "Legal Proceedings",
    "II-1A": "Risk Factors",
}


@dataclass(frozen=True)
class FilingHeader:
    accession: str
    form: str
    filing_date: str
    report_period: str | None
    available_at: str
    is_amendment: bool


@dataclass(frozen=True)
class ChangeRow:
    change_id: str
    kind: str
    block_kind: str
    old_text: str | None
    new_text: str | None
    old_locator: str | None
    new_locator: str | None
    sentence_edits: tuple[tuple[str, str], ...]
    table_cells: tuple[tuple[str, int, str | None, str | None], ...]
    table_rows_added: tuple[str, ...]
    table_rows_removed: tuple[str, ...]
    moved: bool


@dataclass(frozen=True)
class SectionTab:
    key: str
    title: str
    added: int
    removed: int
    modified: int
    magnitude: float
    changes: tuple[ChangeRow, ...]


@dataclass(frozen=True)
class EightKPanel:
    items: tuple[tuple[str, str, str], ...]  # (item, title, category)
    exhibits: tuple[tuple[str, str, str | None], ...]  # (number, status, filename)
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class LineagePanel:
    original: str | None
    amendments: tuple[str, ...]
    effective: str | None


@dataclass(frozen=True)
class MetricRow:
    name: str
    status: str
    value: str | None
    formula: str
    accessions: tuple[str, ...]
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class FindingLink:
    finding_type: str
    summary: str
    evidence_ids: tuple[str, ...]
    targets: tuple[tuple[str, str, str], ...]  # (evidence_id, section_key, change_id)


@dataclass(frozen=True)
class Workspace:
    current: FilingHeader
    comparator: FilingHeader | None
    tabs: tuple[SectionTab, ...]
    eight_k: EightKPanel | None
    lineage: LineagePanel | None
    metrics: tuple[MetricRow, ...]
    findings: tuple[FindingLink, ...]
    version: str = "workspace/1"

    def tab(self, key: str) -> SectionTab | None:
        return next((t for t in self.tabs if t.key == key), None)


def _header(f: Filing) -> FilingHeader:
    return FilingHeader(
        f.accession,
        f.form,
        f.filing_date.isoformat(),
        f.report_period.isoformat() if f.report_period else None,
        f.available_at.isoformat(),
        f.is_amendment,
    )


def _row(c: Change) -> ChangeRow:
    t = c.table
    return ChangeRow(
        c.change_id,
        c.kind.value,
        c.block_kind,
        c.old.text if c.old else None,
        c.new.text if c.new else None,
        c.old.locator if c.old else None,
        c.new.locator if c.new else None,
        tuple((e.op, e.text) for e in c.sentence_edits),
        tuple((x.row_label, x.column, x.old, x.new) for x in t.cell_changes) if t else (),
        t.added_rows if t else (),
        t.removed_rows if t else (),
        c.moved,
    )


@dataclass
class WorkspaceInputs:
    current: Filing
    comparator: Filing | None = None
    diff: DiffResult | None = None
    eight_k: EightKParseResult | None = None
    lineage: PeriodLineage | None = None
    metrics: Mapping[str, MetricResult] = field(default_factory=dict)
    findings: Sequence[tuple[str, str, Sequence[str]]] = ()  # (type, summary, evidence ids)
    evidence_to_change: Mapping[str, tuple[str, str]] = field(
        default_factory=dict
    )  # id -> (section, change)


def build_workspace(inp: WorkspaceInputs) -> Workspace:
    tabs: list[SectionTab] = []
    if inp.diff is not None:
        for stat in inp.diff.sections:
            changes = inp.diff.for_section(stat.section_key)
            if not changes:
                continue
            tabs.append(
                SectionTab(
                    stat.section_key,
                    SECTION_TITLES.get(stat.section_key, stat.section_key),
                    stat.added,
                    stat.removed,
                    stat.modified,
                    stat.change_magnitude,
                    tuple(_row(c) for c in changes),
                )
            )
    eight = None
    if inp.eight_k is not None:
        eight = EightKPanel(
            tuple((e.item, e.title, e.category) for e in inp.eight_k.events),
            tuple((x.exhibit_number, x.status.value, x.filename) for x in inp.eight_k.exhibits),
            inp.eight_k.diagnostics,
        )
    lin = None
    if inp.lineage is not None:
        lin = LineagePanel(
            inp.lineage.original.accession if inp.lineage.original else None,
            tuple(a.accession for a in inp.lineage.amendments),
            inp.lineage.effective.accession if inp.lineage.effective else None,
        )
    metrics = tuple(
        MetricRow(
            m.name,
            m.status.value,
            str(m.value) if m.value is not None else None,
            m.formula,
            m.accessions,
            m.fact_ids,
        )
        for _, m in sorted(inp.metrics.items())
    )
    links = tuple(
        FindingLink(
            ftype,
            summary,
            tuple(ev),
            tuple((e, *inp.evidence_to_change[e]) for e in ev if e in inp.evidence_to_change),
        )
        for ftype, summary, ev in inp.findings
    )
    return Workspace(
        _header(inp.current),
        _header(inp.comparator) if inp.comparator else None,
        tuple(tabs),
        eight,
        lin,
        metrics,
        links,
    )
