"""The 80 % uncertainty band of the balance forecast, measured instead of assumed (T063).

The old band was `1.2816 * holdout_rmse * sqrt(n)`: it assumes daily errors are independent and cancel out. The
rolling-origin folds of `evaluate.rolling_origin` show they are not (a level change or a bias accumulates), so the
band is computed at training time from the **cumulative** errors of those folds and stored in the model metadata.

Method (`fit_band`), kept deliberately small because there are only a handful of origins (6 on the 2025
statement, 4 of them reach day 90):

* `r(o, h)` = actual balance minus forecast balance `h` days after origin `o` (= `-cum_error`).
* Growth: width grows like `h ** p`. `p` is the slope of log(mean |r|) over log(h) for the horizons 7..H, with H the
  longest horizon at least two origins reach; clamped to [0.5, 1] (0.5 = independent daily errors, 1 = a steady
  bias or level error). Beyond H the curve continues along its tangent (linear), so it never flattens.
* Scale and asymmetry: `z = r / h ** p` pooled over all origins and horizons up to H; the 10 % and 90 % quantiles of
  `z` give the lower and upper constant. A side that the data never exceeds stays at 0 (the band brackets the
  forecast, `lower <= 0 <= upper`). The signed bias is thus kept: a model that is too high gets a wide lower side.
* Few origins: both constants are inflated by `sqrt(1 + 1 / n_origins)`, the usual small-sample widening of a
  prediction interval. The pooled points of one origin are strongly correlated, so the quantiles are an honest
  estimate only in this shrunk sense: see the T063 report for the leave-one-origin-out coverage.

The stored spec is plain JSON (`spec["anchors"]` lists the offsets at 7, 14, 30, 60, 90 days for reading).
`offsets(spec, days)` returns `(lower_offset, upper_offset)` to add to the forecast balance.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

LEVEL = 0.8
INTERVAL_Z_80 = 1.2816  # one-sided z of a two-sided 80 % interval (the old band)
MIN_ORIGINS = 2
MIN_GROWTH, MAX_GROWTH = 0.5, 1.0
MIN_FIT_HORIZON = 7  # shorter horizons are dominated by daily noise and do not tell the growth
ANCHOR_HORIZONS = (7, 14, 30, 60, 90)


def legacy_offsets(sigma_daily: float, days_ahead: int) -> tuple[float, float]:
    """The pre-T063 band: symmetric `1.2816 * sigma * sqrt(n)`."""
    if days_ahead <= 0:
        return 0.0, 0.0
    width = INTERVAL_Z_80 * sigma_daily * math.sqrt(days_ahead)
    return -width, width


def _growth(spec: dict[str, Any], days_ahead: float) -> float:
    p, reach = float(spec["p"]), float(spec["horizon_measured"])
    if days_ahead <= reach:
        return days_ahead ** p
    return reach ** p * (1.0 + p * (days_ahead - reach) / reach)  # tangent of the power law at `reach`


def offsets(spec: dict[str, Any], days_ahead: int) -> tuple[float, float]:
    """`(lower_offset, upper_offset)`: add to the forecast balance `days_ahead` days after the last booking.
    `lower_offset <= 0 <= upper_offset`; both move away from 0 as `days_ahead` grows."""
    if days_ahead <= 0:
        return 0.0, 0.0
    scale = _growth(spec, float(days_ahead))
    return -float(spec["lower"]) * scale, float(spec["upper"]) * scale


def fit_band(folds: pd.DataFrame) -> dict[str, Any] | None:
    """Band spec from the fold frame of `evaluate.rolling_origin` (None when fewer than 2 origins or no growth data)."""
    n_origins = int(folds["origin"].nunique()) if len(folds) else 0
    if n_origins < MIN_ORIGINS:
        return None
    truth = (-folds["cum_error"]).to_frame("r").assign(origin=folds["origin"].to_numpy(),
                                                       horizon=folds["horizon"].to_numpy())
    reaching = truth.groupby("horizon")["origin"].nunique()
    reach = int(reaching[reaching >= MIN_ORIGINS].index.max()) if (reaching >= MIN_ORIGINS).any() else 0
    if reach < 1:
        return None
    pool = truth[truth["horizon"] <= reach]

    mean_abs = pool.assign(a=pool["r"].abs()).groupby("horizon")["a"].mean()
    fit = mean_abs[(mean_abs.index >= min(MIN_FIT_HORIZON, reach)) & (mean_abs > 1e-9)]
    if len(fit) >= 2 and fit.index.nunique() >= 2:
        p = float(np.polyfit(np.log(fit.index.to_numpy(dtype=float)), np.log(fit.to_numpy()), 1)[0])
    else:
        p = MAX_GROWTH
    p = min(max(p, MIN_GROWTH), MAX_GROWTH)

    z = pool["r"].to_numpy() / pool["horizon"].to_numpy(dtype=float) ** p
    inflate = math.sqrt(1.0 + 1.0 / n_origins)
    tail = (1.0 - LEVEL) / 2.0
    q_low, q_high = np.quantile(z, [tail, 1.0 - tail])
    spec: dict[str, Any] = {
        "method": "pooled-power-growth", "level": LEVEL, "p": round(p, 4),
        "lower": round(float(max(0.0, -q_low) * inflate), 4),
        "upper": round(float(max(0.0, q_high) * inflate), 4),
        "horizon_measured": reach, "n_origins": n_origins, "inflation": round(inflate, 4),
    }
    spec["anchors"] = {str(h): [round(v, 2) for v in offsets(spec, h)] for h in ANCHOR_HORIZONS}
    return spec
