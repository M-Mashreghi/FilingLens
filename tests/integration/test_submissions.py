"""Task 2.2: SEC submissions + bulk metadata adapter."""

from __future__ import annotations

import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from filinglens.application.metadata import FilingMetadataService
from filinglens.domain.availability import AvailabilityBasis
from filinglens.domain.errors import ConflictError
from filinglens.infrastructure.sec.client import RateGovernor, SecHttpClient
from filinglens.infrastructure.sec.submissions import (
    BulkSubmissionsArchive,
    SubmissionsAdapter,
    parse_acceptance,
    parse_filing_columns,
)
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sec" / "submissions"
CIK = 1234567


def _client(requested: list[str]) -> SecHttpClient:
    def handler(req: httpx.Request) -> httpx.Response:
        requested.append(str(req.url))
        name = str(req.url).rsplit("/", 1)[-1]
        path = FIX / name
        if not path.exists():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    return SecHttpClient(
        user_agent="t t@example.com",
        live=False,
        transport=httpx.MockTransport(handler),
        governor=RateGovernor(1000),
    )


def _service() -> tuple[FilingMetadataService, SqliteRepositories]:
    repos = SqliteRepositories(Database(":memory:"))
    clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)  # noqa: E731
    return FilingMetadataService(repos.filings, repos.issuers, clock=clock), repos


def test_per_company_recent_and_history_parse() -> None:
    requested: list[str] = []
    batch = SubmissionsAdapter(_client(requested)).fetch(CIK)
    assert requested == [
        "https://data.sec.gov/submissions/CIK0001234567.json",
        "https://data.sec.gov/submissions/CIK0001234567-submissions-001.json",
    ]
    assert batch.issuer.name == "Example Devices Inc."
    assert batch.issuer.former_names == ("Example Computer Corp",)
    assert batch.issuer.fiscal_year_end == "0930"
    by_acc = {f.accession: f for f in batch.filings}
    assert len(by_acc) == 7
    tenk = by_acc["0001234567-24-000031"]
    assert tenk.form == "10-K" and tenk.report_period == date(2024, 9, 30)
    assert tenk.primary_document == "exdv-20240930.htm" and tenk.is_inline_xbrl is True
    amend = by_acc["0001234567-25-000010"]
    assert amend.is_amendment and amend.base_form == "10-K"
    eightk = by_acc["0001234567-24-000012"]
    assert eightk.items == ("2.02", "9.01") and eightk.report_period is None
    old = by_acc["0001234567-22-000029"]
    assert old.filing_date == date(2022, 11, 4)
    assert len(batch.source_versions) == 2


def test_acceptance_time_is_eastern_and_conservative() -> None:
    acc = parse_acceptance("2024-10-31T18:01:36.000Z")
    assert acc == datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC)  # 18:01 EDT
    assert parse_acceptance("") is None and parse_acceptance(None) is None
    # The published "Z" time read as UTC would be earlier: ET reading never leaks early.
    assert acc > datetime(2024, 10, 31, 18, 1, 36, tzinfo=UTC)


def test_after_hours_acceptance_precedes_filing_date() -> None:
    batch = SubmissionsAdapter(_client([])).fetch(CIK)
    f = next(x for x in batch.filings if x.accession == "0001234567-23-000030")
    # accepted 2023-11-02 18:08 ET, filing date 2023-11-03 (after 17:30 cutoff)
    assert f.availability_basis is AvailabilityBasis.ACCEPTED
    assert f.available_at == datetime(2023, 11, 2, 22, 8, 27, tzinfo=UTC)


def test_column_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        parse_filing_columns(
            CIK, {"accessionNumber": ["0001234567-25-000010"], "form": []}, source="x"
        )


def test_ingest_is_idempotent_and_records_source() -> None:
    svc, repos = _service()
    batch = SubmissionsAdapter(_client([])).fetch(CIK)
    first = svc.ingest(
        batch.issuer, batch.filings, source=batch.source, source_versions=batch.source_versions
    )
    second = svc.ingest(
        batch.issuer, batch.filings, source=batch.source, source_versions=batch.source_versions
    )
    assert (first.filings_seen, first.filings_new) == (7, 7)
    assert (second.filings_seen, second.filings_new) == (7, 0)
    assert len(repos.filings.list_for_cik(CIK)) == 7
    obs = repos.filings.observations("0001234567-24-000031")
    assert obs[0][0] == "sec-submissions" and obs[0][1].startswith("sha256:")
    assert repos.issuers.get(CIK).name == "Example Devices Inc."  # type: ignore[union-attr]


def _bulk_zip(tmp_path: Path) -> Path:
    path = tmp_path / "submissions.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for p in FIX.glob("*.json"):
            zf.write(p, p.name)
        zf.writestr(
            "CIK0009999999.json",
            (FIX / "CIK0001234567.json")
            .read_text()
            .replace('"1234567"', '"9999999"')
            .replace("0001234567-", "0009999999-"),
        )
    return path


def test_bulk_and_per_company_converge(tmp_path: Path) -> None:
    svc, repos = _service()
    api = SubmissionsAdapter(_client([])).fetch(CIK)
    svc.ingest(api.issuer, api.filings, source=api.source, source_versions=api.source_versions)
    snapshot_before = {f.accession: f for f in repos.filings.list_for_cik(CIK)}

    archive = BulkSubmissionsArchive(_bulk_zip(tmp_path))
    batches = list(archive.iter_batches(ciks={CIK}))
    assert len(batches) == 1
    bulk = batches[0]
    assert {f.accession for f in bulk.filings} == set(snapshot_before)
    result = svc.ingest(
        bulk.issuer, bulk.filings, source=bulk.source, source_versions=bulk.source_versions
    )
    assert result.filings_new == 0
    after = {f.accession: f for f in repos.filings.list_for_cik(CIK)}
    assert after == snapshot_before  # same canonical identity and content
    sources = {o[0] for o in repos.filings.observations("0001234567-24-000031")}
    assert sources == {"sec-submissions", "sec-bulk-submissions"}


def test_bulk_without_filter_reads_all_issuers(tmp_path: Path) -> None:
    archive = BulkSubmissionsArchive(_bulk_zip(tmp_path))
    assert sorted(b.issuer.cik for b in archive.iter_batches()) == [CIK, 9999999]


def test_conflicting_source_metadata_is_not_silently_merged() -> None:
    svc, _ = _service()
    batch = SubmissionsAdapter(_client([])).fetch(CIK)
    svc.ingest(batch.issuer, batch.filings, source="a", source_versions=("v1",))
    tampered = [
        f.__class__(
            **{
                **{
                    k: getattr(f, k)
                    for k in (
                        "accession",
                        "cik",
                        "filing_date",
                        "report_period",
                        "accepted_at",
                        "primary_document",
                        "is_amendment",
                        "amends_accession",
                        "source",
                        "items",
                    )
                },
                "form": "8-K" if f.form == "10-Q" else f.form,
            }
        )
        for f in batch.filings
    ]
    with pytest.raises(ConflictError):
        svc.ingest(batch.issuer, tampered, source="b", source_versions=("v2",))


def test_as_of_view_excludes_future_filings() -> None:
    svc, _ = _service()
    batch = SubmissionsAdapter(_client([])).fetch(CIK)
    svc.ingest(
        batch.issuer, batch.filings, source=batch.source, source_versions=batch.source_versions
    )
    # Just before the FY2024 10-K acceptance (2024-10-31 22:01:36 UTC)
    view = svc.filings_as_of(CIK, datetime(2024, 10, 31, 22, 1, 35, tzinfo=UTC))
    accs = [f.accession for f in view]
    assert "0001234567-24-000031" not in accs
    assert "0001234567-25-000010" not in accs and "0001234567-25-000004" not in accs
    assert accs[-1] == "0001234567-24-000020"
    view2 = svc.filings_as_of(CIK, datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC), forms={"10-k"})
    assert [f.accession for f in view2] == [
        "0001234567-22-000029",
        "0001234567-23-000030",
        "0001234567-24-000031",
    ]
    for f in svc.filings_as_of(CIK, datetime(2026, 1, 1, tzinfo=UTC)):
        assert f.available_at <= datetime(2026, 1, 1, tzinfo=UTC)
