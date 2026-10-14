"""Task 6.6: export and research artifact packaging."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from filinglens.reporting.export import (
    CSV_COLUMNS,
    ExportDocument,
    export_csv,
    export_html,
    export_json,
    package_artifacts,
)
from filinglens.reporting.report import (
    CitedEvidence,
    ReportFinding,
    ReportInputs,
    ReportIntegrityError,
)

FOCAL = "0001234567-24-000031"


def inputs(**kw: object) -> ReportInputs:
    base: dict[str, object] = dict(
        run_id="run-9",
        issuer_name="Example <Devices> & Co",
        cik=1234567,
        focal_accession=FOCAL,
        focal_form="10-K",
        comparator_accession="0001234567-23-000030",
        comparator_reason="prior fiscal-year 10-K",
        as_of="2026-01-01T00:00:00+00:00",
        findings=(
            ReportFinding(
                "new_risk_factor",
                "1A",
                'Export controls risk, "new"; added.',
                "May restrict sales.",
                "high",
                88.0,
                ("ev-rf", "ev-met"),
                "risk_factors",
                ("Which products?",),
            ),
        ),
        evidence=(
            CitedEvidence(
                "ev-rf",
                "text_change",
                FOCAL,
                "main.htm",
                "1A",
                "/html/body/p[5]",
                "Export controls...",
            ),
            CitedEvidence(
                "ev-met", "xbrl_metric", None, None, None, None, "revenue_growth = 0.020220"
            ),
        ),
        blocked_findings=1,
        component_versions={
            "calculation": "trend-engine/1",
            "prompt": "filing-change-analyst/1",
            "llm_model": "fake-model-1",
        },
        snapshot_ids=("a" * 64, "b" * 64),
    )
    base.update(kw)
    return ReportInputs(**base)  # type: ignore[arg-type]


def test_json_schema_and_provenance() -> None:
    doc = ExportDocument.model_validate_json(export_json(inputs()))
    assert doc.schema_version == "export-schema/1"
    assert (
        doc.run.run_id == "run-9"
        and doc.run.focal_accession == FOCAL
        and doc.run.as_of.startswith("2026")
    )
    assert doc.provenance.snapshot_ids == ["a" * 64, "b" * 64]
    assert doc.provenance.component_versions["prompt"] == "filing-change-analyst/1"
    assert doc.provenance.inputs_hash == inputs().inputs_hash
    cite = doc.findings[0].citations[0]
    assert (cite.accession, cite.section, cite.locator) == (FOCAL, "1A", "/html/body/p[5]")
    assert "not legal materiality" in doc.research_priority_note
    with pytest.raises(ValidationError):
        ExportDocument.model_validate({**json.loads(export_json(inputs())), "extra": 1})


def test_csv_findings_table() -> None:
    rows = list(csv.DictReader(io.StringIO(export_csv(inputs()))))
    assert list(rows[0]) == CSV_COLUMNS
    r = rows[0]
    assert r["citations"] == "ev-rf;ev-met" and r["research_priority"] == "88.00"
    assert r["citation_locators"].startswith(f"{FOCAL}|main.htm|1A|/html/body/p[5]")
    assert r["summary"] == 'Export controls risk, "new"; added.'  # CSV quoting preserved


def test_html_is_escaped_and_printable() -> None:
    page = export_html(inputs())
    assert page.startswith("<!doctype html>") and "@media print" in page
    assert "Example &lt;Devices&gt; &amp; Co" in page and "<Devices>" not in page
    assert "[ev-rf]" in page


def test_no_hidden_or_uncited_claims_exported() -> None:
    bad = inputs(
        findings=(ReportFinding("x", None, "Model-only claim.", "", "low", None, (), "other"),)
    )
    for fn in (export_json, export_csv, export_html):
        with pytest.raises(ReportIntegrityError):
            fn(bad)
    doc = json.loads(export_json(inputs()))
    assert len(doc["findings"]) == 1 and doc["provenance"]["blocked_findings"] == 1


def test_package_writes_hashed_manifest(tmp_path: Path) -> None:
    manifest = package_artifacts(inputs(), tmp_path / "out")
    assert set(manifest) == {"report.md", "report.json", "findings.csv", "report.html"}
    for name, digest in manifest.items():
        assert hashlib.sha256((tmp_path / "out" / name).read_bytes()).hexdigest() == digest
    meta = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert (
        meta["schema_version"] == "export-schema/1" and meta["inputs_hash"] == inputs().inputs_hash
    )
    again = package_artifacts(inputs(), tmp_path / "out2")
    assert again == manifest  # deterministic exports
