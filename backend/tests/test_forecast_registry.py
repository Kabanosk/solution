"""Forecast model through MLflow: log + register, load by alias, the API service picks it up (T026).

Real `mlflow` against a temporary `sqlite:///` tracking URI and a temp artifact dir; no server, no Postgres.
Skipped when the optional extra is not installed (`uv run --extra mlflow ...`).
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pandas as pd
import pytest

mlflow = pytest.importorskip("mlflow")

from cfo.api import services  # noqa: E402
from cfo.config import get_settings  # noqa: E402
from cfo.forecast import model as m  # noqa: E402

CHAMPION_URI = f"models:/{m.REGISTERED_MODEL_NAME}@{m.CHAMPION_ALIAS}"
FUTURE = pd.date_range("2025-12-01", "2026-07-31")


def _history(revenue: float = 200.0) -> pd.DataFrame:
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-11-30"):
        if day.month != 6:
            rows.append(
                (
                    day,
                    revenue + (100.0 if day.day_of_week >= 5 else 0.0),
                    "revenue",
                    None,
                )
            )
        if day.day == 28:
            rows.append((day, -2000.0, "payroll", "kitchen"))
        if day.day == 1:
            rows.append((day, -1000.0, "rent", "front"))
        if day.day == 15:
            rows.append((day, -4000.0, "partner_investment", None))
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


@pytest.fixture
def history() -> pd.DataFrame:
    return _history()


@pytest.fixture
def tracking(tmp_path, monkeypatch):
    """A fresh sqlite tracking + registry store; artifacts in the temp dir (never `./mlruns` of the repo)."""
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    mlflow.set_tracking_uri(uri)
    mlflow.create_experiment(
        m.MLFLOW_EXPERIMENT, artifact_location=(tmp_path / "artifacts").as_uri()
    )
    yield uri
    mlflow.set_tracking_uri(None)


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """The API service with an empty model dir and clean caches; `FORECAST_MODEL_URI` is set by each test."""
    monkeypatch.setenv(
        "FORECAST_MODEL_PATH", str(tmp_path / "local" / "liquidity_xgb.json")
    )
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    services.reset_forecast_cache()
    get_settings.cache_clear()
    yield monkeypatch
    services.reset_forecast_cache()
    get_settings.cache_clear()


def _set_model_uri(monkeypatch, uri: str) -> None:
    monkeypatch.setenv("FORECAST_MODEL_URI", uri)
    get_settings.cache_clear()


# -- model.py: log, register, load -------------------------------------------------------------------------
def test_logging_returns_the_run_id_and_stores_the_sidecar(history, tracking):
    model = m.train(history, log_to_mlflow=False)
    run_id = m.log_to_mlflow_if_configured(model)
    assert isinstance(run_id, str) and run_id
    assert model.mlflow_run_id == run_id and model.model_version is None
    run = mlflow.get_run(run_id)
    assert run.data.metrics["holdout_mae"] == pytest.approx(
        model.metrics["holdout_mae"]
    )
    names = {a.path for a in mlflow.MlflowClient().list_artifacts(run_id)}
    assert {m.META_ARTIFACT, "feature_importances.json"} <= names
    # not registered unless asked
    assert mlflow.MlflowClient().search_registered_models() == []


def test_register_and_load_by_alias_gives_equal_predictions(history, tracking):
    model = m.train(history, register=True)
    assert model.mlflow_run_id and model.model_version == "1"

    loaded = m.ForecastModel.load_from_mlflow(CHAMPION_URI)
    assert loaded.mlflow_run_id == model.mlflow_run_id
    assert loaded.model_version == "1"
    assert loaded.trained_at is not None and loaded.trained_at.tzinfo is not None
    assert (
        loaded.shutdown_days == model.shutdown_days
        and loaded.due_days == model.due_days
    )
    assert loaded.trained_until == model.trained_until
    assert loaded.metrics == pytest.approx(model.metrics)
    pd.testing.assert_series_equal(loaded.predict(FUTURE), model.predict(FUTURE))
    pd.testing.assert_series_equal(
        loaded.feature_importances(), model.feature_importances()
    )
    assert loaded.explain("2026-06-01", "2026-06-30")[
        "top_drivers"
    ]  # SHAP works on the rebuilt regressor


def test_load_by_version_number_and_by_run_uri(history, tracking):
    model = m.train(history, register=True)
    by_version = m.ForecastModel.load_from_mlflow(
        f"models:/{m.REGISTERED_MODEL_NAME}/1"
    )
    assert (by_version.mlflow_run_id, by_version.model_version) == (
        model.mlflow_run_id,
        "1",
    )
    by_run = m.ForecastModel.load_from_mlflow(f"runs:/{model.mlflow_run_id}/model")
    assert by_run.mlflow_run_id == model.mlflow_run_id and by_run.model_version is None
    pd.testing.assert_series_equal(by_run.predict(FUTURE), model.predict(FUTURE))


def test_custom_name_and_alias_and_alias_moves_to_the_new_version(history, tracking):
    first = m.train(history, log_to_mlflow=False)
    m.log_to_mlflow_if_configured(
        first, register=True, model_name="liq-test", alias="staging"
    )
    second = m.train(_history(revenue=400.0), log_to_mlflow=False)
    m.log_to_mlflow_if_configured(
        second, register=True, model_name="liq-test", alias="staging"
    )
    assert (first.model_version, second.model_version) == ("1", "2")
    loaded = m.ForecastModel.load_from_mlflow("models:/liq-test@staging")
    assert loaded.model_version == "2" and loaded.mlflow_run_id == second.mlflow_run_id
    pd.testing.assert_series_equal(loaded.predict(FUTURE), second.predict(FUTURE))


def test_resolve_does_not_download_the_model(history, tracking):
    model = m.train(history, register=True)
    ref = m.resolve_mlflow_model(CHAMPION_URI)
    assert (ref.run_id, ref.version) == (model.mlflow_run_id, "1")
    assert (
        ref.uri == f"models:/{m.REGISTERED_MODEL_NAME}/1"
    )  # pinned: alias moves do not race the download


# -- the API service ---------------------------------------------------------------------------------------
def test_api_service_uses_the_mlflow_model_and_reports_run_id(
    history, tracking, api_env
):
    model = m.train(history, register=True)
    _set_model_uri(api_env, CHAMPION_URI)
    api_env.setattr(services, "_transactions", lambda repo, **kw: history)

    served, trained_at = services.forecast_model(history)
    assert served.mlflow_run_id == model.mlflow_run_id and trained_at is not None
    assert not m.default_model_path().exists()  # nothing was trained locally

    out = services.liquidity_forecast(SimpleNamespace(conn=None), 60, "day")
    assert out.model.mlflow_run_id == model.mlflow_run_id
    assert out.model.version == "1"
    assert out.model.trained_at == trained_at
    expected = m.liquidity_forecast(60, model=model, transactions=history)
    assert float(out.points[-1].balance.amount) == pytest.approx(
        float(expected["balance"].iloc[-1]), abs=0.01
    )


def test_api_service_caches_and_follows_a_promoted_version(history, tracking, api_env):
    m.train(history, register=True)
    _set_model_uri(api_env, CHAMPION_URI)
    first, _ = services.forecast_model(history)
    assert first.model_version == "1"

    loads: list[str] = []
    real_load = m.ForecastModel.load_from_mlflow.__func__
    api_env.setattr(
        m.ForecastModel,
        "load_from_mlflow",
        classmethod(lambda cls, uri: (loads.append(uri), real_load(cls, uri))[1]),
    )

    promoted = m.train(
        _history(revenue=400.0), register=True
    )  # version 2 takes the alias
    assert promoted.model_version == "2"
    assert (
        services.forecast_model(history)[0] is first
    )  # inside the 60 s window: no re-resolve
    assert loads == []

    clock = time.monotonic()
    api_env.setattr(
        services.time, "monotonic", lambda: clock + services.MLFLOW_RECHECK_SECONDS + 1
    )
    second, _ = services.forecast_model(history)
    assert (
        second.model_version == "2" and second.mlflow_run_id == promoted.mlflow_run_id
    )
    assert len(loads) == 1

    # window expired again but the alias did not move: resolved, not downloaded again
    api_env.setattr(
        services.time, "monotonic", lambda: clock + 3 * services.MLFLOW_RECHECK_SECONDS
    )
    assert services.forecast_model(history)[0] is second
    assert len(loads) == 1


def test_unreachable_tracking_uri_falls_back_to_the_local_model(
    history, api_env, caplog
):
    api_env.setenv(
        "MLFLOW_TRACKING_URI", "http://127.0.0.1:9"
    )  # nothing listens on the discard port
    _set_model_uri(api_env, CHAMPION_URI)
    try:
        with caplog.at_level(logging.WARNING, logger=services.log.name):
            served, _ = services.forecast_model(history)
    finally:
        mlflow.set_tracking_uri(None)
    assert served.mlflow_run_id is None and served.model_version is None
    assert m.default_model_path().is_file()  # trained on first use, as before
    assert any("MLflow" in r.getMessage() for r in caplog.records)

    api_env.setattr(services, "_transactions", lambda repo, **kw: history)
    out = services.liquidity_forecast(SimpleNamespace(conn=None), 30, "day")
    assert out.model.mlflow_run_id is None and out.model.version.startswith(
        "trained-until-"
    )


def test_missing_registered_model_falls_back(history, tracking, api_env):
    _set_model_uri(api_env, "models:/does-not-exist@champion")
    served, _ = services.forecast_model(history)
    assert served.mlflow_run_id is None


def test_slow_mlflow_does_not_block_the_request(history, tracking, api_env):
    _set_model_uri(api_env, CHAMPION_URI)
    api_env.setattr(services, "MLFLOW_LOAD_TIMEOUT_SECONDS", 0.2)
    api_env.setattr(m, "resolve_mlflow_model", lambda uri: time.sleep(5))
    started = time.monotonic()
    served, _ = services.forecast_model(history)
    assert time.monotonic() - started < 4
    assert served.mlflow_run_id is None


def test_without_model_uri_mlflow_is_not_consulted(history, tracking, api_env):
    m.train(history, register=True)
    api_env.delenv("FORECAST_MODEL_URI", raising=False)
    get_settings.cache_clear()
    served, _ = services.forecast_model(history)
    assert served.mlflow_run_id is None and m.default_model_path().is_file()
