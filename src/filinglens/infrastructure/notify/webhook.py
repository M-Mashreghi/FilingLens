"""Generic JSON webhook notifier (optional). Never used for SEC traffic."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from filinglens.domain.models import Alert

SEC_HOSTS = ("sec.gov",)


class WebhookError(RuntimeError):
    pass


class HttpWebhookNotifier:
    def __init__(
        self, url: str, *, timeout: float = 5.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme not in ("https", "http") or not host:
            raise ValueError("webhook URL must be http(s)")
        if host.endswith(SEC_HOSTS):
            raise ValueError("webhooks must not target SEC infrastructure")
        self.url = url
        self._client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def send(self, alert: Alert, deep_link: str) -> None:
        payload = {
            "alert_id": alert.alert_id,
            "kind": alert.kind,
            "priority": alert.priority,
            "reason": alert.reason,
            "run_id": alert.run_id,
            "finding_id": alert.finding_id,
            "link": deep_link,
            "created_at": alert.created_at.isoformat(),
        }
        resp = self._client.post(
            self.url, content=json.dumps(payload), headers={"Content-Type": "application/json"}
        )
        if resp.status_code >= 300:
            raise WebhookError(f"webhook returned HTTP {resp.status_code}")
