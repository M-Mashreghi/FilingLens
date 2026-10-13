"""SEC submissions metadata: per-company JSON and the bulk ``submissions.zip`` archive.

Contract notes (see docs/SEC_DATA_CONTRACT.md):

- ``filings.recent`` and the referenced history pages (``filings.files[].name``) are columnar
  arrays; history pages carry the same arrays at the top level.
- ``acceptanceDateTime`` is published with a ``Z`` suffix but represents EDGAR acceptance time
  in US Eastern time. We interpret it as America/New_York. This is also the conservative
  choice for point-in-time use: it yields a later UTC instant than reading it as UTC.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from filinglens.domain.identifiers import cik10
from filinglens.domain.models import Filing, Issuer

SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
BULK_SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
ADAPTER_VERSION = "sec-submissions/1"
_ET = ZoneInfo("America/New_York")


class BytesFetcher(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


def parse_acceptance(value: str | None) -> datetime | None:
    if not value:
        return None
    naive = datetime.fromisoformat(value.replace("Z", "").split(".")[0])
    return naive.replace(tzinfo=_ET)


def _opt_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _opt_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    return bool(int(value))


def _items(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    return tuple(i.strip() for i in value.split(",") if i.strip())


def parse_filing_columns(cik: int, cols: dict[str, list[Any]], source: str) -> list[Filing]:
    n = len(cols.get("accessionNumber", []))
    for key, values in cols.items():
        if isinstance(values, list) and len(values) != n:
            raise ValueError(f"submissions column {key!r} has {len(values)} rows, expected {n}")

    def col(name: str, i: int) -> Any:
        values = cols.get(name)
        return values[i] if values else None

    filings: list[Filing] = []
    for i in range(n):
        form = str(col("form", i))
        filings.append(
            Filing(
                accession=str(col("accessionNumber", i)),
                cik=cik,
                form=form,
                filing_date=date.fromisoformat(col("filingDate", i)),
                report_period=_opt_date(col("reportDate", i)),
                accepted_at=parse_acceptance(col("acceptanceDateTime", i)),
                primary_document=col("primaryDocument", i) or None,
                is_amendment=form.upper().endswith("/A"),
                source=source,
                items=_items(col("items", i)),
                primary_doc_description=col("primaryDocDescription", i) or None,
                is_xbrl=_opt_bool(col("isXBRL", i)),
                is_inline_xbrl=_opt_bool(col("isInlineXBRL", i)),
            )
        )
    return filings


@dataclass(frozen=True)
class SubmissionsDocument:
    issuer: Issuer
    filings: list[Filing]
    history_files: list[str]
    source_url: str
    source_version: str


def parse_submissions(raw: bytes, *, source_url: str) -> SubmissionsDocument:
    data = json.loads(raw)
    cik = int(data["cik"])
    version = "sha256:" + hashlib.sha256(raw).hexdigest()
    issuer = Issuer(
        cik=cik,
        name=data.get("name") or f"CIK {cik}",
        former_names=tuple(f["name"] for f in data.get("formerNames", []) if f.get("name")),
        sic=data.get("sic") or None,
        fiscal_year_end=data.get("fiscalYearEnd") or None,
    )
    filings_block = data.get("filings", {})
    filings = parse_filing_columns(cik, filings_block.get("recent", {}), source="sec-submissions")
    history = [f["name"] for f in filings_block.get("files", [])]
    return SubmissionsDocument(issuer, filings, history, source_url, version)


def parse_history_page(raw: bytes, *, cik: int) -> list[Filing]:
    return parse_filing_columns(cik, json.loads(raw), source="sec-submissions")


@dataclass(frozen=True)
class SubmissionsBatch:
    issuer: Issuer
    filings: list[Filing]
    source: str
    source_versions: tuple[str, ...]


class SubmissionsAdapter:
    """Per-company submissions via the governed SEC client."""

    def __init__(self, client: BytesFetcher) -> None:
        self.client = client

    def fetch(self, cik: int, *, include_history: bool = True) -> SubmissionsBatch:
        url = f"{SUBMISSIONS_BASE}/CIK{cik10(cik)}.json"
        doc = parse_submissions(self.client.get_bytes(url), source_url=url)
        filings = list(doc.filings)
        versions = [doc.source_version]
        if include_history:
            for name in doc.history_files:
                raw = self.client.get_bytes(f"{SUBMISSIONS_BASE}/{name}")
                versions.append("sha256:" + hashlib.sha256(raw).hexdigest())
                filings.extend(parse_history_page(raw, cik=doc.issuer.cik))
        return SubmissionsBatch(doc.issuer, filings, "sec-submissions", tuple(versions))


class BulkSubmissionsArchive:
    """Reads a locally downloaded ``submissions.zip`` (download it with the governed client)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def version(self) -> str:
        h = hashlib.sha256()
        with self.path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()

    def iter_batches(self, ciks: set[int] | None = None) -> Iterator[SubmissionsBatch]:
        version = self.version()
        with zipfile.ZipFile(self.path) as zf:
            names = set(zf.namelist())
            for name in sorted(names):
                if "-submissions-" in name or not name.endswith(".json"):
                    continue
                cik = int(name.removeprefix("CIK").removesuffix(".json"))
                if ciks is not None and cik not in ciks:
                    continue
                doc = parse_submissions(zf.read(name), source_url=f"{BULK_SUBMISSIONS_URL}#{name}")
                filings = list(doc.filings)
                for hist in doc.history_files:
                    if hist in names:
                        filings.extend(parse_history_page(zf.read(hist), cik=cik))
                yield SubmissionsBatch(doc.issuer, filings, "sec-bulk-submissions", (version,))
