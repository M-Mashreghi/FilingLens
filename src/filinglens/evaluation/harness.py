"""Golden evaluation corpus runner (``eval-harness/1``).

Runs deterministic parser, comparator, retrieval, finding and XBRL checks over a versioned
corpus manifest, computes metrics, compares them with thresholds and (optionally) a stored
baseline so regressions are detectable in CI. Model outputs come from the corpus (fake
provider); paid/live LLM evaluation is a separate, optional run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from filinglens.domain.models import Filing
from filinglens.lineage.comparator import ComparatorMode, select_comparator
from filinglens.llm.client import EvidenceItem, FakeProvider, StructuredLLMClient
from filinglens.llm.validation import EvidenceBinding, RunScope, Verdict, validate
from filinglens.llm.workflow import FilingChangeWorkflow
from filinglens.parsing.normalizer import normalize_html
from filinglens.parsing.sections import SectionStatus, parse_sections
from filinglens.retrieval.index import RetrievalFilter, RetrievalIndex
from filinglens.xbrl.ixbrl import extract_ixbrl

HARNESS_VERSION = "eval-harness/1"


@dataclass
class EvalReport:
    corpus_version: str
    metrics: dict[str, float]
    failures: list[str] = field(default_factory=list)
    threshold_violations: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    harness_version: str = HARNESS_VERSION

    @property
    def passed(self) -> bool:
        return not self.threshold_violations and not self.regressions

    def to_json(self) -> str:
        return json.dumps(
            {
                "corpus_version": self.corpus_version,
                "harness_version": self.harness_version,
                "metrics": self.metrics,
                "threshold_violations": self.threshold_violations,
                "regressions": self.regressions,
                "failures": self.failures,
                "passed": self.passed,
            },
            indent=2,
            sort_keys=True,
        )


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _sections(root: Path, cases: list[dict[str, Any]], report: EvalReport) -> None:
    ok = total = amb_hit = amb_total = 0
    for case in cases:
        doc = normalize_html((root / case["doc"]).read_bytes())
        res = parse_sections(doc, case["form"])
        for key, exp in case["expect"].items():
            total += 1
            sec = res.get(key)
            good = (
                sec is not None
                and sec.status is SectionStatus.OK
                and exp["contains"] in sec.text
                and exp["excludes"] not in sec.text
            )
            ok += good
            if not good:
                report.failures.append(f"section {case['doc']}#{key}")
        for key in case.get("ambiguous", []):
            amb_total += 1
            sec = res.get(key)
            amb_hit += bool(sec and sec.status is SectionStatus.AMBIGUOUS)
    report.metrics["section_success_rate"] = ok / total if total else 1.0
    report.metrics["ambiguous_detection_rate"] = amb_hit / amb_total if amb_total else 1.0


def _comparators(spec: dict[str, Any], report: EvalReport) -> None:
    filings: dict[str, Filing] = {}
    for i, f in enumerate(spec["filings"], start=1):
        acc = _dt(f["accepted"])
        filings[f["id"]] = Filing(
            accession=f"0001234567-{acc.year % 100:02d}-{i:06d}",
            cik=1234567,
            form=f["form"],
            filing_date=acc.date(),
            report_period=date.fromisoformat(f["period"]),
            accepted_at=acc,
        )
    by_acc = {v.accession: k for k, v in filings.items()}
    correct = leaks = 0
    for case in spec["cases"]:
        as_of = _dt(case["as_of"])
        mode = ComparatorMode(case.get("mode", "default"))
        sel = select_comparator(
            filings[case["focal"]],
            list(filings.values()),
            as_of=as_of,
            fiscal_year_end=spec["fiscal_year_end"],
            mode=mode,
        )
        got = by_acc.get(sel.comparator_accession or "")
        if got == case["expected"]:
            correct += 1
        else:
            report.failures.append(
                f"comparator {case['focal']}: got {got}, expected {case['expected']}"
            )
        if got and filings[got].available_at > as_of:
            leaks += 1
    report.metrics["comparator_accuracy"] = correct / len(spec["cases"])
    report.metrics["point_in_time_violations"] = float(leaks)


def _retrieval(root: Path, spec: dict[str, Any], report: EvalReport) -> None:
    idx = RetrievalIndex()
    for d in spec["documents"]:
        doc = normalize_html((root / d["doc"]).read_bytes(), snapshot_id=None)
        idx.add_document(
            doc,
            accession=d["accession"],
            filename=Path(d["doc"]).name,
            available_at=_dt(d["available_at"]),
            doc_type=d["form"],
            sections=parse_sections(doc, d["form"]),
        )
    hits_ok = wrong = returned = 0
    for q in spec["queries"]:
        accs = frozenset(q["accessions"])
        flt = RetrievalFilter(
            accs,
            datetime(2100, 1, 1, tzinfo=UTC),
            sections=frozenset(q["sections"]) if "sections" in q else None,
        )
        hits = idx.search(q["query"], flt, k=spec["k"])
        returned += len(hits)
        wrong += sum(1 for h in hits if h.chunk.accession not in accs)
        found = any(h.chunk.section_key == q["expected_section"] for h in hits)
        hits_ok += found
        if not found:
            report.failures.append(f"retrieval '{q['query']}'")
    report.metrics["retrieval_recall_at_k"] = hits_ok / len(spec["queries"])
    report.metrics["wrong_accession_rate"] = wrong / returned if returned else 0.0


def _findings(spec: dict[str, Any], report: EvalReport) -> None:
    evidence = [
        EvidenceItem(e["id"], e["kind"], e["content"], None, e.get("section"))
        for e in spec["evidence"]
    ]
    bindings = {
        e["id"]: EvidenceBinding(
            e["id"],
            e["binding"].get("snapshot_id"),
            e["binding"].get("accession"),
            None,
            e["binding"].get("section"),
            tuple(e["binding"].get("fact_ids", ())),
        )
        for e in spec["evidence"]
    }
    scope = RunScope(
        frozenset(spec["scope"]["snapshot_ids"]),
        frozenset(spec["scope"]["accessions"]),
        frozenset(spec["scope"]["fact_ids"]),
    )
    provider = FakeProvider([json.dumps(spec["model_output"])])
    wf = FilingChangeWorkflow(StructuredLLMClient(provider)).run(
        form="10-K", is_amendment=False, evidence=evidence
    )
    results = validate(list(wf.findings), bindings, scope)
    accepted = {r.finding.output.finding_type for r in results if r.verdict is Verdict.ACCEPTED}
    labels: dict[str, str] = spec["labels"]
    true_pos = sum(1 for t in accepted if labels.get(t) == "supported")
    unsupported_accepted = sum(1 for t in accepted if labels.get(t) != "supported")
    expected = set(spec["expected_material"])
    report.metrics["finding_precision"] = true_pos / len(accepted) if accepted else 0.0
    report.metrics["finding_recall"] = len(accepted & expected) / len(expected) if expected else 1.0
    report.metrics["unsupported_claim_rate_in_accepted"] = (
        unsupported_accepted / len(accepted) if accepted else 0.0
    )
    acc_results = [r for r in results if r.verdict is Verdict.ACCEPTED]
    bound = sum(1 for r in acc_results if r.finding.valid_evidence_ids)
    report.metrics["evidence_binding_rate"] = bound / len(acc_results) if acc_results else 1.0


def _xbrl(root: Path, spec: dict[str, Any], report: EvalReport) -> None:
    ext = extract_ixbrl(
        (root / spec["doc"]).read_bytes(), cik=spec["cik"], accession=spec["accession"]
    )
    ok = 0
    for e in spec["expect"]:
        match = [f for f in ext.facts if f.qname == e["qname"] and f.context_id == e["context"]]
        good = (
            len(match) == 1
            and (match[0].value == (Decimal(e["value"]) if e["value"] is not None else None))
            and match[0].is_custom == e["custom"]
            and match[0].is_dimensional == e["dimensional"]
        )
        ok += good
        if not good:
            report.failures.append(f"xbrl {e['qname']}@{e['context']}")
    report.metrics["xbrl_fact_accuracy"] = ok / len(spec["expect"])


def run_eval(
    manifest_path: Path, baseline_path: Path | None = None, tolerance: float = 1e-9
) -> EvalReport:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    root = Path(manifest_path).parent
    report = EvalReport(manifest["corpus_version"], {})
    _sections(root, manifest["sections"], report)
    _comparators(manifest["comparators"], report)
    _retrieval(root, manifest["retrieval"], report)
    _findings(manifest["findings"], report)
    _xbrl(root, manifest["xbrl"], report)
    for name, rule in manifest["thresholds"].items():
        value = report.metrics.get(name)
        if value is None:
            report.threshold_violations.append(f"{name}: metric not computed")
        elif "min" in rule and value < rule["min"]:
            report.threshold_violations.append(f"{name}={value:.4f} < {rule['min']}")
        elif "max" in rule and value > rule["max"]:
            report.threshold_violations.append(f"{name}={value:.4f} > {rule['max']}")
    if baseline_path is not None and Path(baseline_path).exists():
        base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))["metrics"]
        lower_is_better = {k for k, r in manifest["thresholds"].items() if "max" in r}
        for k, old in base.items():
            new = report.metrics.get(k)
            if new is None:
                continue
            worse = new > old + tolerance if k in lower_is_better else new < old - tolerance
            if worse:
                report.regressions.append(f"{k}: {old} -> {new}")
    return report
