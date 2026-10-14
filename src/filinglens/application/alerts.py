"""Research alerts and triage queue (``alerts/1``).

Alerts are internal records with priority, reason and a deep link to a traceable run,
finding or operational error. Duplicates are suppressed by ``dedupe_key``. An optional
webhook notifier is called after the alert is stored; webhook failures are recorded but never
roll back or corrupt analysis/alert state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from filinglens.application.ports import AlertRepository
from filinglens.domain.models import Alert
from filinglens.domain.time import utcnow
from filinglens.lineage.amendments import RestatementSignal
from filinglens.llm.validation import ValidatedFinding, Verdict

ALERTS_VERSION = "alerts/1"
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
OPERATIONAL_KINDS = {"source_failure", "parser_failure", "model_failure"}


class WebhookNotifier(Protocol):
    def send(self, alert: Alert, deep_link: str) -> None: ...


@dataclass
class DeliveryRecord:
    alert_id: str
    ok: bool
    error: str | None = None


def deep_link(alert: Alert) -> str:
    if alert.finding_id and alert.run_id:
        return f"/runs/{alert.run_id}/findings?finding_id={alert.finding_id}"
    if alert.run_id:
        return f"/runs/{alert.run_id}"
    return f"/alerts/{alert.alert_id}"


def _aid(key: str) -> str:
    return "alr-" + hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class AlertService:
    alerts: AlertRepository
    notifier: WebhookNotifier | None = None
    clock: Callable[[], datetime] = utcnow
    deliveries: list[DeliveryRecord] = field(default_factory=list)

    def _raise(
        self,
        *,
        kind: str,
        priority: str,
        reason: str,
        dedupe_key: str,
        run_id: str | None,
        finding_id: str | None = None,
    ) -> tuple[Alert, bool]:
        if kind not in OPERATIONAL_KINDS and run_id is None:
            raise ValueError("research alerts must link to a run")
        candidate = Alert(
            alert_id=_aid(dedupe_key),
            kind=kind,
            priority=priority,
            reason=reason,
            dedupe_key=dedupe_key,
            run_id=run_id,
            finding_id=finding_id,
            created_at=self.clock(),
        )
        stored = self.alerts.add(candidate)
        is_new = stored.alert_id == candidate.alert_id and stored == candidate
        if is_new and self.notifier is not None:
            try:
                self.notifier.send(stored, deep_link(stored))
                self.deliveries.append(DeliveryRecord(stored.alert_id, True))
            except Exception as exc:  # never let delivery failures affect state
                self.deliveries.append(
                    DeliveryRecord(stored.alert_id, False, f"{type(exc).__name__}: {exc}")
                )
        return stored, is_new

    def new_filing(self, run_id: str, accession: str, form: str) -> tuple[Alert, bool]:
        return self._raise(
            kind="new_filing",
            priority="medium",
            reason=f"New {form} filing {accession} queued for analysis",
            dedupe_key=f"new_filing:{accession}",
            run_id=run_id,
        )

    def high_priority_finding(
        self, run_id: str, finding_id: str, vf: ValidatedFinding, threshold: float = 70.0
    ) -> tuple[Alert, bool] | None:
        if (
            vf.verdict is not Verdict.ACCEPTED
            or vf.priority_score is None
            or vf.priority_score < threshold
        ):
            return None
        out = vf.finding.output
        return self._raise(
            kind="high_priority_finding",
            priority="high",
            reason=f"{out.finding_type} ({out.section_or_item or 'n/a'}): {out.summary} "
            f"[research priority {vf.priority_score:.0f}]",
            dedupe_key=f"finding:{run_id}:{out.finding_type}:{out.section_or_item}",
            run_id=run_id,
            finding_id=finding_id,
        )

    def restatement(self, run_id: str, signal: RestatementSignal) -> tuple[Alert, bool]:
        return self._raise(
            kind="restatement_signal",
            priority="high",
            reason=signal.description,
            dedupe_key=f"restatement:{signal.accession}",
            run_id=run_id,
        )

    def operational(
        self, kind: str, error: str, *, run_id: str | None = None
    ) -> tuple[Alert, bool]:
        if kind not in OPERATIONAL_KINDS:
            raise ValueError(f"unknown operational alert kind {kind!r}")
        return self._raise(
            kind=kind,
            priority="high" if kind == "source_failure" else "medium",
            reason=error,
            dedupe_key=f"op:{kind}:{run_id or error}",
            run_id=run_id,
        )

    def triage_queue(self) -> list[Alert]:
        pending = self.alerts.list(acknowledged=False)
        return sorted(
            pending, key=lambda a: (PRIORITY_ORDER.get(a.priority, 9), a.created_at, a.alert_id)
        )

    def acknowledge(self, alert_id: str) -> Alert:
        return self.alerts.acknowledge(alert_id)
