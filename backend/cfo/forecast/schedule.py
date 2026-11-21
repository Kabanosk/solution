"""Fixed costs as a schedule (T068): payroll, rent, social security, utilities and tax are not a learning problem.

Their amount and day of month are (nearly) constant, tax repeats every quarter (880 / 1105 / 2030 EUR by month
position). Letting a booster rediscover that from a year of noisy daily flows costs accuracy; reading it off the
history is exact. `infer(transactions)` derives, per fixed-cost category, from the bookings it is given **only**
(no look ahead: pass the bookings up to the forecast origin) a plain-JSON spec, and `amounts(spec, dates)` turns the
spec into the daily outflows (negative EUR) of any dates, past or future.

How a spec is inferred (per category):

* A month is *complete* when the history covers all of it (its last day is on or before the last booking day, and
  the history started in the first days of its first month). Only complete months are read, so a half-seen
  current month never produces a half-sized payment. Months before the category's first booking are skipped.
* The *profile* of a month is its list of `(day of month, amount)` bookings (several bookings of one day are
  summed). The *period* `p` is the smallest of 1, 2, 3, 4, 6, 12 whose months repeat the month `p` earlier (same days,
  amounts within 5 %) wherever both months are complete, with at least one such pair; a category that repeats under
  no period is treated as monthly. Monthly costs get `p = 1`; the quarterly tax pattern `p = 3`.
* The profile of month *position* `(year * 12 + month - 1) % p` is that of the **most recent** complete month with
  that position (last observed amount and due day). A position never observed uses the latest complete month.
* Bookings already **posted in the current, partial month** count too (they are facts): each posted payment replaces the
  nearest not yet matched entry of its position's profile (within `MATCH_DAYS` days of the due day), so a rent increase paid
  on the 1st is the schedule of every later month, and a payment posted a few days off its usual day moves the due day.
  Entries not posted yet stay scheduled (payroll due on the 28th, origin on the 10th). A payment already posted this month
  is never scheduled again for the same month: `amounts` only reads days after the last booking, where a matched entry
  no longer lies (it was replaced by the posted day, which is on or before the origin).

Partner tranches are not fixed costs of this kind (they end after tranche 12) and stay with
`model.partner_tranche_schedule`: they are not in `CATEGORIES`, so nothing is counted twice.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from cfo.forecast.features import FIXED_COST_CATEGORIES

CATEGORIES = FIXED_COST_CATEGORIES  # payroll, rent, tax, social_security, utilities
PERIODS = (1, 2, 3, 4, 6, 12)
AMOUNT_TOLERANCE = 0.05  # relative; two months "repeat" when their days match and amounts are within this
MATCH_DAYS = 7  # a posted payment replaces the profile entry whose due day is at most this many days away
MAX_FIRST_DAY = 3  # a history that starts after this day of its first month does not count that month as complete

Spec = dict[str, Any]


def _position(month: pd.Period, period: int) -> int:
    return (month.year * 12 + month.month - 1) % period


def _profiles(rows: pd.DataFrame, months: list[pd.Period]) -> dict[pd.Period, list[tuple[int, float]]]:
    """`{month: [(day of month, summed amount), ...]}` for each of `months` (an empty list = nothing booked)."""
    day = rows["booking_date"]
    summed = rows.groupby([day.dt.to_period("M"), day.dt.day])["amount"].sum()
    out: dict[pd.Period, list[tuple[int, float]]] = {m: [] for m in months}
    for (month, d), amount in summed.items():
        if month in out:
            out[month].append((int(d), round(float(amount), 2)))
    return {m: sorted(p) for m, p in out.items()}


def _same(a: list[tuple[int, float]], b: list[tuple[int, float]]) -> bool:
    if len(a) != len(b):
        return False
    return all(da == db and abs(xa - xb) <= AMOUNT_TOLERANCE * max(abs(xa), abs(xb), 1e-9)
               for (da, xa), (db, xb) in zip(a, b, strict=True))


def _period(months: list[pd.Period], profiles: dict[pd.Period, list[tuple[int, float]]]) -> int:
    """The smallest period under which every comparable pair of months repeats (1 when none does)."""
    for p in PERIODS:
        pairs = [(m, m - p) for m in months if (m - p) in profiles]
        if pairs and all(_same(profiles[a], profiles[b]) for a, b in pairs):
            return p
    return 1


def _complete_months(transactions: pd.DataFrame) -> list[pd.Period]:
    first, last = transactions["booking_date"].min().normalize(), transactions["booking_date"].max().normalize()
    months = list(pd.period_range(first.to_period("M"), last.to_period("M"), freq="M"))
    if first.day > MAX_FIRST_DAY:
        months = months[1:]
    return [m for m in months if m.end_time.normalize() <= last]


def _apply_posted(profile: list[tuple[int, float]], posted: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """`profile` with each posted `(day, amount)` replacing the nearest unmatched entry within `MATCH_DAYS` days;
    unmatched profile entries stay (not posted yet), unmatched postings are ignored (not known to recur)."""
    out = list(profile)
    free = set(range(len(out)))
    pairs = sorted(((abs(out[i][0] - day), i, j) for j, (day, _) in enumerate(posted) for i in free),
                   key=lambda t: (t[0], t[1], t[2]))
    used: set[int] = set()
    for distance, i, j in pairs:
        if distance > MATCH_DAYS or i not in free or j in used:
            continue
        out[i] = posted[j]
        free.discard(i)
        used.add(j)
    return sorted(out)


def infer(transactions: pd.DataFrame, *, categories: Iterable[str] = CATEGORIES) -> Spec:
    """The fixed-cost schedule of `transactions` (columns `booking_date`, `amount`, `category`); see the module
    docstring. Only bookings in `transactions` are read. Categories with no booking in a complete month are left out
    (they are then forecast as zero, there is nothing to repeat)."""
    spec: Spec = {"categories": {}}
    if transactions.empty:
        return spec
    complete = _complete_months(transactions)
    last_day = transactions["booking_date"].max().normalize()
    spec["as_of"] = str(last_day.date())
    for category in categories:
        rows = transactions[transactions["category"] == category]
        if rows.empty:
            continue
        started = rows["booking_date"].min().to_period("M")
        months = [m for m in complete if m >= started]
        if not months:
            continue
        profiles = _profiles(rows, months)
        period = _period(months, profiles)
        by_position: dict[int, list[tuple[int, float]]] = {}
        for month in months:  # later months overwrite earlier ones: the most recent complete month of a position
            by_position[_position(month, period)] = profiles[month]
        latest = profiles[months[-1]]
        current = last_day.to_period("M")
        if current not in complete:  # the partial month of the origin: what is posted already is a fact
            posted = _profiles(rows, [current])[current]
            if posted:
                position = _position(current, period)
                by_position[position] = _apply_posted(by_position.get(position, latest), posted)
                if period == 1:
                    latest = by_position[position]
        spec["categories"][category] = {
            "period": period,
            "profiles": {str(pos): [[d, a] for d, a in prof] for pos, prof in sorted(by_position.items())},
            "latest": [[d, a] for d, a in latest],
        }
    return spec


def amounts(spec: Spec, dates: Iterable[Any]) -> pd.DataFrame:
    """Daily scheduled outflows per category (negative EUR; zero where nothing is due), indexed by day (`date`).
    Columns are the categories of the spec. Due days beyond the end of a short month are paid on its last day."""
    index = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().rename("date")
    columns = list(spec.get("categories", {}))
    out = pd.DataFrame(0.0, index=index, columns=columns)
    for category in columns:
        info = spec["categories"][category]
        period, profiles = int(info["period"]), info["profiles"]
        values = []
        for ts, dim in zip(index, index.days_in_month, strict=True):
            profile = profiles.get(str((ts.year * 12 + ts.month - 1) % period), info["latest"])
            values.append(sum(a for d, a in profile if min(int(d), int(dim)) == ts.day))
        out[category] = values
    return out


def total(spec: Spec, dates: Iterable[Any]) -> pd.Series:
    """`amounts` summed over the categories: the schedule's net flow per day."""
    frame = amounts(spec, dates)
    return frame.sum(axis=1).rename("scheduled_fixed") if len(frame.columns) else pd.Series(
        0.0, index=frame.index, name="scheduled_fixed")
