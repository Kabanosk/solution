"""Liquidity forecast: XGBoost on the daily calendar features of `features.py` (T014, D-002, D-009).

* Target: daily net cash flow of the bank account, **without** `partner_investment`. Decision: the 12 monthly CHF
  tranches (15th of each month, tranche 12/12 on 15 Dec 2025) are a contractual schedule that ends, not a
  behaviour. Learned from one year of history the model would project them into 2026 and hide the cash that is
  freed after December. They are added on top as known `scheduled_payments` (`partner_tranche_schedule` derives
  the remaining ones from the bookings). Pass your own series to plan other fixed payments.
* The yearly summer shutdown is learned through `shutdown_flag` / `month` / `day_of_year`; the model then
  reproduces the June dip in 2026.
* `liquidity_forecast(days, start_balance=None)` walks the predicted daily net flow (plus scheduled payments)
  forward from the last known balance. The balance is the running sum of all bookings (the statement has no
  opening balance, the organizer's liquidity chart also starts at 0).
* The model is saved to `models/liquidity_xgb.json` (booster) + `.meta.json` (calendar inference, metrics);
  `models/` is git-ignored. Params, metrics and the booster go to MLflow only when `MLFLOW_TRACKING_URI` is set
  and `mlflow` is installed (optional extra `mlflow`).
* `feature_importances()` and `explain()` give T016 the numbers it hands to the LLM to explain a forecast.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from xgboost import XGBRegressor

from cfo.config import REPO_ROOT
from cfo.forecast import features as feat

log = logging.getLogger(__name__)

PARTNER_CATEGORY = "partner_investment"
PARTNER_TRANCHES_TOTAL = 12
EXCLUDED_CATEGORIES = (PARTNER_CATEGORY,)
FEATURE_COLUMNS = list(feat.CALENDAR_COLUMNS)
MODEL_FILENAME = "liquidity_xgb.json"
MLFLOW_EXPERIMENT = "liquidity-forecast"

# Small, shallow and deterministic: ~330 training days, strong calendar signal, no randomness.
DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 200, "max_depth": 3, "learning_rate": 0.05, "min_child_weight": 3,
    "subsample": 1.0, "reg_lambda": 1.0, "random_state": 0, "n_jobs": 1, "tree_method": "hist",
}


def default_model_path() -> Path:
    """`$FORECAST_MODEL_PATH` or `<repo>/models/liquidity_xgb.json`."""
    env = os.environ.get("FORECAST_MODEL_PATH")
    return Path(env) if env else REPO_ROOT / "models" / MODEL_FILENAME


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

    def features_for(self, dates: Iterable[Any]) -> pd.DataFrame:
        return feat.calendar_features(dates, shutdown_days=self.shutdown_days, due_days=self.due_days)[
            self.feature_columns]

    def predict(self, dates: Iterable[Any]) -> pd.Series:
        """Predicted daily net cash flow (EUR, without scheduled payments), indexed by date."""
        x = self.features_for(dates)
        return pd.Series(self.regressor.predict(x), index=x.index, name="predicted_net")

    def feature_importances(self) -> pd.Series:
        """Share of total gain per feature (sums to 1), largest first; features never used are 0."""
        gain = self.regressor.get_booster().get_score(importance_type="total_gain")
        raw = pd.Series({c: gain.get(c, 0.0) for c in self.feature_columns}, dtype=float)
        total = raw.sum()
        return (raw / total if total else raw).sort_values(ascending=False).rename("importance")

    def contributions(self, dates: Iterable[Any]) -> pd.DataFrame:
        """Per-day SHAP contributions in EUR per feature plus a `bias` column; rows sum to `predict`."""
        import xgboost as xgb

        x = self.features_for(dates)
        raw = self.regressor.get_booster().predict(xgb.DMatrix(x), pred_contribs=True)
        return pd.DataFrame(raw, index=x.index, columns=[*self.feature_columns, "bias"])

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
        meta = {
            "shutdown_days": sorted([m, d] for m, d in self.shutdown_days),
            "due_days": {k: list(v) for k, v in self.due_days.items()},
            "trained_until": str(self.trained_until.date()),
            "params": self.params, "metrics": self.metrics,
            "excluded_categories": list(self.excluded_categories),
            "feature_columns": self.feature_columns,
        }
        _meta_path(path).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path | None = None) -> ForecastModel:
        path = Path(path) if path else default_model_path()
        meta = json.loads(_meta_path(path).read_text(encoding="utf-8"))
        regressor = XGBRegressor()
        regressor.load_model(path)
        return cls(
            regressor=regressor,
            shutdown_days=frozenset((int(m), int(d)) for m, d in meta["shutdown_days"]),
            due_days={k: tuple(v) for k, v in meta["due_days"].items()},
            trained_until=pd.Timestamp(meta["trained_until"]),
            params=meta["params"], metrics=meta["metrics"],
            excluded_categories=tuple(meta["excluded_categories"]),
            feature_columns=list(meta["feature_columns"]),
        )


# -- training ---------------------------------------------------------------------------------------------
def _training_table(transactions: pd.DataFrame, excluded: Iterable[str]) -> pd.DataFrame:
    table = feat.daily_features(transactions, exclude_categories=tuple(excluded))
    return table.dropna(subset=["net_cash_flow"])


def _backtest(table: pd.DataFrame, params: dict[str, Any], holdout_days: int) -> dict[str, float]:
    """Fit on all but the last `holdout_days`, score on them. One year of history cannot be cross-validated by
    year (the shutdown occurs once), so the check is a time holdout of ordinary days, against a naive mean."""
    if holdout_days <= 0 or len(table) < 2 * holdout_days:
        return {}
    train, test = table.iloc[:-holdout_days], table.iloc[-holdout_days:]
    regressor = XGBRegressor(**params).fit(train[FEATURE_COLUMNS], train["net_cash_flow"])
    pred = regressor.predict(test[FEATURE_COLUMNS])
    err = pred - test["net_cash_flow"].to_numpy()
    naive = test["net_cash_flow"].to_numpy() - train["net_cash_flow"].mean()
    return {
        "holdout_days": float(holdout_days),
        "holdout_mae": float(np.abs(err).mean()),
        "holdout_rmse": float(np.sqrt((err ** 2).mean())),
        "holdout_naive_mae": float(np.abs(naive).mean()),
        "holdout_total_error": float(err.sum()),
    }


def train(
    transactions: pd.DataFrame,
    *,
    params: Mapping[str, Any] | None = None,
    holdout_days: int = 60,
    exclude_categories: Iterable[str] = EXCLUDED_CATEGORIES,
    log_to_mlflow: bool = True,
) -> ForecastModel:
    """Fit on the daily net cash flow of `transactions` (columns of `features.fetch_transactions`)."""
    if transactions.empty:
        raise ValueError("no transactions to train on")
    excluded = tuple(exclude_categories)
    merged = {**DEFAULT_PARAMS, **(params or {})}
    table = _training_table(transactions, excluded)
    metrics = _backtest(table, merged, holdout_days)
    regressor = XGBRegressor(**merged).fit(table[FEATURE_COLUMNS], table["net_cash_flow"])
    fitted = regressor.predict(table[FEATURE_COLUMNS])
    metrics["train_mae"] = float(np.abs(fitted - table["net_cash_flow"].to_numpy()).mean())
    metrics["train_days"] = float(len(table))
    model = ForecastModel(
        regressor=regressor,
        shutdown_days=feat.infer_shutdown_days(transactions),
        due_days=feat.infer_due_days(transactions),
        trained_until=table.index.max(),
        params=merged, metrics=metrics, excluded_categories=excluded,
    )
    if log_to_mlflow:
        log_to_mlflow_if_configured(model)
    return model


def train_from_db(conn: psycopg.Connection | None = None, **kwargs: Any) -> ForecastModel:
    return train(feat.fetch_transactions(conn), **kwargs)


def log_to_mlflow_if_configured(model: ForecastModel) -> bool:
    """Log params, metrics and the booster to MLflow when `MLFLOW_TRACKING_URI` is set; otherwise do nothing.

    `mlflow` is an optional dependency and is only imported when the variable is set. A tracking failure never
    breaks training (it is logged). Returns whether a run was logged.
    """
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        return False
    try:
        mlflow = importlib.import_module("mlflow")
        mlflow_xgboost = importlib.import_module("mlflow.xgboost")
    except ImportError:
        log.warning("MLFLOW_TRACKING_URI is set but mlflow is not installed (uv sync --extra mlflow)")
        return False
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(MLFLOW_EXPERIMENT)
        with mlflow.start_run(run_name=f"xgb-until-{model.trained_until.date()}"):
            mlflow.log_params({k: str(v) for k, v in model.params.items()})
            mlflow.log_param("excluded_categories", ",".join(model.excluded_categories))
            mlflow.log_metrics(model.metrics)
            mlflow_xgboost.log_model(model.regressor.get_booster(), "model")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "feature_importances.json"
                path.write_text(model.feature_importances().to_json(indent=2), encoding="utf-8")
                mlflow.log_artifact(str(path))
        return True
    except Exception:  # noqa: BLE001 - tracking is best effort
        log.exception("MLflow logging failed; the model is still trained")
        return False


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
    model = model or get_model(tx)
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
