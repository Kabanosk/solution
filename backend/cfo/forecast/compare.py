"""Compare the forecast feature sets with the rolling-origin harness of `evaluate.py` (T061).

`python -m cfo.forecast.compare [--json]` scores every set of `features.FEATURE_SETS` on the organizer's statement
(same origins, same horizon) and names the best one: the lowest `balance_error_90d`. That name is the default of
`model.train` unless a decision says otherwise: since T068 the default is `components` by the owner's decision D-039
(`.house/reports/T068.md`), while the verdict of `--weekly` (below) stays as measured.

The mean balance error hides which origin is wrong, so the result also carries `per_origin`: for every set and
origin the cumulative error (predicted - actual, EUR) after 30 days and at the end of the fold.

`python -m cfo.forecast.compare --json --weekly` (T068) is the verdict on a candidate model. It scores every set on the
6 month-end folds (comparable with T060-T062) **and** on about 27 weekly folds (`folds.weekly_origins`), checks
December 2025 and the monthly forecast to July 2026, and answers `"verdict": "better"` only when the best candidate
(a set that is not one of the `LEGACY_SETS`) clears every point of `BAR` against the reference (`REFERENCE_SET`, the
default before T068, kept with its own parameters), otherwise `"verdict": "not_better"` with the reason. The bar is
frozen in the task file (`.house/tasks/T068-*.md`): nothing here may be loosened to make a candidate pass.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from cfo.forecast import evaluate as ev
from cfo.forecast import features as feat
from cfo.forecast import folds as fold_origins
from cfo.forecast import model as fm

RANK_KEY = "balance_error_90d"
LEGACY_SETS = ("calendar", "level", "level_baseline")  # sets that existed before T068: references, not candidates
REFERENCE_SET = "level_baseline"  # the default model before T068: what a candidate must beat
# The bar of T068 (frozen in the task file). Every point must hold, all on the organizer's 2025 statement.
BALANCE_GAIN = 0.10  # weekly-fold balance_error_90d at least this much below the reference
DECEMBER_MAE_MAX = 240.0  # December 2025 daily MAE
JUNE_NET_MAX = -10_000.0  # June 2026 net, and it must be the lowest month
JAN_MAY_MEAN_MIN = 9_000.0  # mean monthly net Jan-May 2026
DECEMBER_ORIGIN = "2025-11-30"
FORECAST_END = "2026-07-31"


def _trainer(feature_set: str) -> ev.Trainer:
    # no band: it does not change a prediction and costs several fits per fold
    return lambda tx: fm.train(tx, feature_set=feature_set, log_to_mlflow=False, holdout_days=0, band=False)


def _per_origin(folds: pd.DataFrame) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for origin, fold in folds.groupby("origin"):
        at30 = fold.loc[fold["horizon"] == 30, "cum_error"]
        out[str(pd.Timestamp(origin).date())] = {
            "days": float(len(fold)),
            "cum_error_30d": float(at30.iloc[0]) if len(at30) else math.nan,
            "cum_error_end": float(fold["cum_error"].iloc[-1]),
        }
    return out


def compare(transactions: pd.DataFrame, feature_sets: Iterable[str] | None = None, **kw: Any) -> dict[str, Any]:
    """Score each feature set; `kw` goes to `rolling_origin` (`origins`, `horizon_days`).

    Returns `{"scores": {set: metrics}, "per_origin": {set: {origin: {...}}}, "best_feature_set": name}`.
    """
    names = list(feature_sets) if feature_sets is not None else list(feat.FEATURE_SETS)
    if kw.get("origins") is not None:
        kw["origins"] = list(kw["origins"])  # a one-shot iterable must serve every feature set
    scores: dict[str, dict[str, float]] = {}
    per_origin: dict[str, dict[str, dict[str, float]]] = {}
    for name in names:
        folds = ev.rolling_origin(transactions, _trainer(name), **kw)
        scores[name], per_origin[name] = ev.score(folds), _per_origin(folds)
    ranked = [n for n in names if not math.isnan(scores[n][RANK_KEY])]
    if not ranked:
        raise ValueError(f"no feature set reaches 90 days in any fold: cannot rank by {RANK_KEY}")
    return {"scores": scores, "per_origin": per_origin, "best_feature_set": min(ranked, key=lambda n: scores[n][RANK_KEY])}


# -- the verdict (T068) -------------------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """JSON-safe copy: NaN / infinity become None, numpy scalars plain numbers."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (int, float, bool)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def december_check(transactions: pd.DataFrame, feature_set: str, *, origin: str = DECEMBER_ORIGIN) -> dict[str, float]:
    """A forecast made on `origin` (30 Nov 2025) for the 31 days after it, against what happened: daily MAE, the
    predicted and the real December net (partner tranches left out on both sides, as everywhere in the harness)."""
    folds = ev.rolling_origin(transactions, _trainer(feature_set), origins=[origin], horizon_days=31)
    return {"daily_mae": float(folds["error"].abs().mean()), "predicted_net": float(folds["predicted_net"].sum()),
            "actual_net": float(folds["actual_net"].sum())}


def monthly_forecast(transactions: pd.DataFrame, feature_set: str, *, end: str = FORECAST_END) -> dict[str, float]:
    """Predicted net per calendar month from the day after the last booking to `end`, as the product forecasts it
    (`model.liquidity_forecast`: refreshed level and schedule, partner tranches excluded)."""
    model = fm.train(transactions, feature_set=feature_set, log_to_mlflow=False, holdout_days=0, band=False)
    last = transactions["booking_date"].max().normalize()
    days = (pd.Timestamp(end) - last).days
    net = fm.liquidity_forecast(days, model=model, transactions=transactions, scheduled_payments=[])["predicted_net"]
    return {str(k.date())[:7]: round(float(v), 2) for k, v in net.resample("MS").sum().items()}


def _jan_may(monthly: dict[str, float]) -> float:
    values = [v for k, v in monthly.items() if "2026-01" <= k <= "2026-05"]
    return sum(values) / len(values) if values else math.nan


def _june_is_lowest(monthly: dict[str, float]) -> bool:
    return bool(monthly) and min(monthly, key=monthly.__getitem__) == "2026-06"


def evaluate_bar(reference: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """The points of the bar for one candidate against the reference. Both are dicts with `weekly` (score dict),
    `december` (`december_check`), `monthly_to_nov` and `monthly_full` (`monthly_forecast` of the history up to 30 Nov /
    the whole statement). Each point: `id`, `description`, `value`, `threshold`, `passed`."""
    ref_w, cand_w = reference["weekly"], candidate["weekly"]
    actual = candidate["december"]["actual_net"]
    ref_gap = abs(reference["december"]["predicted_net"] - actual)
    cand_gap = abs(candidate["december"]["predicted_net"] - actual)
    points = [
        ("weekly_balance_error_90d", f"weekly-fold balance_error_90d at least {BALANCE_GAIN:.0%} below the reference",
         cand_w["balance_error_90d"], ref_w["balance_error_90d"] * (1 - BALANCE_GAIN)),
        ("weekly_daily_mae", "weekly-fold daily_mae not worse than the reference",
         cand_w["daily_mae"], ref_w["daily_mae"]),
        ("december_daily_mae", f"December 2025 daily MAE <= {DECEMBER_MAE_MAX:.0f}",
         candidate["december"]["daily_mae"], DECEMBER_MAE_MAX),
        ("december_net", "predicted December 2025 net closer to the real one than the reference's",
         cand_gap, ref_gap),
    ]
    out: list[dict[str, Any]] = []
    for pid, text, value, threshold in points:
        passed = bool(math.isfinite(value) and (value < threshold if pid in ("december_net",) else value <= threshold))
        out.append({"id": pid, "description": text, "value": value, "threshold": threshold, "passed": passed})
    for label, key in (("trained on bookings to 30 Nov 2025", "monthly_to_nov"), ("trained on the whole statement", "monthly_full")):
        monthly = candidate[key]
        out.append({"id": f"june_2026_lowest_{key}", "description": f"June 2026 is the lowest month ({label})",
                    "value": monthly.get("2026-06", math.nan), "threshold": min(monthly.values()) if monthly else math.nan,
                    "passed": _june_is_lowest(monthly)})
        june = monthly.get("2026-06", math.nan)
        out.append({"id": f"june_2026_below_{key}", "description": f"June 2026 net below {JUNE_NET_MAX:,.0f} EUR ({label})",
                    "value": june, "threshold": JUNE_NET_MAX, "passed": bool(math.isfinite(june) and june < JUNE_NET_MAX)})
        mean = _jan_may(monthly)
        out.append({"id": f"jan_may_mean_{key}", "description": f"mean monthly net Jan-May 2026 >= {JAN_MAY_MEAN_MIN:,.0f} EUR ({label})",
                    "value": mean, "threshold": JAN_MAY_MEAN_MIN, "passed": bool(math.isfinite(mean) and mean >= JAN_MAY_MEAN_MIN)})
    return out


def _profile(transactions: pd.DataFrame, name: str, weekly_scores: dict[str, float]) -> dict[str, Any]:
    until_nov = transactions[transactions["booking_date"] <= pd.Timestamp(DECEMBER_ORIGIN)]
    return {
        "weekly": weekly_scores, "december": december_check(transactions, name),
        "monthly_to_nov": monthly_forecast(until_nov, name), "monthly_full": monthly_forecast(transactions, name),
    }


def assess(transactions: pd.DataFrame, feature_sets: Iterable[str] | None = None) -> dict[str, Any]:
    """Month-end and weekly scores of every feature set, the December and monthly checks, the bar and the verdict.

    Returns a JSON-safe dict (see the module docstring); `verdict` is `"better"` or `"not_better"`, `best_feature_set`
    the winning candidate or, when none clears the bar, the reference."""
    names = list(feature_sets) if feature_sets is not None else list(feat.FEATURE_SETS)
    if REFERENCE_SET not in names:
        names.append(REFERENCE_SET)
    origins = fold_origins.weekly_origins(transactions)
    if not origins:
        raise ValueError("the history is too short for weekly folds")
    month_end = compare(transactions, names)
    weekly = compare(transactions, names, origins=origins)
    candidates = [n for n in names if n not in LEGACY_SETS]
    result: dict[str, Any] = {
        "reference": REFERENCE_SET, "candidates": candidates,
        "month_end": month_end, "weekly": {**weekly, "n_origins": len(origins), "first": str(origins[0].date()),
                                          "last": str(origins[-1].date())},
        "december_2025": {}, "monthly_forecast": {"trained_to_2025-11-30": {}, "trained_to_end": {}},
    }
    profiles: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys([REFERENCE_SET, *candidates, *[n for n in names if n in LEGACY_SETS]]):
        profiles[name] = _profile(transactions, name, weekly["scores"][name])
        result["december_2025"][name] = profiles[name]["december"]
        result["monthly_forecast"]["trained_to_2025-11-30"][name] = profiles[name]["monthly_to_nov"]
        result["monthly_forecast"]["trained_to_end"][name] = profiles[name]["monthly_full"]
    result["bar"], result["verdict"], result["best_feature_set"], result["reason"] = {}, "not_better", REFERENCE_SET, ""
    ranked = [n for n in candidates if not math.isnan(weekly["scores"][n][RANK_KEY])]
    if not ranked:
        result["reason"] = "there is no candidate feature set to judge"
        return _clean(result)
    best = min(ranked, key=lambda n: weekly["scores"][n][RANK_KEY])
    bar = evaluate_bar(profiles[REFERENCE_SET], profiles[best])
    failed = [p for p in bar if not p["passed"]]
    result["best_candidate"] = best
    result["bar"] = bar
    if failed:
        result["reason"] = f"{best} does not clear the bar: " + "; ".join(
            f"{p['description']} (value {p['value']:,.1f}, threshold {p['threshold']:,.1f})" for p in failed)
    else:
        result.update(verdict="better", best_feature_set=best,
                      reason=f"{best} clears every point of the bar against {REFERENCE_SET}")
    return _clean(result)


def _table(result: dict[str, Any]) -> str:
    scores = result["scores"]
    names = list(scores)
    keys = list(next(iter(scores.values())))
    width = max(map(len, keys))
    lines = [f"{'metric':<{width}}  " + "  ".join(f"{n:>12}" for n in names)]
    for key in keys:
        cells = "  ".join(f"{scores[n][key]:>12.0f}" if key == "n_origins" else f"{scores[n][key]:>12.3f}"
                          for n in names)
        lines.append(f"{key:<{width}}  {cells}")
    lines.append("")
    lines.append("cumulative error per origin, EUR (predicted - actual): after 30 days / at the end of the fold")
    for name, origins in result["per_origin"].items():
        cells = ", ".join(f"{o} {v['cum_error_30d']:+.0f}/{v['cum_error_end']:+.0f} ({v['days']:.0f} d)"
                          for o, v in origins.items())
        lines.append(f"  {name}: {cells}")
    lines.append("")
    lines.append(f'"best_feature_set": "{result["best_feature_set"]}"')
    return "\n".join(lines)


def _verdict_table(result: dict[str, Any]) -> str:
    def sub(block: dict[str, Any]) -> str:  # a score table without its "best_feature_set" line (the verdict has its own)
        text = _table(block).rsplit("\n\n", 1)[0]
        return f"{text}\n(lowest balance_error_90d: {block['best_feature_set']})"

    weekly = result["weekly"]
    heading = f"weekly folds ({weekly['n_origins']} origins, {weekly['first']} .. {weekly['last']}):"
    lines = ["month-end folds (6 origins, comparable with T060-T062):", sub(result["month_end"]), "",
             heading, sub(weekly), "", "December 2025 (forecast from 30 Nov):"]
    for name, d in result["december_2025"].items():
        lines.append(f"  {name}: daily MAE {d['daily_mae']:.1f}, predicted net {d['predicted_net']:,.0f} "
                     f"(real {d['actual_net']:,.0f})")
    for label, per_set in result["monthly_forecast"].items():
        lines.append(f"monthly net forecast, {label}:")
        for name, months in per_set.items():
            lines.append(f"  {name}: " + ", ".join(f"{k} {v:,.0f}" for k, v in months.items()))
    lines.append("")
    lines.append(f"the bar for {result.get('best_candidate', '-')} against {result['reference']}:")
    for p in result["bar"]:
        lines.append(f"  [{'PASS' if p['passed'] else 'FAIL'}] {p['description']}: {p['value']:,.1f} vs {p['threshold']:,.1f}")
    lines.append(f"{result['reason']}")
    lines.append(f'"verdict": "{result["verdict"]}"')
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cfo.forecast.compare",
                                     description="Score every forecast feature set with the rolling-origin "
                                                 "backtest on the organizer's bank statement.")
    parser.add_argument("--json", action="store_true", help="print the result as one JSON object")
    parser.add_argument("--weekly", action="store_true",
                        help="also score on weekly origins, check December 2025 and the monthly forecast and answer "
                             "with a verdict on the candidate feature sets (the T068 bar)")
    args = parser.parse_args(argv)
    if args.weekly:
        result = assess(ev.load_statement())
        print(json.dumps(result, indent=2, allow_nan=False) if args.json else _verdict_table(result))
        return 0
    result = compare(ev.load_statement())
    print(json.dumps(result, indent=2) if args.json else _table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
