"""SEC traffic, integrity & release qualification gate (``release-gate/1``).

Runs automated release checks and produces a qualification record:

- fair-access load test: concurrent clients through the governed SEC client against an
  offline transport; the configured rate must never be exceeded in any 1-second window;
- cache behaviour: repeat requests are served from cache;
- bulk bootstrap path: bulk and per-company submissions converge;
- golden evaluation vs thresholds and stored baseline (includes no-look-ahead, wrong-accession
  and unsupported-claim metrics);
- raw snapshot immutability and amendment lineage invariants.

The record lists KPI values, thresholds, waivers and an explicit human sign-off field that
automation never fills.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from filinglens import __version__
from filinglens.domain.models import Filing, SourceSnapshot
from filinglens.evaluation.harness import run_eval
from filinglens.infrastructure.sec.client import InMemoryHttpCache, RateGovernor, SecHttpClient
from filinglens.infrastructure.sec.submissions import BulkSubmissionsArchive, parse_submissions
from filinglens.infrastructure.storage.raw_store import FileSystemRawStore, RawIntegrityError
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.lineage.amendments import LineageEngine

GATE_VERSION = "release-gate/1"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualificationRecord:
    gate_version: str
    package_version: str
    code_commit: str | None
    created_at: str
    checks: list[CheckResult]
    kpis: dict[str, float]
    thresholds: dict[str, dict[str, float]]
    waivers: list[dict[str, str]]
    signed_off_by: str | None = None
    signed_off_at: str | None = None

    @property
    def automated_passed(self) -> bool:
        waived = {w["check"] for w in self.waivers}
        return all(c.passed or c.name in waived for c in self.checks)

    def to_json(self) -> str:
        d = asdict(self)
        d["automated_passed"] = self.automated_passed
        return json.dumps(d, indent=2, sort_keys=True, default=str)


def fair_access_load_test(
    *, rps: float = 10.0, workers: int = 6, per_worker: int = 5
) -> CheckResult:
    stamps: list[float] = []
    lock = threading.Lock()

    def handler(req: httpx.Request) -> httpx.Response:
        with lock:
            stamps.append(time.monotonic())
        return httpx.Response(200, content=b"{}")

    client = SecHttpClient(
        user_agent="FilingLens qualification qa@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        max_rps=rps,
        max_concurrency=workers,
        governor=RateGovernor(rps),
    )

    def worker(i: int) -> None:
        for j in range(per_worker):
            client.get(f"https://www.sec.gov/Archives/edgar/data/1/{i}-{j}.txt", use_cache=False)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    peak = max(
        sum(1 for s in stamps[k:] if s < start + 1.0 - 1e-3) for k, start in enumerate(stamps)
    )
    allowed = math.ceil(rps)
    return CheckResult(
        "fair_access_load",
        peak <= allowed and len(stamps) == workers * per_worker,
        {
            "requests": len(stamps),
            "peak_per_second": peak,
            "allowed": allowed,
            "configured_rps": rps,
        },
    )


def cache_behaviour_check() -> CheckResult:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x", headers={"ETag": '"1"'})

    client = SecHttpClient(
        user_agent="FilingLens qualification qa@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        cache=InMemoryHttpCache(),
        governor=RateGovernor(1000),
    )
    for _ in range(5):
        client.get("https://data.sec.gov/submissions/CIK0000000001.json")
    hit_rate = client.metrics.cache_hits / client.metrics.requests
    return CheckResult(
        "cache_behaviour",
        calls == 1 and hit_rate >= 0.8,
        {"network_calls": calls, "cache_hit_rate": hit_rate},
    )


def bulk_bootstrap_check(submissions_fixture: Path) -> CheckResult:
    raw = submissions_fixture.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "submissions.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr(submissions_fixture.name, raw)
        (bulk,) = list(BulkSubmissionsArchive(zpath).iter_batches())
    api = parse_submissions(raw, source_url="fixture")
    same = {f.accession: f for f in bulk.filings} == {f.accession: f for f in api.filings}
    return CheckResult("bulk_bootstrap_convergence", same, {"filings": len(api.filings)})


def immutability_check() -> CheckResult:
    with tempfile.TemporaryDirectory() as tmp:
        store = FileSystemRawStore(Path(tmp))
        key = store.put(b"original bytes")
        path = Path(tmp) / key
        path.chmod(0o644)
        path.write_bytes(b"tampered")
        refused_overwrite = False
        try:
            store.put(b"original bytes")
        except RawIntegrityError:
            refused_overwrite = True
        detected_read = False
        try:
            store.get(key)
        except RawIntegrityError:
            detected_read = True
    repos = SqliteRepositories(Database(":memory:"))
    s1 = SourceSnapshot.from_bytes(
        b"a",
        source_url="u1",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_type=None,
        adapter="q",
        adapter_version="1",
    )
    s2 = SourceSnapshot.from_bytes(
        b"a",
        source_url="u2",
        retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
        content_type=None,
        adapter="q",
        adapter_version="2",
    )
    repos.snapshots.add(s1)
    kept_first = repos.snapshots.add(s2) == s1
    ok = refused_overwrite and detected_read and kept_first
    return CheckResult(
        "raw_snapshot_immutability",
        ok,
        {
            "refused_overwrite": refused_overwrite,
            "detected_corruption": detected_read,
            "first_capture_kept": kept_first,
        },
    )


def amendment_lineage_check() -> CheckResult:
    def f(acc: str, form: str, accepted: datetime) -> Filing:
        return Filing(
            accession=acc,
            cik=1,
            form=form,
            filing_date=accepted.date(),
            accepted_at=accepted,
            report_period=date(2024, 9, 28),
        )

    orig = f("0000000001-24-000001", "10-K", datetime(2024, 11, 1, tzinfo=UTC))
    amend = f("0000000001-25-000001", "10-K/A", datetime(2025, 2, 1, tzinfo=UTC))
    engine = LineageEngine([orig, amend])
    before = engine.period(orig.accession, datetime(2025, 1, 1, tzinfo=UTC)).effective
    after = engine.period(orig.accession, datetime(2025, 3, 1, tzinfo=UTC)).effective
    repos = SqliteRepositories(Database(":memory:"))
    repos.filings.add(orig)
    repos.filings.add(amend)
    separate = (
        repos.filings.get(orig.accession) == orig and repos.filings.get(amend.accession) == amend
    )
    ok = before == orig and after == amend and separate
    return CheckResult(
        "amendment_lineage",
        ok,
        {"as_of_before_amendment_effective": before.accession if before else None},
    )


def run_qualification(
    *,
    eval_manifest: Path,
    eval_baseline: Path,
    submissions_fixture: Path,
    code_commit: str | None = None,
    waivers: list[dict[str, str]] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    load_rps: float = 10.0,
) -> QualificationRecord:
    checks = [
        fair_access_load_test(rps=load_rps),
        cache_behaviour_check(),
        bulk_bootstrap_check(submissions_fixture),
        immutability_check(),
        amendment_lineage_check(),
    ]
    report = run_eval(eval_manifest, eval_baseline)
    checks.append(
        CheckResult(
            "golden_evaluation",
            report.passed,
            {
                "threshold_violations": report.threshold_violations,
                "regressions": report.regressions,
                "corpus_version": report.corpus_version,
            },
        )
    )
    pit = report.metrics.get("point_in_time_violations", 1.0)
    checks.append(CheckResult("no_look_ahead", pit == 0, {"point_in_time_violations": pit}))
    unsupported = report.metrics.get("unsupported_claim_rate_in_accepted", 1.0)
    checks.append(
        CheckResult(
            "citation_integrity",
            unsupported == 0 and report.metrics.get("evidence_binding_rate", 0) == 1.0,
            {
                "unsupported_claim_rate_in_accepted": unsupported,
                "evidence_binding_rate": report.metrics.get("evidence_binding_rate"),
            },
        )
    )
    thresholds = json.loads(Path(eval_manifest).read_text(encoding="utf-8"))["thresholds"]
    return QualificationRecord(
        gate_version=GATE_VERSION,
        package_version=__version__,
        code_commit=code_commit,
        created_at=clock().isoformat(),
        checks=checks,
        kpis=dict(report.metrics),
        thresholds=thresholds,
        waivers=list(waivers or []),
    )


def fingerprint(record: QualificationRecord) -> str:
    return hashlib.sha256(record.to_json().encode()).hexdigest()
