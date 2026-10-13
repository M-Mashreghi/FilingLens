"""Task 3.1: comparable filing selection policy."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from filinglens.application.comparison import record_comparator
from filinglens.application.runs import RunService
from filinglens.domain.models import Filing
from filinglens.infrastructure.storage.sqlite import Database, SqliteRepositories
from filinglens.lineage.comparator import (
    AmendmentPolicy,
    ComparatorError,
    ComparatorMode,
    fiscal_quarter,
    select_comparator,
)

CIK = 1234567
_seq = iter(range(1, 10_000))


def f(form: str, period: str | None, accepted: str, items: tuple[str, ...] | None = None) -> Filing:
    acc_dt = datetime.fromisoformat(accepted).replace(tzinfo=UTC)
    return Filing(
        accession=f"0001234567-{acc_dt.year % 100:02d}-{next(_seq):06d}",
        cik=CIK,
        form=form,
        filing_date=acc_dt.date(),
        report_period=date.fromisoformat(period) if period else None,
        accepted_at=acc_dt,
        items=items,
    )


# Apple-like 52/53-week fiscal year ending late September (FYE 0928).
K22 = f("10-K", "2022-09-24", "2022-10-28T20:00:00")
K23 = f("10-K", "2023-09-30", "2023-11-03T20:00:00")
K24 = f("10-K", "2024-09-28", "2024-11-01T20:00:00")
Q1_24 = f("10-Q", "2023-12-30", "2024-02-02T20:00:00")
Q2_24 = f("10-Q", "2024-03-30", "2024-05-03T20:00:00")
Q3_24 = f("10-Q", "2024-06-29", "2024-08-02T20:00:00")
Q1_25 = f("10-Q", "2024-12-28", "2025-01-31T20:00:00")
Q2_25 = f("10-Q", "2025-03-29", "2025-05-02T20:00:00")
K23A = f("10-K/A", "2023-09-30", "2024-01-15T20:00:00")
HISTORY = [K22, K23, K24, Q1_24, Q2_24, Q3_24, Q1_25, Q2_25, K23A]
LATE = datetime(2026, 1, 1, tzinfo=UTC)
FYE = "0928"


@pytest.mark.parametrize(
    "period,expected",
    [
        ("2024-09-28", 4),
        ("2023-12-30", 1),
        ("2024-03-30", 2),
        ("2024-06-29", 3),
        ("2023-09-30", 4),
        ("2024-10-05", 4),
        ("2024-12-28", 1),
        ("2025-01-03", 1),
    ],
)
def test_fiscal_quarter_52_53_week(period: str, expected: int) -> None:
    assert fiscal_quarter(date.fromisoformat(period), FYE) == expected


def test_fiscal_quarter_calendar_and_unknown() -> None:
    assert fiscal_quarter(date(2024, 3, 31), "1231") == 1
    assert fiscal_quarter(date(2024, 12, 31), "1231") == 4
    assert fiscal_quarter(date(2024, 2, 15), "1231") is None
    assert fiscal_quarter(date(2024, 3, 31), None) is None


# ---- comparator matrix ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "focal,mode,expected",
    [
        (K24, ComparatorMode.DEFAULT, K23),
        (K23, ComparatorMode.DEFAULT, K22),
        (Q1_25, ComparatorMode.DEFAULT, Q1_24),
        (Q2_25, ComparatorMode.DEFAULT, Q2_24),
        (Q2_25, ComparatorMode.SEQUENTIAL, Q1_25),
        (Q3_24, ComparatorMode.SEQUENTIAL, Q2_24),
        (Q1_25, ComparatorMode.SEQUENTIAL, None),
        (K22, ComparatorMode.DEFAULT, None),
        (Q1_24, ComparatorMode.DEFAULT, None),
    ],
)
def test_comparator_matrix(focal: Filing, mode: ComparatorMode, expected: Filing | None) -> None:
    sel = select_comparator(focal, HISTORY, as_of=LATE, fiscal_year_end=FYE, mode=mode)
    assert sel.comparator_accession == (expected.accession if expected else None), sel.reason
    assert sel.reason and sel.policy_version == "comparator-policy/1"


def test_52_53_week_gap_is_full_confidence_and_q1_reason() -> None:
    sel = select_comparator(K24, HISTORY, as_of=LATE, fiscal_year_end=FYE)
    assert sel.confidence == 1.0 and "gap 364 days" in sel.reason
    seq = select_comparator(
        Q1_25, HISTORY, as_of=LATE, fiscal_year_end=FYE, mode=ComparatorMode.SEQUENTIAL
    )
    assert "fiscal Q4" in seq.reason


def test_no_future_leakage() -> None:
    # As of just before K23 was accepted, K24's comparator search must not see K23.
    as_of = K23.available_at - timedelta(seconds=1)
    sel = select_comparator(K22, HISTORY, as_of=as_of, fiscal_year_end=FYE)
    assert sel.comparator_accession is None
    with pytest.raises(ComparatorError):
        select_comparator(K24, HISTORY, as_of=as_of, fiscal_year_end=FYE)  # focal not yet public
    sel2 = select_comparator(Q2_24, HISTORY, as_of=Q2_24.available_at, fiscal_year_end=FYE)
    excluded = dict(sel2.excluded)
    assert excluded[K24.accession] == "not publicly available at as_of"
    assert K23A.accession not in excluded  # amendment was already public


def test_amendment_leakage_and_policy() -> None:
    # Before the amendment was public, the original is the only candidate.
    before = select_comparator(
        K24,
        HISTORY,
        as_of=K24.available_at,
        fiscal_year_end=FYE,
        amendment_policy=AmendmentPolicy.LATEST_AVAILABLE,
    )
    assert before.comparator_accession == K23A.accession  # K23A (Jan 2024) is public by Nov 2024
    hist = [h for h in HISTORY if h is not K23A] + [
        f("10-K/A", "2023-09-30", "2025-03-01T20:00:00")
    ]
    early = select_comparator(
        K24,
        hist,
        as_of=K24.available_at,
        fiscal_year_end=FYE,
        amendment_policy=AmendmentPolicy.LATEST_AVAILABLE,
    )
    assert early.comparator_accession == K23.accession  # later amendment not yet public
    default = select_comparator(K24, HISTORY, as_of=LATE, fiscal_year_end=FYE)
    assert default.comparator_accession == K23.accession
    assert any("amendment" in n for n in default.notes)


def test_focal_amendment_compares_prior_period() -> None:
    sel = select_comparator(K23A, HISTORY, as_of=LATE, fiscal_year_end=FYE)
    assert sel.comparator_accession == K22.accession
    assert any("focal is an amendment" in n for n in sel.notes)


def test_missing_prior_year_and_fiscal_year_change() -> None:
    k_short = f("10-K", "2024-12-31", "2025-02-20T20:00:00")  # FYE moved to December
    sel = select_comparator(k_short, [*HISTORY, k_short], as_of=LATE, fiscal_year_end="1231")
    assert sel.comparator_accession == K24.accession
    assert sel.confidence < 1.0 and sel.notes


def test_quarterly_without_fye_falls_back_to_gap() -> None:
    sel = select_comparator(Q2_25, HISTORY, as_of=LATE, fiscal_year_end=None)
    assert sel.comparator_accession == Q2_24.accession and sel.confidence < 1.0


def test_eight_k_event_baselines() -> None:
    e1 = f("8-K", None, "2024-05-02T20:30:00", ("2.02", "9.01"))
    e2 = f("8-K", None, "2024-08-01T20:30:00", ("2.02", "9.01"))
    ceo = f("8-K", None, "2024-09-01T20:30:00", ("5.02",))
    hist = [e1, e2, ceo]
    assert select_comparator(e2, hist, as_of=LATE).comparator_accession == e1.accession
    no_base = select_comparator(ceo, hist, as_of=LATE)
    assert no_base.comparator_accession is None and "no meaningful prior baseline" in no_base.reason
    assert select_comparator(e1, hist, as_of=LATE).comparator_accession is None


def test_unsupported_form() -> None:
    with pytest.raises(ComparatorError):
        select_comparator(f("S-1", None, "2024-01-01T00:00:00"), [], as_of=LATE)


def test_selection_reason_persisted_in_run_manifest() -> None:
    repos = SqliteRepositories(Database(":memory:"))
    svc = RunService(repos.runs, repos.snapshots, repos.evidence, id_factory=lambda: "run-1")
    run = svc.create(cik=CIK, as_of=LATE, focal_accession=K24.accession)
    sel = select_comparator(K24, HISTORY, as_of=LATE, fiscal_year_end=FYE)
    m = record_comparator(svc, run.run_id, sel)
    assert m.comparator_accession == K23.accession
    assert m.comparator_reason and "prior fiscal-year 10-K" in m.comparator_reason
    assert m.component_versions["comparator_policy"] == "comparator-policy/1"
    assert svc.get(run.run_id).comparator_accession == K23.accession
