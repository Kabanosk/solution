"""Read-side logic of the API: turns the database (via `Repository`) into the response models of `schemas.py`.

Routers stay thin and call these functions. Everything here is read-only. Money is computed in `Decimal` from the
bank statement (`transactions`) and the invoices; the forecast comes from `cfo.forecast.model` (T015).
"""

from __future__ import annotations

import math
import threading
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd

from cfo.analysis.cost_metrics import load_series, to_api_series
from cfo.api.errors import ApiError
from cfo.api.schemas import (
    CostsByTeam,
    ApprovalOut,
    DecisionOut,
    ForecastGap,
    ForecastModelInfo,
    ForecastPoint,
    ForecastSummary,
    Kpis,
    LiquidityForecast,
    MonthlyCost,
    RuleOut,
    TeamCosts,
    UndocumentedPayments,
    eur,
)
from cfo.api.settings import get_api_settings
from cfo.db.repo import Repository
from cfo.domain import ApprovalRequest, Decision, Rule
from cfo.forecast import features as feat
from cfo.forecast import model as fmodel

MAX_HORIZON_DAYS = 365
KPI_HORIZON_DAYS = MAX_HORIZON_DAYS
FIXED_COST_CATEGORIES = tuple(feat.DEFAULT_DUE_DAYS)  # payroll, rent, tax, social_security, utilities
INTERVAL_Z_80 = 1.2816  # one-sided z of a two-sided 80 % interval
MODEL_NAME = "xgboost-liquidity"
COMPANY_TEAM_NAME = "Company-wide (tax, social security)"


def _utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _transactions(repo: Repository, *, required: bool = True) -> pd.DataFrame:
    tx = feat.fetch_transactions(repo.conn)
    if required and tx.empty:
        raise ApiError(503, "no_data", "The database has no transactions yet; seed it with "
                                       "`python -m cfo.data.load --data data/generated`.")
    return tx


# --- safety buffer (D-013) ----------------------------------------------------------------------------


def safety_buffer(tx: pd.DataFrame) -> Decimal:
    """`SAFETY_BUFFER_EUR` if set, else one month of fixed costs: the largest month of payroll, rent, tax, social
    security and utilities in the data (17,730 EUR on the seeded year, D-013 says about 17.7k)."""
    configured = get_api_settings().safety_buffer_eur
    if configured is not None:
        return configured
    fixed = tx[tx["category"].isin(FIXED_COST_CATEGORIES)]
    if fixed.empty:
        return Decimal(0)
    monthly = (-fixed["amount"]).groupby(fixed["booking_date"].dt.to_period("M")).sum()
    return _dec(round(float(monthly.max()), 2))


# --- forecast -------------------------------------------------------------------------------------------

_model_lock = threading.Lock()
_model_cache: dict[tuple[str, int], fmodel.ForecastModel] = {}


def forecast_model(tx: pd.DataFrame) -> tuple[fmodel.ForecastModel, datetime | None]:
    """The saved model (memory, then disk), or trained on `tx` and saved on first use. Returns (model, saved at)."""
    path = fmodel.default_model_path()
    with _model_lock:
        meta = fmodel._meta_path(path)
        if not (path.is_file() and meta.is_file()):
            fmodel.get_model(tx, path=path)  # trains and saves
        mtime = path.stat().st_mtime_ns
        key = (str(path), mtime)
        if key not in _model_cache:
            _model_cache.clear()
            _model_cache[key] = fmodel.ForecastModel.load(path)
        return _model_cache[key], datetime.fromtimestamp(mtime / 1e9, tz=timezone.utc)


def _sigma_daily(model: fmodel.ForecastModel) -> float:
    m = model.metrics
    return float(m.get("holdout_rmse") or (m.get("train_mae", 0.0) * 1.25))


def _forecast_frame(tx: pd.DataFrame, days: int) -> tuple[pd.DataFrame, fmodel.ForecastModel, datetime | None]:
    model, saved_at = forecast_model(tx)
    df = fmodel.liquidity_forecast(days, model=model, transactions=tx)
    return df, model, saved_at


def _gaps(balance: pd.Series, buffer: float) -> list[ForecastGap]:
    gaps: list[ForecastGap] = []
    run: list[pd.Timestamp] = []
    for day, value in [*balance.items(), (None, math.inf)]:
        if value < buffer:
            run.append(day)
        elif run:
            low = balance.loc[run].idxmin()
            gaps.append(ForecastGap(start=run[0].date(), end=run[-1].date(), lowest_date=low.date(),
                                    lowest_balance=eur(balance.loc[low])))
            run = []
    return gaps


def liquidity_forecast(repo: Repository, horizon_days: int, granularity: str) -> LiquidityForecast:
    tx = _transactions(repo)
    df, model, saved_at = _forecast_frame(tx, horizon_days)
    buffer = safety_buffer(tx)
    sigma = _sigma_daily(model)

    def point(day: pd.Timestamp) -> ForecastPoint:
        n = (day - pd.Timestamp(df.attrs["last_known_date"])).days
        band = INTERVAL_Z_80 * sigma * math.sqrt(n)
        bal = float(df.loc[day, "balance"])
        return ForecastPoint(date=day.date(), balance=eur(bal), lower=eur(bal - band), upper=eur(bal + band))

    days = list(df.index)
    lowest_day = df["balance"].idxmin()
    if granularity == "week":
        sampled = days[6::7]
        if lowest_day not in sampled:  # never hide the trough from a chart
            sampled = sorted([*sampled, lowest_day])
    else:
        sampled = days
    return LiquidityForecast(
        stub=False,
        as_of=date.fromisoformat(df.attrs["last_known_date"]),
        horizon_days=horizon_days,
        granularity=granularity,  # type: ignore[arg-type]
        currency="EUR",
        opening_balance=eur(df.attrs["start_balance"]),
        safety_buffer=eur(buffer),
        lowest_point=point(lowest_day),
        gaps=_gaps(df["balance"], float(buffer)),
        model=ForecastModelInfo(name=MODEL_NAME, version=f"trained-until-{model.trained_until.date()}",
                                mlflow_run_id=None, trained_at=saved_at),
        points=[point(d) for d in sampled],
    )


def forecast_summary(tx: pd.DataFrame, horizon_days: int = KPI_HORIZON_DAYS) -> ForecastSummary:
    df, _, _ = _forecast_frame(tx, horizon_days)
    buffer = safety_buffer(tx)
    low_day, low = fmodel.lowest_balance(df)
    negative = df.index[df["balance"] < 0]
    below = df.index[df["balance"] < float(buffer)]
    return ForecastSummary(
        horizon_days=horizon_days,
        lowest_balance=eur(low),
        lowest_date=low_day.date(),
        first_negative_date=negative[0].date() if len(negative) else None,
        safety_buffer=eur(buffer),
        first_below_buffer_date=below[0].date() if len(below) else None,
    )


# --- kpis -----------------------------------------------------------------------------------------------


def kpis(repo: Repository) -> Kpis:
    tx = _transactions(repo)
    amounts = tx["amount"].map(_dec)
    as_of = tx["booking_date"].max().normalize()
    balance = sum(amounts, Decimal(0))
    recent = tx["booking_date"] > as_of - pd.Timedelta(days=30)
    change_30d = sum(amounts[recent], Decimal(0))

    # Averages over whole calendar months; a month that is still running at `as_of` is left out.
    months = tx["booking_date"].dt.to_period("M")
    last_month = as_of.to_period("M")
    month_end_reached = as_of == last_month.end_time.normalize()
    complete = months if month_end_reached or months.nunique() == 1 else months[months < last_month]
    n_months = max(complete.nunique(), 1)
    in_scope = amounts[months.isin(complete.unique())]
    inflows = sum((a for a in in_scope if a > 0), Decimal(0))
    outflows = sum((a for a in in_scope if a < 0), Decimal(0))

    undocumented = repo.list_transactions(undocumented_only=True)
    pending = repo.conn.execute("SELECT count(*) AS n FROM approval_requests WHERE status = 'pending'").fetchone()
    alerts = sum(1 for s in load_series(repo) if s.points and s.points[-1].anomaly is not None)
    return Kpis(
        stub=False,
        as_of=as_of.date(),
        balance=eur(balance),
        balance_change_30d=eur(change_30d),
        avg_monthly_inflows=eur(inflows / n_months),
        avg_monthly_outflows=eur(outflows / n_months),
        forecast=forecast_summary(tx),
        undocumented_payments=UndocumentedPayments(
            count=len(undocumented), total=eur(sum((-t.amount for t in undocumented), Decimal(0)))),
        pending_approvals=int(pending["n"]),
        open_cost_alerts=alerts,
    )


# --- costs by team --------------------------------------------------------------------------------------


def _month_starts(start: date, end: date) -> list[date]:
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def costs_by_team(repo: Repository, start: date | None, end: date | None) -> CostsByTeam:
    """Bank lines and unlinked invoices per team and month (T014 `monthly_cost_by_team`).

    Defaults: first day of the first booking month to the last booking day. Months whose first day lies in
    `[from, to]` are included, plus the month containing `from`.
    """
    tx = _transactions(repo, required=False)
    invoices = feat.fetch_invoices(repo.conn)
    first = tx["booking_date"].min().date() if not tx.empty else date.today()
    last = tx["booking_date"].max().date() if not tx.empty else date.today()
    start, end = start or date(first.year, first.month, 1), end or last
    months = _month_starts(start, end)
    monthly = feat.monthly_cost_by_team(tx, invoices) if not tx.empty else pd.DataFrame(columns=["month", "team_id", "cost"])
    cost: dict[tuple[str, date], Decimal] = {}
    for row in monthly.itertuples():
        cost[(row.team_id, row.month.date())] = _dec(round(float(row.cost), 2))

    teams = {t.id: t for t in repo.list_teams()}
    in_range = set(months)
    team_ids = sorted({t for t, m in cost if m in in_range} | set(teams))
    totals = {t: sum((cost.get((t, m), Decimal(0)) for m in months), Decimal(0)) for t in team_ids}
    grand = sum(totals.values(), Decimal(0))
    items = []
    for team_id in team_ids:
        team = teams.get(team_id)
        if team is None and not totals[team_id]:
            continue
        total = totals[team_id]
        items.append(TeamCosts(
            team_id=team_id,
            team_name=team.name if team else (COMPANY_TEAM_NAME if team_id == feat.UNASSIGNED_TEAM else team_id),
            owner_id=team.owner_id if team else None,
            monthly_budget=eur(team.monthly_budget) if team and team.monthly_budget is not None else None,
            total=eur(total),
            avg_monthly=eur(total / len(months)) if months else eur(0),
            share=format((total / grand).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f") if grand else "0.0000",
            by_month=[MonthlyCost(month=m, cost=eur(cost.get((team_id, m), Decimal(0)))) for m in months],
        ))
    items.sort(key=lambda t: Decimal(t.total.amount), reverse=True)
    return CostsByTeam(stub=False, **{"from": start}, to=end, total=eur(grand), items=items)


# --- decisions, rules, cost metrics -----------------------------------------------------------------------


def _decision_out(d: Decision) -> DecisionOut:
    return DecisionOut(**{**d.model_dump(), "ts": _utc(d.ts)})


def list_decisions(repo: Repository, *, level: str | None, status: str | None, action_type: str | None,
                   limit: int, offset: int) -> tuple[list[DecisionOut], int]:
    where, params = ["TRUE"], []
    for column, value in (("level", level), ("status", status), ("action_type", action_type)):
        if value is not None:
            where.append(f"{column} = %s")
            params.append(value)
    clause = " AND ".join(where)
    total = repo.conn.execute(f"SELECT count(*) AS n FROM decisions WHERE {clause}", params).fetchone()["n"]
    rows = repo._all(f"SELECT * FROM decisions WHERE {clause} ORDER BY ts DESC, id LIMIT %s OFFSET %s",
                     [*params, limit, offset])
    return [_decision_out(Decision(**r)) for r in rows], int(total)


def get_decision(repo: Repository, decision_id: str) -> DecisionOut | None:
    rows = repo._all("SELECT * FROM decisions WHERE id = %s", [decision_id])
    return _decision_out(Decision(**rows[0])) if rows else None


def list_rules(repo: Repository, *, active: bool | None) -> list[RuleOut]:
    rules: list[Rule] = repo.list_rules(active_only=False)
    return [RuleOut(**{**r.model_dump(), "created_at": _utc(r.created_at)}) for r in rules
            if active is None or r.active == active]


def cost_metric_series(repo: Repository, kind: str | None) -> list:
    return [s for s in to_api_series(load_series(repo)) if kind is None or s.kind == kind]


# --- approvals ----------------------------------------------------------------------------------------------

APPROVAL_FILTERS = {  # `state` query value -> SQL condition on approval_requests.status
    "pending": "status = 'pending'",
    "resolved": "status <> 'pending'",
    "approved": "status = 'approved'",
    "rejected": "status = 'rejected'",
}


def approval_out(repo: Repository, req: ApprovalRequest) -> ApprovalOut:
    decision = get_decision(repo, req.decision_id)
    return ApprovalOut(**{**req.model_dump(), "created_at": _utc(req.created_at),
                          "resolved_at": _utc(req.resolved_at) if req.resolved_at else None, "decision": decision})


def list_approvals(repo: Repository, *, state: str | None, limit: int, offset: int) -> tuple[list[ApprovalOut], int]:
    clause = APPROVAL_FILTERS[state] if state else "TRUE"
    total = repo.conn.execute(f"SELECT count(*) AS n FROM approval_requests WHERE {clause}").fetchone()["n"]
    rows = repo._all(f"SELECT * FROM approval_requests WHERE {clause} ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                     [limit, offset])
    return [approval_out(repo, ApprovalRequest(**r)) for r in rows], int(total)


def get_approval(repo: Repository, approval_id: str) -> ApprovalOut | None:
    req = repo.get_approval_request(approval_id)
    return approval_out(repo, req) if req else None
