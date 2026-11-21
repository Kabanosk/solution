"""T053: rolling-origin backtest that scores balances. Synthetic histories with a known answer; the organizer's
statement test is skipped without the CSV."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from cfo.data.source import SourceNotFound, find_source_csv, load_bank_csv
from cfo.forecast import evaluate as ev
from cfo.forecast import model as m

METRIC_KEYS = {"daily_mae", "daily_rmse", "naive_mae", "balance_error_30d", "balance_error_60d",
               "balance_error_90d", "balance_bias_90d", "monthly_net_mape", "n_origins"}


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


def _flat_history(start="2025-01-01", end="2025-12-31", daily=1000.0) -> pd.DataFrame:
    """One revenue booking per day, plus partner tranches on the 15th that every scorer must ignore."""
    rows = [(d, daily, "revenue", None) for d in pd.date_range(start, end)]
    rows += [(d, -4000.0, "partner_investment", None) for d in pd.date_range(start, end) if d.day == 15]
    return _tx(rows)


class _Stub:
    """Stands in for a ForecastModel: only `predict` and `excluded_categories` are used by the harness."""

    excluded_categories = m.EXCLUDED_CATEGORIES

    def __init__(self, fn):
        self.fn = fn

    def predict(self, dates):
        idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().rename("date")
        return pd.Series([self.fn(d) for d in idx], index=idx, name="predicted_net")


def _truth(tx: pd.DataFrame) -> pd.Series:
    net = tx[tx["category"] != "partner_investment"].groupby(tx["booking_date"].dt.normalize())["amount"].sum()
    return net.reindex(pd.date_range(net.index.min(), net.index.max()), fill_value=0.0)


def _perfect(tx: pd.DataFrame, offset: float = 0.0):
    truth = _truth(tx)
    return lambda _train: _Stub(lambda d: float(truth.get(d, 0.0)) + offset)


# -- origins and fold shape --------------------------------------------------------------------------------
def test_default_origins_are_month_ends_from_the_sixth_month_with_30_days_of_truth():
    tx = _flat_history()
    folds = ev.rolling_origin(tx, _perfect(tx))
    origins = sorted(folds["origin"].unique())
    assert [str(o.date()) for o in origins] == ["2025-06-30", "2025-07-31", "2025-08-31", "2025-09-30",
                                                "2025-10-31", "2025-11-30"]


def test_fold_columns_horizon_and_cumulative_error():
    tx = _flat_history()
    folds = ev.rolling_origin(tx, _perfect(tx, offset=-100.0), origins=["2025-06-30"], horizon_days=90)
    assert {"origin", "date", "horizon", "predicted_net", "actual_net", "error", "cum_error"} <= set(folds.columns)
    assert len(folds) == 90 and folds["horizon"].tolist() == list(range(1, 91))
    assert folds["date"].iloc[0] == pd.Timestamp("2025-07-01") and folds["date"].iloc[-1] == pd.Timestamp("2025-09-28")
    assert (folds["actual_net"] == 1000.0).all() and (folds["error"] == -100.0).all()  # tranches excluded
    assert folds["cum_error"].iloc[29] == pytest.approx(-3000.0)


def test_folds_are_truncated_where_the_history_ends():
    tx = _flat_history()
    folds = ev.rolling_origin(tx, _perfect(tx), origins=["2025-11-30"], horizon_days=90)
    assert len(folds) == 31 and folds["date"].iloc[-1] == pd.Timestamp("2025-12-31")


def test_no_usable_origin_is_an_error():
    tx = _flat_history(end="2025-03-31")
    with pytest.raises(ValueError):
        ev.rolling_origin(tx, _perfect(tx))


# -- the scores --------------------------------------------------------------------------------------------
def test_a_perfect_trainer_scores_zero():
    tx = _flat_history()
    s = ev.evaluate(tx, _perfect(tx))
    assert set(s) == METRIC_KEYS and s["n_origins"] == 6
    for key in METRIC_KEYS - {"n_origins", "naive_mae", "monthly_net_mape"}:
        assert s[key] == 0.0, key
    assert s["monthly_net_mape"] == 0.0
    assert s["naive_mae"] == 0.0  # flat history: the training mean is the truth


def test_a_trainer_100_eur_a_day_too_low_has_known_balance_errors():
    tx = _flat_history()
    s = ev.evaluate(tx, _perfect(tx, offset=-100.0))
    assert s["balance_error_30d"] == pytest.approx(3000.0)
    assert s["balance_error_60d"] == pytest.approx(6000.0)
    assert s["balance_error_90d"] == pytest.approx(9000.0)
    assert s["balance_bias_90d"] == pytest.approx(-9000.0)
    assert s["daily_mae"] == pytest.approx(100.0) and s["daily_rmse"] == pytest.approx(100.0)
    assert s["monthly_net_mape"] == pytest.approx(100.0 / 1000.0)  # 10 % of the monthly net, a fraction


def test_a_level_step_shows_a_much_larger_balance_error_than_daily_mae_suggests():
    rows = [(d, 1000.0 if d < pd.Timestamp("2025-07-01") else 1300.0, "revenue", None)
            for d in pd.date_range("2025-01-01", "2025-12-31")]
    tx = _tx(rows)

    def mean_trainer(train):  # predicts the training mean: blind to the step
        mean = _truth(train).mean()
        return _Stub(lambda d: mean)

    s = ev.evaluate(tx, mean_trainer)
    assert s["daily_mae"] < 300.0
    assert s["balance_error_90d"] > 40 * s["daily_mae"]  # errors of one sign accumulate
    assert s["balance_bias_90d"] < -s["daily_mae"] * 40


def test_naive_mae_uses_the_training_mean_of_each_origin():
    rows = [(d, 1000.0 if d < pd.Timestamp("2025-07-01") else 1300.0, "revenue", None)
            for d in pd.date_range("2025-01-01", "2025-12-31")]
    tx = _tx(rows)
    folds = ev.rolling_origin(tx, _perfect(tx), origins=["2025-06-30"], horizon_days=30)
    assert (folds["naive_net"] == 1000.0).all()
    assert ev.score(folds)["naive_mae"] == pytest.approx(300.0)


def test_score_of_no_folds_is_an_error():
    with pytest.raises(ValueError):
        ev.score(pd.DataFrame(columns=["origin", "date", "horizon", "predicted_net", "actual_net", "error",
                                       "cum_error", "naive_net"]))


# -- the target is owned by the scorer --------------------------------------------------------------------
def test_actuals_do_not_depend_on_the_excluded_categories_of_the_trained_model():
    tx = _flat_history()

    class _Greedy(_Stub):
        excluded_categories = ()  # a candidate that wants to score the tranches too

    class _Other(_Stub):
        excluded_categories = ("revenue",)

    a = ev.rolling_origin(tx, lambda _t: _Greedy(lambda d: 0.0), origins=["2025-08-31"], horizon_days=30)
    b = ev.rolling_origin(tx, lambda _t: _Other(lambda d: 0.0), origins=["2025-08-31"], horizon_days=30)
    assert a["actual_net"].tolist() == b["actual_net"].tolist() == [1000.0] * 30
    assert a["naive_net"].tolist() == b["naive_net"].tolist()


def test_the_caller_owns_the_excluded_categories():
    tx = _flat_history()
    folds = ev.rolling_origin(tx, _perfect(tx), origins=["2025-08-31"], horizon_days=30, exclude_categories=())
    sept15 = folds.loc[folds["date"] == pd.Timestamp("2025-09-15"), "actual_net"].iloc[0]
    assert sept15 == 1000.0 - 4000.0  # now the tranche is part of the target
    s = ev.evaluate(tx, _perfect(tx), origins=["2025-08-31"], horizon_days=30, exclude_categories=())
    assert set(s) == METRIC_KEYS


def test_repeated_and_unsorted_origins_are_deduplicated():
    tx = _flat_history()
    once = ev.rolling_origin(tx, _perfect(tx, offset=-100.0), origins=["2025-06-30", "2025-08-31"])
    messy = ev.rolling_origin(tx, _perfect(tx, offset=-100.0),
                              origins=["2025-08-31", "2025-06-30 00:00", pd.Timestamp("2025-08-31"), "2025-06-30"])
    pd.testing.assert_frame_equal(once, messy)
    assert not messy.duplicated(["origin", "date"]).any()
    assert ev.score(messy)["n_origins"] == 2


def test_bookings_with_a_time_of_day_score_like_bookings_at_midnight():
    midnight = _flat_history()
    at_nine = midnight.assign(booking_date=midnight["booking_date"] + pd.Timedelta(hours=9))
    low = -100.0
    a = ev.rolling_origin(midnight, _perfect(midnight, offset=low), origins=["2025-08-31"], horizon_days=30)
    b = ev.rolling_origin(at_nine, _perfect(midnight, offset=low), origins=["2025-08-31"], horizon_days=30)
    pd.testing.assert_frame_equal(a, b)
    assert (b["actual_net"] == 1000.0).all()
    assert ev.evaluate(at_nine, _perfect(midnight, offset=low)) == ev.evaluate(midnight, _perfect(midnight, offset=low))


# -- no leakage --------------------------------------------------------------------------------------------
def test_each_trainer_sees_only_bookings_up_to_its_origin():
    tx = _flat_history()
    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def spy(train):
        seen.append((train["booking_date"].min(), train["booking_date"].max()))
        return _Stub(lambda d: 0.0)

    folds = ev.rolling_origin(tx, spy, origins=["2025-06-30", "2025-09-15"])
    assert [str(hi.date()) for _, hi in seen] == ["2025-06-30", "2025-09-15"]
    assert sorted(folds["origin"].unique()) == [pd.Timestamp("2025-06-30"), pd.Timestamp("2025-09-15")]


def test_changing_bookings_after_an_origin_does_not_change_that_folds_prediction():
    origin = "2025-08-31"
    base = _flat_history()
    # after the origin: a revenue gap (would be a learnable shutdown), a tax habit (due days) and a level shift
    after = base["booking_date"] > pd.Timestamp(origin)
    changed = base.copy()
    changed.loc[after & (changed["booking_date"].dt.month == 10) & (changed["category"] == "revenue"), "amount"] = 0.0
    changed = pd.concat([changed, _tx([(d, -700.0, "tax", None) for d in ("2025-09-12", "2025-10-12", "2025-11-12",
                                                                       "2025-12-12")])], ignore_index=True)
    a = ev.rolling_origin(base, lambda tx: m.train(tx, log_to_mlflow=False, holdout_days=0),
                          origins=[origin], horizon_days=90)
    b = ev.rolling_origin(changed, lambda tx: m.train(tx, log_to_mlflow=False, holdout_days=0),
                          origins=[origin], horizon_days=90)
    assert len(a) == len(b) == 90
    assert a["predicted_net"].tolist() == b["predicted_net"].tolist()
    assert a["actual_net"].tolist() != b["actual_net"].tolist()  # only the truth differs


# -- the real model on synthetic data -----------------------------------------------------------------------
def test_default_trainer_is_the_model_and_does_not_log_to_mlflow(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://nowhere.invalid:5000")
    tx = _flat_history()
    s = ev.evaluate(tx, origins=["2025-08-31"], horizon_days=30)
    assert set(s) == METRIC_KEYS and s["n_origins"] == 1
    assert s["daily_mae"] < 50.0  # flat series: the model finds it
    assert math.isnan(s["balance_error_60d"]) and math.isnan(s["balance_error_90d"])  # the fold is only 30 days


# -- the organizer's statement ------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        df = ev.load_statement()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    return df


def test_statement_baseline_scores_are_finite_and_use_december(statement):
    assert statement["booking_date"].max() == pd.Timestamp("2025-12-31")
    s = ev.evaluate(statement)
    assert set(s) == METRIC_KEYS and s["n_origins"] == 6
    assert all(np.isfinite(v) for v in s.values())
    assert s["daily_mae"] < s["naive_mae"]


def test_cli_prints_json_with_the_balance_error(statement, capsys):
    ev.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert set(out) == METRIC_KEYS
    assert "balance_error_90d" in out
