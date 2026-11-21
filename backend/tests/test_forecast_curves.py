"""Training curves of the forecast booster: recorded per boosting round, logged as stepped MLflow metrics (T064).

Real `mlflow` on a temporary sqlite store, a small synthetic history. Skipped without the optional extras.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from xgboost import XGBRegressor

mlflow = pytest.importorskip("mlflow")
pytest.importorskip("optuna")

from cfo.forecast import features as feat  # noqa: E402
from cfo.forecast import model as m  # noqa: E402
from cfo.forecast import tune  # noqa: E402

CURVES = ("train_rmse_curve", "train_mae_curve", "holdout_train_rmse_curve", "holdout_train_mae_curve",
          "holdout_rmse_curve", "holdout_mae_curve")
SMALL = {"n_estimators": 30}


def _history() -> pd.DataFrame:
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-11-30"):
        if day.month != 6:
            rows.append((day, 200.0 + (100.0 if day.day_of_week >= 5 else 0.0), "revenue", None))
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
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    mlflow.set_tracking_uri(uri)
    yield uri
    mlflow.set_tracking_uri(None)


@pytest.fixture
def no_tracking(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)


def _history_of(run_id: str, key: str):
    return mlflow.MlflowClient().get_metric_history(run_id, key)


# -- the fit is not changed --------------------------------------------------------------------------------
@pytest.mark.parametrize("feature_set", sorted(feat.FEATURE_SETS))
def test_the_eval_set_does_not_change_the_fit(history, no_tracking, feature_set):
    model = m.train(history, feature_set=feature_set, params=SMALL, band=False)
    _, table = m._usable_table(history, m.EXCLUDED_CATEGORIES, feature_set)
    columns = feat.feature_set_columns(feature_set)
    plain = XGBRegressor(**model.params).fit(table[columns], table["net_cash_flow"] - m._offset(table, feature_set))
    assert np.array_equal(model.regressor.predict(table[columns]), plain.predict(table[columns]))
    assert model.regressor.get_booster().save_raw("json") == plain.get_booster().save_raw("json")
    assert "eval_metric" not in model.params  # the recorded configuration is the one the user passed


@pytest.mark.parametrize("feature_set", sorted(feat.FEATURE_SETS))
def test_curves_have_one_point_per_boosting_round(history, no_tracking, feature_set):
    model = m.train(history, feature_set=feature_set, params=SMALL, band=False)
    assert set(model.training_curves) == set(CURVES)
    for name in CURVES:
        assert len(model.training_curves[name]) == SMALL["n_estimators"], name
    # training error falls with more rounds, the last point is the final model's error on its own data
    assert model.training_curves["train_rmse_curve"][-1] < model.training_curves["train_rmse_curve"][0]


def test_without_a_holdout_there_are_only_training_curves(history, no_tracking):
    model = m.train(history, params=SMALL, holdout_days=0, band=False)
    assert set(model.training_curves) == {"train_rmse_curve", "train_mae_curve"}


def test_curves_are_not_part_of_meta_save_and_load(history, no_tracking, tmp_path):
    model = m.train(history, params=SMALL, band=False)
    assert model.training_curves and not any("curve" in k for k in model.meta()) and "curve" not in str(model.meta())
    path = model.save(tmp_path / "model.json")
    loaded = m.ForecastModel.load(path)
    assert loaded.training_curves == {}
    assert np.array_equal(loaded.predict(pd.date_range("2025-12-01", "2025-12-20")),
                          model.predict(pd.date_range("2025-12-01", "2025-12-20")))


# -- logged as stepped metrics -------------------------------------------------------------------------------
def test_logged_curves_have_steps_0_to_n_minus_1(history, tracking):
    model = m.train(history, params=SMALL, band=False)
    for name in CURVES:
        points = _history_of(model.mlflow_run_id, name)
        assert [p.step for p in sorted(points, key=lambda p: p.step)] == list(range(SMALL["n_estimators"])), name
        assert [p.value for p in sorted(points, key=lambda p: p.step)] == pytest.approx(model.training_curves[name])


def test_the_scalar_metrics_are_unchanged(history, tracking):
    model = m.train(history, params=SMALL, band=False)
    run = mlflow.get_run(model.mlflow_run_id)
    for key in ("holdout_mae", "holdout_rmse", "train_mae", "holdout_naive_mae", "holdout_total_error", "train_days"):
        assert run.data.metrics[key] == pytest.approx(model.metrics[key])
        assert len(_history_of(model.mlflow_run_id, key)) == 1  # a scalar, not a curve


def test_curves_go_out_in_batches_of_at_most_1000(history, tracking, monkeypatch):
    calls: list[int] = []
    real = mlflow.MlflowClient.log_batch

    def spy(self, run_id, metrics=(), params=(), tags=(), **kw):
        if all(mt.key.endswith("_curve") for mt in metrics):  # mlflow itself batches the scalars, params, a summary
            calls.append(len(metrics))
        return real(self, run_id, metrics=metrics, params=params, tags=tags, **kw)

    monkeypatch.setattr(mlflow.MlflowClient, "log_batch", spy)
    model = m.train(history, params={"n_estimators": 250}, band=False)  # 6 curves x 250 = 1500 points
    assert sum(calls) == 1500 and max(calls) <= 1000 and len(calls) >= 2
    assert len(_history_of(model.mlflow_run_id, "holdout_rmse_curve")) == 250


def test_nothing_is_logged_without_a_tracking_uri(history, no_tracking, tmp_path):
    model = m.train(history, params=SMALL, band=False)
    assert model.mlflow_run_id is None and model.training_curves
    assert not list(tmp_path.iterdir())  # no mlruns, no database


# -- tuning --------------------------------------------------------------------------------------------------
def test_the_promoted_model_run_has_the_curves(history, tracking):
    result = tune.search(history, n_trials=1, seed=0, feature_sets=["calendar"])
    promo = tune.promote(result)
    n = result.best_params["n_estimators"]
    assert len(_history_of(promo.run_id, "holdout_rmse_curve")) == n


def _children(client, parent_id: str, experiment: str):
    exp = client.get_experiment_by_name(experiment)
    return [r for r in client.search_runs([exp.experiment_id], f"tags.mlflow.parentRunId = '{parent_id}'")]


def test_trial_runs_have_no_curves_by_default(history, tracking):
    result = tune.search(history, n_trials=2, seed=0, feature_sets=["calendar"])
    kids = _children(mlflow.MlflowClient(), result.parent_run_id, tune.TUNING_EXPERIMENT)
    assert len(kids) == 2
    assert all(not _history_of(k.info.run_id, "holdout_rmse_curve") for k in kids)


def test_log_curves_logs_the_holdout_curve_of_each_trial(history, tracking):
    result = tune.search(history, n_trials=2, seed=0, feature_sets=["calendar"], log_curves=True)
    kids = _children(mlflow.MlflowClient(), result.parent_run_id, tune.TUNING_EXPERIMENT)
    for kid in kids:
        n = int(kid.data.params["n_estimators"])
        points = _history_of(kid.info.run_id, "holdout_rmse_curve")
        assert sorted(p.step for p in points) == list(range(n))
        assert len(_history_of(kid.info.run_id, "holdout_mae_curve")) == n


def test_the_parent_run_logs_the_optimisation_progress_per_trial(history, tracking):
    result = tune.search(history, n_trials=4, seed=0, feature_sets=["calendar"])
    trial = sorted(_history_of(result.parent_run_id, "trial_balance_error_90d"), key=lambda p: p.step)
    best = sorted(_history_of(result.parent_run_id, "best_so_far_balance_error_90d"), key=lambda p: p.step)
    assert [p.step for p in trial] == [p.step for p in best] == [0, 1, 2, 3]
    values = [p.value for p in best]
    assert values == sorted(values, reverse=True)  # never gets worse
    assert values[-1] == pytest.approx(min(p.value for p in trial))


def test_the_tune_cli_has_a_log_curves_flag(history, tracking, monkeypatch, capsys):
    from cfo.forecast import evaluate as ev

    monkeypatch.setattr(ev, "load_statement", lambda: history)
    seen: dict = {}
    real = tune.search

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(tune, "search", spy)
    assert tune.main(["--trials", "1", "--source", "csv", "--feature-sets", "calendar", "--log-curves"]) == 0
    assert seen["log_curves"] is True
    capsys.readouterr()
    assert tune.main(["--trials", "1", "--source", "csv", "--feature-sets", "calendar"]) == 0
    assert seen["log_curves"] is False
