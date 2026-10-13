"""Task 1.3: issuer / CIK / ticker resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from filinglens.application.issuer_resolver import (
    IssuerResolver,
    ReferenceDataCache,
    ReferenceDataset,
    ResolutionStatus,
    TickerRecord,
    normalize_name,
)
from filinglens.domain.identifiers import cik10, parse_cik
from filinglens.infrastructure.sec.reference import (
    SecTickerReferenceAdapter,
    dataset_from_bytes,
    parse_reference_json,
    persist_identifiers,
)
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sec"
T0 = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def dataset() -> ReferenceDataset:
    raw = (FIX / "company_tickers_exchange.json").read_bytes()
    return dataset_from_bytes(raw, source="fixture", retrieved_at=T0)


@pytest.fixture
def resolver(dataset: ReferenceDataset) -> IssuerResolver:
    return IssuerResolver(dataset, aliases={"Apple Computer Inc": 320193, "Google": 1652044})


def test_cik_canonicalization() -> None:
    assert cik10(320193) == "0000320193"
    assert cik10("CIK0000320193") == "0000320193"
    assert parse_cik("0000320193") == 320193
    for bad in ("", "abc", "12345678901", "0"):
        with pytest.raises(ValueError):
            parse_cik(bad)


@pytest.mark.parametrize(
    "query,cik,match",
    [
        ("AAPL", 320193, "ticker"),
        ("aapl", 320193, "ticker"),
        ("0000320193", 320193, "cik"),
        ("320193", 320193, "cik"),
        ("CIK0000789019", 789019, "cik"),
        ("BRK.B", 1067983, "ticker"),
        ("BRK-A", 1067983, "ticker"),
        ("NYSE:BRK-B", 1067983, "exchange_ticker"),
        ("nasdaq : msft", 789019, "exchange_ticker"),
        ("Microsoft Corporation", 789019, "name_exact"),
        ("apple inc", 320193, "name_exact"),
        ("Apple Computer, Inc.", 320193, "alias"),
        ("google", 1652044, "alias"),
    ],
)
def test_resolves(resolver: IssuerResolver, query: str, cik: int, match: str) -> None:
    res = resolver.resolve(query)
    assert res.status is ResolutionStatus.RESOLVED, res
    assert res.cik == cik
    assert res.candidates[0].match_type == match


def test_share_classes_resolve_to_single_issuer(resolver: IssuerResolver) -> None:
    res = resolver.resolve("GOOG")
    assert res.cik == 1652044
    assert res.candidates[0].tickers == ("GOOG", "GOOGL")


def test_ticker_shared_by_multiple_ciks_is_ambiguous(resolver: IssuerResolver) -> None:
    res = resolver.resolve("SHR")
    assert res.status is ResolutionStatus.AMBIGUOUS
    assert {c.cik for c in res.candidates} == {1800003, 1800004}
    assert res.cik is None
    assert resolver.resolve("OTC:SHR").cik == 1800004


def test_name_collision_after_normalization_is_ambiguous(resolver: IssuerResolver) -> None:
    res = resolver.resolve("Delta Holdings")
    assert res.status is ResolutionStatus.AMBIGUOUS
    assert [c.cik for c in res.candidates] == [1800001, 1800002]


def test_fuzzy_name_is_never_auto_resolved(resolver: IssuerResolver) -> None:
    res = resolver.resolve("Amazon")
    assert res.status is ResolutionStatus.AMBIGUOUS
    assert res.candidates[0].cik == 1018724
    assert res.candidates[0].match_type == "name_fuzzy"


def test_not_found(resolver: IssuerResolver) -> None:
    assert resolver.resolve("ZZZZQ").status is ResolutionStatus.NOT_FOUND
    assert resolver.resolve("NYSE:AAPL").status is ResolutionStatus.NOT_FOUND
    assert resolver.resolve("   ").status is ResolutionStatus.NOT_FOUND


def test_unlisted_cik_resolves_without_ticker(resolver: IssuerResolver) -> None:
    res = resolver.resolve("1800005")
    assert res.cik == 1800005 and res.candidates[0].tickers == ()


def test_resolution_retains_source_version(
    resolver: IssuerResolver, dataset: ReferenceDataset
) -> None:
    res = resolver.resolve("AAPL")
    assert res.source_version == dataset.source_version
    assert res.source_version.startswith("sha256:")
    assert res.retrieved_at == T0


def test_ticker_is_not_permanent_identity() -> None:
    old = ReferenceDataset(
        records=(TickerRecord(cik=111, name="Old Co", ticker="XYZ"),),
        source="s",
        source_version="v1",
        retrieved_at=T0,
    )
    new = ReferenceDataset(
        records=(TickerRecord(cik=222, name="New Co", ticker="XYZ"),),
        source="s",
        source_version="v2",
        retrieved_at=T0 + timedelta(days=400),
    )
    r_old, r_new = IssuerResolver(old).resolve("XYZ"), IssuerResolver(new).resolve("XYZ")
    assert (r_old.cik, r_old.source_version) == (111, "v1")
    assert (r_new.cik, r_new.source_version) == (222, "v2")


def test_legacy_company_tickers_format() -> None:
    recs = parse_reference_json((FIX / "company_tickers.json").read_bytes())
    assert {(r.cik, r.ticker) for r in recs} == {(320193, "AAPL"), (789019, "MSFT")}
    with pytest.raises(ValueError):
        parse_reference_json(b"[1,2]")


def test_normalize_name() -> None:
    assert normalize_name("The Coca-Cola Company") == "coca cola"
    assert normalize_name("AT&T Inc.") == "at and t"
    assert normalize_name("Nestlé S.A.") == "nestle"
    assert normalize_name("Amazon.com, Inc.") == "amazon com"


def test_cache_respects_ttl_and_records_versions() -> None:
    calls: list[int] = []
    now = [T0]

    def loader() -> ReferenceDataset:
        calls.append(1)
        return ReferenceDataset(
            records=(), source="s", source_version=f"v{len(calls)}", retrieved_at=now[0]
        )

    cache = ReferenceDataCache(loader, ttl=timedelta(hours=1), clock=lambda: now[0])
    assert cache.get().source_version == "v1"
    now[0] = T0 + timedelta(minutes=30)
    assert cache.get().source_version == "v1"
    now[0] = T0 + timedelta(hours=2)
    assert cache.get().source_version == "v2"
    assert [v for v, _ in cache.history] == ["v1", "v2"]


def test_adapter_uses_injected_fetch_and_persists(dataset: ReferenceDataset) -> None:
    urls: list[str] = []

    def fetch(url: str) -> bytes:
        urls.append(url)
        return (FIX / "company_tickers_exchange.json").read_bytes()

    ds = SecTickerReferenceAdapter(fetch).load()
    assert urls == ["https://www.sec.gov/files/company_tickers_exchange.json"]
    assert ds.source_version == dataset.source_version
    repos = SqliteRepositories(Database(":memory:"))
    n = persist_identifiers(ds, repos.identifiers)
    assert n == 11  # null-ticker row skipped
    stored = repos.identifiers.find_by_ticker("AAPL")
    assert stored[0].source_version == ds.source_version and stored[0].cik == 320193
