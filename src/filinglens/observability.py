"""Observability: correlation context, stage telemetry and error categories (``telemetry/1``).

``log_context`` binds correlation/run/accession ids to every log record emitted inside it
(via contextvars), so a run is traceable end-to-end in structured logs. ``Telemetry`` keeps
in-process counters and stage timings that ``/health/detailed`` exposes. Values are scrubbed
by the logging formatters; no secrets are stored here.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

TELEMETRY_VERSION = "telemetry/1"
_CTX: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("filinglens_log_ctx")

ERROR_CATEGORIES = ("source", "parser", "alignment", "model", "validation", "internal")


@contextmanager
def log_context(**fields: str | None) -> Iterator[dict[str, str]]:
    current = dict(_CTX.get({}))
    current.setdefault("correlation_id", uuid.uuid4().hex[:16])
    current.update({k: v for k, v in fields.items() if v is not None})
    token = _CTX.set(current)
    try:
        yield current
    finally:
        _CTX.reset(token)


def current_context() -> dict[str, str]:
    return dict(_CTX.get({}))


class ContextFilter(logging.Filter):
    """Injects the active correlation context into each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for k, v in _CTX.get({}).items():
            if not hasattr(record, k):
                setattr(record, k, v)
        return True


def install_context_filter(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger()
    for h in target.handlers:
        if not any(isinstance(f, ContextFilter) for f in h.filters):
            h.addFilter(ContextFilter())
    if not any(isinstance(f, ContextFilter) for f in target.filters):
        target.addFilter(ContextFilter())


@dataclass
class StageStats:
    count: int = 0
    errors: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0


@dataclass
class Telemetry:
    stages: dict[str, StageStats] = field(default_factory=dict)
    errors_by_category: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @contextmanager
    def stage(self, name: str, *, error_category: str = "internal") -> Iterator[None]:
        log = logging.getLogger("filinglens.stage")
        start = time.perf_counter()
        log.info("stage start", extra={"stage": name})
        failed = False
        try:
            yield
        except Exception:
            failed = True
            self.error(error_category)
            log.exception("stage failed", extra={"stage": name, "error_category": error_category})
            raise
        finally:
            elapsed = time.perf_counter() - start
            with self._lock:
                st = self.stages.setdefault(name, StageStats())
                st.count += 1
                st.errors += int(failed)
                st.total_seconds += elapsed
                st.max_seconds = max(st.max_seconds, elapsed)
            if not failed:
                log.info("stage end", extra={"stage": name, "elapsed_ms": round(elapsed * 1000, 2)})

    def error(self, category: str) -> None:
        cat = category if category in ERROR_CATEGORIES else "internal"
        with self._lock:
            self.errors_by_category[cat] = self.errors_by_category.get(cat, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": TELEMETRY_VERSION,
                "stages": {k: vars(v).copy() for k, v in sorted(self.stages.items())},
                "errors_by_category": dict(sorted(self.errors_by_category.items())),
            }
