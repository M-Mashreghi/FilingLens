"""Task 6.5: research alerts and triage queue."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from filinglens.application.alerts import AlertService, deep_link
from filinglens.domain.models import Alert
from filinglens.infrastructure.notify.webhook import HttpWebhookNotifier
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.lineage.amendments import RestatementSignal
from filinglens.llm.client import FindingOutput, InterpretationStatus
from filinglens.llm.validation import ValidatedFinding, Verdict
from filinglens.llm.workflow import AnalystFinding, Basis

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def service(notifier=None):  # type: ignore[no-untyped-def]
    repos = SqliteRepositories(Database(":memory:"))
    tick = iter(range(1000))
    return AlertService(
        repos.alerts, notifier, clock=lambda: T0 + timedelta(seconds=next(tick))
    ), repos


def vf(score: float | None, verdict: Verdict = Verdict.ACCEPTED) -> ValidatedFinding:
    out = FindingOutput(
        finding_type="new_risk_factor",
        document_type="10-K",
        section_or_item="Item 1A",
        direction="new",
        severity="high",
        confidence=0.8,
        summary="Export controls risk added.",
        why_it_matters="w",
        evidence_ids=["ev-1"],
        open_questions=[],
        interpretation_status=InterpretationStatus.SUPPORTED,
    )
    af = AnalystFinding(out, ("ev-1",), (), (Basis.SOURCE_FACT,), InterpretationStatus.SUPPORTED)
    return ValidatedFinding(af, verdict, (), (), score, ())


def test_duplicate_alerts_suppressed() -> None:
    svc, repos = service()
    a, new1 = svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    b, new2 = svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    assert new1 and not new2 and a == b
    svc.high_priority_finding("run-1", "f1", vf(85.0))
    svc.high_priority_finding("run-1", "f1", vf(90.0))
    assert len(repos.alerts.list()) == 2


def test_alert_kinds_and_thresholds() -> None:
    svc, _ = svc_repos = service()
    assert svc.high_priority_finding("run-1", "f1", vf(50.0)) is None
    assert svc.high_priority_finding("run-1", "f1", vf(None)) is None
    assert svc.high_priority_finding("run-1", "f1", vf(95.0, Verdict.BLOCKED)) is None
    sig = RestatementSignal(
        "0001234567-25-000008", "non_reliance_8k", T0, "8-K Item 4.02 non-reliance disclosure"
    )
    r, _ = svc.restatement("run-2", sig)
    assert r.kind == "restatement_signal" and r.priority == "high"
    op, _ = svc.operational("source_failure", "SEC returned 503 after 4 attempts", run_id="run-3")
    assert op.kind == "source_failure"
    with pytest.raises(ValueError):
        svc.operational("mystery", "x")
    with pytest.raises(ValueError):
        svc._raise(kind="new_filing", priority="low", reason="r", dedupe_key="k", run_id=None)
    del svc_repos


def test_every_alert_is_traceable() -> None:
    svc, repos = service()
    svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    svc.high_priority_finding("run-1", "f1", vf(88.0))
    svc.operational("model_failure", "malformed model output: schema violation")
    for a in repos.alerts.list():
        link = deep_link(a)
        if a.kind in ("source_failure", "parser_failure", "model_failure"):
            assert a.reason and link.startswith("/alerts/")
        else:
            assert a.run_id and link.startswith(f"/runs/{a.run_id}")
    finding_alert = next(a for a in repos.alerts.list() if a.kind == "high_priority_finding")
    assert deep_link(finding_alert) == "/runs/run-1/findings?finding_id=f1"


def test_triage_queue_order_and_acknowledgement() -> None:
    svc, _ = service()
    svc.new_filing("run-1", "0001234567-25-000004", "10-Q")  # medium
    hi, _ = svc.high_priority_finding("run-1", "f1", vf(90.0))  # high
    queue = svc.triage_queue()
    assert [a.priority for a in queue] == ["high", "medium"]
    svc.acknowledge(hi.alert_id)
    assert [a.kind for a in svc.triage_queue()] == ["new_filing"]


def test_webhook_failure_does_not_corrupt_state() -> None:
    calls: list[str] = []

    class Flaky:
        def send(self, alert: Alert, link: str) -> None:
            calls.append(alert.alert_id)
            raise ConnectionError("webhook down")

    svc, repos = service(Flaky())
    a, new = svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    assert new and repos.alerts.get(a.alert_id) == a
    assert svc.deliveries[0].ok is False and "webhook down" in (svc.deliveries[0].error or "")
    svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    assert calls == [a.alert_id]  # duplicates are not re-sent


def test_http_webhook_adapter() -> None:
    seen: list[dict[str, object]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(500 if len(seen) > 1 else 204)

    n = HttpWebhookNotifier("https://hooks.example.com/x", transport=httpx.MockTransport(handler))
    svc, _ = service(n)
    svc.new_filing("run-1", "0001234567-25-000004", "10-Q")
    svc.new_filing("run-2", "0001234567-25-000006", "8-K")
    assert seen[0]["link"] == "/runs/run-1" and [d.ok for d in svc.deliveries] == [True, False]
    with pytest.raises(ValueError):
        HttpWebhookNotifier("https://www.sec.gov/hook")
    with pytest.raises(ValueError):
        HttpWebhookNotifier("ftp://x")
