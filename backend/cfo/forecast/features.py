"""Feature tables for the liquidity forecast, built from the company database.

Two views of the same bank/invoice data:

* `daily_features` / `load_daily_features`: one row per calendar day with the net cash flow (target) and
  calendar features. Calendar columns exist for every day, also future ones (pass `end=`), so a model trained on
  history can be asked about June 2026. The features that make the yearly summer shutdown learnable are
  `month`, `day_of_year`, `shutdown_flag` and the weekday / due-day flags.
* `monthly_cost_by_team` / `load_monthly_cost_by_team`: monthly cost per team from bank outflows plus invoices
  that are not on the statement (ads, cloud and the oven are paid by company card, see T002 report).

Pure functions take DataFrames (easy to test); `fetch_*` / `load_*` read them from Postgres, read-only.

Data caveats (T002): the 12 Swiss tranches are on the statement as printed (CHF figure on an EUR account),
so `partner_investment` outflows are unconverted; pass `exclude_categories=("partner_investment",)` to drop them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import date, timedelta
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row

from cfo.config import get_settings

NON_COST_CATEGORIES = ("revenue", "partner_investment")
UNASSIGNED_TEAM = "company"

# Used when the history has too few bookings of a category to infer its due days.
DEFAULT_DUE_DAYS: dict[str, tuple[int, ...]] = {
    "payroll": (28,),
    "rent": (1,),
    "tax": (10, 15),
    "social_security": (27,),
    "utilities": (10,),
}
DUE_COLUMNS = {f"{category}_due": category for category in DEFAULT_DUE_DAYS}

CALENDAR_COLUMNS = [
    "weekday", "is_weekend", "month", "quarter", "day_of_month", "day_of_year", "week_of_year",
    "days_to_month_end", "is_month_start", "is_month_end", "is_public_holiday", "shutdown_flag", *DUE_COLUMNS,
]

# Recent-level features (T061): how much the business earns and spends per open day right now. They replace the
# columns that memorise one year (`month`, `day_of_year`, ...) so a revenue step is carried forward as the new level.
LEVEL_WINDOWS = (28, 90)  # number of open days the level is averaged over
LEVEL_MIN_OPEN_DAYS = 7  # fewer open days before a day: no level yet (NaN)
FIXED_COST_CATEGORIES = tuple(DEFAULT_DUE_DAYS)  # booked on due days, not a function of how busy the days are
LEVEL_COLUMNS = [f"recent_{kind}_{n}d" for kind in ("revenue", "variable_cost") for n in LEVEL_WINDOWS]
# Calendar columns that identify a position inside one particular year; a level model must not use them.
YEAR_MEMORY_COLUMNS = ("month", "quarter", "day_of_year", "week_of_year")
# Named feature sets chosen by `model.train(feature_set=...)`; each is a pure function of (dates, history).
_NO_MEMORY = [c for c in CALENDAR_COLUMNS if c not in YEAR_MEMORY_COLUMNS]
FEATURE_SETS: dict[str, list[str]] = {
    "calendar": list(CALENDAR_COLUMNS),
    # the booster sees the recent levels as inputs (trees cannot extrapolate a level they saw for a few weeks)
    "level": [*_NO_MEMORY, *LEVEL_COLUMNS],
    # the booster sees no level at all: it learns the deviation from the recent level, i.e. the target is the net
    # flow minus `level_baseline`, which is added back when predicting
    "level_baseline": list(_NO_MEMORY),
}
OFFSET_SETS = frozenset({"level_baseline"})
BASELINE_COLUMN = "recent_level_baseline"  # the offset as it appears in `contributions` / `explain`


def level_baseline(frame: pd.DataFrame) -> pd.Series:
    """Recent net flow of an open day before fixed costs: revenue minus variable cost, each averaged over the
    28- and 90-day levels (0 where no level exists yet)."""
    revenue = (frame["recent_revenue_28d"] + frame["recent_revenue_90d"]) / 2
    cost = (frame["recent_variable_cost_28d"] + frame["recent_variable_cost_90d"]) / 2
    return (revenue - cost).fillna(0.0).rename(BASELINE_COLUMN)


def applied_baseline(frame: pd.DataFrame) -> pd.Series:
    """What a `level_baseline` set subtracts from the target and adds back to the prediction: the baseline of an
    open day, 0 on shutdown days (no revenue, only fixed costs, whatever the level)."""
    return (level_baseline(frame) * (1 - frame["shutdown_flag"])).rename(BASELINE_COLUMN)


def feature_set_columns(name: str) -> list[str]:
    try:
        return list(FEATURE_SETS[name])
    except KeyError:
        raise ValueError(f"unknown feature_set {name!r}; choose one of {sorted(FEATURE_SETS)}") from None


def needs_levels(name: str) -> bool:
    """Whether a feature set needs the recent levels (as model inputs, as the baseline, or both)."""
    return name in OFFSET_SETS or any(c in LEVEL_COLUMNS for c in feature_set_columns(name))


# -- reading from the database ---------------------------------------------------------------------------
def _query(conn: psycopg.Connection | None, sql: str) -> list[dict[str, Any]]:
    owns = conn is None
    from cfo.tenancy import connect
    conn = conn or connect(get_settings().database_url)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        if owns:
            conn.close()


def fetch_transactions(conn: psycopg.Connection | None = None) -> pd.DataFrame:
    """All bank statement lines; `booking_date` is datetime64, `amount` float (signed EUR)."""
    rows = _query(conn, "SELECT booking_date, amount, category, team_id, counterparty, invoice_id "
                        "FROM transactions ORDER BY booking_date, id")
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id", "counterparty", "invoice_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    df["amount"] = df["amount"].astype(float)
    return df


def fetch_invoices(conn: psycopg.Connection | None = None) -> pd.DataFrame:
    """All supplier invoices; `issue_date` is datetime64, `total` float EUR."""
    rows = _query(conn, "SELECT number, supplier, issue_date, total, category, team_id, transaction_id "
                        "FROM invoices ORDER BY issue_date, number")
    df = pd.DataFrame(rows, columns=["number", "supplier", "issue_date", "total", "category", "team_id",
                                     "transaction_id"])
    df["issue_date"] = pd.to_datetime(df["issue_date"])
    df["total"] = df["total"].astype(float)
    return df


# -- calendar ---------------------------------------------------------------------------------------------
def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def public_holidays(year: int) -> set[date]:
    """German nationwide public holidays (the pizzeria is a GmbH on a German statement)."""
    easter = _easter(year)
    moving = [easter - timedelta(days=2), easter + timedelta(days=1), easter + timedelta(days=39),
              easter + timedelta(days=50)]
    fixed = [date(year, 1, 1), date(year, 5, 1), date(year, 10, 3), date(year, 12, 25), date(year, 12, 26)]
    return set(moving + fixed)


def infer_due_days(transactions: pd.DataFrame, *, min_months: int = 3) -> dict[str, tuple[int, ...]]:
    """Day(s) of month on which each fixed cost is booked: days seen in at least `min_months` distinct months.

    Falls back to `DEFAULT_DUE_DAYS` for a category without such a day.
    """
    due: dict[str, tuple[int, ...]] = {}
    for category, default in DEFAULT_DUE_DAYS.items():
        rows = transactions[transactions["category"] == category]
        dates = rows["booking_date"]
        months_per_day = dates.groupby(dates.dt.day).apply(lambda s: s.dt.to_period("M").nunique())
        days = tuple(int(d) for d, n in months_per_day.items() if n >= min_months)
        due[category] = days or default
    return due


def infer_shutdown_days(transactions: pd.DataFrame, *, min_run: int = 7) -> frozenset[tuple[int, int]]:
    """(month, day) pairs of the yearly shutdown, derived from runs of zero-revenue days.

    A shutdown is a run of at least `min_run` consecutive calendar days without any revenue booking inside the
    observed span. A run that reaches the last observed day is ignored: the data may simply have ended there.
    Keyed by (month, day) so the same window applies to future years.
    """
    if transactions.empty:
        return frozenset()
    days = pd.date_range(transactions["booking_date"].min(), transactions["booking_date"].max(), freq="D")
    revenue = (transactions[transactions["category"] == "revenue"].groupby("booking_date")["amount"].sum()
               .reindex(days, fill_value=0.0))
    pairs: set[tuple[int, int]] = set()
    run: list[pd.Timestamp] = []
    for day, amount in revenue.items():
        if amount == 0:
            run.append(day)
            continue
        if len(run) >= min_run:
            pairs.update((d.month, d.day) for d in run)
        run = []
    # the open run at the end (if any) is deliberately dropped
    return frozenset(pairs)


def closed_days(transactions: pd.DataFrame, *, min_run: int = 7) -> pd.Series:
    """1 for every day of the history inside a run of at least `min_run` days without revenue, else 0.

    Unlike `infer_shutdown_days` this also counts a run that reaches the last booking day. For *training rows*
    that is right: the days did happen and were closed (other bookings continue, revenue does not). It is not a
    claim about the future: the (month, day) window of an unfinished run is not used for forecast dates. Indexed
    by calendar day from the first to the last booking.
    """
    days = pd.date_range(transactions["booking_date"].min().normalize(), transactions["booking_date"].max().normalize(),
                         freq="D", name="date")
    revenue = (transactions[transactions["category"] == "revenue"].groupby(transactions["booking_date"].dt.normalize())
               ["amount"].sum().reindex(days, fill_value=0.0))
    closed = pd.Series(0, index=days, dtype="int64")
    run: list[pd.Timestamp] = []
    for day, amount in [*revenue.items(), (None, 1.0)]:  # the sentinel closes a run that reaches the end
        if amount == 0:
            run.append(day)
            continue
        if len(run) >= min_run:
            closed.loc[run] = 1
        run = []
    return closed


def calendar_features(
    dates: Iterable[Any],
    *,
    shutdown_days: Collection[tuple[int, int]] = (),
    due_days: dict[str, tuple[int, ...]] | None = None,
) -> pd.DataFrame:
    """Calendar features for any dates, past or future, indexed by day (`date`).

    `shutdown_flag` is 1 when the (month, day) lies in a known shutdown window (see `infer_shutdown_days`);
    `<category>_due` is 1 when the day is a due day of that fixed cost (clipped to the last day of short months).
    """
    index = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().rename("date")
    due_days = due_days or DEFAULT_DUE_DAYS
    out = pd.DataFrame(index=index)
    out["weekday"] = index.day_of_week
    out["is_weekend"] = (index.day_of_week >= 5).astype("int8")
    out["month"] = index.month
    out["quarter"] = index.quarter
    out["day_of_month"] = index.day
    out["day_of_year"] = index.day_of_year
    out["week_of_year"] = index.isocalendar().week.to_numpy().astype(int)
    out["days_to_month_end"] = index.days_in_month - index.day
    out["is_month_start"] = (index.day == 1).astype("int8")
    out["is_month_end"] = index.is_month_end.astype("int8")
    holidays = set().union(*(public_holidays(y) for y in index.year.unique())) if len(index) else set()
    out["is_public_holiday"] = [int(d.date() in holidays) for d in index]
    shutdown = set(shutdown_days)
    out["shutdown_flag"] = [int((d.month, d.day) in shutdown) for d in index]
    for column, category in DUE_COLUMNS.items():
        days = due_days.get(category, DEFAULT_DUE_DAYS[category])
        flags = [any(min(due, dim) == day for due in days) for day, dim in zip(index.day, index.days_in_month)]
        out[column] = [int(f) for f in flags]
    return out[CALENDAR_COLUMNS].astype("int64")


# -- recent level -----------------------------------------------------------------------------------------
def recent_levels(
    transactions: pd.DataFrame,
    dates: Iterable[Any],
    *,
    windows: Iterable[int] = LEVEL_WINDOWS,
) -> pd.DataFrame:
    """Recent revenue and variable cost per open day, for each of `dates` (columns `LEVEL_COLUMNS`).

    A pure function of (dates, history), built the same way for training rows and for the forecast:

    * An *open* day is a calendar day with a revenue booking (the summer shutdown and any other closed day have
      none), so a closed stretch at the end of the history does not drag the level to zero.
    * `recent_revenue_<n>d` / `recent_variable_cost_<n>d` are the mean revenue / variable cost (food, drinks, any
      category that is not revenue, partner money or a fixed due-day cost; EUR, positive) over the last `n` open
      days **strictly before** the day. A booking on or after day D never changes the value of day D.
    * For days after the last booking nothing newer exists, so the value at the end of the history is held
      constant: that is the "origin held constant over the horizon" rule.
    * NaN while fewer than `LEVEL_MIN_OPEN_DAYS` open days precede the day.
    """
    index = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize().rename("date")
    windows = tuple(windows)
    columns = [f"recent_{kind}_{n}d" for kind in ("revenue", "variable_cost") for n in windows]
    if transactions.empty or len(index) == 0:
        return pd.DataFrame(float("nan"), index=index, columns=columns)
    day = transactions["booking_date"].dt.normalize()
    variable = ~transactions["category"].isin(("revenue", "partner_investment", *FIXED_COST_CATEGORIES))
    daily = pd.DataFrame({
        "revenue": transactions["amount"].where(transactions["category"] == "revenue", 0.0),
        "variable_cost": -transactions["amount"].where(variable, 0.0),
    }).groupby(day).sum()
    open_days = daily[daily["revenue"] > 0]
    out = pd.DataFrame(index=index)
    if open_days.empty:
        return out.reindex(columns=columns)
    # the value known on day D comes from the last open day strictly before D
    known_from = open_days.index.searchsorted(index, side="left") - 1
    for n in windows:
        rolled = open_days.rolling(n, min_periods=LEVEL_MIN_OPEN_DAYS).mean()
        for kind in ("revenue", "variable_cost"):
            values = rolled[kind].to_numpy()
            out[f"recent_{kind}_{n}d"] = [values[i] if i >= 0 else float("nan") for i in known_from]
    return out[columns]


# -- daily cash flow --------------------------------------------------------------------------------------
def daily_cash_flow(
    transactions: pd.DataFrame,
    *,
    start: Any = None,
    end: Any = None,
    exclude_categories: Collection[str] = (),
) -> pd.DataFrame:
    """Net cash flow per calendar day (days without bookings are 0, not missing).

    Columns: `net_cash_flow`, `inflow`, `outflow` (<= 0), `revenue`, `n_transactions`; index `date`.
    `start`/`end` widen or clip the range; days after the last booking inside it are zero-filled, so pass
    `end` only to clip, and use `daily_features` to extend into the future.
    """
    tx = transactions[~transactions["category"].isin(list(exclude_categories))]
    if transactions.empty:
        return pd.DataFrame(columns=["net_cash_flow", "inflow", "outflow", "revenue", "n_transactions"],
                            index=pd.DatetimeIndex([], name="date"), dtype=float)
    first = pd.Timestamp(start) if start is not None else transactions["booking_date"].min()
    last = pd.Timestamp(end) if end is not None else transactions["booking_date"].max()
    days = pd.date_range(first, last, freq="D", name="date")
    tx = tx.assign(day=tx["booking_date"].dt.normalize())
    grouped = tx.groupby("day")
    out = pd.DataFrame({
        "net_cash_flow": grouped["amount"].sum(),
        "inflow": tx[tx["amount"] > 0].groupby("day")["amount"].sum(),
        "outflow": tx[tx["amount"] < 0].groupby("day")["amount"].sum(),
        "revenue": tx[tx["category"] == "revenue"].groupby("day")["amount"].sum(),
        "n_transactions": grouped["amount"].size(),
    }).reindex(days).fillna(0.0)
    out["n_transactions"] = out["n_transactions"].astype(int)
    out.index.name = "date"
    return out


def daily_features(
    transactions: pd.DataFrame,
    *,
    end: Any = None,
    exclude_categories: Collection[str] = (),
    min_shutdown_run: int = 7,
) -> pd.DataFrame:
    """Daily cash flow joined with calendar features; extends to `end` (e.g. 2026-07-31) with NaN cash flow.

    History rows have the cash-flow columns (target and companions); future rows only calendar features.
    Shutdown windows and due days are inferred from the full history in `transactions`.
    """
    history = daily_cash_flow(transactions, exclude_categories=exclude_categories)
    if history.empty:  # no bookings: no first day to start from
        columns = [*history.columns, *CALENDAR_COLUMNS]
        return pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], name="date"), dtype=float)
    shutdown = infer_shutdown_days(transactions, min_run=min_shutdown_run)
    due = infer_due_days(transactions)
    last = pd.Timestamp(end) if end is not None else history.index.max()
    days = pd.date_range(history.index.min(), max(last, history.index.min()), freq="D", name="date")
    features = calendar_features(days, shutdown_days=shutdown, due_days=due)
    out = features.join(history.reindex(days))
    return out[list(history.columns) + CALENDAR_COLUMNS]


def load_daily_features(conn: psycopg.Connection | None = None, **kwargs: Any) -> pd.DataFrame:
    """`daily_features` from the database (read-only)."""
    return daily_features(fetch_transactions(conn), **kwargs)


# -- monthly cost per team --------------------------------------------------------------------------------
def monthly_cost_by_team(
    transactions: pd.DataFrame,
    invoices: pd.DataFrame,
    *,
    by_category: bool = False,
    include_unassigned: bool = True,
) -> pd.DataFrame:
    """Monthly cost (positive EUR) per team: bank outflows plus invoices not on the statement.

    Bank side: every booking except revenue and partner investment, `cost = -amount` (refunds reduce it).
    Invoice side: only invoices without a linked payment (`transaction_id` empty); linked ones are already in
    the bank lines. Invoices count in their issue month, payments in their booking month.
    Costs without a team (tax, social security) go to team `company` unless `include_unassigned=False`.

    Columns: `month` (first day), `team_id`, [`category`,] `cost`. Months between the first and last month are
    zero-filled for every team/category that ever has a cost.
    """
    bank = transactions[~transactions["category"].isin(NON_COST_CATEGORIES)]
    bank = pd.DataFrame({"date": bank["booking_date"], "team_id": bank["team_id"],
                         "category": bank["category"], "cost": -bank["amount"]})
    unlinked = invoices[invoices["transaction_id"].isna()]
    inv = pd.DataFrame({"date": unlinked["issue_date"], "team_id": unlinked["team_id"],
                        "category": unlinked["category"], "cost": unlinked["total"]})
    lines = pd.concat([bank, inv], ignore_index=True)
    keys = ["team_id", "category"] if by_category else ["team_id"]
    lines["team_id"] = lines["team_id"].fillna(UNASSIGNED_TEAM)
    lines["category"] = lines["category"].fillna("other")
    if not include_unassigned:
        lines = lines[lines["team_id"] != UNASSIGNED_TEAM]
    columns = ["month", *keys, "cost"]
    if lines.empty:
        return pd.DataFrame(columns=columns)
    lines["month"] = lines["date"].dt.to_period("M").dt.to_timestamp()
    monthly = lines.groupby(["month", *keys])["cost"].sum().unstack(keys, fill_value=0.0)
    months = pd.date_range(monthly.index.min(), monthly.index.max(), freq="MS")
    monthly = monthly.reindex(months, fill_value=0.0)
    out = monthly.stack(keys, future_stack=True).rename("cost").reset_index()
    out = out.rename(columns={"level_0": "month"}) if "month" not in out.columns else out
    return out.sort_values(["month", *keys]).reset_index(drop=True)[columns]


def load_monthly_cost_by_team(conn: psycopg.Connection | None = None, **kwargs: Any) -> pd.DataFrame:
    """`monthly_cost_by_team` from the database (read-only)."""
    if conn is None:
        with psycopg.connect(get_settings().database_url) as own:
            return monthly_cost_by_team(fetch_transactions(own), fetch_invoices(own), **kwargs)
    return monthly_cost_by_team(fetch_transactions(conn), fetch_invoices(conn), **kwargs)
