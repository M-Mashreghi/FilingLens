"""Optional live SEC smoke test (Task 7.4). Skipped unless FILINGLENS_LIVE_SEC=1.

Budget: at most 2 governed requests at 1 request/second with a declared User-Agent.
"""

from __future__ import annotations

import os

import pytest

from filinglens.config import Settings
from filinglens.infrastructure.sec.client import SecHttpClient
from filinglens.infrastructure.sec.submissions import SubmissionsAdapter

pytestmark = [
    pytest.mark.live_sec,
    pytest.mark.skipif(
        os.environ.get("FILINGLENS_LIVE_SEC") != "1", reason="live SEC smoke disabled"
    ),
]


def test_live_submissions_smoke() -> None:
    settings = Settings()
    client = SecHttpClient(
        user_agent=settings.sec_user_agent, live=True, max_rps=1.0, max_concurrency=1
    )
    batch = SubmissionsAdapter(client).fetch(320193, include_history=False)  # one request
    assert batch.issuer.cik == 320193 and batch.filings
    assert client.metrics.network_requests <= 2
