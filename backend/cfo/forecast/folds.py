"""Which origins to evaluate on (T068). `evaluate.rolling_origin` takes them through `origins=`.

* month ends are the default of `evaluate.py` (from the end of the 6th month): 6 folds on the statement, kept so
  results stay comparable with the T060-T062 reports;
* `weekly_origins` are every Monday after the end of the 5th month of history for as long as `MIN_TRUTH_DAYS` days of
  truth follow: 27 folds on the organizer's statement (2 Jun ... 1 Dec 2025). Four times the folds mean a
  configuration can no longer win on the luck of one or two month ends.
"""

from __future__ import annotations

import pandas as pd

WEEKLY_AFTER_MONTH = 5  # the first weekly origin is the first Monday after the end of this month of history
MIN_TRUTH_DAYS = 30


def weekly_origins(transactions: pd.DataFrame, *, after_month: int = WEEKLY_AFTER_MONTH,
                   min_truth_days: int = MIN_TRUTH_DAYS, every_days: int = 7) -> list[pd.Timestamp]:
    """Mondays after the end of month `after_month` of the history, each followed by at least `min_truth_days` days
    of bookings (empty when the history is too short). `every_days` thins or thickens the grid (7 = every Monday;
    2 = every second day from the first Monday on, which visits every weekday: a denser fold set for tuning, so a
    configuration cannot win on the luck of one weekday's origins)."""
    if every_days < 1:
        raise ValueError("every_days must be >= 1")
    if transactions.empty:
        return []
    days = transactions["booking_date"].dt.normalize()
    first, last = days.min(), days.max()
    start = (first.to_period("M") + (after_month - 1)).end_time.normalize() + pd.Timedelta(days=1)
    stop = last - pd.Timedelta(days=min_truth_days)
    if stop < start:
        return []
    first_monday = start + pd.Timedelta(days=(7 - start.day_of_week) % 7)
    if first_monday > stop:
        return []
    return list(pd.date_range(first_monday, stop, freq=f"{every_days}D"))
