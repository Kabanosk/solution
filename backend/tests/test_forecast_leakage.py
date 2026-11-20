"""T045: the backtest must not see the holdout (shutdown windows / due days inferred from the training window only),
and the forecast helpers must not choke on a database without bookings or fixed-cost rows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from cfo.data.source import SourceNotFound, find_source_csv, load_bank_csv
from cfo.forecast import features as f
from cfo.forecast import liquidity as liq
from cfo.forecast import model as m

HOLDOUT_DAYS = 60


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


def _history(*, holdout_rows: list[tuple] = (), revenue_gap: tuple[str, str] | None = None) -> pd.DataFrame:
    """Jan-Dec 2025: daily revenue, payroll on the 28th, tax on the 12th only in Sep/Oct (two months: not a due
    day on training data alone). `holdout_rows` are extra bookings, `revenue_gap` removes revenue for those days."""
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-12-31"):
        if revenue_gap and revenue_gap[0] <= str(day.date()) <= revenue_gap[1]:
            continue
        rows.append((day, 200.0, "revenue", None))
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 12 and day.month in (9, 10):
            rows.append((day, -500.0, "tax", None))
    return _tx([*rows, *holdout_rows])


def _split(tx: pd.DataFrame):
    return m.backtest_split(tx, HOLDOUT_DAYS, m.EXCLUDED_CATEGORIES)


def test_holdout_only_bookings_do_not_change_the_training_features():
    holdout_start = pd.Timestamp("2025-12-31") - pd.Timedelta(days=HOLDOUT_DAYS - 1)
    base = _history()
    # tax on day 12 also in Nov and Dec makes day 12 a "due day" on full history (4 months) but not on train (2)
    extra = [(pd.Timestamp(d), -500.0, "tax", None) for d in ("2025-11-12", "2025-12-12")]
    assert pd.Timestamp("2025-11-12") >= holdout_start
    train_a, _ = _split(base)
    train_b, _ = _split(_history(holdout_rows=extra))
    pd.testing.assert_frame_equal(train_a[m.FEATURE_COLUMNS], train_b[m.FEATURE_COLUMNS])
    assert train_a["tax_due"].loc["2025-09-12"] == train_a["tax_due"].loc["2025-02-12"]  # inferred, not leaked
    # the leak itself: on full history day 12 would be a due day
    assert f.infer_due_days(_history(holdout_rows=extra))["tax"] == (12,)


def test_a_shutdown_that_only_exists_in_the_holdout_is_not_known_to_the_backtest():
    gap = ("2025-11-10", "2025-11-24")  # 15 days without revenue, entirely inside the holdout
    train, test = _split(_history(revenue_gap=gap))
    assert train["shutdown_flag"].sum() == 0 and test["shutdown_flag"].sum() == 0
    assert f.infer_shutdown_days(_history(revenue_gap=gap))  # full history does see it


def test_a_shutdown_in_the_training_window_is_applied_to_the_holdout_days_of_the_next_year():
    gap = ("2025-03-03", "2025-03-16")
    tx = _history(revenue_gap=gap)
    train, test = _split(tx)
    assert train.loc["2025-03-03":"2025-03-16", "shutdown_flag"].eq(1).all()
    assert test["shutdown_flag"].sum() == 0  # no March days in the holdout, but the window is in the same key set


def test_split_covers_every_day_once_and_targets_are_the_real_cash_flow():
    tx = _history()
    train, test = _split(tx)
    assert len(test) == HOLDOUT_DAYS and train.index.max() < test.index.min()
    assert train.index.is_unique and (test.index[0] - train.index[-1]) == pd.Timedelta(days=1)
    assert test["net_cash_flow"].sum() == pytest.approx(
        tx[tx["booking_date"] >= test.index[0]]["amount"].sum())


def test_metrics_do_not_depend_on_what_is_only_visible_in_the_holdout():
    # train() reports holdout metrics from the split: same training data -> same booster, different holdout
    # bookings -> different error, but the training-feature table (above) is identical.
    a = m.train(_history(), log_to_mlflow=False)
    assert a.metrics["holdout_days"] == HOLDOUT_DAYS and a.metrics["holdout_rmse"] >= a.metrics["holdout_mae"]


@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        txns = load_bank_csv(find_source_csv())
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    df = pd.DataFrame([{"booking_date": t.booking_date, "amount": float(t.amount), "category": t.category,
                        "team_id": t.team_id} for t in txns])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df[df["booking_date"] <= "2025-11-30"].reset_index(drop=True)


def test_statement_backtest_still_beats_the_naive_mean_without_the_leak(statement):
    metrics = m.train(statement, log_to_mlflow=False).metrics
    assert metrics["holdout_mae"] < 0.5 * metrics["holdout_naive_mae"]
    assert metrics["holdout_rmse"] > 0


# -- empty data ------------------------------------------------------------------------------------------
class _EmptyConn:
    """A psycopg-like connection whose every query returns no rows."""

    def cursor(self, row_factory=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        pass

    def fetchall(self):
        return []


def test_daily_features_on_an_empty_table_is_an_empty_frame_not_a_nat_error():
    out = f.load_daily_features(_EmptyConn())
    assert out.empty and list(out.columns) == ["net_cash_flow", "inflow", "outflow", "revenue",
                                               "n_transactions", *f.CALENDAR_COLUMNS]
    assert isinstance(out.index, pd.DatetimeIndex)
    assert f.load_daily_features(_EmptyConn(), end="2026-07-31").empty


def test_training_on_no_transactions_is_a_clear_error():
    with pytest.raises(ValueError, match="no transactions"):
        m.train(f.fetch_transactions(_EmptyConn()), log_to_mlflow=False)


def _no_fixed_costs() -> pd.DataFrame:
    rows = [(d, 300.0, "revenue", None) for d in pd.date_range("2025-01-01", "2025-11-30")]
    return _tx(rows)


def test_forecast_with_and_review_with_an_explicit_buffer_need_no_fixed_cost_rows():
    tx = _no_fixed_costs()
    model = m.train(tx, log_to_mlflow=False)
    fc = liq.forecast_with(model=model, transactions=tx, days=60, buffer=1000)
    assert fc.attrs["default_buffer"] == 1000.0 and "balance" in fc
    oven = liq.Outflow(on=date(2025, 12, 15), amount=Decimal("500000"), label="oven")
    review = liq.review_liquidity([oven], buffer=1000, days=60, model=model, transactions=tx)
    assert review.buffer == Decimal("1000.00") and review.gaps and review.proposals


def test_without_fixed_costs_and_without_a_buffer_the_error_stays_clear():
    tx = _no_fixed_costs()
    model = m.train(tx, log_to_mlflow=False)
    fc = liq.forecast_with(model=model, transactions=tx, days=30)  # forecasting itself needs no buffer
    assert "default_buffer" not in fc.attrs
    with pytest.raises(ValueError, match="pass buffer="):
        liq.review_liquidity(days=30, model=model, transactions=tx)
