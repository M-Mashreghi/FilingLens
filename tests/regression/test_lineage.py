"""Task 3.2: amendment, restatement and filing lineage."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from filinglens.domain.models import Filing
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.lineage.amendments import LineageEngine, amendment_only_changes, link_amendment
from filinglens.parsing.normalizer import normalize_html


def f(acc: str, form: str, period: str | None, accepted: str, **kw: object) -> Filing:
    dt = datetime.fromisoformat(accepted).replace(tzinfo=UTC)
    return Filing(
        accession=acc,
        cik=1234567,
        form=form,
        filing_date=dt.date(),
        report_period=date.fromisoformat(period) if period else None,
        accepted_at=dt,
        **kw,  # type: ignore[arg-type]
    )


K23 = f("0001234567-23-000030", "10-K", "2023-09-30", "2023-11-03T20:00:00")
K24 = f("0001234567-24-000031", "10-K", "2024-09-28", "2024-11-01T20:00:00")
K24A = f("0001234567-25-000010", "10-K/A", "2024-09-28", "2025-02-14T21:30:00")
NR8K = f("0001234567-25-000008", "8-K", None, "2025-02-10T21:00:00", items=("4.02", "9.01"))
Q1 = f("0001234567-25-000004", "10-Q", "2024-12-28", "2025-01-30T21:00:00")
ALL = [K23, K24, K24A, NR8K, Q1]


def test_amendment_links_to_base_by_period() -> None:
    link = link_amendment(K24A, ALL)
    assert link.base_accession == K24.accession and link.confidence >= 0.9
    explicit = f(
        "0001234567-25-000011",
        "10-K/A",
        None,
        "2025-03-01T00:00:00",
        amends_accession=K23.accession,
    )
    assert link_amendment(explicit, ALL).reason == "explicit amends_accession"
    with pytest.raises(ValueError):
        link_amendment(K24, ALL)


def test_ambiguous_or_missing_base_not_guessed() -> None:
    dup = f("0001234567-24-000099", "10-K", "2024-09-28", "2024-12-01T00:00:00")
    link = link_amendment(K24A, [*ALL, dup])
    assert link.base_accession is None and len(link.candidates) == 2
    orphan = f("0001234567-25-000050", "10-Q/A", "2023-06-30", "2025-05-01T00:00:00")
    assert link_amendment(orphan, ALL).base_accession is None


def test_original_and_amendment_remain_separately_queryable() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    for x in ALL:
        repos.filings.add(x)
    assert repos.filings.get(K24.accession) == K24
    assert repos.filings.get(K24A.accession) == K24A
    engine = LineageEngine(repos.filings.list_for_cik(1234567))
    view = engine.period(K24.accession, datetime(2026, 1, 1, tzinfo=UTC))
    assert view.original == K24 and view.amendments == (K24A,)
    assert view.effective == K24A and view.is_amended


def test_historical_as_of_before_amendment_returns_original_state() -> None:
    engine = LineageEngine(ALL)
    before = engine.period(
        K24.accession,
        K24A.available_at.replace(microsecond=0) - (K24A.available_at - K24.available_at) / 2,
    )
    assert before.amendments == () and before.effective == K24
    just_before = engine.period(K24.accession, datetime(2025, 2, 14, 21, 29, 59, tzinfo=UTC))
    assert just_before.effective == K24
    at = engine.period(K24.accession, K24A.available_at)
    assert at.effective == K24A
    assert engine.period(K24.accession, datetime(2024, 1, 1, tzinfo=UTC)).original is None


def test_restatement_signals_are_neutral_and_point_in_time() -> None:
    engine = LineageEngine(ALL)
    assert engine.restatement_signals(datetime(2025, 2, 1, tzinfo=UTC)) == []
    sigs = engine.restatement_signals(datetime(2026, 1, 1, tzinfo=UTC))
    kinds = {(s.accession, s.signal) for s in sigs}
    assert kinds == {(NR8K.accession, "non_reliance_8k"), (K24A.accession, "amendment_filed")}
    for s in sigs:
        assert "fraud" not in s.description.lower() and "misconduct" not in s.description.lower()
    amend = next(s for s in sigs if s.signal == "amendment_filed")
    assert amend.related_accession == K24.accession


def test_amendment_only_changes_isolated() -> None:
    base = normalize_html(
        b"<html><body><p>Revenue was $100 million.</p><p>Risk A applies.</p></body></html>"
    )
    amend = normalize_html(
        b"<html><body><p>Revenue was $100 million.</p><p>Risk A applies.</p>"
        b"<p>Restated: inventory reserve corrected.</p></body></html>"
    )
    delta = amendment_only_changes(base, amend)
    assert delta.amendment_only_text == "Restated: inventory reserve corrected."
    assert delta.removed == () and delta.unchanged_count == 2


def test_amendment_vs_base_and_vs_prior_are_separate_lenses() -> None:
    from filinglens.lineage.comparator import select_comparator

    engine = LineageEngine(ALL)
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    vs_base = engine.links[K24A.accession].base_accession
    vs_prior = select_comparator(
        K24A, ALL, as_of=as_of, fiscal_year_end="0928"
    ).comparator_accession
    assert vs_base == K24.accession and vs_prior == K23.accession
