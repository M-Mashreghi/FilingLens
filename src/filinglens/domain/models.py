"""Core domain entities.

Entities are immutable dataclasses. Identity fields are explicit; timestamps are UTC-aware.
Nullable fields are typed ``X | None`` and default to ``None`` only when genuinely optional.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from filinglens.domain.accession import normalize_accession
from filinglens.domain.availability import AvailabilityBasis, compute_available_at
from filinglens.domain.time import ensure_utc, ensure_utc_opt, utcnow


def _require(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _norm_acc_opt(value: str | None) -> str | None:
    return None if value is None else normalize_accession(value)


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
    items: tuple[str, ...] | None = None  # 8-K item numbers as published, e.g. ("2.02", "9.01")
    primary_doc_description: str | None = None
    is_xbrl: bool | None = None
    is_inline_xbrl: bool | None = None
    # Derived by the availability policy; never supplied by callers.
    available_at: datetime = field(init=False)
    availability_basis: AvailabilityBasis = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "accession", normalize_accession(self.accession))
        object.__setattr__(self, "amends_accession", _norm_acc_opt(self.amends_accession))
        _check_cik(self.cik)
        object.__setattr__(self, "form", _require(self.form, "form").strip().upper())
        object.__setattr__(self, "accepted_at", ensure_utc_opt(self.accepted_at))
        if self.form.endswith("/A") and not self.is_amendment:
            object.__setattr__(self, "is_amendment", True)
        if self.amends_accession is not None:
            if self.amends_accession == self.accession:
                raise ValueError("a filing cannot amend itself")
            if not self.is_amendment:
                raise ValueError("amends_accession set on a non-amendment filing")
        available_at, basis = compute_available_at(self.accepted_at, self.filing_date)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "availability_basis", basis)

    @property
    def base_form(self) -> str:
        return self.form.removesuffix("/A")


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
        object.__setattr__(self, "accession", normalize_accession(self.accession))
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
        if len(self.snapshot_id) != 64 or any(
            c not in "0123456789abcdef" for c in self.snapshot_id
        ):
            raise ValueError("snapshot_id must be a lowercase sha256 hex digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")
        object.__setattr__(self, "accession", _norm_acc_opt(self.accession))
        object.__setattr__(self, "retrieved_at", ensure_utc(self.retrieved_at))

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        source_url: str,
        retrieved_at: datetime,
        content_type: str | None,
        adapter: str,
        adapter_version: str,
        accession: str | None = None,
        filename: str | None = None,
    ) -> SourceSnapshot:
        digest = hashlib.sha256(data).hexdigest()
        return cls(
            snapshot_id=digest,
            source_url=source_url,
            retrieved_at=retrieved_at,
            content_type=content_type,
            size_bytes=len(data),
            adapter=adapter,
            adapter_version=adapter_version,
            storage_key=f"sha256/{digest[:2]}/{digest}",
            accession=accession,
            filename=filename,
        )

    def verify(self, data: bytes) -> bool:
        """True iff ``data`` are exactly the bytes this snapshot identifies."""
        return len(data) == self.size_bytes and hashlib.sha256(data).hexdigest() == self.snapshot_id


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
        object.__setattr__(self, "focal_accession", _norm_acc_opt(self.focal_accession))
        object.__setattr__(self, "comparator_accession", _norm_acc_opt(self.comparator_accession))
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
        object.__setattr__(self, "accession", normalize_accession(self.accession))


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


@dataclass(frozen=True, kw_only=True)
class RetrievalAttempt:
    """One network/cache retrieval event. Separate from immutable snapshot identity."""

    attempt_id: str
    url: str
    accession: str | None
    filename: str | None
    attempted_at: datetime
    status: str  # retrieved | failed | skipped
    snapshot_id: str | None = None
    reason: str | None = None
    from_cache: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "accession", _norm_acc_opt(self.accession))
        object.__setattr__(self, "attempted_at", ensure_utc(self.attempted_at))
        if self.status == "retrieved" and self.snapshot_id is None:
            raise ValueError("a successful retrieval must reference a snapshot")
        if self.status != "retrieved" and self.snapshot_id is not None:
            raise ValueError("only successful retrievals reference a snapshot")


@dataclass(frozen=True, kw_only=True)
class Watchlist:
    """Issuer watch with form filter. Only filings public after ``since`` are new."""

    watchlist_id: str
    cik: int
    forms: tuple[str, ...]
    since: datetime
    active: bool = True

    def __post_init__(self) -> None:
        _check_cik(self.cik)
        object.__setattr__(self, "forms", tuple(sorted({f.upper() for f in self.forms})))
        object.__setattr__(self, "since", ensure_utc(self.since))

    def wants(self, filing: Filing) -> bool:
        return filing.available_at > self.since and (not self.forms or filing.form in self.forms)
