"""Forecast features: pure functions on a small DataFrame fixture, plus one test on the seeded database."""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from cfo.config import REPO_ROOT, get_settings
from cfo.forecast import features as f


def _tx(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    df["booking_date"] = pd.to_datetime(df["booking_date"])
    return df


@pytest.fixture
def history() -> pd.DataFrame:
    """Jan-Aug 2025: daily revenue 100 except a 20-day shutdown 1-20 June; monthly fixed costs."""
    rows: list[tuple] = []
    for day in pd.date_range("2025-01-01", "2025-08-31"):
        if not (pd.Timestamp("2025-06-01") <= day <= pd.Timestamp("2025-06-20")):
            rows.append((day, 100.0, "revenue", None))
        if day.day == 28:
            rows.append((day, -500.0, "payroll", "kitchen"))
        if day.day == 1:
            rows.append((day, -300.0, "rent", "front"))
        if day.day in (10, 15):
            rows.append((day, -50.0, "tax", None))
        if day.day == 27:
            rows.append((day, -80.0, "social_security", None))
        if day.day == 10:
            rows.append((day, -20.0, "utilities", "front"))
    return _tx(rows)


def test_daily_cash_flow_sums_per_day_and_zero_fills(history):
    flow = f.daily_cash_flow(history)
    assert len(flow) == 243  # 1 Jan - 31 Aug, no gaps
    assert flow.loc["2025-02-03", "net_cash_flow"] == 100.0
    assert flow.loc["2025-02-28", "net_cash_flow"] == 100.0 - 500.0
    assert (
        flow.loc["2025-02-28", "outflow"] == -500.0
        and flow.loc["2025-02-28", "inflow"] == 100.0
    )
    assert flow.loc[
        "2025-06-05", ["net_cash_flow", "revenue", "n_transactions"]
    ].tolist() == [0.0, 0.0, 0]
    assert (
        flow.loc["2025-06-10", "net_cash_flow"] == -70.0
    )  # tax + utilities, no revenue
    assert flow["net_cash_flow"].sum() == history["amount"].sum()


def test_daily_cash_flow_exclusion_and_clipping(history):
    flow = f.daily_cash_flow(
        history, start="2025-02-27", end="2025-03-02", exclude_categories=["payroll"]
    )
    assert list(flow.index.strftime("%m-%d")) == ["02-27", "02-28", "03-01", "03-02"]
    assert flow.loc["2025-02-28", "net_cash_flow"] == 100.0


def test_shutdown_inferred_from_zero_revenue_days(history):
    days = f.infer_shutdown_days(history)
    assert days == {(6, d) for d in range(1, 21)}


def test_shutdown_ignores_short_gaps_and_trailing_run():
    rows = [
        (d, 100.0, "revenue", None)
        for d in pd.date_range("2025-01-01", "2025-02-28")
        if d not in pd.date_range("2025-01-10", "2025-01-12")
    ]  # 3-day gap: closed days, not a shutdown
    rows.append(
        (pd.Timestamp("2025-03-20"), -10.0, "rent", None)
    )  # trailing 20 zero-revenue days: data ended
    assert f.infer_shutdown_days(_tx(rows)) == frozenset()


def test_due_days_inferred_with_fallback(history):
    due = f.infer_due_days(history)
    assert due["payroll"] == (28,) and due["rent"] == (1,) and due["tax"] == (10, 15)
    assert due["social_security"] == (27,) and due["utilities"] == (10,)
    assert (
        f.infer_due_days(history.iloc[:0])["payroll"] == f.DEFAULT_DUE_DAYS["payroll"]
    )


def test_calendar_features_for_future_june(history):
    shutdown = f.infer_shutdown_days(history)
    cal = f.calendar_features(
        pd.date_range("2026-05-30", "2026-06-22"),
        shutdown_days=shutdown,
        due_days=f.infer_due_days(history),
    )
    assert list(cal.columns) == f.CALENDAR_COLUMNS
    assert cal.loc["2026-05-31", "shutdown_flag"] == 0
    assert cal.loc[
        "2026-06-01",
        ["shutdown_flag", "month", "weekday", "is_month_start", "rent_due"],
    ].tolist() == [
        1,
        6,
        0,
        1,
        1,
    ]  # 1 June 2026 is a Monday
    assert (
        cal.loc["2026-06-20", "shutdown_flag"] == 1
        and cal.loc["2026-06-21", "shutdown_flag"] == 0
    )
    assert cal.loc["2026-06-10", ["tax_due", "utilities_due"]].tolist() == [1, 1]
    assert (
        cal.loc["2026-06-15", "tax_due"] == 1 and cal.loc["2026-06-16", "tax_due"] == 0
    )
    assert (
        cal.loc["2026-06-21", "is_weekend"] == 1
        and cal.loc["2026-06-22", "is_weekend"] == 0
    )
    assert cal.loc["2026-06-01", "day_of_year"] == 152


def test_due_day_clipped_in_short_month():
    cal = f.calendar_features(
        pd.date_range("2025-02-26", "2025-03-01"), due_days={"payroll": (30,)}
    )
    assert cal["payroll_due"].tolist() == [
        0,
        0,
        1,
        0,
    ]  # 28 Feb is the last day of February


def test_public_holidays_germany():
    assert date(2025, 4, 18) in f.public_holidays(2025)  # Good Friday
    assert date(2025, 4, 21) in f.public_holidays(2025)  # Easter Monday
    assert date(2026, 4, 3) in f.public_holidays(2026)
    assert date(2026, 5, 14) in f.public_holidays(2026)  # Ascension
    cal = f.calendar_features(["2025-12-24", "2025-12-25"])
    assert cal["is_public_holiday"].tolist() == [0, 1]


def test_daily_features_extends_into_future(history):
    out = f.daily_features(history, end="2026-07-31")
    assert out.index[0] == pd.Timestamp("2025-01-01") and out.index[-1] == pd.Timestamp(
        "2026-07-31"
    )
    assert out.loc["2025-08-31", "net_cash_flow"] == 100.0
    assert out.loc["2025-09-01":, "net_cash_flow"].isna().all()
    june26 = out.loc["2026-06-01":"2026-06-30"]
    assert june26["shutdown_flag"].sum() == 20
    assert (
        out.loc["2025-06-05", "shutdown_flag"] == 1
        and out.loc["2025-06-05", "net_cash_flow"] == 0.0
    )
    assert out.index.is_unique


def test_monthly_cost_by_team_combines_bank_and_unlinked_invoices(history):
    invoices = pd.DataFrame(
        [
            ("A-1", "Meta", "2025-02-12", 200.0, "advertising", "marketing", None),
            (
                "A-2",
                "Asimex",
                "2025-02-15",
                999.0,
                "food",
                "kitchen",
                "txn_1",
            ),  # linked: already a bank line
            ("A-3", "Cloud", "2025-04-02", 40.0, "cloud", "it", None),
        ],
        columns=[
            "number",
            "supplier",
            "issue_date",
            "total",
            "category",
            "team_id",
            "transaction_id",
        ],
    )
    invoices["issue_date"] = pd.to_datetime(invoices["issue_date"])
    cost = f.monthly_cost_by_team(history, invoices)
    assert list(cost.columns) == ["month", "team_id", "cost"]

    def at(month: str, team: str) -> float:
        return float(
            cost[(cost["month"] == month) & (cost["team_id"] == team)]["cost"].iloc[0]
        )

    assert (
        at("2025-02-01", "kitchen") == 500.0
    )  # payroll only, the linked invoice is not added
    assert at("2025-02-01", "marketing") == 200.0
    assert at("2025-02-01", "front") == 300.0 + 20.0
    assert (
        at("2025-02-01", "company") == 100.0 + 80.0
    )  # two tax bookings + social security
    assert at("2025-03-01", "marketing") == 0.0  # zero-filled between months
    assert at("2025-04-01", "it") == 40.0
    assert "revenue" not in cost["team_id"].tolist()
    assert cost["month"].min() == pd.Timestamp("2025-01-01") and cost[
        "month"
    ].max() == pd.Timestamp("2025-08-01")
    assert (
        not f.monthly_cost_by_team(history, invoices, include_unassigned=False)[
            "team_id"
        ]
        .eq("company")
        .any()
    )


def test_monthly_cost_by_category(history):
    no_invoices = pd.DataFrame(
        {
            "issue_date": pd.Series([], dtype="datetime64[ns]"),
            "total": [],
            "category": [],
            "team_id": [],
            "transaction_id": [],
        }
    )
    cost = f.monthly_cost_by_team(history, no_invoices, by_category=True)
    row = cost[
        (cost["month"] == "2025-03-01")
        & (cost["team_id"] == "kitchen")
        & (cost["category"] == "payroll")
    ]
    assert row["cost"].iloc[0] == 500.0


# -- seeded database --------------------------------------------------------------------------------------
def _generated_dir() -> Path | None:
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = parent / "data" / "generated"
        if (candidate / "bank" / "history.jsonl").is_file():
            return candidate
    return None


@pytest.fixture
def seeded():
    """Own schema `t014_test` seeded from the generated data; the shared public schema is never touched."""
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    data = _generated_dir()
    if data is None:
        pytest.skip("generated data missing (python -m cfo.data.generate)")
    from cfo.data.load import seed
    from cfo.db.migrate import SCHEMA_PATH
    from cfo.db.repo import Repository

    schema = f"t014_test_{os.getpid()}"
    repo = Repository.connect(url)
    repo.conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    repo.conn.execute(f"CREATE SCHEMA {schema}")
    repo.conn.execute(f"SET search_path TO {schema}")
    repo.conn.commit()
    repo.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    repo.conn.commit()
    seed(repo, data)
    yield repo.conn
    repo.conn.rollback()
    repo.conn.execute(f"DROP SCHEMA {schema} CASCADE")
    repo.conn.commit()
    repo.conn.close()


def test_features_from_seeded_db(seeded):
    out = f.load_daily_features(seeded, end="2026-07-31")
    assert out.index[0] == pd.Timestamp("2025-01-01") and out.index[-1] == pd.Timestamp(
        "2026-07-31"
    )
    history = out.loc[:"2025-11-30"]
    assert (
        history["net_cash_flow"].notna().all()
        and out.loc["2025-12-01":, "net_cash_flow"].isna().all()
    )
    assert history["revenue"].loc["2025-06-01":"2025-06-30"].sum() == 0.0
    assert history["revenue"].loc["2025-07-01":"2025-07-31"].gt(0).any()
    # the June 2025 shutdown is learned and projected onto June 2026
    assert out.loc["2025-06-01":"2025-06-30", "shutdown_flag"].eq(1).all()
    assert out.loc["2026-06-01":"2026-06-30", "shutdown_flag"].eq(1).all()
    assert out.loc["2026-05-01":"2026-05-31", "shutdown_flag"].eq(0).all()
    assert (
        out.loc["2026-06-28", "payroll_due"] == 1
        and out.loc["2026-06-01", "rent_due"] == 1
    )
    assert out["shutdown_flag"].sum() == 60

    costs = f.load_monthly_cost_by_team(seeded)
    assert {"kitchen", "front", "marketing", "it", "equipment"} <= set(costs["team_id"])
    assert costs["cost"].ge(0).all()
    october_equipment = costs[
        (costs["month"] == "2025-10-01") & (costs["team_id"] == "equipment")
    ]["cost"].iloc[0]
    assert october_equipment == pytest.approx(
        3581.90
    )  # the oven (net 3010 + 19% VAT) exists only as an invoice
