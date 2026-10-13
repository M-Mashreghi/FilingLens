"""Persist and reload accession-package manifests."""

from __future__ import annotations

from filinglens.application.ports import FilingDocumentRepository, FilingRepository
from filinglens.domain.errors import NotFoundError
from filinglens.domain.package import PackageManifest


class PackageService:
    def __init__(self, filings: FilingRepository, documents: FilingDocumentRepository) -> None:
        self.filings = filings
        self.documents = documents

    def register(self, manifest: PackageManifest) -> PackageManifest:
        """Store every document of the package, linked to its (already known) filing."""
        filing = self.filings.get(manifest.accession)
        if filing is None:
            raise NotFoundError(f"filing {manifest.accession} must be ingested before its package")
        for doc in manifest.documents:
            self.documents.add(doc)
        return self.load(manifest.accession, source_versions=manifest.source_versions)

    def load(self, accession: str, *, source_versions: tuple[str, ...] = ()) -> PackageManifest:
        filing = self.filings.get(accession)
        if filing is None:
            raise NotFoundError(accession)
        return PackageManifest(
            accession=filing.accession,
            cik=filing.cik,
            form=filing.form,
            documents=tuple(self.documents.list_for_accession(filing.accession)),
            source_versions=source_versions,
        )
