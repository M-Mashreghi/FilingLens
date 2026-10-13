"""Retrieve primary filing documents and policy-selected exhibits as immutable snapshots."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from filinglens.application.ports import RetrievalAttemptRepository, SnapshotRepository
from filinglens.domain.errors import FilingLensError
from filinglens.domain.models import FilingDocument, RetrievalAttempt, SourceSnapshot
from filinglens.domain.package import DocumentRole, PackageManifest
from filinglens.domain.time import utcnow

RETRIEVER_VERSION = "document-retriever/1"


class FetchedDocument(Protocol):
    @property
    def content(self) -> bytes: ...
    @property
    def content_type(self) -> str | None: ...
    @property
    def retrieved_at(self) -> datetime: ...


class DocumentFetcher(Protocol):
    def get(self, url: str) -> FetchedDocument: ...


class RawStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...


class RetrievalStatus(StrEnum):
    RETRIEVED = "retrieved"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class RetrievalOutcome:
    filename: str
    role: str
    status: RetrievalStatus
    reason: str
    snapshot: SourceSnapshot | None = None


@dataclass(frozen=True)
class ExhibitPolicy:
    """Which exhibits are analyst-relevant, per base form (prefix match on document type)."""

    include_prefixes: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "8-K": ("EX-99", "EX-10", "EX-2", "EX-3", "EX-4"),
            "10-K": ("EX-10", "EX-99", "EX-21"),
            "10-Q": ("EX-10", "EX-99"),
        }
    )
    max_exhibits: int = 20
    include_complete_submission_fallback: bool = True
    max_bytes: int = 50 * 1024 * 1024
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/xml",
        "application/xml",
    )
    version: str = "exhibit-policy/1"

    def selects(self, base_form: str, doc: FilingDocument) -> bool:
        if doc.role != DocumentRole.EXHIBIT.value or not doc.doc_type:
            return False
        prefixes = self.include_prefixes.get(base_form.upper(), ())
        dtype = doc.doc_type.upper()
        return any(dtype == p or dtype.startswith(p + ".") for p in prefixes)


class RetrievalError(FilingLensError):
    pass


@dataclass(frozen=True)
class PackageRetrieval:
    accession: str
    outcomes: tuple[RetrievalOutcome, ...]
    primary_snapshot: SourceSnapshot | None
    used_complete_submission_fallback: bool
    policy_version: str

    def snapshots(self) -> list[SourceSnapshot]:
        return [o.snapshot for o in self.outcomes if o.snapshot is not None]


class DocumentRetriever:
    def __init__(
        self,
        fetcher: DocumentFetcher,
        raw_store: RawStore,
        snapshots: SnapshotRepository,
        policy: ExhibitPolicy | None = None,
        *,
        adapter: str = "sec-archives",
        attempts: RetrievalAttemptRepository | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.fetcher = fetcher
        self.raw_store = raw_store
        self.snapshots = snapshots
        self.policy = policy or ExhibitPolicy()
        self.adapter = adapter
        self.attempts = attempts
        self.clock = clock
        self._from_cache = False

    def retrieve_package(self, manifest: PackageManifest) -> PackageRetrieval:
        base_form = (manifest.form or "").removesuffix("/A")
        outcomes: list[RetrievalOutcome] = []
        primary = manifest.primary
        primary_snapshot: SourceSnapshot | None = None
        if primary is None:
            n = len(manifest.by_role(DocumentRole.PRIMARY))
            outcomes.append(
                RetrievalOutcome(
                    filename="",
                    role="primary",
                    status=RetrievalStatus.FAILED,
                    reason=f"primary document not uniquely identified ({n} candidates)",
                )
            )
        else:
            out = self.retrieve(primary)
            outcomes.append(out)
            primary_snapshot = out.snapshot
        used_fallback = False
        if primary_snapshot is None and self.policy.include_complete_submission_fallback:
            full = manifest.by_role(DocumentRole.COMPLETE_SUBMISSION)
            if full:
                out = self.retrieve(full[0])
                outcomes.append(out)
                used_fallback = out.snapshot is not None
        selected = [d for d in manifest.documents if self.policy.selects(base_form, d)]
        for i, doc in enumerate(selected):
            if i >= self.policy.max_exhibits:
                outcomes.append(
                    RetrievalOutcome(
                        doc.filename, doc.role, RetrievalStatus.SKIPPED, "max_exhibits reached"
                    )
                )
                continue
            outcomes.append(self.retrieve(doc))
        return PackageRetrieval(
            accession=manifest.accession,
            outcomes=tuple(outcomes),
            primary_snapshot=primary_snapshot,
            used_complete_submission_fallback=used_fallback,
            policy_version=self.policy.version,
        )

    def retrieve(self, doc: FilingDocument) -> RetrievalOutcome:
        """Fetch one document. A snapshot is recorded only after bytes are stored and verified.

        Every attempt (success, failure or skip) is logged separately from snapshot identity.
        """
        self._from_cache = False
        outcome = self._retrieve(doc)
        if self.attempts is not None:
            self.attempts.add(
                RetrievalAttempt(
                    attempt_id=uuid.uuid4().hex,
                    url=doc.url or "",
                    accession=doc.accession,
                    filename=doc.filename,
                    attempted_at=self.clock(),
                    status=outcome.status.value,
                    snapshot_id=outcome.snapshot.snapshot_id if outcome.snapshot else None,
                    reason=outcome.reason,
                    from_cache=self._from_cache,
                )
            )
        return outcome

    def _retrieve(self, doc: FilingDocument) -> RetrievalOutcome:
        if not doc.url:
            return self._fail(doc, "document has no URL")
        if doc.size_bytes is not None and doc.size_bytes > self.policy.max_bytes:
            return RetrievalOutcome(
                doc.filename,
                doc.role,
                RetrievalStatus.SKIPPED,
                f"declared size {doc.size_bytes} exceeds max_bytes",
            )
        try:
            resp = self.fetcher.get(doc.url)
        except FilingLensError as exc:
            return self._fail(doc, f"fetch failed: {exc}")
        self._from_cache = bool(getattr(resp, "from_cache", False))
        data = resp.content
        if len(data) > self.policy.max_bytes:
            return self._fail(doc, f"payload {len(data)} bytes exceeds max_bytes")
        if not data:
            return self._fail(doc, "empty payload")
        ctype = (resp.content_type or "").split(";")[0].strip().lower() or None
        if ctype is not None and ctype not in self.policy.allowed_content_types:
            return self._fail(doc, f"content type {ctype!r} not allowed")
        snap = SourceSnapshot.from_bytes(
            data,
            source_url=doc.url,
            retrieved_at=resp.retrieved_at,
            content_type=ctype,
            adapter=self.adapter,
            adapter_version=RETRIEVER_VERSION,
            accession=doc.accession,
            filename=doc.filename,
        )
        key = self.raw_store.put(data)
        if key != snap.storage_key or not snap.verify(self.raw_store.get(key)):
            return self._fail(doc, "raw store verification failed")
        stored = self.snapshots.add(snap)
        return RetrievalOutcome(doc.filename, doc.role, RetrievalStatus.RETRIEVED, "ok", stored)

    @staticmethod
    def _fail(doc: FilingDocument, reason: str) -> RetrievalOutcome:
        return RetrievalOutcome(doc.filename, doc.role, RetrievalStatus.FAILED, reason)
