"""Liquidity forecast model: synthetic fixtures for the mechanics, the organizer's statement for the June dip.

The statement tests run on the bank CSV (Jan-Nov 2025 = the history in the database; December arrives as live
events) and are skipped without it. No database is touched: the model works on a transactions DataFrame.
"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from cfo.data.source import SourceNotFound, find_source_csv, load_bank_csv
from cfo.forecast import features as f
from cfo.forecast import model as m

HISTORY_END = "2025-11-30"
# What the data supports (measured on the seeded statement, with margin):
#  * June 2026 predicted net flow is about -17k (June 2025 without the tranche: -17.7k), every other month
#    Dec 2025 - Jul 2026 is positive (+10k ... +17k with the default `level_baseline` set, T061; the old `calendar`
#    set replayed +3k ... +17k): the dip is unmistakable.
#  * The balance does NOT go below zero from the end-of-November balance (23.5k): partner tranche 12/12 is the last
#    one, so 2026 keeps 4.2k a month more than 2025, the balance is about 60k on 31 May and troughs near 45k.
#    The dip is therefore asserted as a drop (>= 10k within June, the deepest drawdown of the horizon), not as < 0.
MIN_JUNE_NET = -10_000.0
MIN_JUNE_DROP = 10_000.0


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


@pytest.fixture
def history() -> pd.DataFrame:
    """Jan-Dec 2025 synthetic: weekday revenue 200 (weekend 300), none in June; costs 28th/1st, tranche on the 15th
    (1-11 of 12 booked)."""
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-11-30"):
        if day.month != 6:
            rows.append((day, 300.0 if day.day_of_week >= 5 else 200.0, "revenue", None))
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 1:
            rows.append((day, -1000.0, "rent", "front"))
        if day.day == 15:
            rows.append((day, -4000.0, "partner_investment", None))
    return _tx(rows)


@pytest.fixture
def trained(history) -> m.ForecastModel:
    return m.train(history, log_to_mlflow=False)


# -- mechanics --------------------------------------------------------------------------------------------
def test_partner_tranches_are_excluded_from_the_target(history):
    table = m._training_table(history, m.EXCLUDED_CATEGORIES)
    assert table.loc["2025-03-15", "net_cash_flow"] == 300.0  # the tranche day (a Saturday) shows revenue only
    assert table["net_cash_flow"].sum() == history[history["category"] != "partner_investment"]["amount"].sum()


def test_remaining_tranches_derived_from_bookings(history):
    schedule = m.partner_tranche_schedule(history)  # 11 of 12 booked: the 15 Dec payment remains
    assert list(schedule.index) == [pd.Timestamp("2025-12-15")] and schedule.iloc[0] == -4000.0
    complete = pd.concat([history, _tx([("2025-12-15", -4000.0, "partner_investment", None)])])
    assert m.partner_tranche_schedule(complete).empty
    assert m.partner_tranche_schedule(history[history["category"] != "partner_investment"]).empty


def test_forecast_shape_balance_and_start_balance(trained, history):
    fc = m.liquidity_forecast(60, model=trained, transactions=history)
    assert list(fc.columns) == ["predicted_net", "scheduled", "net", "balance"]
    assert fc.index[0] == pd.Timestamp("2025-12-01") and len(fc) == 60 and fc.index.is_monotonic_increasing
    assert fc.attrs["last_known_date"] == HISTORY_END
    assert fc.attrs["start_balance"] == pytest.approx(history["amount"].sum())
    assert fc.loc["2025-12-15", "scheduled"] == -4000.0 and fc["scheduled"].sum() == -4000.0
    assert fc["balance"].iloc[-1] == pytest.approx(history["amount"].sum() + fc["net"].sum())

    shifted = m.liquidity_forecast(60, start_balance=1_000.0, model=trained, transactions=history)
    assert shifted["balance"].iloc[0] == pytest.approx(1_000.0 + shifted["net"].iloc[0])
    assert (shifted["balance"] - fc["balance"]).round(6).nunique() == 1  # a constant shift, same model
    own = m.liquidity_forecast(5, model=trained, transactions=history, scheduled_payments={"2025-12-02": -50.0})
    assert own["scheduled"].tolist() == [0.0, -50.0, 0.0, 0.0, 0.0]
    assert m.liquidity_forecast(5, model=trained, transactions=history, scheduled_payments=[])["scheduled"].eq(0).all()
    with pytest.raises(ValueError):
        m.liquidity_forecast(0, model=trained, transactions=history)


def test_synthetic_june_dip_reappears_next_year(trained, history):
    fc = m.liquidity_forecast(220, model=trained, transactions=history)  # to 8 July 2026
    june = fc.loc["2026-06-01":"2026-06-30", "predicted_net"].sum()
    may = fc.loc["2026-05-01":"2026-05-31", "predicted_net"].sum()
    assert june < -2_000 and may > 0
    assert fc.loc["2026-06-01":"2026-06-30", "predicted_net"].max() < 50  # no revenue day, only costs
    assert fc.loc["2026-05-31", "balance"] - fc.loc["2026-06-30", "balance"] > 2_000


def test_save_load_roundtrip(trained, history, tmp_path):
    path = trained.save(tmp_path / "models" / "liquidity.json")
    assert path.is_file() and (tmp_path / "models" / "liquidity.json.meta.json").is_file()
    loaded = m.ForecastModel.load(path)
    days = pd.date_range("2026-05-25", "2026-07-05")
    assert loaded.predict(days).tolist() == pytest.approx(trained.predict(days).tolist())
    assert loaded.shutdown_days == trained.shutdown_days and loaded.due_days == trained.due_days
    assert loaded.trained_until == trained.trained_until and loaded.metrics == trained.metrics


def test_get_model_trains_once_then_loads(history, tmp_path):
    target = tmp_path / "liquidity.json"
    first = m.get_model(history, path=target)
    assert target.is_file()
    loaded = m.get_model(path=target)  # no transactions needed: it loads
    assert loaded.trained_until == first.trained_until
    fc = m.liquidity_forecast(3, transactions=history, model=loaded)
    assert len(fc) == 3


def test_default_model_path_is_under_git_ignored_models(monkeypatch):
    monkeypatch.delenv("FORECAST_MODEL_PATH", raising=False)
    assert m.default_model_path().parent.name == "models"
    monkeypatch.setenv("FORECAST_MODEL_PATH", "/tmp/x/y.json")
    assert str(m.default_model_path()) == "/tmp/x/y.json"


def test_feature_importances_and_explanation(trained):
    imp = trained.feature_importances()
    assert set(imp.index) == set(trained.feature_columns) and imp.sum() == pytest.approx(1.0)
    assert imp.is_monotonic_decreasing and imp.iloc[0] > 0
    assert {"shutdown_flag", "month", "day_of_year", "is_weekend", "weekday"} & set(imp.index[:4])
    contrib = trained.contributions(pd.date_range("2026-06-01", "2026-06-05"))
    assert contrib.sum(axis=1).tolist() == pytest.approx(trained.predict(contrib.index).tolist(), abs=1e-2)
    why = trained.explain("2026-06-01", "2026-06-30", top=3)
    assert why["days"] == 30 and len(why["top_drivers"]) == 3
    assert why["predicted_net_eur"] == pytest.approx(trained.predict(pd.date_range("2026-06-01", "2026-06-30")).sum(),
                                                    abs=1.0)
    names = [d["feature"] for d in why["top_drivers"]]
    assert {"shutdown_flag", "month", "day_of_year"} & set(names)  # the shutdown explains June
    assert why["top_drivers"][0]["contribution_eur"] < 0


# -- MLflow only when configured ---------------------------------------------------------------------------
class _FakeMlflow(types.ModuleType):
    def __init__(self):
        super().__init__("mlflow")
        self.calls: list[tuple] = []
        self.xgboost = types.SimpleNamespace(
            log_model=lambda booster, name, **kwargs: self.calls.append(("model", name)))

    def set_tracking_uri(self, uri): self.calls.append(("uri", uri))
    def set_experiment(self, name): self.calls.append(("experiment", name))
    def log_params(self, params): self.calls.append(("params", sorted(params)))
    def log_param(self, key, value): self.calls.append(("param", key))
    def log_metrics(self, metrics): self.calls.append(("metrics", sorted(metrics)))
    def log_artifact(self, path): self.calls.append(("artifact", path.rsplit("/", 1)[-1]))

    def start_run(self, run_name=None):
        calls = self.calls

        class Run:
            info = types.SimpleNamespace(run_id="run-1")

            def __enter__(self_inner):
                calls.append(("start", run_name))
                return self_inner

            def __exit__(self_inner, *exc): calls.append(("end",))
        return Run()


def test_mlflow_is_not_used_without_tracking_uri(history, monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    fake = _FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    monkeypatch.setitem(sys.modules, "mlflow.xgboost", fake.xgboost)
    m.train(history)
    assert fake.calls == []


def test_mlflow_logging_when_tracking_uri_is_set(history, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    fake = _FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    monkeypatch.setitem(sys.modules, "mlflow.xgboost", fake.xgboost)
    model = m.train(history)
    kinds = [c[0] for c in fake.calls]
    assert kinds[:3] == ["uri", "experiment", "start"] and kinds[-1] == "end"
    assert ("uri", "http://mlflow:5000") in fake.calls and ("model", "model") in fake.calls
    assert ("artifact", "feature_importances.json") in fake.calls
    assert ("artifact", m.META_ARTIFACT) in fake.calls
    assert model.mlflow_run_id == "run-1" and model.model_version is None  # logged, not registered
    logged_metrics = next(c[1] for c in fake.calls if c[0] == "metrics")
    assert "holdout_mae" in logged_metrics and set(logged_metrics) == set(model.metrics)


def test_missing_mlflow_package_does_not_break_training(history, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    monkeypatch.setitem(sys.modules, "mlflow", None)  # import raises ImportError
    assert m.train(history).trained_until == pd.Timestamp(HISTORY_END)


# -- the organizer's statement: the June dip --------------------------------------------------------------
@pytest.fixture(scope="module")
def statement() -> pd.DataFrame:
    try:
        txns = load_bank_csv(find_source_csv())
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    df = pd.DataFrame([{"booking_date": t.booking_date, "amount": float(t.amount), "category": t.category,
                        "team_id": t.team_id} for t in txns])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df[df["booking_date"] <= HISTORY_END].reset_index(drop=True)  # Jan-Nov = the database history


@pytest.fixture(scope="module")
def statement_model(statement) -> m.ForecastModel:
    return m.train(statement, log_to_mlflow=False)


def test_statement_model_beats_naive_baseline(statement_model):
    metrics = statement_model.metrics
    assert metrics["holdout_mae"] < 0.5 * metrics["holdout_naive_mae"]
    assert statement_model.shutdown_days == {(6, d) for d in range(1, 31)}


def test_june_2026_dip_is_clearly_visible(statement, statement_model):
    fc = m.liquidity_forecast(240, model=statement_model, transactions=statement)  # 1 Dec 2025 - 28 Jul 2026
    assert fc.attrs["start_balance"] == pytest.approx(23_552.09, abs=0.01)  # end of November balance
    monthly = fc["predicted_net"].resample("MS").sum()
    assert monthly["2026-06-01"] < MIN_JUNE_NET
    others = monthly.drop(pd.Timestamp("2026-06-01"))
    assert others.iloc[:-1].gt(0).all() and monthly["2026-06-01"] < others.min() - 10_000  # Jul is a partial month

    may_end, june_end = fc.loc["2026-05-31", "balance"], fc.loc["2026-06-30", "balance"]
    assert may_end - june_end > MIN_JUNE_DROP
    # deepest peak-to-trough drawdown of the whole horizon lies in the June shutdown (peak 31 May, trough 30 June)
    drawdown = fc["balance"].cummax() - fc["balance"]
    assert pd.Timestamp("2026-06-01") <= drawdown.idxmax() <= pd.Timestamp("2026-07-15")
    assert drawdown.max() > MIN_JUNE_DROP
    assert fc.loc["2026-06-30", "balance"] < fc.loc["2026-05-01", "balance"]  # June wipes out a month of recovery


def test_stress_balance_goes_negative_in_june(statement, statement_model):
    """Stress, not the base case: a thin buffer (T061: balance 93k lower than the real one; the model now follows the
    current revenue level, so it takes more than the old 55k to get there) goes negative in June
    2026 and ends the month at its lowest point since March; June is where a credit line is needed."""
    fc = m.liquidity_forecast(240, start_balance=23_552.09 - 93_000.0, model=statement_model, transactions=statement)
    june = fc.loc["2026-06-01":"2026-06-30", "balance"]
    assert fc.loc["2026-05-31", "balance"] > 0 > june.min() and june.idxmin() >= pd.Timestamp("2026-06-20")
    assert fc.loc["2026-05-31", "balance"] - june.min() > MIN_JUNE_DROP


def test_partner_tranche_not_learned_but_scheduled(statement, statement_model):
    assert m.partner_tranche_schedule(statement).to_dict() == {pd.Timestamp("2025-12-15"): -4166.67}
    fc = m.liquidity_forecast(240, model=statement_model, transactions=statement)
    assert fc["scheduled"].sum() == pytest.approx(-4166.67)
    # no learned 15th-of-month outflow in 2026: the 15th is an ordinary day, not a -4k day
    assert fc.loc["2026-03-15", "predicted_net"] > -2_000
