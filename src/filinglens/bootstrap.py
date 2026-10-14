"""Composition root: wires repositories and application services for API/UI processes."""

from __future__ import annotations

from dataclasses import dataclass, field

from filinglens.application.issuer_resolver import IssuerResolver, ReferenceDataset
from filinglens.application.metadata import FilingMetadataService
from filinglens.application.runs import RunService
from filinglens.config import Settings
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.reporting.report import ReportService


@dataclass
class Container:
    repos: SqliteRepositories
    reference: ReferenceDataset | None = None
    runs: RunService = field(init=False)
    metadata: FilingMetadataService = field(init=False)
    reports: ReportService = field(init=False)

    def __post_init__(self) -> None:
        self.runs = RunService(self.repos.runs, self.repos.snapshots, self.repos.evidence)
        self.metadata = FilingMetadataService(self.repos.filings, self.repos.issuers)
        self.reports = ReportService(self.repos.reports, self.repos.report_inputs)

    @property
    def resolver(self) -> IssuerResolver | None:
        if self.reference is None:
            return None
        aliases = {n: i.cik for i in self.repos.issuers.list() for n in i.former_names}
        return IssuerResolver(self.reference, aliases=aliases)

    @classmethod
    def from_settings(cls, settings: Settings) -> Container:
        return cls(SqliteRepositories(Database(settings.db_path)))

    @classmethod
    def in_memory(cls) -> Container:
        return cls(SqliteRepositories(Database(":memory:")))
