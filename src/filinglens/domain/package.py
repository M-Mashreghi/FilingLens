"""Accession-package manifest and document-role classification (pure domain logic)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from filinglens.domain.accession import normalize_accession
from filinglens.domain.models import FilingDocument

CLASSIFIER_VERSION = "package-classifier/1"


class DocumentRole(StrEnum):
    PRIMARY = "primary"
    EXHIBIT = "exhibit"
    XBRL = "xbrl"
    COMPLETE_SUBMISSION = "complete_submission"
    INDEX = "index"
    SUPPORTING = "supporting"
    UNKNOWN = "unknown"


_XBRL_SUFFIXES = (
    ".xsd",
    "_cal.xml",
    "_def.xml",
    "_lab.xml",
    "_pre.xml",
    "_htm.xml",
)
_XBRL_NAMES = {"filingsummary.xml", "metalinks.json"}
_GRAPHIC_SUFFIXES = (".jpg", ".jpeg", ".gif", ".png", ".svg")
_RENDERING = re.compile(r"^r\d+\.(htm|xml)$", re.IGNORECASE)


def classify_document(
    *,
    accession: str,
    filename: str,
    doc_type: str | None,
    sequence: int | None,
    form: str | None,
    primary_document: str | None,
) -> DocumentRole:
    acc = normalize_accession(accession)
    name = filename.lower()
    dtype = (doc_type or "").strip().upper()
    if name == f"{acc}.txt":
        return DocumentRole.COMPLETE_SUBMISSION
    if name in (f"{acc}-index.htm", f"{acc}-index.html", f"{acc}-index-headers.html"):
        return DocumentRole.INDEX
    if name == "index.json" or name == "index.xml":
        return DocumentRole.INDEX
    if primary_document and filename == primary_document:
        return DocumentRole.PRIMARY
    if form and dtype and dtype == form.upper() and (sequence in (None, 1)):
        return DocumentRole.PRIMARY
    if dtype.startswith("EX-101") or name.endswith(_XBRL_SUFFIXES) or name in _XBRL_NAMES:
        return DocumentRole.XBRL
    if dtype.startswith("EX-"):
        return DocumentRole.EXHIBIT
    if (
        dtype in ("GRAPHIC", "ZIP", "XML", "JSON", "EXCEL")
        or name.endswith(_GRAPHIC_SUFFIXES)
        or _RENDERING.match(name)
        or name in ("financial_report.xlsx", "report.css", "show.js")
    ):
        return DocumentRole.SUPPORTING
    return DocumentRole.UNKNOWN


@dataclass(frozen=True)
class PackageManifest:
    """Complete inventory of one accession package."""

    accession: str
    cik: int
    form: str | None
    documents: tuple[FilingDocument, ...]
    source_versions: tuple[str, ...]
    classifier_version: str = CLASSIFIER_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "accession", normalize_accession(self.accession))
        for doc in self.documents:
            if doc.accession != self.accession:
                raise ValueError(f"{doc.filename} belongs to {doc.accession}, not {self.accession}")
        names = [d.filename for d in self.documents]
        if len(names) != len(set(names)):
            raise ValueError("duplicate filenames in package manifest")

    def by_role(self, role: DocumentRole) -> list[FilingDocument]:
        return [d for d in self.documents if d.role == role.value]

    @property
    def primary(self) -> FilingDocument | None:
        prim = self.by_role(DocumentRole.PRIMARY)
        return prim[0] if len(prim) == 1 else None

    def get(self, filename: str) -> FilingDocument | None:
        return next((d for d in self.documents if d.filename == filename), None)

    @staticmethod
    def document_id(accession: str, filename: str) -> str:
        return f"{normalize_accession(accession)}/{filename}"
