"""Task 5.5: golden evaluation corpus and regression harness."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from filinglens.evaluation.harness import run_eval

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "eval" / "corpus_v1"
MANIFEST = CORPUS / "manifest.json"
BASELINE = CORPUS / "baseline.json"


def test_manifest_is_versioned() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["corpus_version"] == "golden-corpus/1" and m["thresholds"]


def test_baseline_metrics_reproducible() -> None:
    a = run_eval(MANIFEST, BASELINE)
    b = run_eval(MANIFEST, BASELINE)
    assert a.passed, (a.threshold_violations, a.regressions, a.failures)
    assert a.metrics == b.metrics
    stored = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert stored["metrics"] == a.metrics and stored["corpus_version"] == a.corpus_version


def test_threshold_regression_detected(tmp_path: Path) -> None:
    copy = tmp_path / "corpus"
    shutil.copytree(CORPUS, copy)
    m = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    m["comparators"]["cases"][0]["expected"] = (
        "K22"  # wrong label simulates a comparator regression
    )
    (copy / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    report = run_eval(copy / "manifest.json", copy / "baseline.json")
    assert not report.passed
    assert any(v.startswith("comparator_accuracy") for v in report.threshold_violations)
    assert any(r.startswith("comparator_accuracy") for r in report.regressions)


def test_baseline_regression_detected_even_within_thresholds(tmp_path: Path) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline["metrics"]["wrong_accession_rate"] = -1.0  # pretend the old run was better
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    report = run_eval(MANIFEST, path)
    assert report.regressions == ["wrong_accession_rate: -1.0 -> 0.0"]


def test_unsupported_and_foreign_findings_never_accepted() -> None:
    report = run_eval(MANIFEST)
    assert report.metrics["unsupported_claim_rate_in_accepted"] == 0.0
    assert report.metrics["finding_precision"] == 1.0


def test_fixtures_are_minimized() -> None:
    for path in CORPUS.rglob("*"):
        if path.is_file():
            assert path.stat().st_size < 50_000, path.name
