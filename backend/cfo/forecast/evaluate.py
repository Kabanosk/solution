"""Forecast evaluation harness (T053): how wrong is the predicted *balance* N days after a forecast was made.

The CFO reads the balance, not the daily flow. A single holdout of daily errors (`model._backtest`) hides errors
that accumulate (a level step that the model misses is invisible in the daily MAE and 9k EUR/month wrong in the
balance). So every fold here is a real forecast: at an *origin* the trainer is fitted on the bookings up to that
day and nothing else (its shutdown and due-day inference included, the rule of T045), then its predicted daily
net flow is compared with what happened, day by day, up to `horizon_days` later, and summed up to a balance error.

Interface (a contract for T054 features, T055 tuning, T056 bands; keep the names and metric keys):

* `rolling_origin(transactions, trainer, *, origins=None, horizon_days=90)` -> one row per (origin, day).
* `score(folds)` -> `daily_mae`, `daily_rmse`, `naive_mae`, `balance_error_30d/_60d/_90d` (mean absolute
  cumulative error at that horizon), `balance_bias_90d` (signed mean), `monthly_net_mape`, `n_origins`.
* `evaluate(transactions, trainer=None, **kw)` -> `rolling_origin` + `score`; default trainer is `model.train`.

Conventions:

* Error is `predicted - actual`, so a model that is too low has a negative error and a negative bias.
* Scheduled payments (the partner tranches, `model.EXCLUDED_CATEGORIES`) are excluded from both sides, as in
  `model.py`: the forecast side is `ForecastModel.predict` only, the truth side leaves those categories out. The
  target is owned by the scorer (`exclude_categories` keyword, default the partner tranches), never read from the
  trained model, so scores of different candidates are comparable.
* The default origins are month ends from the 6th month of history on. An origin needs `min(horizon_days, 30)`
  days of truth after it; a fold is cut where the history ends, so late origins only have a short horizon. The
  `balance_error_Nd` scores average over the origins whose fold reaches day N (NaN if none does), `daily_*` and
  `naive_mae` over all fold days.
* `naive_mae` is the error of predicting every day with the mean daily net of the training bookings.
* `monthly_net_mape` is a fraction (0.1 is 10 %): per origin and calendar month that lies completely inside the
  fold, |predicted month net - actual month net| / |actual month net|, averaged; months with a zero net are skipped.
* `python -m cfo.forecast.evaluate [--json]` scores the current model on the organizer's statement.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import pandas as pd

from cfo.forecast import features as feat
from cfo.forecast import model as fm

Trainer = Callable[[pd.DataFrame], "fm.ForecastModel"]  # fit on the transactions it is given, nothing else

DEFAULT_HORIZON_DAYS = 90
MIN_HISTORY_MONTHS = 6  # the first default origin is the end of the 6th month of history
MIN_TRUTH_DAYS = 30
BALANCE_HORIZONS = (30, 60, 90)
METRIC_KEYS = ("daily_mae", "daily_rmse", "naive_mae", "balance_error_30d", "balance_error_60d",
               "balance_error_90d", "balance_bias_90d", "monthly_net_mape", "n_origins")
FOLD_COLUMNS = ["origin", "date", "horizon", "predicted_net", "actual_net", "error", "cum_error", "naive_net"]


def default_trainer(transactions: pd.DataFrame) -> fm.ForecastModel:
    """The current model, without MLflow logging and without its own holdout backtest (not needed per fold)."""
    return fm.train(transactions, log_to_mlflow=False, holdout_days=0)


# -- folds ------------------------------------------------------------------------------------------------
def _default_origins(days: pd.DatetimeIndex, horizon_days: int) -> list[pd.Timestamp]:
    first, last = days[0], days[-1]
    month_end = (first.to_period("M") + (MIN_HISTORY_MONTHS - 1)).end_time.normalize()
    need = pd.Timedelta(days=min(horizon_days, MIN_TRUTH_DAYS))
    origins: list[pd.Timestamp] = []
    while last - month_end >= need:
        origins.append(month_end)
        month_end = (month_end + pd.Timedelta(days=1)).to_period("M").end_time.normalize()
    return origins


def rolling_origin(
    transactions: pd.DataFrame,
    trainer: Trainer,
    *,
    origins: Iterable[Any] | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    exclude_categories: Iterable[str] = fm.EXCLUDED_CATEGORIES,
) -> pd.DataFrame:
    """Backtest folds: one row per (origin, day) with the prediction and the truth.

    `trainer` gets the bookings up to and including the origin day and returns a fitted model whose `predict`
    is used. Columns: `origin`, `date`, `horizon` (1 = the day after the origin), `predicted_net`, `actual_net`,
    `error` (predicted - actual), `cum_error` (running sum of `error` within the origin: the balance error at
    that day) and `naive_net` (mean daily net of the training bookings). See the module docstring for the rest.

    The target is owned by the scorer: `exclude_categories` (default: the partner tranches) is removed from the
    actuals and from `naive_net` whatever the trained model says, so candidates are scored on the same truth.
    Repeated origins count once; origins are sorted.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    if transactions.empty:
        raise ValueError("no transactions to evaluate on")
    transactions = transactions.assign(booking_date=transactions["booking_date"].dt.normalize())  # a copy, by day
    booking_day = transactions["booking_date"]
    first_day, last_day = booking_day.min(), booking_day.max()
    if origins is None:
        chosen = _default_origins(pd.date_range(first_day, last_day, freq="D"), horizon_days)
    else:
        chosen = sorted({pd.Timestamp(o).normalize() for o in origins})
    if not chosen:
        raise ValueError(f"no usable origin: need {MIN_HISTORY_MONTHS} months of history plus "
                         f"{min(horizon_days, MIN_TRUTH_DAYS)} days of truth after the origin")

    excluded = tuple(exclude_categories)
    truth = feat.daily_cash_flow(transactions, exclude_categories=excluded)["net_cash_flow"]
    parts: list[pd.DataFrame] = []
    for origin in chosen:
        days = pd.date_range(origin + pd.Timedelta(days=1), min(origin + pd.Timedelta(days=horizon_days), last_day),
                             freq="D", name="date")
        if days.empty:
            raise ValueError(f"origin {origin.date()} has no bookings after it to compare with")
        train = transactions[booking_day <= origin].reset_index(drop=True)  # the only thing a trainer may see
        model = trainer(train)
        actual = truth.reindex(days, fill_value=0.0)
        predicted = pd.Series(model.predict(days)).reindex(days)
        if predicted.isna().any():
            raise ValueError("the model returned no prediction for some forecast days")
        past = feat.daily_cash_flow(train, end=origin, exclude_categories=excluded)["net_cash_flow"]
        fold = pd.DataFrame({
            "origin": origin,
            "date": days,
            "horizon": range(1, len(days) + 1),
            "predicted_net": predicted.to_numpy(dtype=float),
            "actual_net": actual.to_numpy(dtype=float),
        })
        fold["error"] = fold["predicted_net"] - fold["actual_net"]
        fold["cum_error"] = fold["error"].cumsum()
        fold["naive_net"] = float(past.mean()) if len(past) else math.nan
        parts.append(fold[FOLD_COLUMNS])
    return pd.concat(parts, ignore_index=True)


# -- scores -----------------------------------------------------------------------------------------------
def _monthly_net_mape(folds: pd.DataFrame) -> float:
    ratios: list[float] = []
    for _, fold in folds.groupby("origin"):
        by_month = fold.groupby(fold["date"].dt.to_period("M"))
        for month, rows in by_month:
            if len(rows) < month.days_in_month:
                continue  # the fold starts or ends inside this month
            actual = rows["actual_net"].sum()
            if actual != 0:
                ratios.append(abs(rows["predicted_net"].sum() - actual) / abs(actual))
    return float(sum(ratios) / len(ratios)) if ratios else math.nan


def score(folds: pd.DataFrame) -> dict[str, float]:
    """The metrics of `rolling_origin` folds (see the module docstring for the definitions)."""
    if folds.empty:
        raise ValueError("no folds to score")
    err = folds["error"].to_numpy(dtype=float)
    out: dict[str, float] = {
        "daily_mae": float(abs(err).mean()),
        "daily_rmse": float((err ** 2).mean() ** 0.5),
        "naive_mae": float((folds["actual_net"] - folds["naive_net"]).abs().mean()),
    }
    for horizon in BALANCE_HORIZONS:
        at = folds.loc[folds["horizon"] == horizon, "cum_error"]
        out[f"balance_error_{horizon}d"] = float(at.abs().mean()) if len(at) else math.nan
    at90 = folds.loc[folds["horizon"] == 90, "cum_error"]
    out["balance_bias_90d"] = float(at90.mean()) if len(at90) else math.nan
    out["monthly_net_mape"] = _monthly_net_mape(folds)
    out["n_origins"] = float(folds["origin"].nunique())
    return {key: out[key] for key in METRIC_KEYS}


def evaluate(transactions: pd.DataFrame, trainer: Trainer | None = None, **kw: Any) -> dict[str, float]:
    """`rolling_origin` + `score`; `kw` goes to `rolling_origin` (`origins`, `horizon_days`)."""
    return score(rolling_origin(transactions, trainer or default_trainer, **kw))


# -- the organizer's statement ----------------------------------------------------------------------------
def load_statement() -> pd.DataFrame:
    """The organizer's bank statement, whole year, as a transactions frame (`SourceNotFound` without the CSV)."""
    from cfo.data.source import find_source_csv, load_bank_csv

    txns = load_bank_csv(find_source_csv())
    df = pd.DataFrame([{"booking_date": t.booking_date, "amount": float(t.amount), "category": t.category,
                        "team_id": t.team_id} for t in txns])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df.sort_values("booking_date", kind="stable").reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cfo.forecast.evaluate",
                                     description="Score the current forecast model with a rolling-origin backtest "
                                                 "on the organizer's bank statement.")
    parser.add_argument("--json", action="store_true", help="print the scores as one JSON object")
    args = parser.parse_args(argv)
    scores = evaluate(load_statement())
    if args.json:
        print(json.dumps(scores, indent=2))
    else:
        width = max(map(len, scores))
        for key, value in scores.items():
            print(f"{key:<{width}}  {value:>12.3f}" if key != "n_origins" else f"{key:<{width}}  {value:>12.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
