"""Task 1.4: accession identity, availability policy and immutable provenance."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest

from filinglens.domain.accession import (
    AccessionNumber,
    archive_dir_url,
    complete_submission_url,
    document_url,
    filing_index_url,
    index_json_url,
    normalize_accession,
    parse_archive_url,
)
from filinglens.domain.availability import (
    AvailabilityBasis,
    filter_as_of,
    is_available,
)
from filinglens.domain.errors import ConflictError
from filinglens.domain.models import Filing, SourceSnapshot
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

ACC = "0000320193-24-000123"
ACC_ND = "000032019324000123"


@pytest.mark.parametrize("raw", [ACC, ACC_ND, f"  {ACC} "])
def test_accession_round_trip(raw: str) -> None:
    a = AccessionNumber.parse(raw)
    assert a.dashed == ACC and a.nodash == ACC_ND
    assert AccessionNumber.parse(a.nodash) == AccessionNumber.parse(a.dashed)
    assert (a.filer_id, a.year, a.sequence) == ("0000320193", "24", "000123")


@pytest.mark.parametrize(
    "bad", ["", "0000320193-24-00012", "000032019324-000123", "abc", "0000320193_24_000123"]
)
def test_accession_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_accession(bad)


def test_sec_urls_round_trip() -> None:
    base = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
    assert archive_dir_url(320193, ACC) == base
    assert index_json_url(320193, ACC_ND) == base + "index.json"
    assert filing_index_url(320193, ACC) == base + f"{ACC}-index.htm"
    assert complete_submission_url(320193, ACC) == base + f"{ACC}.txt"
    url = document_url(320193, ACC, "aapl-20240928.htm")
    loc = parse_archive_url(url)
    assert (loc.cik, loc.accession, loc.filename) == (320193, ACC, "aapl-20240928.htm")
    assert document_url(loc.cik, loc.accession, loc.filename or "") == url
    assert parse_archive_url(base).filename is None
    with pytest.raises(ValueError):
        document_url(320193, ACC, "../etc")
    with pytest.raises(ValueError):
        parse_archive_url("https://example.com/Archives/edgar/data/1/000032019324000123/x.htm")


def _filing(**kw: object) -> Filing:
    base: dict[str, object] = {
        "accession": ACC_ND,
        "cik": 320193,
        "form": "10-k",
        "filing_date": date(2024, 11, 1),
        "report_period": date(2024, 9, 28),
        "accepted_at": datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC),
        "primary_document": "aapl-20240928.htm",
    }
    base.update(kw)
    return Filing(**base)  # type: ignore[arg-type]


def test_filing_normalizes_identity_and_form() -> None:
    f = _filing()
    assert f.accession == ACC
    assert f.form == "10-K" and f.base_form == "10-K"


def test_available_at_prefers_accepted_timestamp() -> None:
    f = _filing()
    assert f.availability_basis is AvailabilityBasis.ACCEPTED
    assert f.available_at == datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC)


def test_available_at_falls_back_conservatively_to_end_of_filing_date() -> None:
    f = _filing(accepted_at=None)
    assert f.availability_basis is AvailabilityBasis.FILING_DATE_CONSERVATIVE
    # 23:59:59 America/New_York on 2024-11-01 (EDT, UTC-4)
    assert f.available_at == datetime(2024, 11, 2, 3, 59, 59, tzinfo=UTC)
    # Midday on the filing date the filing is NOT yet eligible under the fallback.
    assert not is_available(f, datetime(2024, 11, 1, 16, 0, tzinfo=UTC))
    assert is_available(f, datetime(2024, 11, 2, 4, 0, tzinfo=UTC))


def test_available_at_handles_standard_time() -> None:
    f = _filing(accepted_at=None, filing_date=date(2024, 1, 15))  # EST, UTC-5
    assert f.available_at == datetime(2024, 1, 16, 4, 59, 59, tzinfo=UTC)


def test_future_filings_excluded_by_as_of() -> None:
    accepted = datetime(2024, 10, 31, 22, 1, 36, tzinfo=UTC)
    f = _filing(accepted_at=accepted)
    later = _filing(
        accession="0000320193-25-000001",
        form="10-K/A",
        amends_accession=ACC,
        filing_date=date(2025, 2, 1),
        accepted_at=datetime(2025, 1, 31, 21, 0, tzinfo=UTC),
    )
    assert filter_as_of([f, later], accepted - timedelta(seconds=1)) == []
    assert filter_as_of([f, later], accepted) == [f]
    assert filter_as_of([f, later], datetime(2025, 6, 1, tzinfo=UTC)) == [f, later]
    with pytest.raises(ValueError):
        filter_as_of([f], datetime(2025, 6, 1))  # naive as_of rejected


def test_amendment_rules() -> None:
    amend = _filing(accession="0000320193-25-000001", form="10-K/A", amends_accession=ACC)
    assert amend.is_amendment and amend.base_form == "10-K"
    with pytest.raises(ValueError):
        _filing(form="10-K/A", amends_accession=ACC)  # cannot amend itself
    with pytest.raises(ValueError):
        _filing(accession="0000320193-25-000001", amends_accession=ACC)  # 10-K is not /A


def test_amendment_cannot_overwrite_original_in_storage() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    original = repos.filings.add(_filing())
    with pytest.raises(ConflictError):
        repos.filings.add(_filing(form="10-K/A"))  # same accession re-labelled as amendment
    repos.filings.add(
        _filing(accession="0000320193-25-000001", form="10-K/A", amends_accession=ACC)
    )
    assert repos.filings.get(ACC) == original
    stored = repos.filings.get("000032019325000001")
    assert stored is not None and stored.amends_accession == ACC


def test_availability_persists_and_updates_when_accepted_time_learned() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    first = repos.filings.add(_filing(accepted_at=None))
    assert first.availability_basis is AvailabilityBasis.FILING_DATE_CONSERVATIVE
    merged = repos.filings.add(_filing())
    assert merged.availability_basis is AvailabilityBasis.ACCEPTED
    assert repos.filings.get(ACC) == merged


def test_snapshot_hash_and_provenance() -> None:
    data = b"<html>10-K</html>"
    t = datetime(2026, 1, 1, tzinfo=UTC)
    snap = SourceSnapshot.from_bytes(
        data,
        source_url=document_url(320193, ACC, "a.htm"),
        retrieved_at=t,
        content_type="text/html",
        adapter="sec-archives",
        adapter_version="1",
        accession=ACC_ND,
        filename="a.htm",
    )
    assert snap.snapshot_id == hashlib.sha256(data).hexdigest()
    assert snap.size_bytes == len(data) and snap.accession == ACC
    assert snap.storage_key.endswith(snap.snapshot_id)
    assert snap.verify(data)
    assert not snap.verify(data + b" ")
    again = SourceSnapshot.from_bytes(
        data,
        source_url="x",
        retrieved_at=t + timedelta(days=1),
        content_type=None,
        adapter="a",
        adapter_version="2",
    )
    assert again.snapshot_id == snap.snapshot_id  # identity is the bytes
    with pytest.raises(ValueError):
        SourceSnapshot(
            snapshot_id="A" * 64,
            source_url="x",
            retrieved_at=t,
            content_type=None,
            size_bytes=1,
            adapter="a",
            adapter_version="1",
            storage_key="k",
        )


def test_snapshot_identity_never_overwritten() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    t = datetime(2026, 1, 1, tzinfo=UTC)
    s1 = SourceSnapshot.from_bytes(
        b"x", source_url="u1", retrieved_at=t, content_type=None, adapter="a", adapter_version="1"
    )
    s2 = SourceSnapshot.from_bytes(
        b"x", source_url="u2", retrieved_at=t, content_type=None, adapter="a", adapter_version="9"
    )
    repos.snapshots.add(s1)
    assert repos.snapshots.add(s2) == s1
    assert repos.snapshots.get(s1.snapshot_id) == s1
