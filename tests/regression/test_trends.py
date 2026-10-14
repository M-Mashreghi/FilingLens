"""Task 4.5: deterministic financial trend engine (hand-verified fixtures)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from filinglens.analytics.trends import CanonicalStore, MetricStatus, PeriodPair, TrendEngine
from filinglens.domain.xbrl import FactSource, XbrlFact

ACC24 = "0001234567-24-000031"
ACC23 = "0001234567-23-000030"
ACC25A = "0001234567-25-000010"
P = PeriodPair(date(2023, 10, 1), date(2024, 9, 28), date(2022, 9, 25), date(2023, 9, 30))


def f(
    concept: str,
    value: str,
    *,
    cur: bool = True,
    instant: bool = False,
    acc: str | None = None,
    unit: str = "USD",
    ns: str = "http://fasb.org/us-gaap/2024",
) -> XbrlFact:
    start = (P.cur_start if cur else P.prior_start) if not instant else None
    end = P.cur_end if cur else P.prior_end
    return XbrlFact(
        source=FactSource.IXBRL,
        cik=1234567,
        accession=acc or (ACC24 if cur else ACC23),
        concept=concept,
        prefix="us-gaap",
        namespace=ns,
        unit=unit,
        value=Decimal(value),
        period_start=start,
        period_end=end,
        is_instant=instant,
        decimals="-6",
    )


FACTS = [
    f("RevenueFromContractWithCustomerExcludingAssessedTax", "391035"),
    f("RevenueFromContractWithCustomerExcludingAssessedTax", "383285", cur=False),
    f("CostOfGoodsAndServicesSold", "210352"),
    f("CostOfGoodsAndServicesSold", "214137", cur=False),
    f("OperatingIncomeLoss", "123216"),
    f("OperatingIncomeLoss", "114301", cur=False),
    f("NetIncomeLoss", "93736"),
    f("NetIncomeLoss", "96995", cur=False),
    f("NetCashProvidedByUsedInOperatingActivities", "118254"),
    f("NetCashProvidedByUsedInOperatingActivities", "110543", cur=False),
    f("PaymentsToAcquirePropertyPlantAndEquipment", "9447"),
    f("CashAndCashEquivalentsAtCarryingValue", "29943", instant=True),
    f("CashAndCashEquivalentsAtCarryingValue", "29965", cur=False, instant=True),
    f("LongTermDebtNoncurrent", "85750", instant=True),
    f("LongTermDebtNoncurrent", "95281", cur=False, instant=True),
    f("AssetsCurrent", "152987", instant=True),
    f("LiabilitiesCurrent", "176392", instant=True),
    f("AccountsReceivableNetCurrent", "33410", instant=True),
    f("AccountsReceivableNetCurrent", "29508", cur=False, instant=True),
    f("WeightedAverageNumberOfDilutedSharesOutstanding", "15408095", unit="shares"),
    f("WeightedAverageNumberOfDilutedSharesOutstanding", "15812547", cur=False, unit="shares"),
]


def _engine(facts: list[XbrlFact] = FACTS, eligible: set[str] | None = None) -> TrendEngine:
    return TrendEngine(CanonicalStore(facts, eligible_accessions=eligible))


def test_hand_verified_formulas() -> None:
    m = _engine().compute(P)
    # (391035 - 383285) / 383285 = 0.020220...
    assert m["revenue_growth"].value == Decimal("0.020220")
    # gross margin derived: (391035-210352)/391035 = 0.462063 ; prior (383285-214137)/383285 = 0.441311
    assert m["gross_profit_margin"].value == Decimal("0.462063")
    assert m["gross_profit_margin_delta"].value == Decimal("0.020752")
    # operating margin 123216/391035 = 0.315102 ; prior 114301/383285 = 0.298214
    assert m["operating_income_margin"].value == Decimal("0.315102")
    assert m["operating_income_margin_delta"].value == Decimal("0.016888")
    assert m["free_cash_flow"].value == Decimal("108807")  # 118254 - 9447
    assert m["cash_change"].value == Decimal("-22")
    assert m["long_term_debt_change"].value == Decimal("-9531")
    assert m["working_capital"].value == Decimal("-23405")
    assert m["current_ratio"].value == Decimal("0.867313")  # 152987/176392
    assert m["net_debt"].value == Decimal("55807")  # 85750 - 29943
    assert m["receivables_growth"].value == Decimal("0.132235")  # 3902/29508
    assert m["diluted_share_change"].value == Decimal("-0.025578")  # -404452/15812547
    assert m["net_income_growth"].value == Decimal("-0.033600")


def test_every_metric_traces_to_facts_and_accessions() -> None:
    m = _engine().compute(P)
    for name, r in m.items():
        if r.status is MetricStatus.OK:
            assert r.fact_ids and r.accessions and r.formula and r.version == "trend-engine/1", name
    assert set(m["revenue_growth"].accessions) == {ACC23, ACC24}
    assert m["free_cash_flow"].unit == "USD" and m["revenue_growth"].unit == "pure"


def test_missing_inputs_insufficient_not_zero() -> None:
    m = _engine().compute(P)
    inv = m["inventory_growth"]
    assert (
        inv.status is MetricStatus.INSUFFICIENT_DATA
        and inv.value is None
        and "missing" in inv.reason
    )
    no_capex = _engine(
        [x for x in FACTS if x.concept != "PaymentsToAcquirePropertyPlantAndEquipment"]
    ).compute(P)
    assert no_capex["free_cash_flow"].status is MetricStatus.INSUFFICIENT_DATA
    assert no_capex["free_cash_flow"].value is None


def test_unit_and_zero_guards() -> None:
    eur = [x for x in FACTS if x.concept != "PaymentsToAcquirePropertyPlantAndEquipment"]
    eur.append(f("PaymentsToAcquirePropertyPlantAndEquipment", "9447", unit="EUR"))
    assert _engine(eur).compute(P)["free_cash_flow"].reason == "unit mismatch"
    zero_prior = [x for x in FACTS if not (x.concept == "NetIncomeLoss" and x.accession == ACC23)]
    zero_prior.append(f("NetIncomeLoss", "0", cur=False))
    r = _engine(zero_prior).compute(P)["net_income_growth"]
    assert r.status is MetricStatus.INSUFFICIENT_DATA and "zero" in r.reason


def test_point_in_time_filtering_enforced() -> None:
    restated = f("RevenueFromContractWithCustomerExcludingAssessedTax", "400000", acc=ACC25A)
    facts = [x for x in FACTS if not (x.concept.startswith("Revenue") and x.accession == ACC24)] + [
        restated
    ]
    # As of before the amendment: only 2023/2024 originals eligible → current revenue missing.
    m = _engine(facts, eligible={ACC23, ACC24}).compute(P)
    assert m["revenue_growth"].status is MetricStatus.INSUFFICIENT_DATA
    store = CanonicalStore(facts, eligible_accessions={ACC23, ACC24})
    assert store.excluded_not_public == 1
    m2 = _engine(facts, eligible={ACC23, ACC24, ACC25A}).compute(P)
    assert m2["revenue_growth"].accessions == (ACC23, ACC25A)


def test_preferred_concept_by_rule_priority() -> None:
    both = [*FACTS, f("Revenues", "999999")]
    r = _engine(both).compute(P)["revenue_growth"]
    assert r.inputs[0].qname == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def test_deterministic() -> None:
    assert _engine().compute(P) == _engine().compute(P)
