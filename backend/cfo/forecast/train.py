"""Retrain the forecast model on the current history: `python -m cfo.forecast.train` (T064).

The nightly CronJob `forecast-retrain` runs `--register --promote-if-better --source db`. It trains the default
configuration on the whole history, logs the run to MLflow, and with `--register` registers it as a new version
under the alias `challenger`. `--promote-if-better` (which implies `--register`) moves `champion` to it only when it
beats the current champion by the margin: the decision is `tune.promote`, the one promotion path, so the search and
the retrain cannot disagree about what "better" means (real 90-day balance error on both sides, the champion scored
now on the same data, `--min-gain` margin, no champion yet = the candidate takes the alias).

Exit code 0 also when nothing changed (the line says why); 2 when the registry could not be asked (champion lookup
failed, MLflow is not configured for `--register`): then nothing was registered and `champion` is untouched.
Reads `MLFLOW_TRACKING_URI`, `DATABASE_URL`, `SOURCE_CSV` from the environment; the JSON goes to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from typing import Any

from cfo.forecast import model as m
from cfo.forecast import tune

log = logging.getLogger(__name__)


def _champion_version(tracking_uri: str | None) -> str | None:
    """The version `champion` points to now (best effort, for the message only)."""
    try:
        with tune._tracking(tracking_uri) as handle:
            if handle is None:
                return None
            return str(handle[0].MlflowClient().get_model_version_by_alias(m.REGISTERED_MODEL_NAME,
                                                                           m.CHAMPION_ALIAS).version)
    except Exception:  # noqa: BLE001 - the message just says "champion" then
        return None


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _message(promotion: tune.Promotion, tracking_uri: str | None, if_better: bool) -> str:
    version = f"v{promotion.version}"
    if promotion.moved_champion:
        return f"champion moved to {version}: {promotion.reason}"
    if not if_better:
        return f"registered {version} as {promotion.alias}; champion untouched"
    if promotion.reason.startswith(tune.CHAMPION_CHANGED):  # another job moved it first: say that, not "margin"
        return f"champion kept, registered {version} as {promotion.alias}: {promotion.reason}"
    champion = _champion_version(tracking_uri)
    kept = f"champion v{champion} kept" if champion else "champion kept"
    if promotion.champion_error is not None and promotion.candidate_error is not None:
        return (f"{kept}: candidate 90-day balance error {_money(promotion.candidate_error)} vs champion "
                f"{_money(promotion.champion_error)} (margin not reached); registered {version} as {promotion.alias}")
    return f"{kept}: {promotion.reason}; registered {version} as {promotion.alias}"


def _min_gain_arg(text: str) -> float:
    try:
        return tune._check_min_gain(float(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cfo.forecast.train",
        description="Train the forecast model with the default configuration on the current history, log it to "
                    "MLflow, optionally register it and promote it to 'champion' when it is better.")
    parser.add_argument("--source", choices=("auto", "db", "csv"), default="auto",
                        help="data: Postgres when DATABASE_URL works, else the CSV (auto); or force one "
                             "('db' fails loudly: use it in a cluster Job)")
    parser.add_argument("--tracking-uri", help="MLflow tracking URI (default: $MLFLOW_TRACKING_URI)")
    parser.add_argument("--register", action="store_true",
                        help="register the trained model as a new version with the alias 'challenger'")
    parser.add_argument("--promote-if-better", action="store_true",
                        help="implies --register; move 'champion' when it beats the current champion by the margin")
    parser.add_argument("--min-gain", type=_min_gain_arg, default=tune.DEFAULT_MIN_GAIN,
                        help="a candidate is better only by this share of the champion's error (default 0.02)")
    parser.add_argument("--json", action="store_true", help="print the result as one JSON object")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    register = args.register or args.promote_if_better
    uri = args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
    if register and not uri:
        log.error("--register / --promote-if-better need MLflow: set MLFLOW_TRACKING_URI (or --tracking-uri)")
        return 2

    transactions, source = tune.load_transactions(args.source)
    if register and tune.refuses_csv_promotion(args.source, source):
        return 2
    log.info("%d bookings from %s, %s .. %s", len(transactions), source, transactions["booking_date"].min().date(),
             transactions["booking_date"].max().date())
    model = m.train(transactions, log_to_mlflow=False)
    data: dict[str, Any] = {
        "feature_set": model.feature_set, "trained_until": str(model.trained_until.date()), "data_source": source,
        "metrics": model.metrics, "mlflow_run_id": None, "registered": None,
    }
    code = 0
    if not register:
        with tune._tracking(args.tracking_uri) as handle:
            data["mlflow_run_id"] = m.log_to_mlflow_if_configured(model) if handle else None
        data["message"] = (f"trained {model.feature_set} until {data['trained_until']}; "
                           + (f"logged as MLflow run {data['mlflow_run_id']}" if data["mlflow_run_id"]
                              else "not logged (MLFLOW_TRACKING_URI is not set or logging failed)"))
    else:
        scores, per_origin = tune.score_config(transactions, model.feature_set, model.params)
        data["balance_error_90d"] = scores.get(tune.PROMOTION_METRIC)
        candidate = tune.TuningResult(
            best_feature_set=model.feature_set, best_params=dict(model.params), best_scores=scores,
            per_origin=per_origin, min_gain=args.min_gain, tracking_uri=args.tracking_uri, data_source=source,
            transactions=transactions)
        try:
            promotion = tune.promote(candidate, if_better=args.promote_if_better, min_gain=args.min_gain)
            data["registered"] = promotion.to_dict()
            data["mlflow_run_id"] = promotion.run_id
            data["message"] = _message(promotion, args.tracking_uri, args.promote_if_better)
        except tune.PromotionError as exc:  # nothing registered, champion untouched
            log.error("promotion aborted: %s", exc)
            data["promotion_error"] = str(exc)
            data["message"] = f"aborted: {exc}"
            code = 2
    print(json.dumps(tune.finite(data), indent=2, allow_nan=False) if args.json else data["message"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
