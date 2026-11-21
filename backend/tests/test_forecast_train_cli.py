"""`python -m cfo.forecast.train`: retrain the forecast model on the current history, log, register, promote (T064).

Real `mlflow` on a temporary sqlite store, a small synthetic history (the pattern of `test_forecast_tune.py`).
Also guards the experiment fix: logging a forecast run must not change the process-wide active experiment, because
the API and the Scout worker write their LLM traces in the same process (`cfo.observability`).
Skipped when the optional extras are missing (`uv run --extra mlflow --extra tuning ...`).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

mlflow = pytest.importorskip("mlflow")
pytest.importorskip("optuna")

from cfo.forecast import evaluate as ev  # noqa: E402
from cfo.forecast import model as m  # noqa: E402
from cfo.forecast import train as train_cli  # noqa: E402
from cfo.forecast import tune  # noqa: E402

CHAMPION_URI = f"models:/{m.REGISTERED_MODEL_NAME}@{m.CHAMPION_ALIAS}"
CHALLENGER_URI = f"models:/{m.REGISTERED_MODEL_NAME}@challenger"
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
def tracking(tmp_path, monkeypatch, history):
    """A fresh sqlite tracking + registry store; the history comes from the CSV loader stub."""
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    mlflow.set_tracking_uri(uri)
    yield uri
    mlflow.set_tracking_uri(None)


def _versions(client) -> list[str]:
    return sorted(str(v.version) for v in client.search_model_versions(f"name='{m.REGISTERED_MODEL_NAME}'"))


def _alias_version(client, alias: str) -> str | None:
    try:
        return str(client.get_model_version_by_alias(m.REGISTERED_MODEL_NAME, alias).version)
    except Exception:  # noqa: BLE001 - no such alias
        return None


def _run(argv: list[str], capsys) -> tuple[int, str]:
    code = train_cli.main(["--source", "csv", *argv])
    return code, capsys.readouterr().out


# -- train and log ---------------------------------------------------------------------------------------
def test_plain_run_trains_logs_a_run_and_registers_nothing(tracking, capsys):
    code, out = _run(["--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert data["feature_set"] and data["mlflow_run_id"] and data["registered"] is None
    client = mlflow.MlflowClient()
    exp = client.get_experiment_by_name(m.MLFLOW_EXPERIMENT)
    assert [r.info.run_id for r in client.search_runs([exp.experiment_id])] == [data["mlflow_run_id"]]
    assert _versions(client) == []


def test_json_has_no_bare_nan_and_the_90_day_error(tracking, capsys):
    code, out = _run(["--register", "--json"], capsys)
    assert code == 0

    def reject(constant):
        raise ValueError(f"bare {constant} in the JSON output")

    data = json.loads(out, parse_constant=reject)
    assert data["balance_error_90d"] is not None and data["trained_until"] == "2025-11-30"
    assert data["registered"]["version"] == "1"


def test_without_a_tracking_uri_it_still_trains_and_says_nothing_was_logged(history, monkeypatch, capsys):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    code, out = _run([], capsys)
    assert code == 0 and "not logged" in out


# -- register and promote --------------------------------------------------------------------------------
def test_register_makes_a_challenger_and_leaves_the_champion_alone(tracking, capsys):
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)  # champion v1
    code, out = _run(["--register", "--json"], capsys)
    assert code == 0
    client = mlflow.MlflowClient()
    assert _versions(client) == ["1", "2"]
    assert _alias_version(client, "challenger") == "2" and _alias_version(client, m.CHAMPION_ALIAS) == "1"
    assert json.loads(out)["registered"]["moved_champion"] is False


def test_promote_if_better_alone_implies_register_and_takes_the_empty_alias(tracking, capsys):
    code, out = _run(["--promote-if-better", "--json"], capsys)
    assert code == 0
    client = mlflow.MlflowClient()
    assert _versions(client) == ["1"] and _alias_version(client, m.CHAMPION_ALIAS) == "1"
    assert json.loads(out)["registered"]["moved_champion"] is True
    assert m.ForecastModel.load_from_mlflow(CHAMPION_URI).predict(pd.date_range("2025-12-01", "2025-12-10")).notna().all()


def test_a_clearly_better_candidate_takes_the_champion_alias(tracking, capsys):
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)  # weak champion v1
    code, out = _run(["--register", "--promote-if-better"], capsys)
    assert code == 0
    assert _alias_version(mlflow.MlflowClient(), m.CHAMPION_ALIAS) == "2"
    assert "champion moved to v2" in out


def test_nothing_changes_for_an_equal_candidate_and_the_line_says_so(tracking, capsys):
    m.train(_history(), register=True, holdout_days=0)  # the same default configuration is champion v1
    code, out = _run(["--register", "--promote-if-better"], capsys)
    assert code == 0
    assert _alias_version(mlflow.MlflowClient(), m.CHAMPION_ALIAS) == "1"
    assert "champion v1 kept: candidate 90-day balance error" in out and " vs " in out


def test_promotion_rule_is_the_one_of_tune_promote(tracking, monkeypatch, capsys):
    calls: list[dict] = []
    real = tune.promote

    def spy(result, **kwargs):
        calls.append(kwargs)
        return real(result, **kwargs)

    monkeypatch.setattr(tune, "promote", spy)
    _run(["--register", "--promote-if-better", "--min-gain", "0.05"], capsys)
    assert calls == [{"if_better": True, "min_gain": 0.05}]


def test_a_failing_champion_lookup_exits_2_and_registers_nothing(tracking, monkeypatch, capsys, caplog):
    m.train(_history(), register=True, holdout_days=0)
    from mlflow.exceptions import MlflowException

    def broken(self, *a, **k):
        raise MlflowException("server on fire", error_code="INTERNAL_ERROR")

    monkeypatch.setattr(mlflow.MlflowClient, "get_model_version_by_alias", broken)
    code, out = _run(["--register", "--promote-if-better", "--json"], capsys)
    monkeypatch.undo()
    assert code == 2
    assert "could not look up the current champion" in caplog.text
    assert json.loads(out)["promotion_error"]
    client = mlflow.MlflowClient()
    assert _versions(client) == ["1"] and _alias_version(client, m.CHAMPION_ALIAS) == "1"


def test_register_without_a_tracking_uri_is_an_error(history, monkeypatch, capsys, caplog):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setattr(ev, "load_statement", lambda: history)
    code, _ = _run(["--register"], capsys)
    assert code == 2 and "MLFLOW_TRACKING_URI" in caplog.text


def test_min_gain_must_not_be_negative(tracking, capsys):
    with pytest.raises(SystemExit) as exc:
        train_cli.main(["--source", "csv", "--min-gain", "-0.1"])
    assert exc.value.code == 2


def test_source_db_fails_loudly(tracking, monkeypatch):
    import psycopg

    def refuse(*a, **k):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)
    with pytest.raises(psycopg.OperationalError):
        train_cli.main(["--source", "db"])


# -- the active experiment is not touched ------------------------------------------------------------------
@pytest.fixture
def agent_experiment(tracking):
    """What `init_tracing()` does at process start: `cfo-agent` is the active experiment."""
    experiment = mlflow.set_experiment("cfo-agent")
    yield experiment.experiment_id


def _active_experiment_id() -> str:
    """The experiment the next `start_run()` (and a trace) would land in."""
    with mlflow.start_run() as run:
        return run.info.experiment_id


def test_logging_a_trained_model_keeps_the_active_experiment(history, agent_experiment):
    m.train(history, band=False)
    assert _active_experiment_id() == agent_experiment
    forecast_runs = mlflow.search_runs(experiment_names=[m.MLFLOW_EXPERIMENT])
    assert len(forecast_runs) == 1  # the run itself still went to liquidity-forecast


def test_the_train_cli_keeps_the_active_experiment(agent_experiment, capsys):
    code, _ = _run(["--register", "--promote-if-better"], capsys)
    assert code == 0 and _active_experiment_id() == agent_experiment


def test_a_search_and_its_promotion_keep_the_active_experiment(history, agent_experiment):
    result = tune.search(history, n_trials=2, seed=0)
    tune.promote(result, if_better=True)
    assert _active_experiment_id() == agent_experiment
    assert len(mlflow.search_runs(experiment_names=[tune.TUNING_EXPERIMENT])) == 3  # parent + 2 trials
    assert len(mlflow.search_runs(experiment_names=[m.MLFLOW_EXPERIMENT])) == 1


def test_a_custom_experiment_name_is_honoured(history, agent_experiment, monkeypatch):
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "forecast-sandbox")
    m.train(history, band=False)
    assert len(mlflow.search_runs(experiment_names=["forecast-sandbox"])) == 1
    assert _active_experiment_id() == agent_experiment


# -- review round 1: a promotion must not run on the CSV fallback ------------------------------------------------
@pytest.fixture
def no_database(monkeypatch):
    """`--source auto` cannot reach Postgres and so falls back to the organizer's CSV (the history stub)."""
    import psycopg

    def refuse(*a, **k):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", refuse)


@pytest.mark.parametrize("flags", [["--register"], ["--promote-if-better"], ["--register", "--promote-if-better"]])
def test_train_refuses_to_promote_from_the_csv_fallback(tracking, no_database, capsys, caplog, flags):
    code = train_cli.main(["--source", "auto", *flags])
    assert code == 2
    assert "--source db" in caplog.text and "--source csv" in caplog.text
    assert _versions(mlflow.MlflowClient()) == []


@pytest.mark.parametrize("flags", [["--promote"], ["--promote-if-better"]])
def test_tune_refuses_to_promote_from_the_csv_fallback(tracking, no_database, capsys, caplog, flags):
    code = tune.main(["--source", "auto", "--trials", "1", "--feature-sets", "calendar", *flags])
    assert code == 2
    assert "--source db" in caplog.text and "--source csv" in caplog.text
    client = mlflow.MlflowClient()
    assert _versions(client) == []
    assert client.get_experiment_by_name(tune.TUNING_EXPERIMENT) is None  # refused before the search started


def test_plain_runs_keep_the_csv_fallback(tracking, no_database, capsys):
    assert train_cli.main(["--source", "auto"]) == 0
    assert tune.main(["--source", "auto", "--trials", "1", "--feature-sets", "calendar"]) == 0


def test_the_csv_can_be_promoted_on_purpose(tracking, no_database, capsys):
    assert train_cli.main(["--source", "csv", "--register"]) == 0
    assert tune.main(["--source", "csv", "--trials", "1", "--feature-sets", "calendar", "--promote"]) == 0
    assert _versions(mlflow.MlflowClient()) == ["1", "2"]


# -- review round 1: the champion moved between the comparison and the write ----------------------------------------
@pytest.fixture
def rival(monkeypatch):
    """Another job moves `champion` to version 1 right after our candidate was registered (once `armed`)."""
    real = m.log_to_mlflow_if_configured
    armed = {"on": False}

    def log_then_rival(model, **kwargs):
        run_id = real(model, **kwargs)
        if armed["on"]:
            mlflow.MlflowClient().set_registered_model_alias(m.REGISTERED_MODEL_NAME, m.CHAMPION_ALIAS, "1")
        return run_id

    monkeypatch.setattr(m, "log_to_mlflow_if_configured", log_then_rival)
    return armed


def test_promote_does_not_overwrite_a_champion_that_changed_meanwhile(tracking, rival):
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)  # v1
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)  # v2 is the champion when the decision is made
    rival["on"] = True
    result = tune.TuningResult(best_feature_set="level_baseline", best_params={}, transactions=_history(),
                               best_scores=tune.score_config(_history(), "level_baseline", {})[0])
    promo = tune.promote(result, if_better=True)
    assert promo.version == "3" and not promo.moved_champion and promo.reason.startswith(tune.CHAMPION_CHANGED)
    client = mlflow.MlflowClient()
    assert _alias_version(client, m.CHAMPION_ALIAS) == "1"  # the rival's write stands
    assert _alias_version(client, "challenger") == "3"


def test_the_train_cli_reports_a_lost_race_and_exits_0(tracking, rival, capsys):
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)
    rival["on"] = True
    code, out = _run(["--register", "--promote-if-better"], capsys)
    assert code == 0 and "changed during promotion" in out and "margin not reached" not in out
    assert _alias_version(mlflow.MlflowClient(), m.CHAMPION_ALIAS) == "1"


def test_an_unchanged_champion_is_still_replaced(tracking, capsys):
    m.train(_history(), **BAD_TRAIN, register=True, holdout_days=0)
    code, out = _run(["--register", "--promote-if-better"], capsys)
    assert code == 0 and _alias_version(mlflow.MlflowClient(), m.CHAMPION_ALIAS) == "2"
