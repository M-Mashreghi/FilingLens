"""Typed API request/response contracts (``api/1``)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Problem(BaseModel):
    """RFC 9457-style problem details."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class IssuerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cik: int
    name: str
    former_names: list[str]
    fiscal_year_end: str | None = None


class CandidateOut(BaseModel):
    cik: int
    name: str
    tickers: list[str]
    score: float
    match_type: str


class ResolutionOut(BaseModel):
    query: str
    status: str
    cik: int | None
    candidates: list[CandidateOut]
    source_version: str
    reason: str


class FilingOut(BaseModel):
    accession: str
    cik: int
    form: str
    filing_date: date
    report_period: date | None
    accepted_at: datetime | None
    available_at: datetime
    availability_basis: str
    primary_document: str | None
    is_amendment: bool
    items: list[str] | None = None


class DocumentOut(BaseModel):
    accession: str
    filename: str
    sequence: int | None
    doc_type: str | None
    description: str | None
    size_bytes: int | None
    role: str
    url: str | None


class RunCreate(BaseModel):
    cik: int = Field(gt=0)
    as_of: datetime
    focal_accession: str | None = None
    comparator_accession: str | None = None


class RunTransition(BaseModel):
    target: str
    error: str | None = None


class RunOut(BaseModel):
    run_id: str
    cik: int
    state: str
    as_of: datetime
    focal_accession: str | None
    comparator_accession: str | None
    supersedes_run_id: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class FindingOut(BaseModel):
    finding_id: str
    run_id: str
    finding_type: str
    summary: str
    severity: str
    confidence: float
    evidence_ids: list[str]
    interpretation_status: str
    direction: str | None
    why_it_matters: str | None
    open_questions: list[str]


class EvidenceOut(BaseModel):
    evidence_id: str
    run_id: str
    snapshot_id: str
    accession: str
    locator: str
    text: str
    filename: str | None
    section: str | None


class ReportOut(BaseModel):
    report_id: str
    run_id: str
    version: int
    body_markdown: str
    created_at: datetime
