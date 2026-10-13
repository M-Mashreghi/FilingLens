"""Core domain entities.

Entities are immutable dataclasses. Identity fields are explicit; timestamps are UTC-aware.
Nullable fields are typed ``X | None`` and default to ``None`` only when genuinely optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from filinglens.domain.time import ensure_utc, ensure_utc_opt, utcnow


def _require(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _check_cik(cik: int) -> int:
    if not 0 < cik < 10**10:
        raise ValueError(f"CIK out of range: {cik}")
    return cik


@dataclass(frozen=True, kw_only=True)
class Issuer:
    """Canonical SEC filer. CIK is the durable identity; names/tickers are aliases."""

    cik: int
    name: str
    former_names: tuple[str, ...] = ()
    sic: str | None = None
    fiscal_year_end: str | None = None  # MMDD as published by SEC
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        _require(self.name, "name")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, kw_only=True)
class SecurityIdentifier:
    """A ticker/exchange alias observed for an issuer at a point in time. Never a durable id."""

    cik: int
    ticker: str
    exchange: str | None
    source: str
    source_version: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        object.__setattr__(self, "ticker", _require(self.ticker, "ticker").upper())
        object.__setattr__(self, "retrieved_at", ensure_utc(self.retrieved_at))


@dataclass(frozen=True, kw_only=True)
class Filing:
    """A submission package identified by accession number (dashed form)."""

    accession: str
    cik: int
    form: str
    filing_date: date
    report_period: date | None = None
    accepted_at: datetime | None = None
    primary_document: str | None = None
    is_amendment: bool = False
    amends_accession: str | None = None
    source: str = "unknown"

    def __post_init__(self) -> None:
        _require(self.accession, "accession")
        _check_cik(self.cik)
        _require(self.form, "form")
        object.__setattr__(self, "accepted_at", ensure_utc_opt(self.accepted_at))
        if self.form.upper().endswith("/A") and not self.is_amendment:
            object.__setattr__(self, "is_amendment", True)


@dataclass(frozen=True, kw_only=True)
class FilingDocument:
    """A document inside an accession package; identity is (accession, filename)."""

    accession: str
    filename: str
    sequence: int | None = None
    doc_type: str | None = None
    description: str | None = None
    size_bytes: int | None = None
    url: str | None = None
    role: str = "unknown"  # primary | exhibit | xbrl | complete_submission | supporting | unknown

    def __post_init__(self) -> None:
        _require(self.accession, "accession")
        _require(self.filename, "filename")


@dataclass(frozen=True, kw_only=True)
class SourceSnapshot:
    """Immutable raw bytes captured from a source. ``snapshot_id`` is the sha256 of the bytes."""

    snapshot_id: str
    source_url: str
    retrieved_at: datetime
    content_type: str | None
    size_bytes: int
    adapter: str
    adapter_version: str
    storage_key: str
    accession: str | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        if len(self.snapshot_id) != 64:
            raise ValueError("snapshot_id must be a sha256 hex digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")
        object.__setattr__(self, "retrieved_at", ensure_utc(self.retrieved_at))


class RunState(StrEnum):
    CREATED = "Created"
    RESOLVING = "Resolving"
    COLLECTING = "Collecting"
    PARSING = "Parsing"
    ALIGNING = "Aligning"
    ANALYZING = "Analyzing"
    REVIEWING = "Reviewing"
    COMPLETED = "Completed"
    NEEDS_INPUT = "NeedsInput"
    BLOCKED_BY_SOURCE = "BlockedBySource"
    FAILED = "Failed"


@dataclass(frozen=True, kw_only=True)
class ComparisonRun:
    run_id: str
    cik: int
    focal_accession: str | None
    comparator_accession: str | None
    as_of: datetime
    state: RunState = RunState.CREATED
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    supersedes_run_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _require(self.run_id, "run_id")
        _check_cik(self.cik)
        for name in ("as_of", "created_at", "updated_at"):
            object.__setattr__(self, name, ensure_utc(getattr(self, name)))


@dataclass(frozen=True, kw_only=True)
class EvidenceSpan:
    """Exact source location supporting a finding."""

    evidence_id: str
    run_id: str
    snapshot_id: str
    accession: str
    locator: str
    text: str
    filename: str | None = None
    section: str | None = None

    def __post_init__(self) -> None:
        _require(self.evidence_id, "evidence_id")
        _require(self.locator, "locator")


@dataclass(frozen=True, kw_only=True)
class Finding:
    finding_id: str
    run_id: str
    finding_type: str
    summary: str
    severity: str
    confidence: float
    evidence_ids: tuple[str, ...]
    interpretation_status: str
    direction: str | None = None
    why_it_matters: str | None = None
    open_questions: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, kw_only=True)
class Alert:
    alert_id: str
    kind: str
    priority: str
    reason: str
    dedupe_key: str
    run_id: str | None = None
    finding_id: str | None = None
    acknowledged: bool = False
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        _require(self.dedupe_key, "dedupe_key")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, kw_only=True)
class Report:
    report_id: str
    run_id: str
    version: int
    body_markdown: str
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("report version starts at 1")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
