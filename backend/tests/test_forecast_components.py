"""T068: the `components` feature set: fixed costs as an inferred schedule plus a learned variable part."""

from __future__ import annotations

import json

import pandas as pd
import pytest
from cfo.data.source import SourceNotFound
from cfo.forecast import evaluate as ev
from cfo.forecast import features as f
from cfo.forecast import folds
from cfo.forecast import model as m
from cfo.forecast import schedule as sc

TAX = {0: [(10, -880.0)], 1: [(10, -880.0), (15, -225.0)], 2: [(10, -1150.0), (10, -880.0)]}  # by month % 3


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


def fixed_rows(start: str, end: str, *, rent: float = -1000.0, rent_from: str | None = None) -> list[tuple]:
    """A known fixed-cost calendar: payroll on the 28th, rent on the 1st, utilities on the 10th, social security on
    the 27th, tax by month position in the quarter (880 / 1105 / 2030, as on the organizer's statement)."""
    rows: list[tuple] = []
    for day in pd.date_range(start, end):
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 1:
            amount = rent if rent_from is None or day >= pd.Timestamp(rent_from) else -1000.0
            rows.append((day, amount, "rent", "front"))
        if day.day == 10:
            rows.append((day, -150.0, "utilities", None))
        if day.day == 27:
            rows.append((day, -600.0, "social_security", None))
        for d, amount in TAX[(day.year * 12 + day.month - 1) % 3]:
            if day.day == d:
                rows.append((day, amount, "tax", None))
    return rows


def history(start="2025-01-01", end="2025-12-31", *, step_day="2025-07-01", factor=1.25, rent=-1000.0,
            rent_from=None, partner=False, partner_until="2025-12-31") -> pd.DataFrame:
    """Revenue 200 (weekend 300) that steps by `factor` on `step_day`, food 30 % of revenue, the fixed calendar."""
    rows = fixed_rows(start, end, rent=rent, rent_from=rent_from)
    for day in pd.date_range(start, end):
        revenue = (300.0 if day.day_of_week >= 5 else 200.0) * (factor if day >= pd.Timestamp(step_day) else 1.0)
        rows.append((day, revenue, "revenue", None))
        rows.append((day, -0.3 * revenue, "food", "kitchen"))
        if partner and day.day == 15 and day <= pd.Timestamp(partner_until):
            rows.append((day, -4000.0, "partner_investment", None))
    return _tx(rows)


def truth_for(dates, start="2024-01-01", end="2027-12-31") -> pd.Series:
    """Net fixed-cost flow of the synthetic calendar for `dates`."""
    frame = _tx(fixed_rows(start, end))
    daily = frame.groupby("booking_date")["amount"].sum()
    return daily.reindex(pd.DatetimeIndex(dates), fill_value=0.0)


# -- schedule inference ------------------------------------------------------------------------------------
def test_schedule_reproduces_a_synthetic_fixed_cost_calendar_exactly():
    spec = sc.infer(history(end="2025-12-31"))
    dates = pd.date_range("2026-01-01", "2026-12-31")
    assert sc.total(spec, dates).tolist() == truth_for(dates).tolist()
    by_category = sc.amounts(spec, dates)
    assert set(by_category.columns) == {"payroll", "rent", "utilities", "social_security", "tax"}
    assert by_category["tax"].groupby(by_category.index.month).sum().tolist() == [
        -880.0, -1105.0, -2030.0, -880.0, -1105.0, -2030.0, -880.0, -1105.0, -2030.0, -880.0, -1105.0, -2030.0]


def test_schedule_detects_the_quarterly_period_and_monthly_costs():
    cats = sc.infer(history(end="2025-12-31"))["categories"]
    assert cats["tax"]["period"] == 3
    assert {cats[c]["period"] for c in ("payroll", "rent", "utilities", "social_security")} == {1}


def test_schedule_needs_only_a_few_months_to_see_the_quarter():
    short = history(end="2025-05-31")  # Jan-May: one repetition of the quarterly pattern
    spec = sc.infer(short)
    dates = pd.date_range("2025-06-01", "2025-12-31")
    assert sc.total(spec, dates).tolist() == truth_for(dates).tolist()


def test_the_schedule_is_leak_free():
    base = history(end="2025-09-30")
    origin = pd.Timestamp("2025-08-31")
    seen = base[base["booking_date"] <= origin]
    changed = base.copy()
    later = changed["booking_date"] > origin
    changed.loc[later & (changed["category"] == "rent"), "amount"] = -9999.0
    changed.loc[later & (changed["category"] == "payroll"), "booking_date"] += pd.Timedelta(days=2)
    spec_seen = sc.infer(seen)
    assert sc.infer(changed[changed["booking_date"] <= origin]) == spec_seen  # what follows the origin is invisible
    assert sc.amounts(spec_seen, ["2025-09-01"])["rent"].iloc[0] == -1000.0
    assert sc.infer(changed) != spec_seen  # and the bookings after it do change a schedule that may read them


def test_a_half_seen_month_does_not_make_a_half_sized_payment():
    spec = sc.infer(history(end="2025-06-15"))  # origin in the middle of June: June is incomplete
    assert spec["as_of"] == "2025-06-15"
    june = sc.amounts(spec, pd.date_range("2025-06-16", "2025-06-30"))
    assert june["payroll"].sum() == -2000.0 and june["social_security"].sum() == -600.0
    assert june["tax"].sum() == 0.0  # the June tax is due on the 10th: already passed (and booked) at the origin


def test_the_schedule_follows_a_changed_amount_and_clips_due_days_in_short_months():
    rows = fixed_rows("2025-01-01", "2025-06-30", rent_from="2025-04-01", rent=-1200.0)
    spec = sc.infer(_tx(rows), categories=("rent",))
    assert sc.amounts(spec, ["2025-07-01"])["rent"].iloc[0] == -1200.0  # the most recent amount, not an average
    assert spec["categories"]["rent"]["period"] == 1
    late = {"categories": {"rent": {"period": 1, "profiles": {"0": [[31, -500.0]]}, "latest": [[31, -500.0]]}}}
    april = sc.amounts(late, pd.date_range("2025-04-28", "2025-05-03"))["rent"]
    assert april["2025-04-30"] == -500.0 and april["2025-05-01"] == 0.0  # due on the 31st: paid on the 30th in April


def test_an_empty_or_fixed_cost_free_history_gives_an_empty_schedule():
    assert sc.infer(_tx([]))["categories"] == {}
    only_revenue = _tx([("2025-01-01", 100.0, "revenue", None), ("2025-02-01", 100.0, "revenue", None)])
    spec = sc.infer(only_revenue)
    assert spec["categories"] == {}
    assert sc.total(spec, ["2025-03-01", "2025-03-02"]).tolist() == [0.0, 0.0]


def test_partner_tranches_are_not_in_the_fixed_schedule():
    assert "partner_investment" not in sc.CATEGORIES
    spec = sc.infer(history(partner=True))
    assert "partner_investment" not in spec["categories"]


# -- the model ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def tx() -> pd.DataFrame:
    return history(partner=True)


@pytest.fixture(scope="module")
def model(tx) -> m.ForecastModel:
    return m.train(tx, feature_set="components", log_to_mlflow=False, holdout_days=0)


def test_components_is_a_registered_feature_set_without_year_memory_or_due_flags():
    columns = f.feature_set_columns("components")
    assert not set(columns) & set(f.YEAR_MEMORY_COLUMNS)
    assert not set(columns) & set(f.DUE_COLUMNS)  # the schedule owns the due days
    assert {"weekday", "shutdown_flag", "is_public_holiday"} <= set(columns)
    assert f.needs_levels("components")


def test_components_prediction_is_schedule_plus_variable_part(model):
    days = pd.date_range("2026-01-01", "2026-03-31")
    x = model._frame(days)
    variable = pd.Series(model.regressor.predict(x[model.feature_columns]), index=x.index)
    variable += f.applied_baseline(x)
    scheduled = sc.total(model.schedule, days)
    assert model.predict(days).to_numpy() == pytest.approx((variable + scheduled).to_numpy())
    # the fixed-cost part is exact: payroll, rent, ... as in the calendar
    assert scheduled.tolist() == truth_for(days).tolist()


def test_the_variable_part_does_not_contain_the_fixed_costs(model):
    days = pd.date_range("2026-01-01", "2026-01-31")
    x = model._frame(days)
    variable = pd.Series(model.regressor.predict(x[model.feature_columns]), index=x.index) + f.applied_baseline(x)
    # a revenue-only day without a due date: no fixed cost leaks in, and the daily flow is positive (200 - 30 %)
    plain = variable[~variable.index.day.isin([1, 10, 27, 28])]
    assert plain.min() > 0


def test_contributions_and_explain_rows_sum_to_predict_with_named_schedule_parts(model):
    days = pd.date_range("2026-01-01", "2026-02-28")
    contrib = model.contributions(days)
    assert contrib.sum(axis=1).to_numpy() == pytest.approx(model.predict(days).to_numpy(), abs=1e-3)
    assert {"scheduled_payroll", "scheduled_rent", "scheduled_tax"} <= set(contrib.columns)
    assert contrib["scheduled_payroll"].sum() == pytest.approx(-4000.0)  # 28 Jan and 28 Feb
    why = model.explain("2026-01-01", "2026-01-31", top=8)
    names = [d["feature"] for d in why["top_drivers"]]
    assert "scheduled_payroll" in names and "scheduled_rent" in names
    assert why["predicted_net_eur"] == pytest.approx(float(model.predict(pd.date_range("2026-01-01", "2026-01-31")).sum()),
                                                     abs=0.05)


def test_a_closed_month_has_no_variable_flow_but_the_fixed_costs_continue():
    h = history()
    june = h["booking_date"].between("2025-06-01", "2025-06-30") & h["category"].isin(["revenue", "food"])
    mod = m.train(h[~june], feature_set="components", log_to_mlflow=False, holdout_days=0, band=False)
    days = pd.date_range("2026-06-01", "2026-06-30")
    assert (days.month, days.day) and all((6, d) in mod.shutdown_days for d in range(1, 31))
    predicted = mod.predict(days)
    assert predicted.to_numpy() == pytest.approx(truth_for(days).to_numpy())  # only the schedule is left
    contrib = mod.contributions(days)
    assert contrib.sum(axis=1).to_numpy() == pytest.approx(predicted.to_numpy(), abs=1e-3)
    assert contrib["shutdown_flag"].sum() < 0  # the shutdown is what removes the variable flow
    open_days = mod.predict(pd.date_range("2026-07-01", "2026-07-31"))
    assert open_days.sum() > 0


def test_components_follows_a_25_percent_revenue_step():
    h = history()
    days = pd.date_range("2026-01-01", "2026-06-30")
    comp = m.train(h, feature_set="components", log_to_mlflow=False, holdout_days=0)
    # the true variable flow after the step: revenue 200 (weekend 300) x 1.25, food 30 % of it
    variable = pd.Series([(300.0 if d.day_of_week >= 5 else 200.0) * 1.25 * 0.7 for d in days], index=days)
    truth = variable + truth_for(days)
    monthly_variable = variable.resample("MS").sum()
    err = (comp.predict(days) - truth).resample("MS").sum().abs() / monthly_variable
    assert err.max() < 0.10, err.round(3).to_dict()
    cal = m.train(h, feature_set="calendar", log_to_mlflow=False, holdout_days=0)
    cal_err = (cal.predict(days) - truth).resample("MS").sum().abs() / monthly_variable
    assert cal_err.max() > 0.10, cal_err.round(3).to_dict()


def test_components_has_a_lower_daily_error_than_level_baseline_on_a_history_with_fixed_costs():
    h = history()
    origins = ["2025-08-31", "2025-10-31"]
    maes = {}
    for name in ("level_baseline", "components"):
        folds_ = ev.rolling_origin(h, lambda t, n=name: m.train(t, feature_set=n, log_to_mlflow=False, holdout_days=0,
                                                              band=False), origins=origins, horizon_days=60)
        maes[name] = ev.score(folds_)["daily_mae"]
    assert maes["components"] < 0.9 * maes["level_baseline"], maes


# -- persistence and compatibility -------------------------------------------------------------------------
def test_meta_round_trip_and_save_load_keep_the_schedule(model, tmp_path):
    meta = json.loads(json.dumps(model.meta()))  # plain JSON
    assert meta["feature_set"] == "components" and meta["schedule"]["categories"]["tax"]["period"] == 3
    days = pd.date_range("2026-01-01", "2026-02-28")
    back = m.ForecastModel.from_meta(model.regressor, meta)
    assert back.predict(days).tolist() == model.predict(days).tolist()
    path = model.save(tmp_path / "components.json")
    loaded = m.ForecastModel.load(path)
    assert loaded.schedule == model.schedule and loaded.predict(days).tolist() == model.predict(days).tolist()


def test_old_metadata_without_a_schedule_loads_unchanged(tx):
    old = m.train(tx, feature_set="level_baseline", log_to_mlflow=False, holdout_days=0, band=False)
    meta = old.meta()
    meta.pop("schedule")  # a file written before T068
    meta.pop("band", None)
    back = m.ForecastModel.from_meta(old.regressor, json.loads(json.dumps(meta)))
    days = pd.date_range("2026-01-01", "2026-01-31")
    assert back.schedule is None and back.feature_set == "level_baseline"
    assert back.predict(days).tolist() == old.predict(days).tolist()
    calendar = m.train(tx, feature_set="calendar", log_to_mlflow=False, holdout_days=0, band=False)
    assert m.ForecastModel.from_meta(calendar.regressor, json.loads(json.dumps(
        {k: v for k, v in calendar.meta().items() if k != "schedule"}))).predict(days).tolist() == \
        calendar.predict(days).tolist()


def test_a_schedule_never_changes_a_non_component_model(tx):
    plain = m.train(tx, feature_set="level_baseline", log_to_mlflow=False, holdout_days=0, band=False)
    assert plain.schedule is None
    days = pd.date_range("2026-01-01", "2026-01-31")
    with_one = m.ForecastModel.from_meta(plain.regressor, {**plain.meta(), "schedule": sc.infer(tx)})
    assert with_one.predict(days).tolist() == plain.predict(days).tolist()


def test_with_levels_from_refreshes_the_schedule_and_the_level(model, tx):
    longer = pd.concat([tx, _tx(fixed_rows("2026-01-01", "2026-04-30", rent=-1500.0)
                                + [(d, 220.0 * 1.25, "revenue", None) for d in pd.date_range("2026-01-01", "2026-04-30")])],
                       ignore_index=True)
    fresh = model.with_levels_from(longer)
    assert fresh is not model and fresh.regressor is model.regressor
    assert sc.amounts(model.schedule, ["2026-05-01"])["rent"].iloc[0] == -1000.0  # stored model untouched
    assert sc.amounts(fresh.schedule, ["2026-05-01"])["rent"].iloc[0] == -1500.0
    fc = m.liquidity_forecast(31, model=model, transactions=longer, scheduled_payments=[])
    assert fc["predicted_net"].sum() == pytest.approx(fresh.predict(fc.index).sum())


def test_partner_tranches_stay_a_separate_scheduled_payment():
    part = history(end="2025-10-31", partner=True, partner_until="2025-08-31")  # 8 of 12 tranches booked
    mod = m.train(part, feature_set="components", log_to_mlflow=False, holdout_days=0, band=False)
    assert "partner_investment" not in mod.schedule["categories"]  # not counted in the fixed schedule
    fc = m.liquidity_forecast(61, model=mod, transactions=part)
    partner = m.partner_tranche_schedule(part).reindex(fc.index).fillna(0.0)
    assert partner.sum() == -8000.0 and fc["scheduled"].tolist() == partner.tolist()  # Nov 15 and Dec 15, once each
    assert fc["net"].tolist() == (fc["predicted_net"] + fc["scheduled"]).tolist()
    assert fc["predicted_net"].tolist() == mod.with_levels_from(part).predict(fc.index).tolist()


def test_the_band_works_for_components(tx):
    mod = m.train(tx, feature_set="components", log_to_mlflow=False, holdout_days=0)
    assert mod.band_spec is not None
    lower, upper = mod.band(30)
    assert lower <= 0 <= upper


def test_explicit_components_on_a_history_too_short_for_levels_raises():
    short = history(start="2025-12-01", end="2025-12-10")
    with pytest.raises(ValueError, match="level"):
        m.train(short, feature_set="components", log_to_mlflow=False)


def test_components_holdout_backtest_scores_the_full_net_flow(tx):
    mod = m.train(tx, feature_set="components", log_to_mlflow=False, holdout_days=60, band=False)
    assert mod.metrics["holdout_mae"] < 80  # a clean synthetic history: the full flow (fixed costs included) is exact


def test_train_without_fixed_cost_bookings_still_works():
    h = history()
    h = h[~h["category"].isin(sc.CATEGORIES)]
    mod = m.train(h, feature_set="components", log_to_mlflow=False, holdout_days=0, band=False)
    assert mod.schedule["categories"] == {}
    assert mod.predict(pd.date_range("2026-01-01", "2026-01-10")).notna().all()


# -- weekly folds ------------------------------------------------------------------------------------------
def test_weekly_origins_are_mondays_from_the_end_of_month_5_with_30_days_of_truth():
    h = history()
    origins = folds.weekly_origins(h)
    assert origins[0] == pd.Timestamp("2025-06-02") and origins[-1] == pd.Timestamp("2025-12-01")
    assert all(o.day_of_week == 0 for o in origins) and len(origins) == 27
    assert all((pd.Timestamp("2025-12-31") - o).days >= 30 for o in origins)
    assert folds.weekly_origins(h[h["booking_date"] < "2025-03-01"]) == []


# -- the organizer's statement ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        return ev.load_statement()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")


def test_statement_schedule_matches_december_exactly(statement):
    spec = sc.infer(statement[statement["booking_date"] <= "2025-11-30"])
    december = statement[(statement["booking_date"] >= "2025-12-01") & statement["category"].isin(sc.CATEGORIES)]
    days = pd.date_range("2025-12-01", "2025-12-31")
    actual = december.groupby("booking_date")["amount"].sum().reindex(days, fill_value=0.0)
    assert sc.total(spec, days).tolist() == pytest.approx(actual.tolist())
    assert spec["categories"]["tax"]["period"] == 3


# -- the verdict -------------------------------------------------------------------------------------------
def _profile(weekly_bal, weekly_mae, dec_mae, dec_net, monthly):
    return {"weekly": {"balance_error_90d": weekly_bal, "daily_mae": weekly_mae},
            "december": {"daily_mae": dec_mae, "predicted_net": dec_net, "actual_net": 13_560.0},
            "monthly_to_nov": monthly, "monthly_full": monthly}


GOOD_MONTHS = {"2026-01": 13_000.0, "2026-02": 11_000.0, "2026-03": 14_000.0, "2026-04": 12_000.0, "2026-05": 14_000.0,
               "2026-06": -16_000.0, "2026-07": 15_000.0}


def test_the_bar_passes_only_when_every_point_holds():
    from cfo.forecast import compare as cmp

    reference = _profile(7_375.0, 313.0, 285.0, 17_381.0, GOOD_MONTHS)
    good = cmp.evaluate_bar(reference, _profile(6_600.0, 300.0, 230.0, 14_000.0, GOOD_MONTHS))
    assert all(p["passed"] for p in good)
    cases = {
        "weekly_balance_error_90d": _profile(6_700.0, 300.0, 230.0, 14_000.0, GOOD_MONTHS),  # only 9.2 % better
        "weekly_daily_mae": _profile(6_000.0, 320.0, 230.0, 14_000.0, GOOD_MONTHS),
        "december_daily_mae": _profile(6_000.0, 300.0, 241.0, 14_000.0, GOOD_MONTHS),
        "december_net": _profile(6_000.0, 300.0, 230.0, 20_000.0, GOOD_MONTHS),
        "june_2026_lowest_monthly_full": _profile(6_000.0, 300.0, 230.0, 14_000.0, {**GOOD_MONTHS, "2026-07": -20_000.0}),
        "june_2026_below_monthly_full": _profile(6_000.0, 300.0, 230.0, 14_000.0, {**GOOD_MONTHS, "2026-06": -9_000.0}),
        "jan_may_mean_monthly_full": _profile(6_000.0, 300.0, 230.0, 14_000.0,
                                              {**GOOD_MONTHS, "2026-01": 2_000.0, "2026-02": 2_000.0}),
    }
    for failing, profile in cases.items():
        bar = cmp.evaluate_bar(reference, profile)
        failed = {p["id"] for p in bar if not p["passed"]}
        assert failing in failed and all(f_.startswith(failing.rsplit("_monthly", 1)[0]) for f_ in failed), (failing, failed)
    nan = cmp.evaluate_bar(reference, _profile(float("nan"), 300.0, 230.0, 14_000.0, GOOD_MONTHS))
    assert not nan[0]["passed"]  # a missing number never passes


def test_the_weekly_verdict_is_an_honest_json_with_every_point_of_the_bar(statement):
    from cfo.forecast import compare as cmp

    result = cmp.assess(statement)
    json.dumps(result, allow_nan=False)  # no bare NaN
    assert result["verdict"] in ("better", "not_better") and result["reference"] == "level_baseline"
    assert result["weekly"]["n_origins"] == 27 and result["month_end"]["scores"]["level_baseline"]["n_origins"] == 6
    assert {"calendar", "level", "level_baseline", "components"} <= set(result["weekly"]["scores"])
    assert len(result["bar"]) == 10 and result["reason"]
    assert (result["verdict"] == "better") == all(p["passed"] for p in result["bar"])
    assert result["best_feature_set"] == (result["best_candidate"] if result["verdict"] == "better" else result["reference"])
    assert result["december_2025"]["components"]["actual_net"] == pytest.approx(13_560.09, abs=0.01)
    # the numbers of the T061 report for the reference are reproduced to the digit
    assert result["weekly"]["scores"]["level_baseline"]["daily_mae"] == pytest.approx(313.0, abs=0.5)
    assert result["december_2025"]["level_baseline"]["predicted_net"] == pytest.approx(17_381.0, abs=1.0)


def test_a_denser_origin_grid_visits_every_weekday_and_keeps_the_same_bounds():
    h = history()
    dense = folds.weekly_origins(h, every_days=2)
    assert dense[0] == pd.Timestamp("2025-06-02") and dense[-1] <= pd.Timestamp("2025-12-01")
    assert {o.day_of_week for o in dense} == set(range(7)) and len(dense) > 80
    assert set(folds.weekly_origins(h)) <= set(folds.weekly_origins(h, every_days=1))
    with pytest.raises(ValueError, match="every_days"):
        folds.weekly_origins(h, every_days=0)


# -- review round 1 ----------------------------------------------------------------------------------------
def _closed_december(closed: bool) -> pd.DataFrame:
    """The synthetic history with June closed and, if `closed`, 1-30 December without revenue or food (31 Dec is open)."""
    h = history()
    june = h["booking_date"].between("2025-06-01", "2025-06-30") & h["category"].isin(["revenue", "food"])
    dec = h["booking_date"].between("2025-12-01", "2025-12-30") & h["category"].isin(["revenue", "food"]) & closed
    return h[~june & ~dec].sort_values("booking_date", kind="stable").reset_index(drop=True)


def test_the_components_holdout_split_ignores_what_the_holdout_bookings_look_like():
    open_dec, closed_dec = _closed_december(False), _closed_december(True)
    ta, sa = m.backtest_split(open_dec, 45, feature_set="components")
    tb, sb = m.backtest_split(closed_dec, 45, feature_set="components")
    assert sa.index.equals(sb.index) and sa.index[0] == pd.Timestamp("2025-11-17")  # the same cutoff
    pd.testing.assert_frame_equal(ta.drop(columns="net_cash_flow"), tb.drop(columns="net_cash_flow"))  # same features
    assert ta["net_cash_flow"].equals(tb["net_cash_flow"])
    assert (sb.index >= "2025-12-01").sum() == 31  # the closed December days stay in the score
    assert (sb["shutdown_flag"] == 1).sum() == 0  # the flags come from the bookings before the cutoff: no closed run yet
    assert not (ta["shutdown_flag"] == 1).any()  # training rows: closed days dropped, by the bookings before the cutoff


def test_closed_days_in_the_holdout_are_scored_with_their_fixed_payments():
    closed = _closed_december(True)
    mod_open = m.train(_closed_december(False), feature_set="components", log_to_mlflow=False, holdout_days=45, band=False)
    mod_closed = m.train(closed, feature_set="components", log_to_mlflow=False, holdout_days=45, band=False)
    assert mod_closed.metrics["holdout_days"] == 45.0
    # no revenue in December: the model that does not know about it predicts revenue on the closed days and is off
    assert mod_closed.metrics["holdout_mae"] > mod_open.metrics["holdout_mae"]
    test = m.backtest_split(closed, 45, feature_set="components")[1]
    full = f.daily_cash_flow(closed, exclude_categories=m.EXCLUDED_CATEGORIES)["net_cash_flow"]
    assert full.reindex(test.index).abs().sum() > 0  # the 28th payroll etc. lie in the scored days


def _raised_rent_history(end: str, *, rent_day: int = 1) -> pd.DataFrame:
    """The synthetic history to July with rent 1,000, then rent 1,200 booked on `rent_day` of August."""
    base = history(end="2025-07-31")
    base = base[base["booking_date"] >= "2025-01-01"]
    extra = _tx(fixed_rows("2025-08-01", end) + [(d, 200.0, "revenue", None) for d in pd.date_range("2025-08-01", end)])
    extra = extra[extra["category"] != "rent"]
    raised = _tx([(pd.Timestamp(f"2025-08-{rent_day:02d}"), -1200.0, "rent", "front")])
    return pd.concat([base, extra, raised], ignore_index=True)


def test_a_rent_raise_already_paid_this_month_is_used_and_not_paid_twice():
    spec = sc.infer(_raised_rent_history("2025-08-10"))  # origin on the 10th, the new rent was posted on the 1st
    august = sc.amounts(spec, pd.date_range("2025-08-11", "2025-08-31"))
    assert august["rent"].sum() == 0.0  # this month's rent is posted already: not scheduled a second time
    later = sc.amounts(spec, pd.date_range("2025-09-01", "2025-12-31"))["rent"]
    assert later[later != 0].tolist() == [-1200.0] * 4  # the latest posted amount, every following month


def test_a_payment_not_yet_posted_this_month_is_still_scheduled():
    spec = sc.infer(_raised_rent_history("2025-08-10"))
    august = sc.amounts(spec, pd.date_range("2025-08-11", "2025-08-31"))
    assert august["payroll"].sum() == -2000.0 and august["social_security"].sum() == -600.0  # 27th / 28th still due
    tax = august["tax"]  # August is position 1 of the quarter: 880 on the 10th (posted) and 225 on the 15th (due)
    assert tax[tax != 0].to_dict() == {pd.Timestamp("2025-08-15"): -225.0}


def test_a_payment_posted_a_few_days_off_its_usual_day_moves_the_due_day_and_is_not_doubled():
    spec = sc.infer(_raised_rent_history("2025-08-10", rent_day=3))
    assert sc.amounts(spec, pd.date_range("2025-08-11", "2025-08-31"))["rent"].sum() == 0.0
    later = sc.amounts(spec, pd.date_range("2025-09-01", "2025-09-30"))["rent"]
    assert later[later != 0].to_dict() == {pd.Timestamp("2025-09-03"): -1200.0}  # once, on the day it was last paid


def test_with_levels_from_uses_the_rent_posted_this_month(model):
    days = pd.date_range("2026-01-01", "2026-01-10")
    january = _tx(fixed_rows("2026-01-01", "2026-01-10", rent=-1500.0)
                  + [(d, 200.0, "revenue", None) for d in days] + [(d, -60.0, "food", "kitchen") for d in days])
    longer = pd.concat([history(partner=True), january], ignore_index=True)
    fresh = model.with_levels_from(longer)
    assert sc.amounts(fresh.schedule, pd.date_range("2026-01-11", "2026-01-31"))["rent"].sum() == 0.0
    assert sc.amounts(fresh.schedule, ["2026-02-01"])["rent"].iloc[0] == -1500.0
    assert sc.amounts(model.schedule, ["2026-02-01"])["rent"].iloc[0] == -1000.0  # the stored model is untouched
