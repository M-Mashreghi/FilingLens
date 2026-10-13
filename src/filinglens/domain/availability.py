"""Public-availability policy for point-in-time research.

Policy (``AVAILABILITY_POLICY_VERSION``):

1. If an accepted/disseminated timestamp is known, ``available_at`` = that timestamp (UTC).
2. Otherwise only the filing *date* is known. EDGAR assigns a filing date that can be on or
   after acceptance, so the conservative assumption is end of the filing date in US Eastern
   time (23:59:59 America/New_York). This can only delay eligibility, never leak it early.

A source is eligible for a run iff ``available_at <= as_of``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Protocol, TypeVar
from zoneinfo import ZoneInfo

from filinglens.domain.time import ensure_utc

AVAILABILITY_POLICY_VERSION = "availability/1"
SEC_TZ = ZoneInfo("America/New_York")


class AvailabilityBasis(StrEnum):
    ACCEPTED = "accepted_timestamp"
    FILING_DATE_CONSERVATIVE = "filing_date_end_of_day_et"


def compute_available_at(
    accepted_at: datetime | None, filing_date: date
) -> tuple[datetime, AvailabilityBasis]:
    if accepted_at is not None:
        return ensure_utc(accepted_at), AvailabilityBasis.ACCEPTED
    eod = datetime.combine(filing_date, time(23, 59, 59), tzinfo=SEC_TZ)
    return eod.astimezone(UTC), AvailabilityBasis.FILING_DATE_CONSERVATIVE


class HasAvailability(Protocol):
    @property
    def available_at(self) -> datetime: ...


A = TypeVar("A", bound=HasAvailability)


def is_available(item: HasAvailability, as_of: datetime) -> bool:
    return item.available_at <= ensure_utc(as_of)


def filter_as_of(items: Iterable[A], as_of: datetime) -> list[A]:
    """Keep only items publicly available by ``as_of``."""
    cutoff = ensure_utc(as_of)
    return [i for i in items if i.available_at <= cutoff]
