"""Liquidity gaps, what-if and proposals (T016, D-013).

Mechanics run on a synthetic restaurant (always); the numbers of D-013 run on the organizer's statement and are
skipped without the CSV. The hand-off to Scout's liquidity loop is tested in `tests/scout/`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from cfo.data.source import SourceNotFound, find_source_csv, load_bank_csv
from cfo.domain import EventKind, PolicyLevel, Rule
from cfo.forecast import liquidity as liq
from cfo.forecast import model as fm
from scout.policy import PolicyContext

HISTORY_END = "2025-11-30"
SYNTHETIC_FIXED = Decimal("16700.00")  # payroll 8600 + rent 3800 + social security 2650 + tax 1000 + utilities 650


# -- synthetic restaurant ------------------------------------------------------------------------------------
def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


@pytest.fixture(scope="module")
def history() -> pd.DataFrame:
    """Jan-Nov 2025: revenue 700 a day (900 at weekends) except in June (closed), monthly fixed costs 16,700."""
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", HISTORY_END):
        if day.month != 6:
            rows.append((day, 900.0 if day.day_of_week >= 5 else 700.0, "revenue", None))
        fixed = {28: ("payroll", -8600.0), 1: ("rent", -3800.0), 27: ("social_security", -2650.0),
                 10: ("tax", -1000.0), 11: ("utilities", -650.0)}
        if day.day in fixed:
            rows.append((day, fixed[day.day][1], fixed[day.day][0], "company"))
    return _tx(rows)


@pytest.fixture(scope="module")
def model(history) -> fm.ForecastModel:
    return fm.train(history, log_to_mlflow=False)


START_BALANCE = 24_000.0


def base_forecast(model, history, **kw) -> pd.DataFrame:
    return liq.forecast_with(model=model, transactions=history, start_balance=START_BALANCE, **kw)


def june_trough(fc: pd.DataFrame) -> float:
    return float(fc.loc["2026-06-01":"2026-07-10", "balance"].min())


# -- buffer ------------------------------------------------------------------------------------------------
def test_buffer_defaults_to_one_month_of_fixed_costs(history):
    assert liq.default_buffer(history) == SYNTHETIC_FIXED
    assert liq.fixed_costs_monthly(history) == SYNTHETIC_FIXED
    no_fixed = history[~history["category"].isin(liq.FIXED_COST_CATEGORIES)]
    with pytest.raises(ValueError):
        liq.default_buffer(no_fixed)


def test_buffer_is_configurable_and_taken_from_the_forecast(model, history):
    fc = base_forecast(model, history)
    assert fc.attrs["default_buffer"] == float(SYNTHETIC_FIXED)
    assert liq.detect_gaps(fc) == []  # buffer from the forecast
    gaps = liq.detect_gaps(fc, buffer=Decimal("45000"))  # a bigger cushion is breached
    assert gaps and all(g.buffer == Decimal("45000.00") for g in gaps)
    assert liq.detect_gaps(fc, transactions=history) == []  # or from the transactions
    bare = fc.copy()
    bare.attrs.clear()
    with pytest.raises(ValueError, match="no buffer"):
        liq.detect_gaps(bare)


def test_gap_means_below_buffer_not_below_zero_and_equal_is_fine():
    idx = pd.date_range("2026-06-01", periods=6, name="date")
    df = pd.DataFrame({"balance": [20_000, 17_000, 17_700, 5_000, 17_700, 18_000]}, index=idx)
    gaps = liq.detect_gaps(df, buffer=17_700)  # 17,700 is exactly the buffer: no gap
    assert [(g.start, g.end) for g in gaps] == [(date(2026, 6, 2), date(2026, 6, 2)),
                                                (date(2026, 6, 4), date(2026, 6, 4))]
    deep = gaps[1]
    assert deep.lowest_balance == Decimal("5000.00") and deep.shortfall == Decimal("12700.00")
    assert deep.recovery_date == date(2026, 6, 5) and deep.month == "2026-06"
    assert all(g.lowest_balance > 0 for g in gaps)  # nothing near zero: still gaps


# -- base data: no gap -----------------------------------------------------------------------------------------
def test_no_gap_on_base_data_and_the_june_dip_is_visible(model, history):
    fc = base_forecast(model, history)
    assert liq.detect_gaps(fc) == []
    june_net = fc.loc["2026-06-01":"2026-06-30", "predicted_net"].sum()
    assert june_net < 0 and june_trough(fc) > float(SYNTHETIC_FIXED)


def test_review_without_gap_proposes_no_credit_line(model, history):
    review = liq.review_liquidity(model=model, transactions=history, start_balance=START_BALANCE)
    assert review.gaps == [] and review.buffer == SYNTHETIC_FIXED
    assert all(p.action_type != liq.CREDIT_LINE for p in review.proposals)


# -- what-if ------------------------------------------------------------------------------------------------
def test_forecast_with_nothing_equals_the_base_forecast(model, history):
    plain = fm.liquidity_forecast(liq.DEFAULT_HORIZON_DAYS, START_BALANCE, model=model, transactions=history)
    same = base_forecast(model, history)
    assert same["balance"].tolist() == pytest.approx(plain["balance"].tolist())
    assert same["what_if"].eq(0).all()


def test_what_if_outflow_shifts_the_balance_from_its_day_on(model, history):
    base = base_forecast(model, history)
    oven = liq.Outflow(on=date(2026, 3, 15), amount=Decimal("25000"), label="oven")
    what_if = base_forecast(model, history, extra_outflows=[oven])
    diff = (what_if["balance"] - base["balance"]).round(6)
    assert diff[: "2026-03-14"].eq(0).all() and diff["2026-03-15":].eq(-25_000.0).all()
    assert what_if.loc["2026-03-15", "what_if"] == -25_000.0
    assert what_if.attrs["outflows"] == [oven]
    for alt in ([(date(2026, 3, 15), 25000, "oven")], [{"on": date(2026, 3, 15), "amount": "25000.00"}]):
        again = base_forecast(model, history, extra_outflows=alt)
        assert again["balance"].tolist() == pytest.approx(what_if["balance"].tolist())
    two = base_forecast(model, history, extra_outflows=[(date(2026, 3, 15), 10_000), (date(2026, 3, 15), 15_000)])
    assert two["balance"].tolist() == pytest.approx(what_if["balance"].tolist())


def test_what_if_rejects_outflows_outside_the_horizon_or_with_wrong_sign(model, history):
    with pytest.raises(ValueError, match="outside the forecast horizon"):
        base_forecast(model, history, extra_outflows=[(date(2027, 12, 1), 100)])
    with pytest.raises(ValueError, match="outside the forecast horizon"):
        base_forecast(model, history, extra_outflows=[(date(2025, 11, 1), 100)])
    with pytest.raises(ValueError, match="positive"):
        base_forecast(model, history, extra_outflows=[(date(2026, 3, 15), -100)])


@pytest.fixture(scope="module")
def oven_review(model, history):
    """An oven bought in May that is just big enough to break the buffer in the June shutdown."""
    base = base_forecast(model, history)
    oven = (Decimal(str(june_trough(base))) - SYNTHETIC_FIXED + Decimal("5000")).quantize(Decimal("1"))
    outflow = liq.Outflow(on=date(2026, 5, 15), amount=oven, label="oven")
    review = liq.review_liquidity([outflow], model=model, transactions=history, start_balance=START_BALANCE)
    return review, outflow


def test_oven_purchase_creates_a_june_gap_and_a_credit_line_proposal(oven_review):
    review, outflow = oven_review
    assert review.gaps, "the oven should break the safety buffer"
    gap = max(review.gaps, key=lambda g: g.shortfall)
    assert date(2026, 6, 1) <= gap.start <= date(2026, 7, 10) and gap.lowest_balance < review.buffer
    assert gap.shortfall == review.buffer - gap.lowest_balance

    (proposal,) = review.proposals
    assert proposal.action_type == liq.CREDIT_LINE and proposal.level == PolicyLevel.APPROVAL
    assert proposal.needs_approval and "credit line" in proposal.policy_reason
    # amount covers the shortfall with a reserve, in whole thousands, as Decimal EUR
    assert isinstance(proposal.amount, Decimal)
    assert proposal.amount >= gap.shortfall * Decimal("1.2") and proposal.amount % 1000 == 0
    assert proposal.amount < gap.shortfall * Decimal("1.2") + 1000
    assert proposal.period_start < gap.start and proposal.period_end > gap.end
    assert proposal.month == gap.start.strftime("%Y-%m") == "2026-06"
    assert proposal.min_balance == gap.lowest_balance and proposal.buffer == SYNTHETIC_FIXED


def test_credit_line_reason_is_plain_language_with_drivers(oven_review):
    review, outflow = oven_review
    (proposal,) = review.proposals
    reason = proposal.reason
    assert "June 2026" in reason and "closed about 4 weeks" in reason
    assert "revenue drops by about" in reason and "fixed costs of 16,700 EUR per month continue" in reason
    assert f"oven {outflow.amount:,.0f} EUR on 2026-05-15" in reason
    assert "the summer shutdown" in reason  # the top SHAP driver in words, with its EUR contribution
    assert "safety buffer of 16,700 EUR" in reason and f"{proposal.amount:,.0f} EUR" in reason
    assert proposal.drivers and proposal.drivers[0]["feature"] == "shutdown_flag"
    assert proposal.drivers[0]["contribution_eur"] < 0
    assert proposal.importances and abs(sum(proposal.importances.values())) <= 1.0 + 1e-6
    assert "shutdown_flag" not in reason  # feature names are translated


def test_ceo_rule_can_lower_the_level_of_a_proposal(oven_review):
    review, _ = oven_review
    gaps = review.gaps
    rule = Rule(text="credit lines under my radar", level=PolicyLevel.NOTIFY, action_type="request_credit_line")
    proposal = liq.propose_credit_line(gaps, policy_context=PolicyContext(rules=[rule]))
    assert proposal.level == PolicyLevel.NOTIFY and not proposal.needs_approval
    with pytest.raises(ValueError):
        liq.propose_credit_line([])


def test_generic_reason_without_evidence_still_states_the_numbers(oven_review):
    review, _ = oven_review
    proposal = liq.propose_credit_line(review.gaps)
    assert "safety buffer of 16,700 EUR" in proposal.reason and proposal.drivers == []


# -- surplus -> investment ----------------------------------------------------------------------------------
def test_surplus_is_invested_so_that_june_stays_above_the_buffer(model, history):
    fc = base_forecast(model, history)
    surplus = liq.find_surplus(fc)
    assert surplus is not None and surplus.weak_month == "2026-06" and surplus.weak_month_net < 0
    assert surplus.amount % 500 == 0 and surplus.amount >= liq.MIN_INVESTMENT
    assert surplus.invest_on < date(2026, 6, 1)  # parked at the peak before the shutdown
    after = fc.loc[pd.Timestamp(surplus.invest_on):, "balance"] - float(surplus.amount)
    assert after.min() >= float(SYNTHETIC_FIXED)  # nothing after investing breaks the buffer ...
    assert after.loc["2026-06-01":"2026-06-30"].min() >= float(SYNTHETIC_FIXED)  # ... June included
    assert after.min() < float(SYNTHETIC_FIXED) + float(liq.INVESTMENT_STEP)  # ... and it is not undersized

    proposal = liq.propose_investment(surplus, liq.Evidence(model=model, transactions=history))
    assert proposal.action_type == liq.INVESTMENT and proposal.amount == surplus.amount
    assert proposal.level == PolicyLevel.APPROVAL and proposal.instrument == "money market fund"
    assert proposal.period_start == surplus.invest_on and proposal.period_end == date(2026, 6, 30)
    assert "June 2026" in proposal.reason and "closed about 4 weeks" in proposal.reason
    assert f"{surplus.amount:,.0f} EUR" in proposal.reason and "safety buffer of 16,700 EUR" in proposal.reason
    assert proposal.min_balance == surplus.lowest_balance - surplus.amount >= SYNTHETIC_FIXED


def test_review_of_the_base_case_proposes_the_investment(model, history):
    review = liq.review_liquidity(model=model, transactions=history, start_balance=START_BALANCE)
    (proposal,) = review.proposals
    assert proposal.action_type == liq.INVESTMENT and proposal.level == PolicyLevel.APPROVAL


def test_no_surplus_when_there_is_a_gap_or_no_headroom(model, history, oven_review):
    review, _ = oven_review
    assert liq.find_surplus(review.forecast) is None  # a gap: no investing
    fc = base_forecast(model, history)
    assert liq.find_surplus(fc, buffer=Decimal(str(june_trough(fc))) - Decimal("100")) is None  # < MIN_INVESTMENT
    flat = fc.copy()
    flat["predicted_net"] = 100.0  # no weak month ahead
    assert liq.find_surplus(flat) is None


def test_event_payload_matches_what_the_agent_consumes(oven_review):
    review, _ = oven_review
    (proposal,) = review.proposals
    event = proposal.to_event()
    p = event.payload
    assert event.kind == EventKind.METRIC and p["signal"] == "liquidity_forecast"
    assert p["requested_amount"] == str(proposal.amount) and p["action_type"] == "request_credit_line"
    assert p["month"] == "2026-06" and p["why"] == proposal.reason and Decimal(p["min_balance"]) < review.buffer


# -- the organizer's statement (D-013 numbers) ---------------------------------------------------------------
@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        txns = load_bank_csv(find_source_csv())
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    df = pd.DataFrame([{"booking_date": t.booking_date, "amount": float(t.amount), "category": t.category,
                        "team_id": t.team_id} for t in txns])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df[df["booking_date"] <= HISTORY_END].reset_index(drop=True)


@pytest.fixture(scope="module")
def statement_model(statement) -> fm.ForecastModel:
    return fm.train(statement, log_to_mlflow=False)


def test_statement_buffer_is_about_17_7k(statement):
    assert liq.default_buffer(statement) == Decimal("17730.00")


def test_statement_base_case_has_no_gap_and_proposes_a_june_safe_investment(statement, statement_model):
    review = liq.review_liquidity(model=statement_model, transactions=statement)
    assert review.gaps == []
    (proposal,) = review.proposals
    assert proposal.action_type == liq.INVESTMENT and proposal.level == PolicyLevel.APPROVAL
    assert Decimal("15000") <= proposal.amount <= Decimal("30000")
    june = review.forecast.loc["2026-06-01":"2026-06-30", "balance"].min()
    assert june - float(proposal.amount) >= float(review.buffer)
    assert "June 2026" in proposal.reason and "closed about 4 weeks" in proposal.reason


def test_statement_oven_in_march_breaks_the_buffer_in_june(statement, statement_model):
    oven = liq.Outflow(on=date(2026, 3, 15), amount=Decimal("30000"), label="oven")
    review = liq.review_liquidity([oven], model=statement_model, transactions=statement)
    june_gap = [g for g in review.gaps if g.start.month == 6 or g.lowest_date.month in (6, 7)]
    assert june_gap, "30k oven should break the 17.7k buffer around the June shutdown"
    (proposal,) = review.proposals
    assert proposal.action_type == liq.CREDIT_LINE and proposal.level == PolicyLevel.APPROVAL
    assert proposal.month == "2026-06" and Decimal("5000") <= proposal.amount <= Decimal("12000")
    assert "oven 30,000 EUR on 2026-03-15" in proposal.reason and "closed about 4 weeks" in proposal.reason


def test_statement_oven_of_25k_only_just_touches_the_buffer(statement, statement_model):
    """D-013 expected a clear breach for 25k; the model leaves the June trough ~0.2k below the buffer (2 July)."""
    oven = liq.Outflow(on=date(2026, 3, 15), amount=Decimal("25000"), label="oven")
    review = liq.review_liquidity([oven], model=statement_model, transactions=statement)
    assert sum(g.shortfall for g in review.gaps) < Decimal("1000")
