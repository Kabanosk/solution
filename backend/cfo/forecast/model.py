"""Liquidity forecast: XGBoost on the daily calendar features of `features.py` (T014, D-002, D-009).

* Target: daily net cash flow of the bank account, **without** `partner_investment`. Decision: the 12 monthly CHF
  tranches (15th of each month, tranche 12/12 on 15 Dec 2025) are a contractual schedule that ends, not a
  behaviour. Learned from one year of history the model would project them into 2026 and hide the cash that is
  freed after December. They are added on top as known `scheduled_payments` (`partner_tranche_schedule` derives
  the remaining ones from the bookings). Pass your own series to plan other fixed payments.
* The yearly summer shutdown is learned through `shutdown_flag` (and `month` / `day_of_year` in the `calendar`
  set); the model then reproduces the June dip in 2026.
* Feature sets (`train(feature_set=...)`, `features.FEATURE_SETS`, scored by `python -m cfo.forecast.compare`):
  `calendar` is the calendar columns only (it replays the last year, a revenue step is lost); `level` drops the
  year-memorising columns and gives the booster the recent revenue / variable cost per open day
  (`features.recent_levels`) as inputs; `level_baseline` (the default) drops them too but learns the deviation from
  the recent level: target = net flow minus `features.level_baseline`, added back when predicting. The level
  values at the end of the history are stored in the model (`level_values`, saved through `meta()`) and held
  constant for future dates; for level sets `shutdown_flag` also marks a closed stretch at the end of the history.
  `components` (T068) splits the forecast in two: the fixed costs (payroll, rent, social security, utilities, tax) are
  a schedule read off the bookings up to the origin (`schedule.infer`, stored in the model, refreshed by
  `with_levels_from`) and the booster, which learns the deviation from the recent level like `level_baseline`, only sees
  the variable flows (revenue, food, drinks) of open days. Prediction = schedule + variable part, zero variable part
  on shutdown days; `explain` names the schedule parts (`scheduled_payroll`, ...). Partner tranches stay a separate
  scheduled payment (`partner_tranche_schedule`).
* The model is saved to `models/liquidity_xgb.json` (booster) + `.meta.json` (calendar inference, metrics);
  `models/` is git-ignored. Params, metrics and the booster go to MLflow only when `MLFLOW_TRACKING_URI` is set
  and `mlflow` is installed (optional extra `mlflow`). With `register=True` the run's model becomes a new version
  of the registered model `liquidity-forecast` and takes the alias `champion`;
  `ForecastModel.load_from_mlflow("models:/liquidity-forecast@champion")` rebuilds the full model from MLflow
  (booster + the sidecar logged as a run artifact). Auth, workspace and TLS come from MLflow's own environment
  variables (`MLFLOW_TRACKING_AUTH`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, ...); nothing is set here.
* The 80 % band of the balance is measured at training time on the rolling-origin folds (`band.py`, `train(band=True)`),
  stored under the metadata key `band` and read through `ForecastModel.band(days_ahead)`; a model without it
  falls back to the old `1.2816 * sigma * sqrt(n)` (T063).
* `feature_importances()` and `explain()` give T016 the numbers it hands to the LLM to explain a forecast.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from xgboost import XGBRegressor

from cfo.config import REPO_ROOT
from cfo.forecast import band as fband
from cfo.forecast import features as feat
from cfo.forecast import schedule as fsched

log = logging.getLogger(__name__)

PARTNER_CATEGORY = "partner_investment"
PARTNER_TRANCHES_TOTAL = 12
EXCLUDED_CATEGORIES = (PARTNER_CATEGORY,)
FEATURE_COLUMNS = list(feat.CALENDAR_COLUMNS)  # the `calendar` feature set (kept for old callers)
# The feature set `train` uses unless told otherwise: the best of `python -m cfo.forecast.compare` (T061).
DEFAULT_FEATURE_SET = "level_baseline"
MIN_LEVEL_TRAIN_DAYS = 28  # open training days a level set needs (fewer: the default falls back to `calendar`)
MODEL_FILENAME = "liquidity_xgb.json"
MLFLOW_EXPERIMENT = "liquidity-forecast"  # `MLFLOW_EXPERIMENT_NAME` overrides
REGISTERED_MODEL_NAME = "liquidity-forecast"
CHAMPION_ALIAS = "champion"
MLFLOW_MODEL_ARTIFACT = "model"
META_ARTIFACT = "forecast_model.meta.json"  # the sidecar of `ForecastModel.save`, as a run artifact
_REGISTRY_ALIAS_URI = re.compile(r"^models:/(?P<name>[^/@]+)@(?P<alias>[^/@]+)$")
_REGISTRY_VERSION_URI = re.compile(r"^models:/(?P<name>[^/@]+)/(?P<version>\d+)$")

# Small, shallow and deterministic: ~330 training days, strong calendar signal, no randomness.
DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 200, "max_depth": 3, "learning_rate": 0.05, "min_child_weight": 3,
    "subsample": 1.0, "reg_lambda": 1.0, "random_state": 0, "n_jobs": 1, "tree_method": "hist",
}
# Per feature set on top of DEFAULT_PARAMS (`calendar` keeps DEFAULT_PARAMS exactly). The level sets learn a
# smaller, noisier deviation target and under-fit with 200 slow trees (December daily MAE 282 -> 234 EUR with these);
# not a tuned optimum. `python -m cfo.forecast.tune` (T062) found better balance errors, but not in a form that keeps
# the statement tests (see .house/reports/T062.md), so these stay the defaults for now.
FEATURE_SET_PARAMS: dict[str, dict[str, Any]] = {
    "level": {"n_estimators": 400, "learning_rate": 0.1},
    "level_baseline": {"n_estimators": 400, "learning_rate": 0.1},
    # T068: median-of-3-seeds configuration of `python -m cfo.forecast.tune --constrained --weekly --origin-every 2
    # --seed {0,1,2} --feature-sets components --reference-set level_baseline --trials 100`: minimise balance_error_90d on
    # 91 origins (every second day from 2 Jun to 1 Dec 2025, i.e. every weekday) subject to daily MAE <= level_baseline's.
    # See .house/reports/T068.md: tuned on the 27 Monday folds alone the same search overfits the weekday (T068 report).
    "components": {
        "n_estimators": 326, "max_depth": 2, "learning_rate": 0.2768, "min_child_weight": 20, "subsample": 0.9904,
        "colsample_bytree": 0.7761, "reg_lambda": 3.2495, "reg_alpha": 0.02, "gamma": 0.1091, "objective": "reg:squarederror",
    },
}


def default_model_path() -> Path:
    """`$FORECAST_MODEL_PATH` or `<repo>/models/liquidity_xgb.json`."""
    env = os.environ.get("FORECAST_MODEL_PATH")
    from cfo.tenancy import current_tenant
    tenant = current_tenant(required=False)
    base = Path(env) if env else REPO_ROOT / "models" / MODEL_FILENAME
    return base.parent / "tenants" / tenant.id / base.name if tenant else base


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


# -- the model --------------------------------------------------------------------------------------------
@dataclass
class ForecastModel:
    """A fitted regressor plus what is needed to build features for future dates."""

    regressor: XGBRegressor
    shutdown_days: frozenset[tuple[int, int]]
    due_days: dict[str, tuple[int, ...]]
    trained_until: pd.Timestamp
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    excluded_categories: tuple[str, ...] = EXCLUDED_CATEGORIES
    feature_columns: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    feature_set: str = "calendar"  # name in `features.FEATURE_SETS`; old models have no name and are `calendar`
    level_values: dict[str, float] = field(default_factory=dict)  # `features.LEVEL_COLUMNS` at `trained_until`
    baseline_mean: float = 0.0  # mean open-day baseline of the training days (reference for `contributions`)
    band_spec: dict[str, Any] | None = None  # measured 80 % band (`band.fit_band`); None = old sqrt formula (T063)
    # Fixed costs as a schedule (`schedule.infer`, T068); only a `components` model has one, every other set ignores it.
    schedule: dict[str, Any] | None = None
    # Where the model lives in MLflow (set by `log_to_mlflow_if_configured` / `load_from_mlflow`; not saved).
    mlflow_run_id: str | None = None
    model_version: str | None = None  # registered model version, None when not from the registry
    trained_at: datetime | None = None  # start of the MLflow run

    def _frame(self, dates: Iterable[Any]) -> pd.DataFrame:
        """Calendar columns plus (for level sets) the level columns of the origin, held constant for every date."""
        x = feat.calendar_features(dates, shutdown_days=self.shutdown_days, due_days=self.due_days)
        for column, value in self.level_values.items():
            x[column] = value
        return x

    def with_levels_from(self, transactions: pd.DataFrame) -> ForecastModel:
        """A shallow copy that forecasts from the level of `transactions` instead of the stored one.

        The recent level is frozen when a model is trained, but a saved model is reused while bookings keep
        arriving. This recomputes the level values at the forecast origin (the day after the last booking) from
        the current bookings; the booster is shared and `self` is not changed. A calendar model, or bookings
        without enough open days for a level, give back `self`."""
        if not self.level_values or transactions.empty:
            return self
        origin = transactions["booking_date"].max().normalize() + pd.Timedelta(days=1)
        fresh = feat.recent_levels(transactions, [origin]).iloc[0]
        if fresh.isna().any():
            return self
        changes: dict[str, Any] = {"level_values": {k: float(v) for k, v in fresh.items()}}
        if self.schedule is not None:  # the fixed costs as the current bookings show them (a new rent, a new quarter)
            spec = fsched.infer(transactions, categories=_schedule_categories(self.excluded_categories))
            if spec["categories"]:
                changes["schedule"] = spec
        return dataclasses.replace(self, **changes)

    def features_for(self, dates: Iterable[Any]) -> pd.DataFrame:
        return self._frame(dates)[self.feature_columns]

    def predict(self, dates: Iterable[Any]) -> pd.Series:
        """Predicted daily net cash flow (EUR, without scheduled payments), indexed by date."""
        x = self._frame(dates)
        raw = self.regressor.predict(x[self.feature_columns])
        if feat.has_schedule(self.feature_set):  # trained on open days only: the variable part is zero on closed days
            raw = raw * (1 - x["shutdown_flag"].to_numpy())
        if self.feature_set in feat.OFFSET_SETS:  # the booster learned the deviation from the recent level
            raw = raw + feat.applied_baseline(x).to_numpy()
        if self.schedule is not None and feat.has_schedule(self.feature_set):  # + the fixed costs, exactly
            raw = raw + fsched.total(self.schedule, x.index).to_numpy()
        return pd.Series(raw, index=x.index, name="predicted_net")

    def band(self, days_ahead: int) -> tuple[float, float]:
        """`(lower_offset, upper_offset)` of the 80 % interval of the balance `days_ahead` days after the last
        booking, to be added to the forecast balance. From the cumulative errors of the rolling-origin folds
        (`band_spec`, T063); a model without them (older file) uses `1.2816 * sigma * sqrt(n)`."""
        if self.band_spec:
            return fband.offsets(self.band_spec, days_ahead)
        sigma = float(self.metrics.get("holdout_rmse") or (self.metrics.get("train_mae", 0.0) * 1.25))
        return fband.legacy_offsets(sigma, days_ahead)

    def feature_importances(self) -> pd.Series:
        """Share of total gain per feature (sums to 1), largest first; features never used are 0."""
        gain = self.regressor.get_booster().get_score(importance_type="total_gain")
        raw = pd.Series({c: gain.get(c, 0.0) for c in self.feature_columns}, dtype=float)
        total = raw.sum()
        return (raw / total if total else raw).sort_values(ascending=False).rename("importance")

    def contributions(self, dates: Iterable[Any]) -> pd.DataFrame:
        """Per-day SHAP contributions in EUR per feature plus a `bias` column; rows sum to `predict`."""
        import xgboost as xgb

        frame = self._frame(dates)
        x = frame[self.feature_columns]
        raw = self.regressor.get_booster().predict(xgb.DMatrix(x), pred_contribs=True)
        out = pd.DataFrame(raw, index=x.index, columns=[*self.feature_columns, "bias"])
        if feat.has_schedule(self.feature_set):  # the booster's output is switched off on closed days (see `predict`)
            out["shutdown_flag"] -= out.sum(axis=1) * frame["shutdown_flag"]
        if self.feature_set in feat.OFFSET_SETS:
            # The forecast is booster + applied baseline. Split the baseline into: its training average (to
            # `bias`), how far the current level is from that average (`recent_level_baseline`) and the baseline
            # that a shutdown day does not earn (to `shutdown_flag`); rows still sum to `predict`.
            level = feat.level_baseline(frame)
            out["bias"] += self.baseline_mean
            out["shutdown_flag"] -= level * frame["shutdown_flag"]
            out.insert(len(self.feature_columns), feat.BASELINE_COLUMN, level - self.baseline_mean)
        if self.schedule is not None and feat.has_schedule(self.feature_set):
            # the fixed-cost schedule, one named part per category (`scheduled_payroll`, ...): rows still sum to `predict`
            for category, values in fsched.amounts(self.schedule, x.index).items():
                out[feat.SCHEDULE_PREFIX + str(category)] = values.to_numpy()
        return out

    def explain(self, start: Any, end: Any, *, top: int = 5) -> dict[str, Any]:
        """Why the model predicts what it predicts for a period (the input of T016's explanation).

        Returns the predicted net flow over the period, the `top` features by absolute summed contribution
        (EUR, signed) and the global importances. Compare with the average period to see what is unusual.
        """
        days = pd.date_range(start, end, freq="D")
        contrib = self.contributions(days)
        drivers = contrib.drop(columns="bias").sum().sort_values(key=lambda s: s.abs(), ascending=False)
        return {
            "start": str(days[0].date()), "end": str(days[-1].date()), "days": len(days),
            "predicted_net_eur": round(float(contrib.sum().sum()), 2),
            "baseline_eur": round(float(contrib["bias"].sum()), 2),
            "top_drivers": [{"feature": name, "contribution_eur": round(float(value), 2)}
                            for name, value in drivers.head(top).items()],
            "feature_importances": {k: round(float(v), 4) for k, v in self.feature_importances().head(top).items()},
        }

    # -- persistence
    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else default_model_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.regressor.save_model(path)
        _meta_path(path).write_text(json.dumps(self.meta(), indent=2), encoding="utf-8")
        return path

    def meta(self) -> dict[str, Any]:
        """Everything besides the booster that is needed to rebuild the model (the `.meta.json` sidecar)."""
        return {
            "shutdown_days": sorted([m, d] for m, d in self.shutdown_days),
            "due_days": {k: list(v) for k, v in self.due_days.items()},
            "trained_until": str(self.trained_until.date()),
            "params": self.params, "metrics": self.metrics,
            "excluded_categories": list(self.excluded_categories),
            "feature_columns": self.feature_columns,
            "feature_set": self.feature_set, "level_values": dict(self.level_values),
            "baseline_mean": self.baseline_mean,
            "band": self.band_spec,
            "schedule": self.schedule,
        }

    @classmethod
    def from_meta(cls, regressor: XGBRegressor, meta: Mapping[str, Any], **extra: Any) -> ForecastModel:
        return cls(
            regressor=regressor,
            shutdown_days=frozenset((int(m), int(d)) for m, d in meta["shutdown_days"]),
            due_days={k: tuple(v) for k, v in meta["due_days"].items()},
            trained_until=pd.Timestamp(meta["trained_until"]),
            params=dict(meta["params"]), metrics=dict(meta["metrics"]),
            excluded_categories=tuple(meta["excluded_categories"]),
            feature_columns=list(meta["feature_columns"]),
            feature_set=str(meta.get("feature_set") or "calendar"),
            level_values={k: float(v) for k, v in (meta.get("level_values") or {}).items()},
            baseline_mean=float(meta.get("baseline_mean") or 0.0),
            band_spec=dict(meta["band"]) if meta.get("band") else None,
            schedule=json.loads(json.dumps(meta["schedule"])) if meta.get("schedule") else None,
            **extra,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> ForecastModel:
        path = Path(path) if path else default_model_path()
        meta = json.loads(_meta_path(path).read_text(encoding="utf-8"))
        regressor = XGBRegressor()
        regressor.load_model(path)
        return cls.from_meta(regressor, meta)

    @classmethod
    def load_from_mlflow(cls, uri: str) -> ForecastModel:
        """Rebuild the model from MLflow: `models:/liquidity-forecast@champion`, `models:/<name>/<version>` or
        `runs:/<run id>/model`. Needs the run that logged the model (the sidecar is its artifact).

        Uses the tracking server of `MLFLOW_TRACKING_URI`. Raises on any problem (missing package, unreachable
        server, unknown model); callers that must not fail catch it. `mlflow_run_id`, `model_version` and
        `trained_at` are set on the result.
        """
        mlflow = _mlflow()
        ref = resolve_mlflow_model(uri)
        loaded = importlib.import_module("mlflow.xgboost").load_model(ref.uri)
        with tempfile.TemporaryDirectory() as tmp:
            if isinstance(loaded, XGBRegressor):  # someone logged the sklearn wrapper instead of the booster
                regressor = loaded
            else:
                booster_file = Path(tmp) / MODEL_FILENAME
                loaded.save_model(booster_file)
                regressor = XGBRegressor()
                regressor.load_model(booster_file)
            meta_file = mlflow.artifacts.download_artifacts(
                run_id=ref.run_id, artifact_path=META_ARTIFACT, dst_path=tmp)
            meta = json.loads(Path(meta_file).read_text(encoding="utf-8"))
        started = mlflow.MlflowClient().get_run(ref.run_id).info.start_time
        trained_at = datetime.fromtimestamp(started / 1000, tz=timezone.utc) if started else None
        return cls.from_meta(regressor, meta, mlflow_run_id=ref.run_id, model_version=ref.version,
                             trained_at=trained_at)


# -- training ---------------------------------------------------------------------------------------------
def _training_table(transactions: pd.DataFrame, excluded: Iterable[str],
                    feature_set: str = "calendar") -> pd.DataFrame:
    """History days with the target and the calendar columns, plus the level columns for a level feature set
    (lagged: a row only sees bookings before it; rows without a week of open days behind them are dropped). For a
    level set `shutdown_flag` also marks a closed run at the end of the history (`features.closed_days`). For a
    `components` set the target is the *variable* net flow (the fixed-cost categories are left out of it) and closed
    days are not training rows (the forecast switches the variable part off there)."""
    target_excluded = tuple(excluded)
    if feat.has_schedule(feature_set):  # the fixed costs are the schedule's job: the booster learns the variable flows
        target_excluded = (*target_excluded, *fsched.CATEGORIES)
    table = feat.daily_features(transactions, exclude_categories=target_excluded).dropna(subset=["net_cash_flow"])
    if feat.needs_levels(feature_set):
        table = table.join(feat.recent_levels(transactions, table.index)).dropna(subset=feat.LEVEL_COLUMNS)
        # a closed stretch that reaches the end of the history is still closed days for the training rows
        table["shutdown_flag"] = np.maximum(table["shutdown_flag"], feat.closed_days(transactions).reindex(
            table.index, fill_value=0))
    if feat.has_schedule(feature_set):  # closed days carry no variable flow by definition: nothing to learn there
        table = table[table["shutdown_flag"] == 0]
    return table


def backtest_split(transactions: pd.DataFrame, holdout_days: int,
                   exclude_categories: Iterable[str] = EXCLUDED_CATEGORIES,
                   feature_set: str = "calendar") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(train, test) feature tables of a time holdout: the last `holdout_days` calendar days are the test set.

    Shutdown windows and due days are inferred from the bookings **before** the first test day only, and that
    inference builds the features of both parts: what the holdout bookings look like cannot reach the training
    features (T045; the old split inferred from the full history and scored optimistically). The targets are the
    real daily cash flows of each part. Columns: `net_cash_flow` + the columns of `feature_set`, indexed by `date`.
    Level features come from the same bookings: lagged for training days, held at the last training day's value
    over the holdout (exactly what a forecast made at that origin would use).
    """
    columns = feat.feature_set_columns(feature_set)
    table = _training_table(transactions, exclude_categories, feature_set)
    first_test = table.index[-holdout_days]
    seen = transactions[transactions["booking_date"] < first_test]
    shutdown = feat.infer_shutdown_days(seen)
    due = feat.infer_due_days(seen)
    features = feat.calendar_features(table.index, shutdown_days=shutdown, due_days=due)
    if feat.needs_levels(feature_set):
        features = features.join(feat.recent_levels(seen, table.index))
        features["shutdown_flag"] = np.maximum(features["shutdown_flag"], feat.closed_days(seen).reindex(
            features.index, fill_value=0))
    # the level columns a set only uses as its baseline travel along, so `_backtest` can add the baseline back
    kept = [*columns, *(c for c in feat.LEVEL_COLUMNS if c not in columns and feat.needs_levels(feature_set))]
    split = features[kept].join(table["net_cash_flow"])[["net_cash_flow", *kept]]
    return split.iloc[:-holdout_days], split.iloc[-holdout_days:]


def _schedule_categories(excluded: Iterable[str]) -> tuple[str, ...]:
    """The fixed-cost categories of the schedule: all of `schedule.CATEGORIES` the model does not exclude."""
    skip = set(excluded)
    return tuple(c for c in fsched.CATEGORIES if c not in skip)


def _offset(frame: pd.DataFrame, feature_set: str) -> pd.Series | float:
    """The baseline a feature set subtracts from the target (0 unless it is an offset set)."""
    return feat.applied_baseline(frame) if feature_set in feat.OFFSET_SETS else 0.0


def _backtest(transactions: pd.DataFrame, table: pd.DataFrame, params: dict[str, Any], holdout_days: int,
              exclude_categories: Iterable[str], feature_set: str = "calendar") -> dict[str, float]:
    """Fit on all but the last `holdout_days`, score on them (see `backtest_split` for what the model may see).
    One year of history cannot be cross-validated by year (the shutdown occurs once), so the check is a time
    holdout of ordinary days, against a naive mean."""
    if holdout_days <= 0 or len(table) < 2 * holdout_days:
        return {}
    columns = feat.feature_set_columns(feature_set)
    train, test = backtest_split(transactions, holdout_days, exclude_categories, feature_set)
    regressor = XGBRegressor(**params).fit(train[columns], train["net_cash_flow"] - _offset(train, feature_set))
    pred = regressor.predict(test[columns]) + np.asarray(_offset(test, feature_set))
    truth, history = test["net_cash_flow"], train["net_cash_flow"]
    if feat.has_schedule(feature_set):
        # the booster covers the variable flows only: add the schedule inferred before the first test day and score
        # the whole net flow (the fixed costs included), which is what the forecast is
        excluded = tuple(exclude_categories)
        seen = transactions[transactions["booking_date"] < test.index[0]]
        pred = pred + fsched.total(fsched.infer(seen, categories=_schedule_categories(excluded)), test.index).to_numpy()
        full = feat.daily_cash_flow(transactions, exclude_categories=excluded)["net_cash_flow"]
        truth, history = full.reindex(test.index, fill_value=0.0), full.reindex(train.index, fill_value=0.0)
    err = pred - truth.to_numpy()
    naive = truth.to_numpy() - history.mean()
    return {
        "holdout_days": float(holdout_days),
        "holdout_mae": float(np.abs(err).mean()),
        "holdout_rmse": float(np.sqrt((err ** 2).mean())),
        "holdout_naive_mae": float(np.abs(naive).mean()),
        "holdout_total_error": float(err.sum()),
    }


def _usable_table(transactions: pd.DataFrame, excluded: tuple[str, ...],
                  requested: str | None) -> tuple[str, pd.DataFrame]:
    """(feature set, training table); the default set falls back to `calendar` when its levels cannot be built."""
    name = requested or DEFAULT_FEATURE_SET
    table = _training_table(transactions, excluded, name)
    if not feat.needs_levels(name):
        if table.empty:
            raise ValueError("no usable training days")
        return name, table
    open_days = int((table["shutdown_flag"] == 0).sum())
    if open_days >= MIN_LEVEL_TRAIN_DAYS:
        return name, table
    reason = (f"feature set {name!r} needs at least {MIN_LEVEL_TRAIN_DAYS} open training days with a recent level "
              f"(a level needs {feat.LEVEL_MIN_OPEN_DAYS} open days before it); this history has {open_days}")
    if requested is not None:
        raise ValueError(f"no usable training days: {reason}")
    log.warning("%s; training the calendar feature set instead", reason)
    table = _training_table(transactions, excluded, "calendar")
    if table.empty:
        raise ValueError("no usable training days")
    return "calendar", table


def train(
    transactions: pd.DataFrame,
    *,
    params: Mapping[str, Any] | None = None,
    holdout_days: int = 60,
    exclude_categories: Iterable[str] = EXCLUDED_CATEGORIES,
    log_to_mlflow: bool = True,
    register: bool = False,
    feature_set: str | None = None,
    band: bool = True,
) -> ForecastModel:
    """Fit on the daily net cash flow of `transactions` (columns of `features.fetch_transactions`).

    `components` (T068) splits the forecast: the fixed costs are a schedule read off the bookings (`schedule.infer`, stored
    in the model) and the booster learns only the variable flows; `train_mae` / `holdout_*` are then scored on the
    variable part / the whole net flow respectively (the holdout adds the schedule inferred before it).

    `feature_set` names the columns the model learns from (`features.FEATURE_SETS`); it is stored in the model and
    its metadata. Left out it is `DEFAULT_FEATURE_SET`, and when that set cannot be built from this history (too
    few open days for the recent level, or every usable day is a shutdown) the model is trained as `calendar`
    with a warning instead of failing. An explicitly requested level set raises a `ValueError` in that case.

    With `MLFLOW_TRACKING_URI` set the run is logged to MLflow (`log_to_mlflow`), and with `register=True` also
    registered as the new `champion` of `liquidity-forecast` (see `log_to_mlflow_if_configured`).

    With `band=True` the 80 % band is measured on the rolling-origin folds (about 6 extra fits, `_measure_band`)
    and stored in the model; `False` skips it (the folds themselves train with `band=False`).
    """
    if transactions.empty:
        raise ValueError("no transactions to train on")
    excluded = tuple(exclude_categories)
    feature_set, table = _usable_table(transactions, excluded, feature_set)
    merged = {**DEFAULT_PARAMS, **FEATURE_SET_PARAMS.get(feature_set, {}), **(params or {})}
    columns = feat.feature_set_columns(feature_set)
    metrics = _backtest(transactions, table, merged, holdout_days, excluded, feature_set)
    regressor = XGBRegressor(**merged).fit(table[columns], table["net_cash_flow"] - _offset(table, feature_set))
    fitted = regressor.predict(table[columns]) + np.asarray(_offset(table, feature_set))
    metrics["train_mae"] = float(np.abs(fitted - table["net_cash_flow"].to_numpy()).mean())
    metrics["train_days"] = float(len(table))
    level_values: dict[str, float] = {}
    last_day = table.index.max()
    if feat.has_schedule(feature_set):  # closed days are not rows: the history still ends at the last booking
        last_day = max(last_day, transactions["booking_date"].max().normalize())
    if feat.needs_levels(feature_set):  # the value of the first day after the history: held constant for the forecast
        after = last_day + pd.Timedelta(days=1)
        level_values = {k: float(v) for k, v in feat.recent_levels(transactions, [after]).iloc[0].items()}
    baseline_mean = 0.0
    if feature_set in feat.OFFSET_SETS:
        baseline_mean = float(feat.level_baseline(table[table["shutdown_flag"] == 0]).mean())
    spec = fsched.infer(transactions, categories=_schedule_categories(excluded)) if feat.has_schedule(feature_set) else None
    model = ForecastModel(
        regressor=regressor,
        shutdown_days=feat.infer_shutdown_days(transactions),
        due_days=feat.infer_due_days(transactions),
        trained_until=last_day,
        params=merged, metrics=metrics, excluded_categories=excluded,
        feature_columns=columns, feature_set=feature_set, level_values=level_values,
        baseline_mean=baseline_mean, schedule=spec,
    )
    if band:
        model.band_spec = _measure_band(transactions, merged, excluded, feature_set)
    if log_to_mlflow:
        log_to_mlflow_if_configured(model, register=register)
    return model


def _measure_band(transactions: pd.DataFrame, params: dict[str, Any], excluded: tuple[str, ...],
                  feature_set: str) -> dict[str, Any] | None:
    """The band spec of a model trained like this one, from `evaluate.rolling_origin` (T063); None when the history
    is too short for two origins (the model then serves the old formula)."""
    from cfo.forecast import evaluate  # evaluate imports this module

    def fold_trainer(history: pd.DataFrame) -> ForecastModel:
        return train(history, params=params, holdout_days=0, exclude_categories=excluded, log_to_mlflow=False,
                     feature_set=feature_set, band=False)

    try:
        # the folds' target drops the same categories the model does (else the difference would show up as bias)
        return fband.fit_band(evaluate.rolling_origin(transactions, fold_trainer, exclude_categories=excluded))
    except ValueError as exc:  # no usable origin, or a fold too short for this feature set
        log.info("no measured forecast band (%s); the old sqrt formula applies", exc)
        return None


def train_from_db(conn: psycopg.Connection | None = None, **kwargs: Any) -> ForecastModel:
    return train(feat.fetch_transactions(conn), **kwargs)


# -- MLflow ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class MlflowModelRef:
    """A model URI resolved to something that no longer moves: `uri` is pinned to a version for registry URIs."""

    uri: str
    run_id: str
    version: str | None  # registered model version; None for `runs:/...`


def _mlflow() -> Any:
    """The `mlflow` module (optional extra), pointed at `MLFLOW_TRACKING_URI` when that is set."""
    mlflow = importlib.import_module("mlflow")
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if uri:
        mlflow.set_tracking_uri(uri)
    return mlflow


def resolve_mlflow_model(uri: str) -> MlflowModelRef:
    """Which run and registered version a model URI points to right now (one cheap call, no download)."""
    mlflow = _mlflow()
    alias, numbered = _REGISTRY_ALIAS_URI.match(uri), _REGISTRY_VERSION_URI.match(uri)
    if alias or numbered:
        client = mlflow.MlflowClient()
        if alias:
            mv = client.get_model_version_by_alias(alias["name"], alias["alias"])
        else:
            mv = client.get_model_version(numbered["name"], numbered["version"])
        pinned = f"models:/{mv.name}/{mv.version}"
        run_id = mv.run_id or mlflow.models.get_model_info(pinned).run_id
        version: str | None = str(mv.version)
    else:
        pinned, version = uri, None
        run_id = mlflow.models.get_model_info(uri).run_id
    if not run_id:
        raise ValueError(f"MLflow model {uri} has no source run; the forecast sidecar cannot be found")
    return MlflowModelRef(uri=pinned, run_id=run_id, version=version)


def log_to_mlflow_if_configured(model: ForecastModel, *, register: bool = False,
                                model_name: str = REGISTERED_MODEL_NAME,
                                alias: str | None = CHAMPION_ALIAS) -> str | None:
    """Log params, metrics, the booster and the sidecar to MLflow when `MLFLOW_TRACKING_URI` is set.

    Returns the run id, or None when nothing was logged. With `register=True` the logged booster becomes a new
    version of the registered model `model_name` and `alias` (default `champion`; None for no alias) is moved
    to it. `model.mlflow_run_id` / `model.model_version` are set accordingly.

    `mlflow` is an optional dependency and is only imported when the variable is set. A tracking failure never
    breaks training (it is logged).
    """
    from cfo.tenancy import current_tenant
    if current_tenant(required=False) is not None or not os.environ.get("MLFLOW_TRACKING_URI"):
        return None
    try:
        mlflow = _mlflow()
        mlflow_xgboost = importlib.import_module("mlflow.xgboost")
    except ImportError:
        log.warning("MLFLOW_TRACKING_URI is set but mlflow is not installed (uv sync --extra mlflow)")
        return None
    run_id: str | None = None
    try:
        import xgboost

        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME") or MLFLOW_EXPERIMENT)
        with mlflow.start_run(run_name=f"xgb-until-{model.trained_until.date()}") as run:
            run_id = run.info.run_id
            mlflow.log_params({k: str(v) for k, v in model.params.items()})
            mlflow.log_param("excluded_categories", ",".join(model.excluded_categories))
            mlflow.log_param("feature_set", model.feature_set)
            mlflow.log_metrics(model.metrics)
            # Pinned requirement: skips MLflow's slow import-based inference and is all the booster needs.
            info = mlflow_xgboost.log_model(model.regressor.get_booster(), name=MLFLOW_MODEL_ARTIFACT,
                                            pip_requirements=[f"xgboost=={xgboost.__version__}"])
            with tempfile.TemporaryDirectory() as tmp:
                importances = Path(tmp) / "feature_importances.json"
                importances.write_text(model.feature_importances().to_json(indent=2), encoding="utf-8")
                mlflow.log_artifact(str(importances))
                meta = Path(tmp) / META_ARTIFACT
                meta.write_text(json.dumps(model.meta(), indent=2), encoding="utf-8")
                mlflow.log_artifact(str(meta))
        model.mlflow_run_id, model.model_version = run_id, None
        if register:
            version = mlflow.register_model(info.model_uri, model_name)
            if alias:
                mlflow.MlflowClient().set_registered_model_alias(model_name, alias, version.version)
            model.model_version = str(version.version)
        return run_id
    except Exception:  # noqa: BLE001 - tracking is best effort
        log.exception("MLflow logging failed; the model is still trained")
        return run_id


def get_model(transactions: pd.DataFrame | None = None, *, path: str | Path | None = None,
              retrain: bool = False, conn: psycopg.Connection | None = None) -> ForecastModel:
    """The saved model, or a freshly trained (and saved) one when none exists or `retrain` is set."""
    target = Path(path) if path else default_model_path()
    if not retrain and target.is_file() and _meta_path(target).is_file():
        return ForecastModel.load(target)
    model = train(transactions if transactions is not None else feat.fetch_transactions(conn))
    model.save(target)
    return model


# -- scheduled payments -----------------------------------------------------------------------------------
def partner_tranche_schedule(transactions: pd.DataFrame, *, total: int = PARTNER_TRANCHES_TOTAL) -> pd.Series:
    """Tranches of the silent partnership still to be paid: monthly, same day and amount as the last booked.

    Based on the number of `partner_investment` bookings so far (11 of 12 by end of November 2025 leaves the
    15 Dec 2025 payment; once all 12 are booked the schedule is empty). Amounts are as printed on the statement.
    """
    rows = transactions[transactions["category"] == PARTNER_CATEGORY].sort_values("booking_date")
    remaining = max(total - len(rows), 0)
    if rows.empty or remaining == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], name="date"))
    last_day, amount = rows["booking_date"].iloc[-1].normalize(), float(rows["amount"].iloc[-1])
    dates = [last_day + pd.DateOffset(months=i) for i in range(1, remaining + 1)]
    return pd.Series(amount, index=pd.DatetimeIndex(dates, name="date"))


def _as_series(payments: Any) -> pd.Series:
    if payments is None:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], name="date"))
    series = pd.Series(payments, dtype=float) if not isinstance(payments, pd.Series) else payments.astype(float)
    series.index = pd.DatetimeIndex(pd.to_datetime(series.index)).normalize().rename("date")
    return series.groupby(level=0).sum()


# -- the forecast -----------------------------------------------------------------------------------------
def last_known_balance(transactions: pd.DataFrame) -> tuple[pd.Timestamp, float]:
    """(last booking day, running sum of all bookings): the statement has no opening balance."""
    return transactions["booking_date"].max().normalize(), round(float(transactions["amount"].sum()), 2)


def liquidity_forecast(
    days: int,
    start_balance: float | None = None,
    *,
    model: ForecastModel | None = None,
    transactions: pd.DataFrame | None = None,
    conn: psycopg.Connection | None = None,
    scheduled_payments: Any = None,
) -> pd.DataFrame:
    """Daily predicted balances for the `days` days after the last booking.

    `start_balance` is the balance at the end of the last booking day; by default the running sum of all
    bookings. Columns, indexed by `date`: `predicted_net` (model), `scheduled` (known payments such as the
    remaining partner tranches, derived from the bookings unless `scheduled_payments` is given; pass an empty
    list for none), `net` (sum of both) and `balance`. `df.attrs` carries `start_balance` and
    `last_known_date`. Without `model` the saved one is loaded, or trained and saved on first use.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    tx = transactions if transactions is not None else feat.fetch_transactions(conn)
    if tx.empty:
        raise ValueError("no transactions: nothing to forecast from")
    model = (model or get_model(tx)).with_levels_from(tx)  # the level of the current bookings, model unchanged
    last_day, known = last_known_balance(tx)
    start = known if start_balance is None else float(start_balance)
    dates = pd.date_range(last_day + pd.Timedelta(days=1), periods=days, freq="D", name="date")
    out = pd.DataFrame({"predicted_net": model.predict(dates)})
    schedule = partner_tranche_schedule(tx) if scheduled_payments is None else _as_series(scheduled_payments)
    out["scheduled"] = schedule.reindex(dates).fillna(0.0)
    out["net"] = out["predicted_net"] + out["scheduled"]
    out["balance"] = start + out["net"].cumsum()
    out.index.name = "date"
    out.attrs.update(start_balance=start, last_known_date=str(last_day.date()))
    return out


def lowest_balance(forecast: pd.DataFrame) -> tuple[pd.Timestamp, float]:
    """(date, balance) of the lowest predicted balance."""
    day = forecast["balance"].idxmin()
    return day, float(forecast.loc[day, "balance"])
