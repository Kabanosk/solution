"""T061: the forecast follows the current revenue level (feature sets `calendar` / `level`), leak-free."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from cfo.data.source import SourceNotFound
from cfo.forecast import compare as cmp
from cfo.forecast import evaluate as ev
from cfo.forecast import features as f
from cfo.forecast import model as m


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


def _stepped_history(start="2025-01-01", end="2025-12-31", step_day="2025-07-01", factor=1.25) -> pd.DataFrame:
    """Daily revenue 200 (weekend 300) that steps up by `factor` on `step_day`; food costs 30 % of revenue,
    payroll on the 28th, rent on the 1st."""
    rows: list[tuple] = []
    for day in pd.date_range(start, end):
        revenue = (300.0 if day.day_of_week >= 5 else 200.0) * (factor if day >= pd.Timestamp(step_day) else 1.0)
        rows.append((day, revenue, "revenue", None))
        rows.append((day, -0.3 * revenue, "food", "kitchen"))
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 1:
            rows.append((day, -1000.0, "rent", "front"))
    return _tx(rows)


def _monthly_net(frame: pd.DataFrame) -> pd.Series:
    daily = f.daily_cash_flow(frame)["net_cash_flow"]
    return daily.resample("MS").sum()


# -- the feature sets --------------------------------------------------------------------------------------
def test_calendar_set_is_unchanged_and_level_set_drops_the_year_memorising_columns():
    assert f.FEATURE_SETS["calendar"] == f.CALENDAR_COLUMNS == m.FEATURE_COLUMNS
    for name in ("level", "level_baseline"):
        columns = f.FEATURE_SETS[name]
        assert not set(columns) & {"month", "quarter", "day_of_year", "week_of_year"}
        assert {"weekday", "day_of_month", "shutdown_flag", "is_public_holiday", "payroll_due",
                "days_to_month_end"} <= set(columns)
    assert set(f.LEVEL_COLUMNS) <= set(f.FEATURE_SETS["level"])  # the raw levels are inputs of `level` only
    with pytest.raises(ValueError, match="unknown feature_set"):
        m.train(_stepped_history(), feature_set="nope", log_to_mlflow=False)


# -- leak-free level features ------------------------------------------------------------------------------
def test_a_booking_after_day_d_does_not_change_the_level_features_of_day_d():
    base = _stepped_history(end="2025-09-30")
    day = pd.Timestamp("2025-08-15")
    later = _tx([(day + pd.Timedelta(days=k), amount, category, None)
                 for k, amount, category in [(0, 5000.0, "revenue"), (0, -900.0, "food"),
                                              (3, 9999.0, "revenue"), (20, -7000.0, "beverages")]])
    dates = pd.date_range("2025-03-01", "2025-08-15")
    a = f.recent_levels(base, dates)
    b = f.recent_levels(pd.concat([base, later], ignore_index=True), dates)
    pd.testing.assert_frame_equal(a, b)  # even the bookings of day D itself and of D+3 / D+20 change nothing
    assert not f.recent_levels(pd.concat([base, later], ignore_index=True), [day + pd.Timedelta(days=1)]).equals(
        f.recent_levels(base, [day + pd.Timedelta(days=1)]))  # the next day does see D's booking


def test_training_rows_never_see_bookings_after_the_row():
    base = _stepped_history()
    changed = base.copy()
    last = changed["booking_date"] > pd.Timestamp("2025-12-20")
    changed.loc[last & (changed["category"] == "revenue"), "amount"] *= 3
    ta = m._training_table(base, m.EXCLUDED_CATEGORIES, "level")
    tb = m._training_table(changed, m.EXCLUDED_CATEGORIES, "level")
    upto = ta.loc[:"2025-12-20", f.LEVEL_COLUMNS]
    pd.testing.assert_frame_equal(upto, tb.loc[:"2025-12-20", f.LEVEL_COLUMNS])


def test_the_forecast_holds_the_level_at_the_origin_constant():
    history = _stepped_history()
    future = f.recent_levels(history, pd.date_range("2026-01-01", "2026-03-31"))
    assert (future.nunique() == 1).all()
    revenue = f.daily_cash_flow(history)["revenue"]
    open_days = revenue[revenue > 0]
    assert future["recent_revenue_28d"].iloc[0] == pytest.approx(open_days.iloc[-28:].mean())
    assert future["recent_revenue_90d"].iloc[0] == pytest.approx(open_days.iloc[-90:].mean())
    food = -history[history["category"] == "food"].groupby("booking_date")["amount"].sum()
    assert future["recent_variable_cost_28d"].iloc[0] == pytest.approx(food.iloc[-28:].mean())


def test_closed_days_at_the_end_do_not_drag_the_level_to_zero():
    history = _stepped_history(end="2025-12-31")
    history = history[~history["booking_date"].between("2025-12-10", "2025-12-31")
                      | ~history["category"].isin(["revenue", "food"])]  # closed (no revenue) for 22 days
    level = f.recent_levels(history, ["2026-01-05"])["recent_revenue_28d"].iloc[0]
    assert level == pytest.approx(1.25 * (5 * 200 + 2 * 300) / 7, rel=1e-6)  # the open-day level, four whole weeks


def test_level_is_nan_until_a_week_of_open_days_exists():
    levels = f.recent_levels(_stepped_history(end="2025-01-31"), pd.date_range("2025-01-01", "2025-01-12"))
    assert levels["recent_revenue_28d"].iloc[:7].isna().all() and levels["recent_revenue_28d"].iloc[7:].notna().all()


# -- the model ---------------------------------------------------------------------------------------------
def test_train_level_features_and_predict_features_are_built_the_same_way():
    history = _stepped_history()
    model = m.train(history, feature_set="level", log_to_mlflow=False, holdout_days=0)
    assert model.feature_set == "level" and set(model.level_values) == set(f.LEVEL_COLUMNS)
    table = m._training_table(history, m.EXCLUDED_CATEGORIES, "level")
    first_future = model.features_for(["2026-01-01"])
    # a day right after the history sees the same window a training row of that day would
    assert first_future[f.LEVEL_COLUMNS].iloc[0].tolist() == pytest.approx(
        f.recent_levels(history, ["2026-01-01"]).iloc[0].tolist())
    assert list(first_future.columns) == model.feature_columns == f.feature_set_columns("level")
    assert set(f.LEVEL_COLUMNS) <= set(table.columns)
    # the baseline set carries the same level values (for its offset) although it does not feed them to the booster
    base = m.train(history, feature_set="level_baseline", log_to_mlflow=False, holdout_days=0)
    assert base.level_values == pytest.approx(model.level_values) and base.feature_columns == f.FEATURE_SETS[
        "level_baseline"]
    assert base.baseline_mean > 0


def test_old_metadata_without_the_new_fields_loads_as_the_calendar_set_with_identical_predictions(tmp_path):
    history = _stepped_history()
    model = m.train(history, feature_set="calendar", log_to_mlflow=False)
    meta = model.meta()
    assert meta["feature_set"] == "calendar" and meta["feature_columns"] == f.CALENDAR_COLUMNS
    old = {k: v for k, v in meta.items() if k not in ("feature_set", "level_values", "baseline_mean")}
    restored = m.ForecastModel.from_meta(model.regressor, old)
    days = pd.date_range("2026-01-01", "2026-07-31")
    assert restored.feature_set == "calendar" and restored.level_values == {}
    assert restored.predict(days).tolist() == model.predict(days).tolist()
    # and on disk
    path = model.save(tmp_path / "old.json")
    sidecar = tmp_path / "old.json.meta.json"
    sidecar.write_text(json.dumps(old), encoding="utf-8")
    assert m.ForecastModel.load(path).predict(days).tolist() == model.predict(days).tolist()


@pytest.mark.parametrize("name", ["level", "level_baseline"])
def test_level_model_survives_save_load_and_meta_roundtrip(tmp_path, name):
    model = m.train(_stepped_history(), feature_set=name, log_to_mlflow=False)
    loaded = m.ForecastModel.load(model.save(tmp_path / "level.json"))
    days = pd.date_range("2026-01-01", "2026-06-30")
    assert loaded.feature_set == name and loaded.level_values == pytest.approx(model.level_values)
    assert loaded.predict(days).tolist() == pytest.approx(model.predict(days).tolist())
    again = m.ForecastModel.from_meta(model.regressor, json.loads(json.dumps(model.meta())))
    assert again.predict(days).tolist() == pytest.approx(model.predict(days).tolist())


def test_the_default_feature_set_is_a_level_set_and_train_uses_it():
    assert m.DEFAULT_FEATURE_SET != "calendar" and m.DEFAULT_FEATURE_SET in f.FEATURE_SETS
    assert f.needs_levels(m.DEFAULT_FEATURE_SET)
    model = m.train(_stepped_history(), log_to_mlflow=False, holdout_days=0)
    assert model.feature_set == m.DEFAULT_FEATURE_SET


def test_explain_names_the_level_features_readably():
    model = m.train(_stepped_history(), feature_set="level", log_to_mlflow=False)
    contrib = model.contributions(pd.date_range("2026-01-01", "2026-01-10"))
    assert {"recent_revenue_28d", "recent_variable_cost_90d"} <= set(contrib.columns)
    why = model.explain("2026-01-01", "2026-01-31", top=20)
    assert why["top_drivers"] and why["days"] == 31
    assert set(model.feature_importances().index) == set(f.feature_set_columns("level"))


def test_explain_shows_the_recent_level_as_a_driver_of_the_baseline_set():
    model = m.train(_stepped_history(), feature_set="level_baseline", log_to_mlflow=False)
    days = pd.date_range("2026-01-01", "2026-01-31")
    contrib = model.contributions(days)
    assert f.BASELINE_COLUMN == "recent_level_baseline" and f.BASELINE_COLUMN in contrib.columns
    assert contrib.sum(axis=1).tolist() == pytest.approx(model.predict(days).tolist(), abs=1e-2)
    why = model.explain("2026-01-01", "2026-01-31", top=3)
    assert why["top_drivers"][0]["feature"] == "recent_level_baseline"  # the level is what carries the forecast
    assert why["predicted_net_eur"] == pytest.approx(model.predict(days).sum(), abs=1.0)


@pytest.mark.parametrize("name", ["level", "level_baseline"])
def test_holdout_backtest_works_for_level_sets(name):
    model = m.train(_stepped_history(), feature_set=name, log_to_mlflow=False)
    assert {"holdout_mae", "holdout_total_error"} <= set(model.metrics)
    _, test = m.backtest_split(_stepped_history(), 60, m.EXCLUDED_CATEGORIES, feature_set=name)
    assert set(f.feature_set_columns(name)) <= set(test.columns)
    assert test[f.LEVEL_COLUMNS].nunique().eq(1).all()  # held at the origin over the whole holdout


# -- the step ----------------------------------------------------------------------------------------------
def test_synthetic_step_is_followed_by_the_level_baseline_set_and_missed_by_the_calendar_set():
    history = _stepped_history()
    truth = _monthly_net(_stepped_history(start="2025-01-01", end="2026-06-30"))["2026-01-01":"2026-06-01"]
    days = pd.date_range("2026-01-01", "2026-06-30")
    errors = {}
    for name in ("level_baseline", "calendar"):
        model = m.train(history, feature_set=name, log_to_mlflow=False, holdout_days=0)
        predicted = model.predict(days).resample("MS").sum()
        errors[name] = ((predicted - truth).abs() / truth.abs()).max()
    assert errors["level_baseline"] < 0.10, errors
    assert errors["calendar"] > 0.10, errors


# -- the organizer's statement -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        return ev.load_statement()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")


@pytest.fixture(scope="module")
def level_model(statement) -> m.ForecastModel:
    return m.train(statement, feature_set="level_baseline", log_to_mlflow=False, holdout_days=0)


def test_statement_forecast_follows_the_current_level(level_model):
    nets = level_model.predict(pd.date_range("2026-01-01", "2026-07-31")).resample("MS").sum()
    jan_may = nets["2026-01-01":"2026-05-01"]
    assert jan_may.mean() >= 9_000, nets.round().to_dict()
    assert nets.idxmin() == pd.Timestamp("2026-06-01") and nets["2026-06-01"] < -10_000, nets.round().to_dict()


def test_statement_december_daily_mae_with_the_level_set(statement):
    folds = ev.rolling_origin(statement, lambda tx: m.train(tx, feature_set="level_baseline", log_to_mlflow=False,
                                                            holdout_days=0),
                              origins=["2025-11-30"], horizon_days=31)
    assert float(np.abs(folds["error"]).mean()) <= 240, float(np.abs(folds["error"]).mean())


def test_compare_scores_every_feature_set_and_names_the_best(statement):
    result = cmp.compare(statement)
    assert set(result["scores"]) == set(f.FEATURE_SETS)
    best = min(result["scores"], key=lambda k: result["scores"][k]["balance_error_90d"])
    assert result["best_feature_set"] == best
    assert result["best_feature_set"] == m.DEFAULT_FEATURE_SET, "the default of train() must be the best set"
    assert result["best_feature_set"] != "calendar"


# -- review round 1 ----------------------------------------------------------------------------------------
def test_default_feature_set_falls_back_to_calendar_when_the_levels_cannot_be_built(caplog):
    short = _stepped_history(start="2025-12-01", end="2025-12-10")  # 10 days: too short for a level model
    with caplog.at_level("WARNING"):
        model = m.train(short, log_to_mlflow=False)
    assert model.feature_set == "calendar" and model.level_values == {}
    assert "calendar" in caplog.text
    fc = m.liquidity_forecast(5, model=model, transactions=short)
    assert len(fc) == 5 and fc["predicted_net"].notna().all()
    with pytest.raises(ValueError, match="level"):  # an explicit request is not silently changed
        m.train(short, feature_set="level_baseline", log_to_mlflow=False)


def test_a_history_that_is_closed_in_every_training_row_falls_back_and_never_gives_nan_contributions():
    # revenue only in the first 9 days, then a long closed run: every row that has a level lies in a shutdown
    rows = [(d, 200.0, "revenue", None) for d in pd.date_range("2025-01-01", "2025-01-09")]
    rows += [(d, -50.0, "food", "kitchen") for d in pd.date_range("2025-01-01", "2025-03-31")]
    history = _tx(rows)
    model = m.train(history, log_to_mlflow=False)
    assert model.feature_set == "calendar"
    why = model.explain("2025-04-01", "2025-04-10")
    assert all(np.isfinite(d["contribution_eur"]) for d in why["top_drivers"])
    with pytest.raises(ValueError):
        m.train(history, feature_set="level_baseline", log_to_mlflow=False)


def _raised(history: pd.DataFrame, days: int, factor: float) -> pd.DataFrame:
    """`history` plus `days` more days whose revenue and food cost are `factor` times the last level."""
    last = history["booking_date"].max()
    extra = _stepped_history(start=str(last.date() + pd.Timedelta(days=1)),
                             end=str(last.date() + pd.Timedelta(days=days)), step_day="2000-01-01", factor=1.25 * factor)
    return pd.concat([history, extra], ignore_index=True)


@pytest.mark.parametrize("name", ["level_baseline", "level"])
def test_liquidity_forecast_refreshes_the_level_from_the_current_bookings(name):
    history = _stepped_history()
    model = m.train(history, feature_set=name, log_to_mlflow=False, holdout_days=0)
    stored = dict(model.level_values)
    same = m.liquidity_forecast(60, model=model, transactions=history, scheduled_payments=[])
    assert same["predicted_net"].tolist() == model.predict(same.index).tolist()  # unchanged history: identical
    longer = _raised(history, 60, 1.30)
    fc = m.liquidity_forecast(60, model=model, transactions=longer, scheduled_payments=[])
    assert model.level_values == stored  # the stored model is not mutated
    levels = f.recent_levels(longer, [longer["booking_date"].max() + pd.Timedelta(days=1)]).iloc[0]
    assert levels["recent_revenue_28d"] > 1.25 * stored["recent_revenue_28d"]
    if name == "level_baseline":  # the baseline carries the level one to one
        assert fc["predicted_net"].sum() > same["predicted_net"].sum() * 1.15
    refreshed = model.with_levels_from(longer)
    assert refreshed is not model and refreshed.regressor is model.regressor
    assert refreshed.level_values == pytest.approx(levels.to_dict())


def test_forecast_with_and_review_use_the_refreshed_level():
    from cfo.forecast import liquidity as liq

    history = _stepped_history()
    model = m.train(history, feature_set="level_baseline", log_to_mlflow=False, holdout_days=0)
    longer = _raised(history, 60, 1.30)
    old = liq.forecast_with(model=model, transactions=history, days=30, start_balance=0.0)
    new = liq.forecast_with(model=model, transactions=longer, days=30, start_balance=0.0)
    assert new["predicted_net"].sum() > old["predicted_net"].sum() * 1.15
    review = liq.review_liquidity(model=model, transactions=longer, start_balance=0.0)
    assert review.forecast["predicted_net"].tolist() == pytest.approx(
        m.liquidity_forecast(len(review.forecast), 0.0, model=model, transactions=longer,
                             scheduled_payments=m.partner_tranche_schedule(longer))["predicted_net"].tolist())


def test_compare_materialises_a_one_shot_origins_iterable():
    history = _stepped_history()
    origins = (o for o in ["2025-08-31", "2025-09-30"])
    result = cmp.compare(history, ["calendar", "level_baseline"], origins=origins)
    assert result["scores"]["calendar"]["n_origins"] == result["scores"]["level_baseline"]["n_origins"] == 2
