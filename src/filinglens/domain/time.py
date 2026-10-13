"""UTC timestamp helpers. All persisted timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes and normalize aware ones to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed (must be timezone-aware): {value!r}")
    return value.astimezone(UTC)


def ensure_utc_opt(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)
