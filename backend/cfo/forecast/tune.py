"""Hyperparameter search for the forecast XGBoost: Optuna, every trial a run in MLflow, the winner promotable (T062).

`python -m cfo.forecast.tune --trials 200 [--tracking-uri U] [--promote | --promote-if-better] [--json]`; the
notebook `notebooks/forecast_tuning.ipynb` is a thin front over the same functions, so the search also runs
headless (cluster Job, T064).

* Objective: `balance_error_90d` of the rolling-origin harness (`evaluate.py`), because the product shows balances.
  Ranking is lexicographic (`rank_key`): `daily_mae` only decides exact ties. When no fold reaches day 90 the
  objective falls back to the longest horizon that exists (`objective_value`).
* Constrained mode (T068, `mode="constrained"`, `--constrained`): minimises `balance_error_90d` **subject to**
  `daily_mae <= the reference's daily_mae` (the current default scored on the same folds). A violator is penalised by
  `CONSTRAINT_WEIGHT` per EUR of excess (only in this mode, and only above the cap), so it cannot win while a feasible
  trial exists; the result is the best feasible trial, ranked as always by `rank_key`. Folds can be passed as `origins=`
  (`folds.weekly_origins`: 27 Monday origins, or a denser grid with `every_days`). `robust_search` repeats the search with
  several seeds and picks the median-of-seeds configuration; it reports whether the seeds' best scores stay within
  `STABILITY_TOLERANCE` (5 %) of the chosen one. The default mode is the T062 behaviour, unchanged.
* Search space (`space`, drawn by `suggest`): feature set, `n_estimators`, `max_depth`, `learning_rate`, `min_child_weight`,
  `subsample`, `colsample_bytree`, `reg_lambda`, `reg_alpha`, `gamma`, loss (`space`). Ranges are small-model sized
  (about 330 training days, 4-6 folds). The current default configuration is enqueued as trial 0, so the search can never
  report something worse than "no tuning" and TPE starts from a known point.
* Overfitting guard: the mean is dominated by one origin (30 Jun, the July revenue step cannot be known from
  Jan-Jun), so every trial also logs its per-origin cumulative errors; `TuningResult.top(10)` shows the spread of
  the best ten; a configuration counts as better than a reference only by a margin (`is_better`, default 2 %).
* MLflow: parent run `tuning-<date>` in experiment `liquidity-forecast-tuning`, nested child run `trial-NNN` per trial
  (params, all `score()` metrics, `trial_number`, per-origin errors), best params / top-10 / trials table /
  optimisation history plot as parent artifacts. Tracking is best effort: without a tracking URI the search runs
  and just does not log; a tracking failure disables logging, never the search.
* `promote`: retrain the best configuration on the full history and register it through
  `model.log_to_mlflow_if_configured(register=True)` with alias `challenger`. With `if_better` the alias `champion`
  moves only when the candidate beats the current champion by the margin, both scored now on the same data (the
  champion's feature set and parameters are re-trained on every fold; a registered model is a fixed fit).
* Data: Postgres (`DATABASE_URL`) when it works, else the organizer's CSV (`--source auto|db|csv`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import psycopg

from cfo.forecast import evaluate as ev
from cfo.forecast import features as feat
from cfo.forecast import model as m

log = logging.getLogger(__name__)

TUNING_EXPERIMENT = "liquidity-forecast-tuning"
CHALLENGER_ALIAS = "challenger"
DEFAULT_FEATURE_SETS: tuple[str, ...] = tuple(feat.FEATURE_SETS)
DEFAULT_MIN_GAIN = 0.02  # a candidate is better only by 2 % of the reference's balance error
OBJECTIVE_METRICS = ("balance_error_90d", "balance_error_60d", "balance_error_30d")  # longest horizon first
LOSSES = ("reg:squarederror", "reg:absoluteerror")
MODE_BALANCE = "balance"  # the T062 objective: balance_error_90d only (the default)
MODE_CONSTRAINED = "constrained"  # T068: balance_error_90d subject to daily_mae <= the reference's
CONSTRAINT_WEIGHT = 100.0  # EUR of objective per EUR of daily MAE above the cap (constrained mode only)
STABILITY_TOLERANCE = 0.05  # the seeds' best scores must stay within this share of the chosen configuration's
_CAP_SLACK = 1e-9  # float noise around "exactly the reference's daily MAE"
# The tunable parameters with the value XGBoost uses when `model.py` does not set them.
_XGB_DEFAULTS: dict[str, Any] = {
    "n_estimators": 100, "max_depth": 6, "learning_rate": 0.3, "min_child_weight": 1, "subsample": 1.0,
    "colsample_bytree": 1.0, "reg_lambda": 1.0, "reg_alpha": 0.0, "gamma": 0.0, "objective": "reg:squarederror",
}


# -- numbers -----------------------------------------------------------------------------------------------
def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def finite(value: Any) -> Any:
    """`value` with every NaN / infinity replaced by None (JSON and MLflow never get a bare NaN)."""
    if isinstance(value, Mapping):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if hasattr(value, "item") and not isinstance(value, (int, float)):  # numpy scalar
        value = value.item()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def objective_value(scores: Mapping[str, float]) -> tuple[float, str]:
    """(value to minimise, metric name): `balance_error_90d`, else the longest balance horizon that exists, else
    `daily_mae`. Only the balance error: the daily MAE must never outweigh a difference in it (see `rank_key`)."""
    for key in OBJECTIVE_METRICS:
        if _is_finite(scores.get(key)):
            return float(scores[key]), key
    return float(scores["daily_mae"]), "daily_mae"


def rank_key(scores: Mapping[str, float]) -> tuple[float, float]:
    """Lexicographic ranking of a trial: the objective first, the daily MAE only decides exact ties."""
    return objective_value(scores)[0], float(scores["daily_mae"])


def constrained_value(scores: Mapping[str, float], cap: float) -> tuple[float, bool]:
    """(value to minimise, feasible) of the constrained objective: `objective_value` (the balance error alone) plus
    `CONSTRAINT_WEIGHT` EUR for every EUR the daily MAE is above `cap`. Feasible = not above the cap; a feasible trial's
    value is its plain objective, so inside the feasible set the ranking is the default lexicographic one."""
    value, _ = objective_value(scores)
    over = max(0.0, float(scores["daily_mae"]) - cap * (1.0 + _CAP_SLACK))
    return value + CONSTRAINT_WEIGHT * over, over == 0.0


_SECRET_QUERY = re.compile(r"=[^&#]*")


def redact_uri(uri: str | None) -> str | None:
    """`uri` without secrets: a password in the userinfo becomes `***`, query values (tokens) are masked."""
    if not uri:
        return uri
    parts = urlsplit(uri)
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        netloc = f"{user}:***@{host}" if ":" in userinfo else f"{user}@{host}"
    query = _SECRET_QUERY.sub("=***", parts.query)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _check_min_gain(value: float) -> float:
    if not _is_finite(value) or value < 0:
        raise ValueError(f"min_gain must be >= 0 (a worse model must never count as better), got {value!r}")
    return float(value)


def is_better(candidate: float, reference: float, min_gain: float = DEFAULT_MIN_GAIN) -> bool:
    """`candidate` beats `reference` by at least `min_gain` of the reference (errors: lower is better)."""
    if not _is_finite(candidate):
        return False
    if not _is_finite(reference):
        return True
    return float(candidate) < float(reference) * (1.0 - min_gain)


def per_origin_errors(folds: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per origin: fold length, cumulative error (predicted - actual, EUR) after 30 days and at the end of the fold
    (day 90 for a full fold). The mean over origins hides which origin is wrong; keep these next to the score."""
    out: dict[str, dict[str, float]] = {}
    for origin, fold in folds.groupby("origin"):
        at30 = fold.loc[fold["horizon"] == 30, "cum_error"]
        out[str(pd.Timestamp(origin).date())] = {
            "days": float(len(fold)),
            "cum_error_30d": float(at30.iloc[0]) if len(at30) else math.nan,
            "cum_error_end": float(fold["cum_error"].iloc[-1]),
        }
    return out


def error_without_first_origin(per_origin: Mapping[str, Mapping[str, float]]) -> float:
    """Mean |cumulative error at the end of the fold| over the origins after the first (NaN if there is none).
    The first origin (30 Jun) carries the July revenue step nobody can know; a gain that exists only there is
    suspect, so this number sits next to the objective in every summary."""
    errors = [abs(v["cum_error_end"]) for _, v in sorted(per_origin.items())[1:] if _is_finite(v["cum_error_end"])]
    return sum(errors) / len(errors) if errors else math.nan


# -- configuration and scoring -------------------------------------------------------------------------
def baseline_params(feature_set: str = m.DEFAULT_FEATURE_SET) -> dict[str, Any]:
    """The tunable parameters of the model as it trains today for `feature_set` (the search's reference)."""
    merged = {**m.DEFAULT_PARAMS, **m.FEATURE_SET_PARAMS.get(feature_set, {})}
    return {key: merged.get(key, default) for key, default in _XGB_DEFAULTS.items()}


def config_trainer(feature_set: str, params: Mapping[str, Any]) -> ev.Trainer:
    """The harness trainer of one configuration (`m.train` per fold, no MLflow, no internal holdout, no band: the
    band does not change a prediction and costs several fits per fold)."""
    def trainer(tx: pd.DataFrame) -> m.ForecastModel:
        return m.train(tx, feature_set=feature_set, params=dict(params), log_to_mlflow=False, holdout_days=0,
                       band=False)

    return trainer


def score_config(transactions: pd.DataFrame, feature_set: str, params: Mapping[str, Any],
                 **rolling_kw: Any) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Rolling-origin scores and per-origin errors of one configuration."""
    folds = ev.rolling_origin(transactions, config_trainer(feature_set, params), **rolling_kw)
    return ev.score(folds), per_origin_errors(folds)


def space(feature_sets: Sequence[str]) -> dict[str, Any]:
    """The search space as Optuna distributions: small-model sized (about 330 training days, 4-6 folds)."""
    from optuna.distributions import CategoricalDistribution as Cat
    from optuna.distributions import FloatDistribution as Flt
    from optuna.distributions import IntDistribution as Int

    return {
        "feature_set": Cat(tuple(feature_sets)),
        "n_estimators": Int(50, 600),
        "max_depth": Int(2, 6),
        "learning_rate": Flt(0.01, 0.3, log=True),
        "min_child_weight": Int(1, 20),
        "subsample": Flt(0.5, 1.0),
        "colsample_bytree": Flt(0.5, 1.0),
        "reg_lambda": Flt(0.1, 30.0, log=True),
        "reg_alpha": Flt(0.0, 5.0),
        "gamma": Flt(0.0, 5.0),
        "objective": Cat(LOSSES),
    }


def suggest(trial: Any, feature_sets: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """Draw (feature set, XGBoost parameters) for an Optuna trial from `space`."""
    from optuna.distributions import CategoricalDistribution, IntDistribution

    drawn: dict[str, Any] = {}
    for name, dist in space(feature_sets).items():
        if isinstance(dist, CategoricalDistribution):
            drawn[name] = trial.suggest_categorical(name, list(dist.choices))
        elif isinstance(dist, IntDistribution):
            drawn[name] = trial.suggest_int(name, dist.low, dist.high)
        else:
            drawn[name] = trial.suggest_float(name, dist.low, dist.high, log=dist.log)
    return drawn.pop("feature_set"), drawn


def param_importance(result: TuningResult) -> pd.Series:
    """How much each searched parameter explains of the objective (Optuna's fANOVA), largest first. Rebuilds an
    in-memory study from the trial table, so it works on a result of any earlier run of the same process."""
    import optuna
    from optuna.trial import TrialState, create_trial

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sets = sorted({t["feature_set"] for t in result.trials})
    dists = space(sets)
    study = optuna.create_study(direction="minimize")
    for t in result.trials:
        if t.get("error"):
            continue
        study.add_trial(create_trial(params={"feature_set": t["feature_set"], **t["params"]},
                                     distributions=dists, value=t["objective"], state=TrialState.COMPLETE))
    importances = optuna.importance.get_param_importances(study)
    return pd.Series(importances, name="importance").sort_values(ascending=False)


# -- result ------------------------------------------------------------------------------------------------
@dataclass
class TuningResult:
    """Outcome of `search` (or a hand-made candidate for `promote`)."""

    best_feature_set: str
    best_params: dict[str, Any]
    best_scores: dict[str, float]
    baseline_scores: dict[str, float] = field(default_factory=dict)  # the model as it trains today
    baseline_feature_set: str = m.DEFAULT_FEATURE_SET
    baseline_params: dict[str, Any] = field(default_factory=dict)
    per_origin: dict[str, dict[str, float]] = field(default_factory=dict)  # of the best trial
    baseline_per_origin: dict[str, dict[str, float]] = field(default_factory=dict)
    trials: list[dict[str, Any]] = field(default_factory=list)
    best_trial_number: int | None = None
    n_trials: int = 0
    seed: int = 0
    seconds_per_trial: float = math.nan
    objective_metric: str = "balance_error_90d"
    min_gain: float = DEFAULT_MIN_GAIN
    tracking_uri: str | None = field(default=None, repr=False)  # may carry credentials: shown only redacted
    experiment: str | None = None
    parent_run_id: str | None = None
    data_source: str | None = None
    mode: str = MODE_BALANCE
    daily_mae_cap: float | None = None  # constrained mode: the reference's daily MAE the best trial may not exceed
    feasible: bool | None = None  # constrained mode: whether the best trial respects the cap
    origins: list[Any] | None = field(default=None, repr=False)  # the folds the scores come from (None = month ends)
    transactions: pd.DataFrame | None = field(default=None, repr=False)

    def train_kwargs(self) -> dict[str, Any]:
        """Arguments of `model.train` that reproduce the best configuration."""
        return {"feature_set": self.best_feature_set, "params": dict(self.best_params)}

    def top(self, k: int = 10) -> pd.DataFrame:
        """The best `k` successful trials by the objective, one row each (parameters flattened)."""
        rows = []
        for t in self.trials:
            if t.get("error") or not t.get("scores"):
                continue
            rows.append({
                "trial": t["number"], "objective_value": t["objective"], "feature_set": t["feature_set"],
                **{k_: t["scores"].get(k_) for k_ in (*OBJECTIVE_METRICS, "daily_mae", "balance_bias_90d")},
                **t["params"], "seconds": t.get("seconds"),
            })
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        return frame.sort_values(["objective_value", "daily_mae", "trial"], kind="stable").head(k).reset_index(drop=True)

    def beats_baseline(self, min_gain: float | None = None) -> bool:
        """The best trial beats the untuned model by the margin (and not by noise)."""
        key = self.objective_metric
        return is_better(self.best_scores.get(key, math.nan), self.baseline_scores.get(key, math.nan),
                         self.min_gain if min_gain is None else min_gain)

    def top10_spread(self) -> dict[str, float]:
        top = self.top(10)
        if top.empty:
            return {}
        raw = top[self.objective_metric].astype(float) if top[self.objective_metric].notna().all() else top["objective_value"]
        return {"n": float(len(top)), "min": float(raw.min()), "median": float(raw.median()),
                "max": float(raw.max()), "std": float(raw.std(ddof=0))}

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary (no NaN); the full trial table is an MLflow artifact, not part of it."""
        key = self.objective_metric
        top = self.top(10)
        return finite({
            "best_balance_error_90d": self.best_scores.get("balance_error_90d"),
            "baseline_balance_error_90d": self.baseline_scores.get("balance_error_90d"),
            "objective_metric": key,
            "best_objective": self.best_scores.get(key),
            "baseline_objective": self.baseline_scores.get(key),
            "beats_baseline": self.beats_baseline(),
            "min_gain": self.min_gain,
            "best_feature_set": self.best_feature_set,
            "best_params": self.best_params,
            "best_trial": self.best_trial_number,
            "best_scores": self.best_scores,
            "baseline_feature_set": self.baseline_feature_set,
            "baseline_params": self.baseline_params,
            "baseline_scores": self.baseline_scores,
            "per_origin": {"best": self.per_origin, "baseline": self.baseline_per_origin},
            "error_without_first_origin": {"best": error_without_first_origin(self.per_origin),
                                           "baseline": error_without_first_origin(self.baseline_per_origin)},
            "top10": top.to_dict(orient="records"),
            "top10_spread": self.top10_spread(),
            "n_trials": self.n_trials,
            "failed_trials": sum(1 for t in self.trials if t.get("error")),
            "seed": self.seed,
            "seconds_per_trial": self.seconds_per_trial,
            "data_source": self.data_source,
            "mode": self.mode, "daily_mae_cap": self.daily_mae_cap, "feasible": self.feasible,
            "mlflow": {"tracking_uri": redact_uri(self.tracking_uri), "experiment": self.experiment,
                       "parent_run_id": self.parent_run_id},
        })


# -- MLflow plumbing ---------------------------------------------------------------------------------------
@contextlib.contextmanager
def _tracking(uri: str | None) -> Iterator[tuple[Any, str] | None]:
    """Point MLflow (and `model.py`, which reads `MLFLOW_TRACKING_URI`) at `uri` or the environment's, then restore.
    Yields (mlflow module, uri), or None when no URI is configured or mlflow is not installed."""
    resolved = uri or os.environ.get("MLFLOW_TRACKING_URI")
    if not resolved:
        yield None
        return
    try:
        import mlflow
    except ImportError:
        log.warning("a tracking URI is set but mlflow is not installed (uv sync --extra mlflow)")
        yield None
        return
    previous_env = os.environ.get("MLFLOW_TRACKING_URI")
    previous_uri = mlflow.get_tracking_uri()
    os.environ["MLFLOW_TRACKING_URI"] = resolved
    mlflow.set_tracking_uri(resolved)
    try:
        yield mlflow, resolved
    finally:
        if previous_env is None:
            os.environ.pop("MLFLOW_TRACKING_URI", None)
        else:
            os.environ["MLFLOW_TRACKING_URI"] = previous_env
        mlflow.set_tracking_uri(previous_uri)


def _ensure_experiment(mlflow: Any, name: str, uri: str) -> None:
    """Select the experiment. A new one on a sqlite store keeps its artifacts next to the database file (never in a
    stray `./mlruns`); on a tracking server the server decides."""
    if mlflow.get_experiment_by_name(name) is None and uri.startswith("sqlite:///"):
        db = Path(uri[len("sqlite:///"):]).resolve()
        mlflow.create_experiment(name, artifact_location=(db.parent / "mlartifacts" / name).as_uri())
    mlflow.set_experiment(name)


class _Tracker:
    """Best-effort MLflow logging of one search: the first failure is logged and switches logging off."""

    def __init__(self, handle: tuple[Any, str] | None, experiment: str):
        self.mlflow = handle[0] if handle else None
        self.uri = handle[1] if handle else None
        self.experiment = experiment
        self.parent_run_id: str | None = None

    def _safe(self, what: str, fn: Callable[[], Any]) -> Any:
        if self.mlflow is None:
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001 - tracking never breaks the search
            log.exception("MLflow %s failed; logging is switched off, the search continues", what)
            self.mlflow = None
            return None

    def start_parent(self, params: Mapping[str, Any]) -> None:
        def go() -> None:
            _ensure_experiment(self.mlflow, self.experiment, self.uri)
            run = self.mlflow.start_run(run_name=f"tuning-{date.today().isoformat()}")
            self.parent_run_id = run.info.run_id
            self.mlflow.log_params({k: str(v) for k, v in params.items()})
        self._safe("parent run", go)

    def log_trial(self, trial: Mapping[str, Any]) -> None:
        def go() -> None:
            with self.mlflow.start_run(run_name=f"trial-{trial['number']:03d}", nested=True):
                self.mlflow.log_params({"feature_set": trial["feature_set"],
                                        **{k: str(v) for k, v in trial["params"].items()}})
                metrics: dict[str, float] = {"trial_number": float(trial["number"]),
                                             "seconds": float(trial["seconds"])}
                if trial.get("error"):
                    self.mlflow.set_tag("error", str(trial["error"])[:500])
                else:
                    metrics |= {k: float(v) for k, v in trial["scores"].items() if _is_finite(v)}
                    metrics["objective_value"] = float(trial["objective"])
                    if trial.get("feasible") is not None:
                        metrics["feasible"] = float(bool(trial["feasible"]))
                    for origin, errors in trial["per_origin"].items():
                        metrics[f"cum_error_{origin.replace('-', '_')}"] = float(errors["cum_error_end"])
                self.mlflow.log_metrics(metrics)
        self._safe(f"trial {trial['number']}", go)

    def finish_parent(self, result: TuningResult) -> None:
        def go() -> None:
            mlflow = self.mlflow
            mlflow.log_dict(finite({"feature_set": result.best_feature_set, "params": result.best_params,
                                    "scores": result.best_scores, "trial": result.best_trial_number}),
                            "best_params.json")
            top = result.top(10)
            mlflow.log_text(top.to_csv(index=False), "top10_trials.csv")
            mlflow.log_text(_trials_frame(result).to_csv(index=False), "trials.csv")
            mlflow.log_text(_per_origin_frame(result).to_csv(index=False), "per_origin.csv")
            mlflow.log_dict(result.to_dict(), "summary.json")
            metrics = {"seconds_per_trial": result.seconds_per_trial, "n_trials": float(result.n_trials),
                       **{f"best_{k}": v for k, v in result.best_scores.items()},
                       **{f"baseline_{k}": v for k, v in result.baseline_scores.items()},
                       **{f"top10_{k}": v for k, v in result.top10_spread().items()}}
            mlflow.log_metrics({k: float(v) for k, v in metrics.items() if _is_finite(v)})
            mlflow.set_tags({"objective_metric": result.objective_metric,
                             "beats_baseline": str(result.beats_baseline()),
                             "best_feature_set": result.best_feature_set})
            _log_history_plot(mlflow, result)
        self._safe("parent artifacts", go)

    def end(self) -> None:
        if self.mlflow is not None:
            self._safe("end run", lambda: self.mlflow.end_run())
        elif self.parent_run_id is not None:  # logging was switched off mid-way: still close the run if we can
            with contextlib.suppress(Exception):
                import mlflow
                mlflow.end_run()


def _trials_frame(result: TuningResult) -> pd.DataFrame:
    rows = []
    for t in result.trials:
        rows.append({"trial": t["number"], "feature_set": t["feature_set"], "objective_value": t.get("objective"),
                     **(t.get("scores") or {}), **t["params"], "seconds": t.get("seconds"),
                     "error": t.get("error")})
    return pd.DataFrame(rows)


def _per_origin_frame(result: TuningResult) -> pd.DataFrame:
    rows = []
    for label, table in (("best", result.per_origin), ("baseline", result.baseline_per_origin)):
        for origin, v in table.items():
            rows.append({"config": label, "origin": origin, **v})
    return pd.DataFrame(rows)


def _log_history_plot(mlflow: Any, result: TuningResult) -> None:
    """Objective of every trial and the best so far (needs matplotlib, which the `tuning` extra installs)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib is not installed: no optimisation history plot")
        return
    frame = _trials_frame(result).dropna(subset=["objective_value"])
    if frame.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.scatter(frame["trial"], frame["objective_value"], s=14, color="#7f8c9a", label="trial")
    ax.plot(frame["trial"], frame["objective_value"].cummin(), color="#0b7285", lw=2, label="best so far")
    if result.baseline_scores.get(result.objective_metric) is not None:
        ax.axhline(result.baseline_scores[result.objective_metric], color="#c2410c", ls="--", lw=1,
                   label="current defaults")
    ax.set_xlabel("trial")
    ax.set_ylabel(result.objective_metric + " (EUR)")
    ax.set_title("Optimisation history")
    ax.legend()
    fig.tight_layout()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "optimization_history.png"
        fig.savefig(path, dpi=110)
        mlflow.log_artifact(str(path))
    plt.close(fig)


# -- the search --------------------------------------------------------------------------------------------
def search(transactions: pd.DataFrame, *, n_trials: int, seed: int = 0,
           feature_sets: Iterable[str] | None = None, tracking_uri: str | None = None,
           min_gain: float = DEFAULT_MIN_GAIN, data_source: str | None = None, mode: str = MODE_BALANCE,
           origins: Iterable[Any] | None = None, reference_set: str | None = None) -> TuningResult:
    """Run `n_trials` Optuna trials (TPE, seeded) scored by the rolling-origin balance error.

    Every trial is a child run of the parent run `tuning-<date>` in experiment `liquidity-forecast-tuning` when MLflow
    is configured (`tracking_uri` or `MLFLOW_TRACKING_URI`). The current default configuration is scored first
    (the reference, `result.baseline_scores`) and is trial 0 when its feature set is searched. A trial that raises is
    recorded with its error and skipped.

    `mode` is `MODE_BALANCE` (the default, T062) or `MODE_CONSTRAINED` (see the module docstring); `origins` replaces the
    default month-end folds of every scoring call (`folds.weekly_origins`); `reference_set` is the feature set the
    search is measured against (default `model.DEFAULT_FEATURE_SET`), its default parameters being trial 0.
    """
    import optuna

    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if mode not in (MODE_BALANCE, MODE_CONSTRAINED):
        raise ValueError(f"mode must be {MODE_BALANCE!r} or {MODE_CONSTRAINED!r}")
    rolling_kw: dict[str, Any] = {} if origins is None else {"origins": list(origins)}
    _check_min_gain(min_gain)
    sets = tuple(feature_sets) if feature_sets is not None else DEFAULT_FEATURE_SETS
    for name in sets:
        feat.feature_set_columns(name)  # raises for an unknown set
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    ref_set = reference_set or m.DEFAULT_FEATURE_SET
    feat.feature_set_columns(ref_set)
    ref_params = baseline_params(ref_set)
    base_scores, base_origin = score_config(transactions, ref_set, ref_params, **rolling_kw)
    cap = float(base_scores["daily_mae"]) if mode == MODE_CONSTRAINED else None
    cache = {(ref_set, json.dumps(ref_params, sort_keys=True)): (base_scores, base_origin)}

    trials: list[dict[str, Any]] = []

    def objective(trial: Any) -> float:
        feature_set, params = suggest(trial, sets)
        record: dict[str, Any] = {"number": trial.number, "feature_set": feature_set, "params": params}
        started = time.perf_counter()
        try:
            key = (feature_set, json.dumps(params, sort_keys=True))
            if key not in cache:
                cache[key] = score_config(transactions, feature_set, params, **rolling_kw)
            scores, per_origin = cache[key]
            if cap is None:
                value, feasible = objective_value(scores)[0], None
            else:
                value, feasible = constrained_value(scores, cap)
            record |= {"scores": scores, "per_origin": per_origin, "objective": value, "feasible": feasible}
        except Exception as exc:  # noqa: BLE001 - one bad configuration must not stop the search
            log.warning("trial %d failed: %s", trial.number, exc)
            record |= {"error": f"{type(exc).__name__}: {exc}", "scores": None, "per_origin": {}, "objective": None,
                       "feasible": None}
        record["seconds"] = time.perf_counter() - started
        trials.append(record)
        tracker.log_trial(record)
        if record.get("error"):
            raise optuna.TrialPruned()
        return record["objective"]

    with _tracking(tracking_uri) as handle:
        tracker = _Tracker(handle, TUNING_EXPERIMENT)
        tracker.start_parent({"n_trials": n_trials, "seed": seed, "feature_sets": ",".join(sets),
                              "min_gain": min_gain, "rows": len(transactions), "data_source": data_source or "",
                              "baseline_feature_set": ref_set, "mode": mode,
                              "folds": "custom origins" if origins is not None else "month ends"})
        started = time.perf_counter()
        try:
            study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
            if ref_set in sets:
                study.enqueue_trial({"feature_set": ref_set, **ref_params})
            study.optimize(objective, n_trials=n_trials)
            elapsed = time.perf_counter() - started
            ok = [t for t in trials if not t.get("error")]
            if not ok:
                raise RuntimeError("every trial failed: " + (trials[0]["error"] if trials else "no trials ran"))
            pool = [t for t in ok if t["feasible"]] if cap is not None and any(t["feasible"] for t in ok) else ok
            # lexicographic: the objective (the balance error alone; penalised above the cap in constrained mode), then
            # the daily MAE; for a feasible or default-mode trial this is exactly `rank_key`
            best = min(pool, key=lambda t: (t["objective"], float(t["scores"]["daily_mae"]), t["number"]))
            result = TuningResult(
                best_feature_set=best["feature_set"], best_params=best["params"], best_scores=best["scores"],
                baseline_scores=base_scores, baseline_feature_set=ref_set, baseline_params=ref_params,
                per_origin=best["per_origin"], baseline_per_origin=base_origin, trials=sorted(
                    trials, key=lambda t: t["number"]),
                best_trial_number=best["number"], n_trials=len(trials), seed=seed,
                seconds_per_trial=elapsed / max(len(trials), 1), objective_metric=objective_value(base_scores)[1],
                min_gain=min_gain, tracking_uri=tracker.uri, experiment=TUNING_EXPERIMENT,
                parent_run_id=tracker.parent_run_id, data_source=data_source, mode=mode, daily_mae_cap=cap,
                feasible=best["feasible"] if cap is not None else None, origins=rolling_kw.get("origins"),
                transactions=transactions)
            tracker.finish_parent(result)
        finally:
            tracker.end()
    return result


# -- robustness over seeds -----------------------------------------------------------------------------
@dataclass
class RobustResult:
    """`robust_search`: one search per seed and the configuration of the median seed."""

    runs: list[TuningResult]
    chosen: TuningResult
    spread: float  # largest |seed score - chosen score| / chosen score
    stable: bool  # spread <= tolerance
    tolerance: float = STABILITY_TOLERANCE

    def to_dict(self) -> dict[str, Any]:
        key = self.chosen.objective_metric
        return finite({
            "seeds": [r.seed for r in self.runs],
            "objective_metric": key,
            "seed_scores": [r.best_scores.get(key) for r in self.runs],
            "seed_daily_mae": [r.best_scores.get("daily_mae") for r in self.runs],
            "seed_feasible": [r.feasible for r in self.runs],
            "seed_configs": [{"seed": r.seed, "feature_set": r.best_feature_set, "params": r.best_params}
                             for r in self.runs],
            "chosen_seed": self.chosen.seed,
            "spread": self.spread, "tolerance": self.tolerance, "stable": self.stable,
        })


def robust_search(transactions: pd.DataFrame, *, n_trials: int, seeds: Sequence[int] = (0, 1, 2),
                  mode: str = MODE_CONSTRAINED, **kw: Any) -> RobustResult:
    """Repeat `search` with each seed (`kw` goes to it) and choose the **median-of-seeds** configuration: the best trial
    of the seed whose best score is the middle one (the lower middle for an even number of seeds). A single seed's optimum
    is partly luck (T062: two seeds landed 25 % apart), the median seed is not the luckiest. Feasible runs only, when
    the mode has a constraint and at least one run is feasible. `spread` is how far the seeds' scores are from the chosen
    one, `stable` whether that is within `STABILITY_TOLERANCE` (5 %)."""
    seeds = list(dict.fromkeys(seeds))
    if len(seeds) < 2:
        raise ValueError("robust_search needs at least two seeds to say anything about stability")
    runs = [search(transactions, n_trials=n_trials, seed=seed, mode=mode, **kw) for seed in seeds]
    key = runs[0].objective_metric
    pool = [r for r in runs if r.feasible] if any(r.feasible for r in runs) else runs
    ranked = sorted(pool, key=lambda r: (float(r.best_scores.get(key, math.inf)), r.seed))
    chosen = ranked[(len(ranked) - 1) // 2]
    centre = float(chosen.best_scores.get(key, math.nan))
    scores = [float(r.best_scores.get(key, math.nan)) for r in runs]
    spread = max(abs(s - centre) for s in scores) / abs(centre) if _is_finite(centre) and centre else math.nan
    return RobustResult(runs=runs, chosen=chosen, spread=spread,
                        stable=_is_finite(spread) and spread <= STABILITY_TOLERANCE)


# -- promote -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Promotion:
    """What `promote` did."""

    model_name: str
    version: str
    run_id: str
    alias: str  # the alias the new version took on registration
    moved_champion: bool
    reason: str
    candidate_error: float | None = None  # the objective metric of the candidate / the champion, same data
    champion_error: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return finite(self.__dict__)


class PromotionError(RuntimeError):
    """The promotion was aborted before anything was registered or moved (e.g. the champion cannot be looked up)."""


PROMOTION_METRIC = "balance_error_90d"  # promotion compares real 90-day balance errors only, never a fallback horizon


def promote(result: TuningResult, *, alias: str = CHALLENGER_ALIAS, if_better: bool = False,
            min_gain: float | None = None, model_name: str = m.REGISTERED_MODEL_NAME) -> Promotion:
    """Retrain the best configuration on the full history and register it as a new version with `alias`.

    With `if_better` the alias `champion` additionally moves to the new version when its `balance_error_90d` beats the
    current champion's by `min_gain` (default: the result's margin; must be >= 0). The champion is scored now, on the same
    data (`result.transactions`) and folds, by re-training its own feature set and parameters. Both need a real 90-day
    error (no fallback horizon). With no champion yet the new version takes the alias. Otherwise the champion stays and
    `reason` says why. When the champion cannot be looked up (any MLflow error other than "not found") nothing is
    registered and `PromotionError` is raised.
    """
    if result.transactions is None:
        raise ValueError("this result carries no transactions to retrain on")
    if if_better and alias == m.CHAMPION_ALIAS:
        raise ValueError("if_better decides about the champion alias; register under another alias")
    margin = _check_min_gain(result.min_gain if min_gain is None else min_gain)
    with _tracking(result.tracking_uri) as handle:
        if handle is None:
            raise RuntimeError("promote needs MLflow: set MLFLOW_TRACKING_URI (or --tracking-uri) and install the "
                               "mlflow extra")
        mlflow, uri = handle
        _ensure_experiment(mlflow, os.environ.get("MLFLOW_EXPERIMENT_NAME") or m.MLFLOW_EXPERIMENT, uri)
        client = mlflow.MlflowClient()
        candidate = result.best_scores.get(PROMOTION_METRIC)
        moved, champion_error, reason = False, None, f"registered as {alias}; the champion alias was not touched"
        if if_better:  # decided before anything is registered: a failing lookup aborts cleanly
            moved, champion_error, reason = _decide_champion(client, mlflow, result, margin, model_name)
        model = m.train(result.transactions, log_to_mlflow=False, **result.train_kwargs())
        run_id = m.log_to_mlflow_if_configured(model, register=True, model_name=model_name, alias=alias)
        if not run_id or model.model_version is None:
            raise RuntimeError("the model could not be registered in MLflow (see the log above)")
        if _is_finite(candidate):
            client.log_metric(run_id, f"tuning_{PROMOTION_METRIC}", float(candidate))
        if result.parent_run_id:
            client.set_tag(run_id, "tuning_run_id", result.parent_run_id)
        version = model.model_version
        if moved:
            client.set_registered_model_alias(model_name, m.CHAMPION_ALIAS, version)
        log.info("promote: version %s alias %s, champion moved: %s (%s)", version, alias, moved, reason)
        return Promotion(model_name=model_name, version=version, run_id=run_id, alias=alias, moved_champion=moved,
                         reason=reason, candidate_error=float(candidate) if _is_finite(candidate) else None,
                         champion_error=champion_error)


def _champion_exists(client: Any, mlflow: Any, model_name: str) -> bool:
    """Whether `model_name@champion` exists. Only "model / alias not found" means no; any other failure aborts."""
    try:
        client.get_model_version_by_alias(model_name, m.CHAMPION_ALIAS)
        return True
    except mlflow.exceptions.MlflowException as exc:
        code, text = getattr(exc, "error_code", ""), str(exc).lower()
        if code == "RESOURCE_DOES_NOT_EXIST" or ("alias" in text and "not found" in text):
            return False
        raise PromotionError(f"could not look up the current champion of {model_name}: {exc}; nothing was "
                             "registered, the champion is untouched") from exc
    except Exception as exc:  # noqa: BLE001 - network and the like: not "no champion"
        raise PromotionError(f"could not look up the current champion of {model_name}: {type(exc).__name__}: "
                             f"{exc}; nothing was registered, the champion is untouched") from exc


def _decide_champion(client: Any, mlflow: Any, result: TuningResult, margin: float,
                     model_name: str) -> tuple[bool, float | None, str]:
    """(move the champion alias?, champion's 90-day error, reason). Compares `balance_error_90d` only."""
    key = PROMOTION_METRIC
    candidate = result.best_scores.get(key)
    if not _is_finite(candidate):
        return False, None, (f"the candidate has no real 90-day balance error (history too short for 90-day folds): "
                             f"no champion move on a shorter horizon; it stays challenger")
    if not _champion_exists(client, mlflow, model_name):
        return True, None, "no champion yet: the new version takes the alias"
    try:
        champion = m.ForecastModel.load_from_mlflow(f"models:/{model_name}@{m.CHAMPION_ALIAS}")
        scores, _ = score_config(result.transactions, champion.feature_set, champion.params,
                                 **({} if result.origins is None else {"origins": result.origins}))
    except Exception as exc:  # noqa: BLE001 - when in doubt keep the champion
        log.exception("could not score the current champion")
        return False, None, f"the champion could not be scored ({type(exc).__name__}: {exc}); it stays"
    champion_error = scores.get(key)
    if not _is_finite(champion_error):
        return False, None, "the champion has no real 90-day balance error on this data; it stays, candidate is challenger"
    shown = float(champion_error)
    if is_better(candidate, shown, margin):
        return True, shown, (f"{key} {float(candidate):.0f} beats the champion's {shown:.0f} by "
                             f"more than {margin:.0%}")
    return False, shown, (f"not better than the champion by {margin:.0%}: {key} candidate {float(candidate):.0f} vs "
                          f"champion {shown:.0f}; it stays challenger")


# -- data and CLI ------------------------------------------------------------------------------------------
def load_transactions(source: str = "auto") -> tuple[pd.DataFrame, str]:
    """(transactions, "postgres" | "csv"): the company database when `DATABASE_URL` works, else the organizer's CSV.
    `source="db"` / `"csv"` forces one of them (a database problem then raises)."""
    if source not in ("auto", "db", "csv"):
        raise ValueError("source must be auto, db or csv")
    if source in ("auto", "db"):
        try:
            from cfo.config import get_settings

            with psycopg.connect(get_settings().database_url, connect_timeout=5) as conn:
                tx = feat.fetch_transactions(conn)
            if len(tx):
                return tx, "postgres"
            if source == "db":
                raise RuntimeError("the database has no transactions")
            log.info("Postgres is reachable but empty: using the CSV")
        except Exception as exc:  # noqa: BLE001
            if source == "db":
                raise
            log.info("Postgres not available (%s): using the CSV", type(exc).__name__)
    return ev.load_statement(), "csv"


def _table(data: Mapping[str, Any]) -> str:
    def fmt(value: Any) -> str:
        return "n/a" if value is None else f"{value:,.0f}"

    key = data["objective_metric"]
    lines = [
        f"trials: {data['n_trials']} ({data['failed_trials']} failed), {data['seconds_per_trial']:.1f} s per trial, "
        f"data: {data['data_source']}",
        f"{key}: baseline {fmt(data['baseline_objective'])} -> best {fmt(data['best_objective'])} "
        f"(better by the {data['min_gain']:.0%} margin: {data['beats_baseline']})",
        f"best feature set: {data['best_feature_set']}",
        "best params: " + json.dumps(data["best_params"]),
        "cumulative error per origin, EUR (best / baseline):",
    ]
    for origin, v in data["per_origin"]["best"].items():
        base = data["per_origin"]["baseline"].get(origin, {})
        lines.append(f"  {origin} ({v['days']:.0f} d): {fmt(v['cum_error_end'])} / {fmt(base.get('cum_error_end'))}")
    spread = data["top10_spread"]
    if spread:
        lines.append(f"top-{spread['n']:.0f} spread of the objective: min {fmt(spread['min'])}, median "
                     f"{fmt(spread['median'])}, max {fmt(spread['max'])}")
    mlflow = data["mlflow"]
    if mlflow["parent_run_id"]:
        lines.append(f"MLflow: {mlflow['tracking_uri']} experiment {mlflow['experiment']} run {mlflow['parent_run_id']}")
    if "promotion" in data and "error" in data["promotion"]:
        lines.append(f"promotion ABORTED: {data['promotion']['error']}")
    elif "promotion" in data:
        p = data["promotion"]
        lines.append(f"promotion: version {p['version']} as {p['alias']}, champion moved: {p['moved_champion']} "
                     f"({p['reason']})")
    if data.get("mode") == MODE_CONSTRAINED:
        lines.append(f"constrained: daily MAE of the best trial {fmt(data['best_scores'].get('daily_mae'))} "
                     f"(cap {fmt(data.get('daily_mae_cap'))}), feasible: {data.get('feasible')}")
    if "robust" in data:
        r = data["robust"]
        lines.append(f"seeds {r['seeds']}: best scores {[fmt(v) for v in r['seed_scores']]}, chosen seed "
                     f"{r['chosen_seed']}, spread {r['spread']:.1%} (tolerance {r['tolerance']:.0%}), stable: {r['stable']}")
    ex = data["error_without_first_origin"]
    lines.append(f"mean |error at the end of the fold| without the first origin: baseline {fmt(ex['baseline'])} -> "
                 f"best {fmt(ex['best'])}")
    lines.append(f'"best_balance_error_90d": {json.dumps(data["best_balance_error_90d"])}')
    return "\n".join(lines)


def _min_gain_arg(text: str) -> float:
    try:
        return _check_min_gain(float(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cfo.forecast.tune",
        description="Optuna search over the forecast XGBoost, scored by the rolling-origin balance error, every "
                    "trial a run in MLflow. Reads MLFLOW_TRACKING_URI, DATABASE_URL, SOURCE_CSV from the environment.")
    parser.add_argument("--trials", type=int, default=200, help="number of Optuna trials (default 200)")
    parser.add_argument("--seed", type=int, default=0, help="sampler seed (default 0)")
    parser.add_argument("--tracking-uri", help="MLflow tracking URI (default: $MLFLOW_TRACKING_URI; none = no logging)")
    parser.add_argument("--source", choices=("auto", "db", "csv"), default="auto",
                        help="data: Postgres when DATABASE_URL works, else the CSV (auto); or force one")
    parser.add_argument("--constrained", action="store_true",
                        help="minimise balance_error_90d subject to daily_mae <= the reference's (T068)")
    parser.add_argument("--weekly", action="store_true",
                        help="score on weekly origins (27 Mondays) instead of the 6 month ends")
    parser.add_argument("--origin-every", type=int, default=7, metavar="DAYS",
                        help="with --weekly: spacing of the origins in days (7 = every Monday; 2 = a dense grid that "
                             "visits every weekday, for robustness)")
    parser.add_argument("--seeds", help="comma separated seeds (at least two): repeat the search and keep the "
                                        "median-of-seeds configuration; replaces --seed")
    parser.add_argument("--reference-set", help="feature set the search is measured against (default: the model's)")
    parser.add_argument("--feature-sets", help="comma separated feature sets to search "
                                               f"(default: {','.join(DEFAULT_FEATURE_SETS)})")
    parser.add_argument("--min-gain", type=_min_gain_arg, default=DEFAULT_MIN_GAIN,
                        help="a candidate is better only by this share of the reference's error (default 0.02)")
    parser.add_argument("--promote", action="store_true",
                        help="retrain the best configuration and register it as 'challenger'")
    parser.add_argument("--promote-if-better", action="store_true",
                        help="like --promote, and move 'champion' when it beats the current champion by the margin")
    parser.add_argument("--json", action="store_true", help="print the result as one JSON object")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    transactions, source = load_transactions(args.source)
    log.info("%d bookings from %s, %s .. %s", len(transactions), source, transactions["booking_date"].min().date(),
             transactions["booking_date"].max().date())
    sets = [s.strip() for s in args.feature_sets.split(",") if s.strip()] if args.feature_sets else None
    kw: dict[str, Any] = {"feature_sets": sets, "tracking_uri": args.tracking_uri, "min_gain": args.min_gain,
                          "data_source": source, "reference_set": args.reference_set,
                          "mode": MODE_CONSTRAINED if args.constrained else MODE_BALANCE}
    if args.weekly:
        from cfo.forecast import folds

        kw["origins"] = folds.weekly_origins(transactions, every_days=args.origin_every)
    robust = None
    if args.seeds:
        robust = robust_search(transactions, n_trials=args.trials,
                               seeds=[int(x) for x in args.seeds.split(",") if x.strip()], **kw)
        result = robust.chosen
    else:
        result = search(transactions, n_trials=args.trials, seed=args.seed, **kw)
    data = result.to_dict()
    if robust is not None:
        data["robust"] = robust.to_dict()
    failed = False
    if args.promote or args.promote_if_better:
        try:
            data["promotion"] = promote(result, if_better=args.promote_if_better, min_gain=args.min_gain).to_dict()
        except PromotionError as exc:  # nothing registered, champion untouched: report it and fail the run
            log.error("promotion aborted: %s", exc)
            data["promotion"] = {"error": str(exc), "moved_champion": False}
            failed = True
    print(json.dumps(data, indent=2, allow_nan=False) if args.json else _table(data))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
