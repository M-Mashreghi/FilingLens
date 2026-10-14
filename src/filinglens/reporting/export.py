"""Export & research artifact packaging (``export-schema/1``).

Exports Markdown, versioned machine-readable JSON, a CSV findings table and printable HTML
from the same ``ReportInputs`` used for the report. Every export carries run id, accessions,
``as_of``, snapshot hashes, component (calculation/prompt/model) versions and citation
locators. Only cited, accepted findings exist in ``ReportInputs``; an export with a dangling
or missing citation is refused.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from filinglens.reporting.report import (
    REPORT_VERSION,
    ReportInputs,
    ReportIntegrityError,
    integrity_problems,
    render_report,
)

EXPORT_SCHEMA_VERSION = "export-schema/1"
PRIORITY_NOTE = "Research priority ranks analyst attention; it is not legal materiality."


class ExportCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    kind: str
    accession: str | None
    document: str | None
    section: str | None
    locator: str | None


class ExportFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_type: str
    category: str
    section: str | None
    severity: str
    research_priority: float | None
    summary: str
    why_it_matters: str
    open_questions: list[str]
    citations: list[ExportCitation]


class ExportRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    cik: int
    issuer_name: str
    focal_accession: str
    focal_form: str
    comparator_accession: str | None
    comparator_reason: str | None
    as_of: str


class ExportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_ids: list[str]
    component_versions: dict[str, str]
    report_version: str
    inputs_hash: str
    blocked_findings: int


class ExportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["export-schema/1"]
    run: ExportRun
    provenance: ExportProvenance
    findings: list[ExportFinding]
    research_priority_note: str


def _check(inputs: ReportInputs) -> None:
    problems = integrity_problems(inputs)
    if problems:
        raise ReportIntegrityError("; ".join(problems))


def to_export_document(inputs: ReportInputs) -> ExportDocument:
    _check(inputs)
    ev = {e.evidence_id: e for e in inputs.evidence}
    findings = [
        ExportFinding(
            finding_type=f.finding_type,
            category=f.category,
            section=f.section,
            severity=f.severity,
            research_priority=f.priority_score,
            summary=f.summary,
            why_it_matters=f.why_it_matters,
            open_questions=list(f.open_questions),
            citations=[
                ExportCitation(
                    evidence_id=c,
                    kind=ev[c].kind,
                    accession=ev[c].accession,
                    document=ev[c].document,
                    section=ev[c].section,
                    locator=ev[c].locator,
                )
                for c in f.citations
            ],
        )
        for f in inputs.findings
    ]
    return ExportDocument(
        schema_version="export-schema/1",
        run=ExportRun(
            run_id=inputs.run_id,
            cik=inputs.cik,
            issuer_name=inputs.issuer_name,
            focal_accession=inputs.focal_accession,
            focal_form=inputs.focal_form,
            comparator_accession=inputs.comparator_accession,
            comparator_reason=inputs.comparator_reason,
            as_of=inputs.as_of,
        ),
        provenance=ExportProvenance(
            snapshot_ids=list(inputs.snapshot_ids),
            component_versions=dict(sorted(inputs.component_versions.items())),
            report_version=REPORT_VERSION,
            inputs_hash=inputs.inputs_hash,
            blocked_findings=inputs.blocked_findings,
        ),
        findings=findings,
        research_priority_note=PRIORITY_NOTE,
    )


def export_json(inputs: ReportInputs) -> str:
    return json.dumps(to_export_document(inputs).model_dump(), indent=2, sort_keys=True)


def export_markdown(inputs: ReportInputs) -> str:
    return render_report(inputs)


CSV_COLUMNS = [
    "run_id",
    "focal_accession",
    "as_of",
    "finding_type",
    "category",
    "section",
    "severity",
    "research_priority",
    "summary",
    "citations",
    "citation_locators",
]


def export_csv(inputs: ReportInputs) -> str:
    doc = to_export_document(inputs)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for f in doc.findings:
        w.writerow(
            {
                "run_id": doc.run.run_id,
                "focal_accession": doc.run.focal_accession,
                "as_of": doc.run.as_of,
                "finding_type": f.finding_type,
                "category": f.category,
                "section": f.section or "",
                "severity": f.severity,
                "research_priority": ""
                if f.research_priority is None
                else f"{f.research_priority:.2f}",
                "summary": f.summary,
                "citations": ";".join(c.evidence_id for c in f.citations),
                "citation_locators": ";".join(
                    "|".join(x or "-" for x in (c.accession, c.document, c.section, c.locator))
                    for c in f.citations
                ),
            }
        )
    return buf.getvalue()


def export_html(inputs: ReportInputs) -> str:
    md = render_report(inputs)
    body: list[str] = []
    in_list = False
    for line in md.splitlines():
        esc = html.escape(line)
        if line.startswith("- ") or line.startswith("  - "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{html.escape(line.lstrip(' -'))}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.strip():
            body.append(f"<p>{esc}</p>")
    if in_list:
        body.append("</ul>")
    title = html.escape(f"FilingLens report {inputs.run_id}")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>"
        "body{font-family:sans-serif;max-width:52rem;margin:2rem auto}"
        "@media print{a{color:inherit}}</style></head><body>" + "\n".join(body) + "</body></html>"
    )


def package_artifacts(inputs: ReportInputs, out_dir: Path) -> dict[str, str]:
    """Write all export formats plus a manifest of sha256 hashes; returns the manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "report.md": export_markdown(inputs),
        "report.json": export_json(inputs),
        "findings.csv": export_csv(inputs),
        "report.html": export_html(inputs),
    }
    manifest: dict[str, str] = {}
    for name, content in files.items():
        data = content.encode("utf-8")
        (out / name).write_bytes(data)
        manifest[name] = hashlib.sha256(data).hexdigest()
    meta = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "run_id": inputs.run_id,
        "inputs_hash": inputs.inputs_hash,
        "files": manifest,
    }
    (out / "manifest.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
