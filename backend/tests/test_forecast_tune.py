"""Hyperparameter search for the forecast model, tracked in MLflow, promotable to `liquidity-forecast` (T062).

Real `mlflow` on a temporary sqlite store (as in `test_forecast_registry.py`), a small synthetic history, a few
trials. Skipped when the optional extras are missing (`uv run --extra mlflow --extra tuning ...`).
"""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

mlflow = pytest.importorskip("mlflow")
pytest.importorskip("optuna")

from cfo.forecast import evaluate as ev  # noqa: E402
from cfo.forecast import model as m  # noqa: E402
from cfo.forecast import tune  # noqa: E402

CHAMPION_URI = f"models:/{m.REGISTERED_MODEL_NAME}@{m.CHAMPION_ALIAS}"
CHALLENGER_URI = f"models:/{m.REGISTERED_MODEL_NAME}@challenger"
FUTURE = pd.date_range("2025-12-01", "2026-03-31")
# a calendar booster with one tiny tree: clearly worse than the default model on this history
BAD = {"feature_set": "calendar", "n_estimators": 1, "max_depth": 1, "learning_rate": 0.01}
BAD_TRAIN = {"feature_set": "calendar", "params": {"n_estimators": 1, "max_depth": 1, "learning_rate": 0.01}}


def _history(revenue: float = 200.0) -> pd.DataFrame:
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-11-30"):
        if day.month != 6:
            rows.append((day, revenue + (100.0 if day.day_of_week >= 5 else 0.0), "revenue", None))
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 1:
            rows.append((day, -1000.0, "rent", "front"))
        if day.day == 15:
            rows.append((day, -4000.0, "partner_investment", None))
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


@pytest.fixture(scope="module")
def history() -> pd.DataFrame:
    return _history()


@pytest.fixture
def tracking(tmp_path, monkeypatch):
    """A fresh sqlite tracking + registry store; nothing is written outside the temp dir."""
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    mlflow.set_tracking_uri(uri)
    yield uri
    mlflow.set_tracking_uri(None)


@pytest.fixture
def no_tracking(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)


def _candidate(history: pd.DataFrame, **config) -> tune.TuningResult:
    """A TuningResult for a hand-picked configuration (scored like a trial)."""
    feature_set = config.pop("feature_set", "level_baseline")
    scores, per_origin = tune.score_config(history, feature_set, config)
    return tune.TuningResult(best_feature_set=feature_set, best_params=config, best_scores=scores,
                             baseline_scores=scores, per_origin=per_origin, transactions=history)


# -- the search ------------------------------------------------------------------------------------------
def test_five_trials_make_one_parent_and_five_child_runs_with_the_metrics(history, tracking):
    result = tune.search(history, n_trials=5, seed=0)
    client = mlflow.MlflowClient()
    experiment = client.get_experiment_by_name(tune.TUNING_EXPERIMENT)
    runs = client.search_runs([experiment.experiment_id], max_results=100)
    parents = [r for r in runs if "mlflow.parentRunId" not in r.data.tags]
    children = [r for r in runs if "mlflow.parentRunId" in r.data.tags]
    assert len(parents) == 1 and len(children) == 5
    parent = parents[0]
    assert parent.info.run_id == result.parent_run_id
    assert parent.info.run_name.startswith("tuning-") and parent.info.run_name[len("tuning-"):][:4].isdigit()
    assert {c.data.tags["mlflow.parentRunId"] for c in children} == {parent.info.run_id}
    for child in children:
        assert set(ev.METRIC_KEYS) <= set(child.data.metrics) and "objective_value" in child.data.metrics
        assert not any(math.isnan(v) for v in child.data.metrics.values())
        assert {"feature_set", "n_estimators", "max_depth", "learning_rate", "objective"} <= set(child.data.params)
        assert "trial_number" in child.data.metrics
        assert any(k.startswith("cum_error_") for k in child.data.metrics)  # per-origin errors
    assert sorted(int(c.data.metrics["trial_number"]) for c in children) == [0, 1, 2, 3, 4]
    names = {a.path for a in client.list_artifacts(parent.info.run_id)}
    assert {"best_params.json", "top10_trials.csv", "trials.csv"} <= names
    assert "optimization_history.png" in names  # matplotlib is part of the tuning extra
    best = json.loads(mlflow.artifacts.load_text(f"{parent.info.artifact_uri}/best_params.json"))
    assert best["feature_set"] == result.best_feature_set and best["params"] == result.best_params
    assert parent.data.metrics["best_balance_error_90d"] == pytest.approx(result.best_scores["balance_error_90d"])


def test_same_seed_same_best_params(history, no_tracking):
    first = tune.search(history, n_trials=4, seed=3)
    second = tune.search(history, n_trials=4, seed=3)
    assert (first.best_feature_set, first.best_params) == (second.best_feature_set, second.best_params)
    assert first.best_scores == second.best_scores
    assert first.parent_run_id is None  # no tracking configured: the search still runs


def test_the_current_defaults_are_the_first_trial_and_the_reference(history, no_tracking):
    result = tune.search(history, n_trials=2, seed=0)
    first = result.trials[0]
    assert (first["feature_set"], first["params"]) == (m.DEFAULT_FEATURE_SET, tune.baseline_params())
    assert first["scores"] == result.baseline_scores
    assert result.best_scores["balance_error_90d"] <= result.baseline_scores["balance_error_90d"]


def test_search_space_stays_small_model_sized(history, no_tracking):
    result = tune.search(history, n_trials=12, seed=1)
    for trial in result.trials:
        p = trial["params"]
        assert 2 <= p["max_depth"] <= 6 and p["n_estimators"] <= 600 and p["learning_rate"] <= 0.3
        assert p["objective"] in ("reg:squarederror", "reg:absoluteerror")
        assert "feature_set" not in p  # feature_set is not an XGBoost parameter
    assert {t["feature_set"] for t in result.trials} <= set(tune.DEFAULT_FEATURE_SETS)


def test_feature_sets_can_be_restricted(history, no_tracking):
    result = tune.search(history, n_trials=3, seed=0, feature_sets=["calendar"])
    assert {t["feature_set"] for t in result.trials} == {"calendar"}


def test_top_table_has_ten_rows_sorted_by_the_objective(history, no_tracking):
    result = tune.search(history, n_trials=12, seed=0)
    top = result.top(10)
    assert len(top) == 10 and list(top["objective_value"]) == sorted(top["objective_value"])
    assert top.iloc[0]["trial"] == result.best_trial_number


def test_a_failing_trial_is_recorded_and_does_not_stop_the_search(history, tracking, monkeypatch):
    real = m.train

    def flaky(tx, **kw):
        if kw.get("params") != tune.baseline_params():  # everything but the reference configuration
            raise ValueError("boom")
        return real(tx, **kw)

    monkeypatch.setattr(m, "train", flaky)
    result = tune.search(history, n_trials=3, seed=0)
    assert result.trials[0].get("error") is None and all("boom" in t["error"] for t in result.trials[1:])
    assert result.best_trial_number == 0
    children = mlflow.MlflowClient().search_runs(
        [mlflow.MlflowClient().get_experiment_by_name(tune.TUNING_EXPERIMENT).experiment_id], max_results=50)
    assert len(children) == 4  # parent + 3 trials, failed ones included


def test_parameter_importance_names_the_searched_parameters(history, no_tracking):
    result = tune.search(history, n_trials=14, seed=2)
    importance = tune.param_importance(result)
    assert set(importance.index) <= set(tune.space(tune.DEFAULT_FEATURE_SETS)) and len(importance) >= 5
    assert importance.sum() == pytest.approx(1.0, abs=1e-6) and importance.is_monotonic_decreasing


# -- objective, margin, NaN --------------------------------------------------------------------------------
def test_objective_is_the_balance_error_and_daily_mae_only_breaks_exact_ties():
    a = {"balance_error_90d": 100.0, "balance_error_60d": 50.0, "balance_error_30d": 10.0, "daily_mae": 5.0}
    b = {**a, "daily_mae": 6.0}
    value, name = tune.objective_value(a)
    assert name == "balance_error_90d" and value == 100.0  # the optimiser sees the balance error alone
    assert tune.rank_key(a) < tune.rank_key(b)  # exact tie: the lower daily MAE ranks first


def test_a_much_worse_daily_mae_never_reverses_two_different_balance_errors():
    a = {"balance_error_90d": 100.0, "daily_mae": 1e9}
    b = {"balance_error_90d": 101.0, "daily_mae": 1.0}
    assert tune.rank_key(a) < tune.rank_key(b)
    result = tune.TuningResult(best_feature_set="x", best_params={}, best_scores=a, trials=[
        {"number": 0, "feature_set": "x", "params": {}, "scores": b, "objective": 101.0},
        {"number": 1, "feature_set": "x", "params": {}, "scores": a, "objective": 100.0}])
    assert list(result.top(2)["trial"]) == [1, 0]


def test_objective_falls_back_to_the_longest_available_horizon_when_90d_is_nan():
    nan = math.nan
    scores = {"balance_error_90d": nan, "balance_error_60d": 50.0, "balance_error_30d": 10.0, "daily_mae": 5.0}
    assert tune.objective_value(scores)[1] == "balance_error_60d"
    scores["balance_error_60d"] = nan
    assert tune.objective_value(scores)[1] == "balance_error_30d"
    scores["balance_error_30d"] = nan
    assert tune.objective_value(scores)[1] == "daily_mae"


def test_json_never_contains_nan():
    clean = tune.finite({"a": math.nan, "b": 1.5, "c": {"d": math.inf}})
    assert clean == {"a": None, "b": 1.5, "c": {"d": None}}
    json.dumps(clean, allow_nan=False)


def test_better_means_better_by_a_margin_not_by_noise():
    assert tune.is_better(97.0, 100.0) and not tune.is_better(98.5, 100.0)  # 2 % default
    assert tune.is_better(98.5, 100.0, min_gain=0.01)
    assert not tune.is_better(100.0, 100.0, min_gain=0.0)
    assert tune.is_better(5.0, math.nan) and not tune.is_better(math.nan, 5.0)


def test_per_origin_errors_cover_every_origin(history):
    folds = ev.rolling_origin(history, lambda tx: m.train(tx, log_to_mlflow=False, holdout_days=0))
    per = tune.per_origin_errors(folds)
    assert set(per) == {str(pd.Timestamp(o).date()) for o in folds["origin"].unique()}
    first = per["2025-06-30"]
    assert first["days"] == 90 and first["cum_error_end"] == pytest.approx(
        folds[folds["origin"] == pd.Timestamp("2025-06-30")]["cum_error"].iloc[-1])


def test_error_without_first_origin_ignores_the_first_origin():
    per = {"2025-07-31": {"cum_error_end": -100.0}, "2025-06-30": {"cum_error_end": 9999.0},
           "2025-08-31": {"cum_error_end": 300.0}}
    assert tune.error_without_first_origin(per) == pytest.approx(200.0)
    assert math.isnan(tune.error_without_first_origin({"2025-06-30": {"cum_error_end": 1.0}}))


def test_the_tracking_uri_is_redacted_everywhere_it_is_shown():
    raw = "https://user:pw@host:5000/mlflow?token=abc123&x=1"
    shown = tune.redact_uri(raw)
    assert "pw" not in shown and "abc123" not in shown and "user:***@host:5000" in shown
    assert tune.redact_uri("sqlite:////tmp/x.db") == "sqlite:////tmp/x.db"
    scores = {"daily_mae": 1.0, "balance_error_90d": 5.0}
    result = tune.TuningResult(best_feature_set="x", best_params={}, best_scores=scores, baseline_scores=scores,
                               seconds_per_trial=1.0, n_trials=1, tracking_uri=raw, parent_run_id="r1", experiment="e")
    for text in (json.dumps(result.to_dict()), tune._table(result.to_dict()), repr(result)):
        assert "pw" not in text and "abc123" not in text
    assert result.tracking_uri == raw  # promote still needs the real one


def test_negative_min_gain_is_rejected(history, tracking, capsys):
    with pytest.raises(ValueError, match="min_gain"):
        tune.promote(_candidate(history), min_gain=-0.1)
    with pytest.raises(ValueError, match="min_gain"):
        tune.search(history, n_trials=1, min_gain=-0.5)
    with pytest.raises(SystemExit) as exc:
        tune.main(["--trials", "1", "--min-gain", "-0.1"])
    assert exc.value.code == 2 and "min-gain" in capsys.readouterr().err


# -- promote -----------------------------------------------------------------------------------------------
def test_promote_registers_a_version_with_the_challenger_alias(history, tracking):
    result = _candidate(history)
    promo = tune.promote(result)
    assert promo.version == "1" and promo.alias == "challenger" and not promo.moved_champion
    client = mlflow.MlflowClient()
    assert str(client.get_model_version_by_alias(m.REGISTERED_MODEL_NAME, "challenger").version) == "1"
    with pytest.raises(Exception):  # champion untouched
        client.get_model_version_by_alias(m.REGISTERED_MODEL_NAME, m.CHAMPION_ALIAS)
    loaded = m.ForecastModel.load_from_mlflow(CHALLENGER_URI)
    assert loaded.feature_set == result.best_feature_set
    predicted = loaded.predict(FUTURE)
    assert len(predicted) == len(FUTURE) and predicted.notna().all()
    run = mlflow.get_run(promo.run_id)
    assert run.data.metrics["tuning_balance_error_90d"] == pytest.approx(result.best_scores["balance_error_90d"])


def test_promote_trains_the_best_configuration_on_the_full_history(history, tracking):
    promo = tune.promote(_candidate(history, **BAD))
    loaded = m.ForecastModel.load_from_mlflow(CHALLENGER_URI)
    assert loaded.params["n_estimators"] == 1 and loaded.trained_until == history["booking_date"].max()
    assert promo.run_id == loaded.mlflow_run_id


def test_promote_if_better_moves_champion_when_the_candidate_is_clearly_better(history, tracking):
    m.train(history, **BAD_TRAIN, register=True, holdout_days=0)  # the current champion: weak
    promo = tune.promote(_candidate(history), if_better=True)
    assert promo.moved_champion and promo.version == "2"
    assert promo.candidate_error < promo.champion_error
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "2"
    assert m.ForecastModel.load_from_mlflow(CHALLENGER_URI).model_version == "2"


def test_promote_if_better_leaves_champion_alone_when_the_candidate_is_worse(history, tracking):
    m.train(history, register=True, holdout_days=0)  # the current champion: the default model
    promo = tune.promote(_candidate(history, **BAD), if_better=True)
    assert not promo.moved_champion and promo.alias == "challenger" and promo.version == "2"
    assert "not better" in promo.reason
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "1"
    assert m.ForecastModel.load_from_mlflow(CHALLENGER_URI).model_version == "2"


def test_promote_if_better_needs_the_margin(history, tracking):
    m.train(history, register=True, holdout_days=0)
    promo = tune.promote(_candidate(history), if_better=True, min_gain=0.02)  # identical model: no gain
    assert not promo.moved_champion


def test_promote_if_better_takes_champion_when_there_is_none(history, tracking):
    promo = tune.promote(_candidate(history), if_better=True)
    assert promo.moved_champion and "no champion" in promo.reason
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "1"


def test_promote_without_tracking_raises(history, no_tracking):
    with pytest.raises(RuntimeError, match="MLFLOW_TRACKING_URI|tracking"):
        tune.promote(_candidate(history))


# -- data and CLI ------------------------------------------------------------------------------------------
def test_data_falls_back_to_the_csv_when_the_database_is_unreachable(history, monkeypatch):
    import psycopg

    def refuse(*a, **k):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    tx, source = tune.load_transactions("auto")
    assert source == "csv" and len(tx) == len(history)
    with pytest.raises(psycopg.OperationalError):
        tune.load_transactions("db")


def test_cli_prints_json_without_nan_and_with_the_best_score(history, tracking, monkeypatch, capsys):
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:9/none")
    code = tune.main(["--trials", "3", "--source", "csv", "--tracking-uri", tracking, "--json"])
    assert code == 0
    out = capsys.readouterr().out

    def reject(constant):
        raise ValueError(f"bare {constant} in the JSON output")

    data = json.loads(out, parse_constant=reject)
    assert data["n_trials"] == 3 and data["best_balance_error_90d"] is not None
    assert {"best_params", "best_feature_set", "baseline_balance_error_90d", "per_origin", "top10",
            "seconds_per_trial", "beats_baseline", "mlflow"} <= set(data)
    assert data["mlflow"]["parent_run_id"]


def test_cli_promote_if_better_reports_what_happened(history, tracking, monkeypatch, capsys):
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    code = tune.main(["--trials", "2", "--source", "csv", "--promote-if-better", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["promotion"]["version"] == "1" and data["promotion"]["moved_champion"] is True
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "1"


# -- promote-if-better hardening ----------------------------------------------------------------------------
def _versions(client) -> list[str]:
    return sorted(str(v.version) for v in client.search_model_versions(f"name='{m.REGISTERED_MODEL_NAME}'"))


def test_a_failing_champion_lookup_aborts_the_promotion_and_leaves_the_champion(history, tracking, monkeypatch):
    m.train(history, register=True, holdout_days=0)  # champion v1
    client = mlflow.MlflowClient()
    from mlflow.exceptions import MlflowException

    def broken(self, *a, **k):
        raise MlflowException("server on fire", error_code="INTERNAL_ERROR")

    monkeypatch.setattr(mlflow.MlflowClient, "get_model_version_by_alias", broken)
    with pytest.raises(tune.PromotionError, match="champion"):
        tune.promote(_candidate(history), if_better=True)
    monkeypatch.undo()
    assert _versions(client) == ["1"]  # nothing registered
    assert str(client.get_model_version_by_alias(m.REGISTERED_MODEL_NAME, m.CHAMPION_ALIAS).version) == "1"


def test_cli_exits_non_zero_with_a_message_when_the_champion_lookup_fails(history, tracking, monkeypatch, capsys,
                                                                          caplog):
    from mlflow.exceptions import MlflowException

    monkeypatch.setattr(ev, "load_statement", lambda: history)

    def broken(self, *a, **k):
        raise MlflowException("server on fire", error_code="INTERNAL_ERROR")

    monkeypatch.setattr(mlflow.MlflowClient, "get_model_version_by_alias", broken)
    code = tune.main(["--trials", "1", "--source", "csv", "--promote-if-better", "--json"])
    assert code != 0
    assert "could not look up the current champion" in caplog.text  # stderr in a real run (logging.basicConfig)
    out = json.loads(capsys.readouterr().out)
    assert out["promotion"]["moved_champion"] is False and "champion" in out["promotion"]["error"]


def test_a_model_without_champion_alias_counts_as_no_champion(history, tracking):
    tune.promote(_candidate(history))  # registered model exists, only the challenger alias
    promo = tune.promote(_candidate(history), if_better=True)
    assert promo.moved_champion and "no champion" in promo.reason


def test_promote_if_better_needs_a_real_90_day_error_for_the_candidate(history, tracking):
    m.train(history, register=True, holdout_days=0)
    result = _candidate(history)
    result.best_scores = {**result.best_scores, "balance_error_90d": math.nan}
    result.objective_metric = "balance_error_60d"  # the search fell back to a shorter horizon
    promo = tune.promote(result, if_better=True)
    assert not promo.moved_champion and promo.alias == "challenger" and "90" in promo.reason
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "1"


def test_promote_if_better_needs_a_real_90_day_error_for_the_champion(history, tracking, monkeypatch):
    m.train(history, **BAD_TRAIN, register=True, holdout_days=0)
    real = tune.score_config

    def champion_without_90(tx, feature_set, params, **kw):
        scores, per = real(tx, feature_set, params, **kw)
        return {**scores, "balance_error_90d": math.nan}, per

    monkeypatch.setattr(tune, "score_config", champion_without_90)
    promo = tune.promote(_candidate(history), if_better=True)
    assert not promo.moved_champion and "90" in promo.reason
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).model_version == "1"


def test_without_a_champion_the_candidate_still_needs_a_90_day_error(history, tracking):
    result = _candidate(history)
    result.best_scores = {**result.best_scores, "balance_error_90d": math.nan}
    promo = tune.promote(result, if_better=True)
    assert not promo.moved_champion and "90" in promo.reason and promo.version == "1"


# -- T068: constrained objective, weekly folds, robustness over seeds -------------------------------------------
@pytest.fixture(scope="module")
def weekly(history) -> list:
    from cfo.forecast import folds

    origins = folds.weekly_origins(history)
    assert len(origins) >= 20
    return origins


def test_default_mode_is_unchanged_and_the_constrained_mode_is_new(history, no_tracking):
    default = tune.search(history, n_trials=2, seed=0)
    assert default.mode == tune.MODE_BALANCE and default.daily_mae_cap is None and default.feasible is None
    assert "daily_mae_cap" in default.to_dict() and default.to_dict()["mode"] == "balance"
    assert tune.MODE_CONSTRAINED != tune.MODE_BALANCE


def test_a_constrained_objective_penalises_a_violator_more_than_it_gains():
    cap = 300.0
    feasible = {"daily_mae": 290.0, "balance_error_90d": 6000.0}
    violator = {"daily_mae": 560.0, "balance_error_90d": 3000.0}  # half the balance error, almost twice the daily error
    assert tune.constrained_value(feasible, cap)[0] < tune.constrained_value(violator, cap)[0]
    assert tune.constrained_value(feasible, cap) == (pytest.approx(6000.0), True)  # a feasible trial: the plain objective
    value, ok = tune.constrained_value({"daily_mae": 301.0, "balance_error_90d": 6000.0}, cap)
    assert not ok and value > 6000.0 + tune.CONSTRAINT_WEIGHT * 0.9
    # the same fall-back to the longest horizon as the plain objective
    assert tune.constrained_value({"daily_mae": 100.0, "balance_error_90d": math.nan, "balance_error_60d": 5.0},
                                  cap)[0] == pytest.approx(5.0)


def test_the_constrained_search_never_returns_a_worse_daily_error_than_the_reference(history, weekly, no_tracking):
    result = tune.search(history, n_trials=6, seed=0, mode=tune.MODE_CONSTRAINED, origins=weekly)
    assert result.mode == tune.MODE_CONSTRAINED
    assert result.daily_mae_cap == pytest.approx(result.baseline_scores["daily_mae"])
    assert result.feasible is True
    assert result.best_scores["daily_mae"] <= result.daily_mae_cap * (1 + 1e-9)
    assert result.best_scores["balance_error_90d"] <= result.baseline_scores["balance_error_90d"] + 1e-9
    assert result.baseline_scores["n_origins"] == float(len(weekly))  # the weekly folds reached every scoring call
    assert len(result.per_origin) == len(weekly)
    for trial in result.trials:
        assert trial["feasible"] == (trial["scores"]["daily_mae"] <= result.daily_mae_cap * (1 + 1e-9))
    data = result.to_dict()
    assert data["mode"] == "constrained" and data["feasible"] is True and data["daily_mae_cap"] is not None


def test_the_reference_of_the_constraint_can_be_another_feature_set(history, weekly, no_tracking):
    result = tune.search(history, n_trials=2, seed=0, mode=tune.MODE_CONSTRAINED, origins=weekly,
                         reference_set="calendar")
    assert result.baseline_feature_set == "calendar"
    assert result.trials[0]["feature_set"] == "calendar"
    assert result.daily_mae_cap == pytest.approx(result.baseline_scores["daily_mae"])


def test_robust_search_picks_the_median_seed_and_reports_the_spread(history, weekly, no_tracking):
    robust = tune.robust_search(history, n_trials=3, seeds=(0, 1, 2), origins=weekly)
    scores = [r.best_scores["balance_error_90d"] for r in robust.runs]
    assert [r.seed for r in robust.runs] == [0, 1, 2]
    chosen = robust.chosen.best_scores["balance_error_90d"]
    assert chosen == sorted(scores)[1]  # the median of three
    spread = max(abs(s - chosen) / chosen for s in scores)
    assert robust.spread == pytest.approx(spread)
    assert robust.stable == (spread <= tune.STABILITY_TOLERANCE)
    assert robust.tolerance == tune.STABILITY_TOLERANCE == 0.05
    data = robust.to_dict()
    assert data["seeds"] == [0, 1, 2] and len(data["seed_scores"]) == 3 and data["chosen_seed"] == robust.chosen.seed
    json.dumps(data, allow_nan=False)


def test_robust_search_demands_at_least_two_seeds(history, no_tracking):
    with pytest.raises(ValueError, match="seed"):
        tune.robust_search(history, n_trials=2, seeds=(0,))


def test_cli_constrained_weekly_with_three_seeds(history, no_tracking, monkeypatch, capsys):
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:9/none")
    code = tune.main(["--trials", "2", "--source", "csv", "--constrained", "--weekly", "--seeds", "0,1,2", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "constrained" and data["robust"]["seeds"] == [0, 1, 2]
    assert data["robust"]["stable"] in (True, False) and data["best_balance_error_90d"] is not None
    assert data["best_scores"]["n_origins"] >= 20
