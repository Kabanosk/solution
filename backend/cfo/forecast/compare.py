"""Compare the forecast feature sets with the rolling-origin harness of `evaluate.py` (T061).

`python -m cfo.forecast.compare [--json]` scores every set of `features.FEATURE_SETS` on the organizer's statement
(same origins, same horizon) and names the best one: the lowest `balance_error_90d`. That name is the default of
`model.train` (`model.DEFAULT_FEATURE_SET`); a test keeps the two in step.

The mean balance error hides which origin is wrong, so the result also carries `per_origin`: for every set and
origin the cumulative error (predicted - actual, EUR) after 30 days and at the end of the fold.
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
from cfo.forecast import model as fm

RANK_KEY = "balance_error_90d"


def _trainer(feature_set: str) -> ev.Trainer:
    return lambda tx: fm.train(tx, feature_set=feature_set, log_to_mlflow=False, holdout_days=0)


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cfo.forecast.compare",
                                     description="Score every forecast feature set with the rolling-origin "
                                                 "backtest on the organizer's bank statement.")
    parser.add_argument("--json", action="store_true", help="print the result as one JSON object")
    args = parser.parse_args(argv)
    result = compare(ev.load_statement())
    print(json.dumps(result, indent=2) if args.json else _table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
