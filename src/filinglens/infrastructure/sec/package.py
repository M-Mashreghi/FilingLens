"""Accession-directory inventory from SEC ``index.json`` plus the filing index page.

``index.json`` lists every file in the directory (name, size, last-modified) but no type or
description. The ``{accession}-index.htm`` page lists Seq/Description/Document/Type/Size for
documents in the submission. Both are merged by filename; files present in only one source
are retained (unknown types are never dropped).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from lxml import html as lxml_html

from filinglens.domain.accession import (
    archive_dir_url,
    document_url,
    filing_index_url,
    index_json_url,
    normalize_accession,
)
from filinglens.domain.models import FilingDocument
from filinglens.domain.package import PackageManifest, classify_document

ADAPTER_VERSION = "sec-package/1"


class BytesFetcher(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


@dataclass(frozen=True)
class IndexRow:
    filename: str
    sequence: int | None
    description: str | None
    doc_type: str | None
    size: int | None


def _int(value: str | None) -> int | None:
    v = (value or "").strip().replace(",", "")
    return int(v) if v.isdigit() else None


def _cell(cells: list[Any], col: dict[str, int], name: str) -> str | None:
    if name not in col:
        return None
    value = str(cells[col[name]].text_content()).replace(" ", " ").strip()
    return value or None


def parse_index_json(raw: bytes) -> dict[str, int | None]:
    data = json.loads(raw)
    items = data.get("directory", {}).get("item", [])
    return {str(i["name"]): _int(str(i.get("size", ""))) for i in items if i.get("type") != "dir"}


def parse_filing_index_html(raw: bytes) -> list[IndexRow]:
    tree = lxml_html.fromstring(raw)
    rows: list[IndexRow] = []
    for table in tree.xpath('//table[contains(@class, "tableFile")]'):
        headers = [h.text_content().strip().lower() for h in table.xpath(".//tr[1]/th")]
        col = {name: i for i, name in enumerate(headers)}
        if "document" not in col:
            continue
        for tr in table.xpath(".//tr[td]"):
            cells = tr.xpath("./td")
            if len(cells) < len(headers):
                continue
            doc_cell = cells[col["document"]]
            links = doc_cell.xpath(".//a")
            text = (links[0].text_content() if links else doc_cell.text_content()).strip()
            filename = text.split()[0] if text else ""
            if not filename:
                continue

            rows.append(
                IndexRow(
                    filename=filename,
                    sequence=_int(_cell(cells, col, "seq")),
                    description=_cell(cells, col, "description"),
                    doc_type=_cell(cells, col, "type"),
                    size=_int(_cell(cells, col, "size")),
                )
            )
    return rows


def build_manifest(
    *,
    cik: int,
    accession: str,
    form: str | None,
    primary_document: str | None,
    directory: dict[str, int | None],
    index_rows: list[IndexRow],
    source_versions: tuple[str, ...],
) -> PackageManifest:
    acc = normalize_accession(accession)
    rows = {r.filename: r for r in index_rows}
    names = sorted(set(directory) | set(rows))
    docs: list[FilingDocument] = []
    for name in names:
        row = rows.get(name)
        role = classify_document(
            accession=acc,
            filename=name,
            doc_type=row.doc_type if row else None,
            sequence=row.sequence if row else None,
            form=form,
            primary_document=primary_document,
        )
        size = directory.get(name)
        if size is None and row is not None:
            size = row.size
        docs.append(
            FilingDocument(
                accession=acc,
                filename=name,
                sequence=row.sequence if row else None,
                doc_type=row.doc_type if row else None,
                description=row.description if row else None,
                size_bytes=size,
                url=document_url(cik, acc, name),
                role=role.value,
            )
        )
    docs.sort(key=lambda d: (d.sequence is None, d.sequence or 0, d.filename))
    return PackageManifest(
        accession=acc, cik=cik, form=form, documents=tuple(docs), source_versions=source_versions
    )


class PackageManifestAdapter:
    def __init__(self, client: BytesFetcher) -> None:
        self.client = client

    def fetch(
        self, *, cik: int, accession: str, form: str | None, primary_document: str | None
    ) -> PackageManifest:
        dir_raw = self.client.get_bytes(index_json_url(cik, accession))
        idx_raw = self.client.get_bytes(filing_index_url(cik, accession))
        versions = tuple("sha256:" + hashlib.sha256(b).hexdigest() for b in (dir_raw, idx_raw))
        return build_manifest(
            cik=cik,
            accession=accession,
            form=form,
            primary_document=primary_document,
            directory=parse_index_json(dir_raw),
            index_rows=parse_filing_index_html(idx_raw),
            source_versions=versions,
        )


__all__ = [
    "PackageManifestAdapter",
    "archive_dir_url",
    "build_manifest",
    "parse_filing_index_html",
    "parse_index_json",
]
