"""Idempotent ingestion of SEC filing metadata into canonical filing identities."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from filinglens.application.ports import FilingRepository, IssuerRepository
from filinglens.domain.availability import filter_as_of
from filinglens.domain.models import Filing, Issuer
from filinglens.domain.time import utcnow


@dataclass(frozen=True)
class IngestionResult:
    issuer_cik: int
    filings_seen: int
    filings_new: int
    source: str
    source_versions: tuple[str, ...]


class FilingMetadataService:
    def __init__(
        self,
        filings: FilingRepository,
        issuers: IssuerRepository,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.filings = filings
        self.issuers = issuers
        self.clock = clock

    def ingest(
        self,
        issuer: Issuer,
        filings: Iterable[Filing],
        *,
        source: str,
        source_versions: tuple[str, ...],
    ) -> IngestionResult:
        """Merge a batch idempotently; conflicting metadata raises ``ConflictError``."""
        existing = self.issuers.get(issuer.cik)
        if existing is None or (existing.name, existing.former_names) != (
            issuer.name,
            issuer.former_names,
        ):
            self.issuers.upsert(issuer)
        observed = self.clock()
        version = "+".join(source_versions)
        seen = new = 0
        for filing in filings:
            seen += 1
            if self.filings.get(filing.accession) is None:
                new += 1
            self.filings.add(filing)
            self.filings.record_observation(filing.accession, source, version, observed)
        return IngestionResult(issuer.cik, seen, new, source, source_versions)

    def filings_as_of(
        self, cik: int, as_of: datetime, *, forms: set[str] | None = None
    ) -> list[Filing]:
        """Point-in-time view: only filings publicly available by ``as_of``."""
        rows = self.filings.list_for_cik(cik)
        if forms is not None:
            wanted = {f.upper() for f in forms}
            rows = [f for f in rows if f.form in wanted]
        return sorted(filter_as_of(rows, as_of), key=lambda f: (f.available_at, f.accession))
