"""Structured logging bootstrap with secret scrubbing."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\s*[=:]\s*)(\S+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
]
_SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "authorization", "llm_api_key"}
_STD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def scrub(text: str) -> str:
    """Redact values that look like credentials."""
    text = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}***", text)
    text = _SECRET_PATTERNS[0].sub(
        lambda m: m.group(0) if m.group(3).startswith("Bearer") else f"{m.group(1)}{m.group(2)}***",
        text,
    )
    return _SECRET_PATTERNS[2].sub("***", text)


def _scrub_value(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "***"
    if isinstance(value, str):
        return scrub(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = _scrub_value(key, value)
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class ScrubbingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ScrubbingTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    from filinglens.observability import ContextFilter

    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
