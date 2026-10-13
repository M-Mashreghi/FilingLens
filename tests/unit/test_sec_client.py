"""Task 2.1: SEC fair-access client and rate governor."""

from __future__ import annotations

import ast
import math
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from filinglens.config import Settings
from filinglens.infrastructure.sec.client import (
    FileHttpCache,
    InMemoryHttpCache,
    RateGovernor,
    RetryPolicy,
    SecConfigurationError,
    SecForbiddenError,
    SecHttpClient,
    SecNotFoundError,
    SecUnavailableError,
    validate_user_agent,
)

URL = "https://data.sec.gov/submissions/CIK0000320193.json"
UA = "FilingLens research test@example.com"
SRC = Path(__file__).resolve().parents[2] / "src" / "filinglens"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            return self.t

    def sleep(self, s: float) -> None:
        with self.lock:
            self.t += s


def _client(handler, **kw: object) -> SecHttpClient:  # type: ignore[no-untyped-def]
    clock = kw.pop("clock", None) or FakeClock()
    params: dict[str, object] = {
        "user_agent": UA,
        "live": False,
        "transport": httpx.MockTransport(handler),
        "governor": RateGovernor(5, clock=clock.now, sleep=clock.sleep),  # type: ignore[attr-defined]
        "sleep": clock.sleep,  # type: ignore[attr-defined]
        "rng": random.Random(0),
    }
    params.update(kw)
    return SecHttpClient(**params)  # type: ignore[arg-type]


# --- configuration -------------------------------------------------------------------------


@pytest.mark.parametrize("ua", ["", "   ", "FilingLens", "python-httpx/0.27", "me@x"])
def test_user_agent_requires_contact(ua: str) -> None:
    with pytest.raises(SecConfigurationError):
        validate_user_agent(ua)


def test_missing_user_agent_blocks_live_mode() -> None:
    with pytest.raises(SecConfigurationError):
        SecHttpClient(user_agent="", live=True)
    with pytest.raises(SecConfigurationError):
        SecHttpClient.from_settings(Settings(sec_live_mode=True, sec_user_agent=""))
    ok = SecHttpClient(user_agent=UA, live=True)
    assert ok.user_agent == UA
    ok.close()


def test_offline_mode_requires_fixture_transport() -> None:
    with pytest.raises(SecConfigurationError):
        SecHttpClient(user_agent=UA, live=False)


def test_rate_above_sec_ceiling_rejected() -> None:
    with pytest.raises(SecConfigurationError):
        SecHttpClient(user_agent=UA, live=True, max_rps=11)


def test_non_sec_hosts_refused() -> None:
    c = _client(lambda r: httpx.Response(200))
    with pytest.raises(SecConfigurationError):
        c.get("https://example.com/x")


def test_sends_declared_user_agent() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["user-agent"])
        return httpx.Response(200, content=b"{}")

    _client(handler).get(URL)
    assert seen == [UA]


# --- rate governor ---------------------------------------------------------------------------


@pytest.mark.parametrize("rps", [1, 2.5, 5, 10])
def test_governor_never_exceeds_ceiling_fake_clock(rps: float) -> None:
    clock = FakeClock()
    gov = RateGovernor(rps, clock=clock.now, sleep=clock.sleep)
    stamps = []
    for _ in range(60):
        gov.acquire()
        stamps.append(clock.now())
    for i, start in enumerate(stamps):
        in_window = [s for s in stamps[i:] if s < start + 1.0 - 1e-6]
        assert len(in_window) <= math.ceil(rps), (rps, len(in_window))


def test_client_requests_never_exceed_configured_rate() -> None:
    clock = FakeClock()
    stamps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        stamps.append(clock.now())
        return httpx.Response(200, content=b"ok")

    c = _client(handler, clock=clock, governor=RateGovernor(4, clock=clock.now, sleep=clock.sleep))
    for i in range(40):
        c.get(f"https://www.sec.gov/Archives/edgar/data/1/{i}.txt")
    for i, start in enumerate(stamps):
        assert len([s for s in stamps[i:] if s < start + 1.0 - 1e-6]) <= 4
    assert c.metrics.network_requests == 40
    assert c.metrics.throttle_wait_seconds > 0


def test_governor_real_time_threads() -> None:
    gov = RateGovernor(10)
    stamps: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(3):
            gov.acquire()
            with lock:
                stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    assert len(stamps) == 12
    # 12 permits at 10/s with burst 1 need >= 1.1 s.
    assert stamps[-1] - stamps[0] >= 1.05
    for i, start in enumerate(stamps):
        assert len([s for s in stamps[i:] if s < start + 1.0 - 1e-3]) <= 10


def test_concurrency_is_bounded() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return httpx.Response(200)

    c = SecHttpClient(
        user_agent=UA,
        live=False,
        transport=httpx.MockTransport(handler),
        max_rps=10,
        max_concurrency=2,
        governor=RateGovernor(10_000),
    )
    threads = [
        threading.Thread(target=c.get, args=(f"https://www.sec.gov/x/{i}",)) for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2


# --- failure paths ---------------------------------------------------------------------------


def test_403_is_not_retried() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403)

    c = _client(handler)
    with pytest.raises(SecForbiddenError):
        c.get(URL)
    assert calls == 1 and c.metrics.forbidden_403 == 1


def test_404_is_explicit() -> None:
    with pytest.raises(SecNotFoundError):
        _client(lambda r: httpx.Response(404)).get(URL)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_errors_are_bounded(status: int) -> None:
    calls = 0
    clock = FakeClock()

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    c = _client(handler, clock=clock, retry=RetryPolicy(max_retries=3, base_delay=1, max_delay=8))
    t0 = clock.now()
    with pytest.raises(SecUnavailableError):
        c.get(URL)
    assert calls == 4 and c.metrics.retries == 3
    assert clock.now() - t0 <= 1 + 2 + 4 + 4 * 0.2 + 1  # bounded backoff (+ rate spacing)


def test_429_honours_retry_after_with_cap() -> None:
    clock = FakeClock()
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, content=b"ok"),
    ]
    c = _client(lambda r: responses.pop(0), clock=clock, retry=RetryPolicy(max_retry_after=5))
    t0 = clock.now()
    assert c.get(URL).content == b"ok"
    assert 5 <= clock.now() - t0 < 6
    assert c.metrics.rate_limited_429 == 1


def test_timeouts_retry_then_fail() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=req)

    c = _client(handler, retry=RetryPolicy(max_retries=2))
    with pytest.raises(SecUnavailableError):
        c.get(URL)
    assert calls == 3 and c.metrics.transport_errors == 3


def test_recovers_after_transient_failure() -> None:
    responses = [httpx.Response(503), httpx.Response(200, content=b"good")]
    c = _client(lambda r: responses.pop(0))
    assert c.get(URL).content == b"good"


def test_unexpected_status_raises() -> None:
    from filinglens.infrastructure.sec.client import SecAccessError

    with pytest.raises(SecAccessError):
        _client(lambda r: httpx.Response(418)).get(URL)


# --- cache -----------------------------------------------------------------------------------


def test_cache_hit_within_ttl_and_conditional_revalidation() -> None:
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    calls: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"body", headers={"ETag": '"v1"'})

    c = _client(
        handler, cache=InMemoryHttpCache(), cache_ttl=timedelta(minutes=10), now=lambda: now[0]
    )
    first = c.get(URL)
    assert not first.from_cache and first.content == b"body"
    second = c.get(URL)
    assert second.from_cache and len(calls) == 1
    now[0] += timedelta(hours=1)
    third = c.get(URL)
    assert third.revalidated and third.content == b"body" and len(calls) == 2
    assert c.metrics.cache_hits == 1 and c.metrics.cache_revalidated == 1


def test_file_cache_round_trip(tmp_path: Path) -> None:
    c = _client(
        lambda r: httpx.Response(200, content=b"x", headers={"ETag": "e"}),
        cache=FileHttpCache(tmp_path),
    )
    c.get(URL)
    entry = FileHttpCache(tmp_path).get(URL)
    assert entry is not None and entry.content == b"x" and entry.etag == "e"


def test_metrics_snapshot() -> None:
    c = _client(lambda r: httpx.Response(200))
    c.get(URL)
    snap = c.metrics.snapshot()
    assert snap["requests"] == 1 and snap["network_requests"] == 1


# --- architecture guard ----------------------------------------------------------------------

HTTP_MODULES = {"httpx", "requests", "urllib3", "aiohttp", "http.client"}


def test_no_module_bypasses_the_governed_client() -> None:
    allowed = {SRC / "infrastructure" / "sec" / "client.py"}
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m in HTTP_MODULES or m.split(".")[0] in HTTP_MODULES or m == "urllib.request":
                    offenders.append(f"{path.relative_to(SRC)} imports {m}")
        text = path.read_text(encoding="utf-8")
        if "data.sec.gov" in text and "infrastructure" not in path.parts:
            offenders.append(f"{path.relative_to(SRC)} references data.sec.gov outside adapters")
    assert offenders == []
