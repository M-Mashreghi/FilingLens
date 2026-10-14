"""The single governed HTTP client for all SEC traffic.

Every SEC adapter must obtain bytes through :class:`SecHttpClient`. The client enforces:

- a descriptive User-Agent with contact information in live mode;
- a token-bucket rate governor (default below the SEC published ceiling);
- bounded concurrency;
- timeouts, bounded retry/backoff for 429/5xx/transport errors, explicit 403/404 handling;
- an HTTP cache with conditional revalidation (network optimisation only, never provenance);
- request/cache/rate-limit metrics.

There is deliberately no anti-bot evasion logic (no UA rotation, proxies or header spoofing).
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import random
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx

from filinglens.config import SEC_PUBLISHED_MAX_RPS, Settings
from filinglens.domain.errors import FilingLensError
from filinglens.domain.time import utcnow

CLIENT_VERSION = "sec-http/1"
ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})
_CONTACT_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}|https?://\S+")


class SecAccessError(FilingLensError):
    """Base for SEC access failures."""


class SecConfigurationError(SecAccessError):
    pass


class SecForbiddenError(SecAccessError):
    """HTTP 403: typically missing/undeclared User-Agent or fair-access block. Never retried."""


class SecNotFoundError(SecAccessError):
    pass


class SecUnavailableError(SecAccessError):
    """Retries exhausted on 429/5xx/timeout/transport errors."""


def validate_user_agent(user_agent: str) -> str:
    ua = user_agent.strip()
    if len(ua) < 8 or not _CONTACT_RE.search(ua):
        raise SecConfigurationError(
            "SEC_USER_AGENT must be descriptive and include contact info, e.g. "
            "'FilingLens research you@example.com'"
        )
    return ua


class RateGovernor:
    """Thread-safe rate governor (GCRA / virtual-scheduling token bucket).

    Permits are spaced at ``1/rate`` seconds; ``burst`` allows that many back-to-back permits
    after idle time. With ``burst=1`` no 1-second window contains more than ``ceil(rate)``
    permits.
    """

    _EPS = 1e-9  # float tolerance only; never meaningfully early

    def __init__(
        self,
        rate_per_sec: float,
        *,
        burst: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate must be positive")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate = rate_per_sec
        self.burst = burst
        self.interval = 1.0 / rate_per_sec
        self._clock = clock
        self._sleep = sleep
        self._tat = clock()  # theoretical arrival time of the next conforming permit
        self._lock = threading.Lock()
        self.total_wait = 0.0

    def acquire(self) -> float:
        """Block until a request may be sent; returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                allowed_at = self._tat - (self.burst - 1) * self.interval
                if now + self._EPS >= allowed_at:
                    self._tat = max(self._tat, now) + self.interval
                    self.total_wait += waited
                    return waited
                delay = allowed_at - now
            self._sleep(delay)
            waited += delay


@dataclass
class CachedEntry:
    url: str
    content: bytes
    headers: dict[str, str]
    stored_at: datetime
    status: int = 200

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")


class HttpCache(Protocol):
    def get(self, url: str) -> CachedEntry | None: ...
    def put(self, entry: CachedEntry) -> None: ...


class InMemoryHttpCache:
    def __init__(self) -> None:
        self._data: dict[str, CachedEntry] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> CachedEntry | None:
        with self._lock:
            return self._data.get(url)

    def put(self, entry: CachedEntry) -> None:
        with self._lock:
            self._data[entry.url] = entry


class FileHttpCache:
    """Rebuildable on-disk cache under ``FILINGLENS_CACHE_DIR`` (not provenance storage)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "http"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        return self.root / f"{key}.body", self.root / f"{key}.meta.json"

    def get(self, url: str) -> CachedEntry | None:
        body, meta = self._paths(url)
        with self._lock:
            if not body.exists() or not meta.exists():
                return None
            m = json.loads(meta.read_text(encoding="utf-8"))
            return CachedEntry(
                url=url,
                content=body.read_bytes(),
                headers=m["headers"],
                stored_at=datetime.fromisoformat(m["stored_at"]),
                status=m.get("status", 200),
            )

    def put(self, entry: CachedEntry) -> None:
        body, meta = self._paths(entry.url)
        with self._lock:
            tmp = body.with_suffix(".tmp")
            tmp.write_bytes(entry.content)
            tmp.replace(body)
            meta.write_text(
                json.dumps(
                    {
                        "url": entry.url,
                        "headers": entry.headers,
                        "stored_at": entry.stored_at.isoformat(),
                        "status": entry.status,
                    }
                ),
                encoding="utf-8",
            )


@dataclass(frozen=True)
class SecResponse:
    url: str
    status: int
    content: bytes
    headers: Mapping[str, str]
    retrieved_at: datetime
    from_cache: bool
    revalidated: bool = False

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")


@dataclass
class SecMetrics:
    requests: int = 0
    network_requests: int = 0
    cache_hits: int = 0
    cache_revalidated: int = 0
    retries: int = 0
    rate_limited_429: int = 0
    forbidden_403: int = 0
    server_errors: int = 0
    transport_errors: int = 0
    throttle_wait_seconds: float = 0.0
    by_status: dict[int, int] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    max_retry_after: float = 60.0

    def backoff(self, attempt: int, rng: random.Random) -> float:
        delay = min(self.max_delay, self.base_delay * (2**attempt))
        return float(delay * (0.5 + rng.random() / 2))


class SecHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        live: bool,
        max_rps: float = 5.0,
        max_concurrency: int = 2,
        timeout: float = 30.0,
        cache: HttpCache | None = None,
        cache_ttl: timedelta = timedelta(hours=1),
        retry: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        governor: RateGovernor | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = utcnow,
        rng: random.Random | None = None,
    ) -> None:
        if max_rps > SEC_PUBLISHED_MAX_RPS:
            raise SecConfigurationError(
                f"max_rps {max_rps} exceeds SEC published ceiling {SEC_PUBLISHED_MAX_RPS}"
            )
        if live:
            self.user_agent = validate_user_agent(user_agent)
        else:
            if transport is None:
                raise SecConfigurationError(
                    "live SEC access is disabled (SEC_LIVE_MODE=false); "
                    "inject a fixture transport or enable live mode"
                )
            self.user_agent = user_agent.strip() or "FilingLens offline-fixtures test@example.com"
        self.live = live
        self.governor = governor or RateGovernor(max_rps, sleep=sleep)
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        self.cache = cache
        self.cache_ttl = cache_ttl
        self.retry = retry or RetryPolicy()
        self._sleep = sleep
        self._now = now
        self._rng = rng or random.Random()
        self.metrics = SecMetrics()
        self._metrics_lock = threading.Lock()
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=False,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> SecHttpClient:
        return cls(
            user_agent=settings.sec_user_agent,
            live=settings.sec_live_mode,
            max_rps=settings.sec_max_rps,
            max_concurrency=settings.sec_max_concurrency,
            cache=FileHttpCache(settings.cache_dir),
            transport=transport,
        )

    @classmethod
    def offline(
        cls,
        responder: Callable[[str], tuple[int, bytes, Mapping[str, str]]],
        **kwargs: object,
    ) -> SecHttpClient:
        """Client backed by an in-process responder (fixtures, drills, qualification).

        ``responder(url) -> (status, body, headers)``; no network is ever used.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            status, body, headers = responder(str(req.url))
            return httpx.Response(status, content=body, headers=dict(headers))

        params: dict[str, object] = {
            "user_agent": "FilingLens offline-fixtures test@example.com",
            "live": False,
        }
        params.update(kwargs)
        return cls(transport=httpx.MockTransport(handler), **params)  # type: ignore[arg-type]

    def close(self) -> None:
        self._http.close()

    def _count(self, **inc: float) -> None:
        with self._metrics_lock:
            for name, value in inc.items():
                setattr(self.metrics, name, getattr(self.metrics, name) + value)

    def get_bytes(self, url: str) -> bytes:
        return self.get(url).content

    def get(self, url: str, *, use_cache: bool = True) -> SecResponse:
        host = urlparse(url).netloc
        if host not in ALLOWED_HOSTS:
            raise SecConfigurationError(f"SEC client only fetches SEC hosts, got {host!r}")
        self._count(requests=1)
        cached = self.cache.get(url) if (self.cache and use_cache) else None
        if cached is not None and self._now() - cached.stored_at < self.cache_ttl:
            self._count(cache_hits=1)
            return SecResponse(
                url=url,
                status=cached.status,
                content=cached.content,
                headers=cached.headers,
                retrieved_at=cached.stored_at,
                from_cache=True,
            )
        headers: dict[str, str] = {}
        if cached is not None:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        resp = self._send_with_retries(url, headers)
        retrieved_at = self._now()
        if resp.status_code == 304 and cached is not None:
            self._count(cache_revalidated=1)
            refreshed = CachedEntry(
                url=url,
                content=cached.content,
                headers=cached.headers,
                stored_at=retrieved_at,
                status=cached.status,
            )
            if self.cache:
                self.cache.put(refreshed)
            return SecResponse(
                url=url,
                status=cached.status,
                content=cached.content,
                headers=cached.headers,
                retrieved_at=retrieved_at,
                from_cache=True,
                revalidated=True,
            )
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        if self.cache is not None:
            self.cache.put(
                CachedEntry(url=url, content=resp.content, headers=hdrs, stored_at=retrieved_at)
            )
        return SecResponse(
            url=url,
            status=resp.status_code,
            content=resp.content,
            headers=hdrs,
            retrieved_at=retrieved_at,
            from_cache=False,
        )

    def _send_with_retries(self, url: str, headers: dict[str, str]) -> httpx.Response:
        attempt = 0
        while True:
            delay: float | None = None
            failure: str
            try:
                resp = self._send_once(url, headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self._count(transport_errors=1)
                failure = f"{type(exc).__name__}: {exc}"
            else:
                status = resp.status_code
                with self._metrics_lock:
                    self.metrics.by_status[status] = self.metrics.by_status.get(status, 0) + 1
                if status < 300 or status == 304:
                    return resp
                if status == 403:
                    self._count(forbidden_403=1)
                    raise SecForbiddenError(
                        f"403 from SEC for {url}; check SEC_USER_AGENT and request rate"
                    )
                if status == 404:
                    raise SecNotFoundError(url)
                if status == 429:
                    self._count(rate_limited_429=1)
                    delay = self._retry_after(resp)
                elif status >= 500:
                    self._count(server_errors=1)
                else:
                    raise SecAccessError(f"unexpected HTTP {status} for {url}")
                failure = f"HTTP {status}"
            if attempt >= self.retry.max_retries:
                raise SecUnavailableError(
                    f"{url}: giving up after {attempt + 1} attempts ({failure})"
                )
            wait = delay if delay is not None else self.retry.backoff(attempt, self._rng)
            self._count(retries=1)
            self._sleep(wait)
            attempt += 1

    def _send_once(self, url: str, headers: dict[str, str]) -> httpx.Response:
        with self._semaphore:
            waited = self.governor.acquire()
            self._count(network_requests=1, throttle_wait_seconds=waited)
            return self._http.get(url, headers=headers)

    def _retry_after(self, resp: httpx.Response) -> float | None:
        raw = resp.headers.get("retry-after")
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed is None:
                return None
            seconds = (parsed - datetime.now(UTC)).total_seconds()
        return max(0.0, min(seconds, self.retry.max_retry_after))
