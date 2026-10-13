"""Accession-number identity and SEC Archives URL conventions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_DASHED = re.compile(r"^(\d{10})-(\d{2})-(\d{6})$")
_NODASH = re.compile(r"^(\d{10})(\d{2})(\d{6})$")

SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


@dataclass(frozen=True, order=True)
class AccessionNumber:
    """``0000320193-24-000123``: filer-agent id, 2-digit year, sequence."""

    filer_id: str
    year: str
    sequence: str

    @classmethod
    def parse(cls, value: str) -> AccessionNumber:
        v = value.strip()
        m = _DASHED.match(v) or _NODASH.match(v)
        if not m:
            raise ValueError(f"not an accession number: {value!r}")
        return cls(m.group(1), m.group(2), m.group(3))

    @property
    def dashed(self) -> str:
        return f"{self.filer_id}-{self.year}-{self.sequence}"

    @property
    def nodash(self) -> str:
        return f"{self.filer_id}{self.year}{self.sequence}"

    def __str__(self) -> str:
        return self.dashed


def normalize_accession(value: str) -> str:
    """Canonical dashed representation."""
    return AccessionNumber.parse(value).dashed


def archive_dir_url(cik: int, accession: str) -> str:
    return f"{SEC_ARCHIVES_BASE}/{cik}/{AccessionNumber.parse(accession).nodash}/"


def document_url(cik: int, accession: str, filename: str) -> str:
    if not filename or "/" in filename or filename in (".", ".."):
        raise ValueError(f"invalid package filename: {filename!r}")
    return archive_dir_url(cik, accession) + filename


def index_json_url(cik: int, accession: str) -> str:
    return archive_dir_url(cik, accession) + "index.json"


def filing_index_url(cik: int, accession: str) -> str:
    return archive_dir_url(cik, accession) + f"{normalize_accession(accession)}-index.htm"


def complete_submission_url(cik: int, accession: str) -> str:
    return archive_dir_url(cik, accession) + f"{normalize_accession(accession)}.txt"


@dataclass(frozen=True)
class ArchiveLocation:
    cik: int
    accession: str
    filename: str | None


def parse_archive_url(url: str) -> ArchiveLocation:
    """Inverse of :func:`document_url` / :func:`archive_dir_url`."""
    parsed = urlparse(url)
    if parsed.netloc != "www.sec.gov":
        raise ValueError(f"not an SEC archives URL: {url!r}")
    parts = [p for p in parsed.path.split("/") if p]
    if parts[:3] != ["Archives", "edgar", "data"] or len(parts) < 5:
        raise ValueError(f"not an SEC archives URL: {url!r}")
    cik = int(parts[3])
    accession = normalize_accession(parts[4])
    filename = parts[5] if len(parts) > 5 else None
    return ArchiveLocation(cik=cik, accession=accession, filename=filename)
