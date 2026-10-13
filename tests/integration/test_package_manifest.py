"""Task 2.3: filing package manifest and attachment inventory."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from filinglens.application.packages import PackageService
from filinglens.domain.errors import NotFoundError
from filinglens.domain.models import Filing, FilingDocument
from filinglens.domain.package import DocumentRole, PackageManifest, classify_document
from filinglens.infrastructure.sec.client import RateGovernor, SecHttpClient
from filinglens.infrastructure.sec.package import (
    PackageManifestAdapter,
    parse_filing_index_html,
    parse_index_json,
)
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

PKG = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "packages" / "000123456724000031"
ACC = "0001234567-24-000031"
CIK = 1234567


def _adapter(requested: list[str]) -> PackageManifestAdapter:
    def handler(req: httpx.Request) -> httpx.Response:
        requested.append(str(req.url))
        path = PKG / str(req.url).rsplit("/", 1)[-1]
        return (
            httpx.Response(200, content=path.read_bytes()) if path.exists() else httpx.Response(404)
        )

    client = SecHttpClient(
        user_agent="t t@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        governor=RateGovernor(1000),
    )
    return PackageManifestAdapter(client)


def _manifest() -> PackageManifest:
    return _adapter([]).fetch(
        cik=CIK, accession=ACC, form="10-K", primary_document="exdv-20240930.htm"
    )


def test_fetch_uses_sec_index_endpoints() -> None:
    requested: list[str] = []
    _adapter(requested).fetch(cik=CIK, accession=ACC, form="10-K", primary_document=None)
    base = "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000031/"
    assert requested == [base + "index.json", base + f"{ACC}-index.htm"]


def test_package_inventory_roles() -> None:
    m = _manifest()
    roles = {d.filename: d.role for d in m.documents}
    assert roles["exdv-20240930.htm"] == "primary"
    assert m.primary is not None and m.primary.filename == "exdv-20240930.htm"
    assert m.primary.sequence == 1 and m.primary.doc_type == "10-K"
    assert {d.filename for d in m.by_role(DocumentRole.EXHIBIT)} == {
        "exdv-ex21.htm",
        "exdv-ex311.htm",
        "exdv-ex32.htm",
    }
    assert {d.filename for d in m.by_role(DocumentRole.XBRL)} == {
        "exdv-ex101.htm",
        "exdv-20240930.xsd",
        "exdv-20240930_lab.xml",
        "exdv-20240930_pre.xml",
        "exdv-20240930_htm.xml",
        "FilingSummary.xml",
        "MetaLinks.json",
    }
    assert roles[f"{ACC}.txt"] == "complete_submission"
    assert roles[f"{ACC}-index.htm"] == "index"
    assert roles["logo.jpg"] == "supporting" and roles["R1.htm"] == "supporting"
    ex21 = m.get("exdv-ex21.htm")
    assert ex21 is not None and ex21.description == "EX-21.1" and ex21.size_bytes == 1200


def test_unknown_attachments_retained() -> None:
    m = _manifest()
    weird = m.get("weird-attachment.bin")
    assert weird is not None and weird.role == "unknown" and weird.size_bytes == 77
    # 18 directory entries, all retained
    assert len(m.documents) == 18


def test_manifest_links_all_documents_to_accession() -> None:
    m = _manifest()
    assert all(d.accession == ACC for d in m.documents)
    assert all(d.url and "/000123456724000031/" in d.url for d in m.documents)
    assert PackageManifest.document_id(ACC, "exdv-ex21.htm") == f"{ACC}/exdv-ex21.htm"
    with pytest.raises(ValueError):
        PackageManifest(
            accession=ACC,
            cik=CIK,
            form="10-K",
            source_versions=(),
            documents=(FilingDocument(accession="0001234567-24-000099", filename="x.htm"),),
        )
    with pytest.raises(ValueError):
        PackageManifest(
            accession=ACC,
            cik=CIK,
            form="10-K",
            source_versions=(),
            documents=(
                FilingDocument(accession=ACC, filename="x"),
                FilingDocument(accession=ACC, filename="x"),
            ),
        )


def test_index_page_only_and_directory_only_files_both_kept() -> None:
    from filinglens.infrastructure.sec.package import IndexRow, build_manifest

    m = build_manifest(
        cik=CIK,
        accession=ACC,
        form="8-K",
        primary_document=None,
        directory={"only-in-dir.htm": 10},
        index_rows=[IndexRow("only-in-index.htm", 1, "8-K", "8-K", 20)],
        source_versions=(),
    )
    assert {d.filename for d in m.documents} == {"only-in-dir.htm", "only-in-index.htm"}
    assert m.primary is not None and m.primary.filename == "only-in-index.htm"


def test_parsers_handle_fixture_shapes() -> None:
    directory = parse_index_json((PKG / "index.json").read_bytes())
    assert directory[f"{ACC}-index.htm"] is None and directory[f"{ACC}.txt"] == 912345
    rows = parse_filing_index_html((PKG / f"{ACC}-index.htm").read_bytes())
    assert [r.filename for r in rows][:2] == ["exdv-20240930.htm", "exdv-ex21.htm"]
    txt = next(r for r in rows if r.filename.endswith(".txt"))
    assert txt.sequence is None and txt.doc_type is None


@pytest.mark.parametrize(
    "filename,dtype,seq,expected",
    [
        ("main.htm", "10-Q", 1, DocumentRole.PRIMARY),
        ("ex991.htm", "EX-99.1", 2, DocumentRole.EXHIBIT),
        ("ex10.htm", "EX-10.1", 3, DocumentRole.EXHIBIT),
        ("x.xsd", "EX-101.SCH", 4, DocumentRole.XBRL),
        ("chart.png", None, None, DocumentRole.SUPPORTING),
        ("mystery.dat", None, None, DocumentRole.UNKNOWN),
        ("amend.htm", "10-Q/A", 1, DocumentRole.UNKNOWN),  # form mismatch → not primary
    ],
)
def test_classifier(
    filename: str, dtype: str | None, seq: int | None, expected: DocumentRole
) -> None:
    got = classify_document(
        accession=ACC,
        filename=filename,
        doc_type=dtype,
        sequence=seq,
        form="10-Q",
        primary_document=None,
    )
    assert got is expected


def test_exhibit_never_mistaken_for_primary_when_primary_document_known() -> None:
    got = classify_document(
        accession=ACC,
        filename="ex991.htm",
        doc_type="EX-99.1",
        sequence=1,
        form="8-K",
        primary_document="form8k.htm",
    )
    assert got is DocumentRole.EXHIBIT


def test_register_and_reload_package() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    svc = PackageService(repos.filings, repos.documents)
    m = _manifest()
    with pytest.raises(NotFoundError):
        svc.register(m)
    repos.filings.add(
        Filing(
            accession=ACC,
            cik=CIK,
            form="10-K",
            filing_date=date(2024, 11, 1),
            accepted_at=datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC),
            primary_document="exdv-20240930.htm",
        )
    )
    stored = svc.register(m)
    assert stored.documents == m.documents
    again = svc.register(m)  # idempotent
    assert again.documents == m.documents
    assert svc.load(ACC).primary == m.primary
