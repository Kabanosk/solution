"""Read endpoints against real data: an isolated Postgres schema seeded from data/generated.

The schema `t019_nov` (dropped afterwards; the shared public schema is never touched) is the statement as
delivered (Jan-Nov 2025). Skips cleanly when Postgres or the generated data is missing. The repo is replaced through FastAPI's dependency override.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from cfo.api import services
from cfo.api.app import create_app
from cfo.api.deps import get_repo
from cfo.config import REPO_ROOT, get_settings

NOV = f"t019_nov_{os.getpid()}"
SCOUT = f"t019_scout_{os.getpid()}"
EMPTY = f"t019_empty_{os.getpid()}"

MONEY = re.compile(r"^-?\d+\.\d{2}$")


def _generated_dir() -> Path | None:
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = parent / "data" / "generated"
        if (candidate / "bank" / "history.jsonl").is_file():
            return candidate
    return None


def _seeded_schema(name: str):
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

    repo = Repository.connect(url)
    repo.conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    repo.conn.execute(f"CREATE SCHEMA {name}")
    repo.conn.execute(f"SET search_path TO {name}")
    repo.conn.commit()
    repo.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    repo.conn.commit()
    seed(repo, data)
    return repo


def _client(repo) -> TestClient:
    app = create_app()

    def override():
        yield repo

    app.dependency_overrides[get_repo] = override
    return TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def model_in_tmp(tmp_path_factory):
    """The forecast model lives in a temp dir: first request trains and saves it, the repo's `models/` stays clean."""
    mp = pytest.MonkeyPatch()
    mp.setenv("FORECAST_MODEL_PATH", str(tmp_path_factory.mktemp("model") / "liquidity_xgb.json"))
    services._model_cache.clear()
    yield
    services._model_cache.clear()
    mp.undo()


@pytest.fixture(scope="module")
def nov_repo():
    repo = _seeded_schema(NOV)
    yield repo
    repo.conn.rollback()
    repo.conn.execute(f"DROP SCHEMA {NOV} CASCADE")
    repo.conn.commit()
    repo.conn.close()


@pytest.fixture(scope="module")
def nov(nov_repo):
    return _client(nov_repo)


def assert_money(m):
    assert set(m) == {"amount", "currency"} and MONEY.match(m["amount"]) and m["currency"] == "EUR", m


def amount(m) -> Decimal:
    return Decimal(m["amount"])


# --- all endpoints are real now --------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/v1/kpis", "/v1/forecast/liquidity", "/v1/costs/by-team", "/v1/cost-metrics",
])
def test_no_endpoint_is_a_stub(nov, path):
    r = nov.get(path)
    assert r.status_code == 200
    assert r.json()["stub"] is False
    assert "x-stub" not in r.headers


# --- kpis ------------------------------------------------------------------------------------------------


def test_kpis_from_the_statement(nov):
    k = nov.get("/v1/kpis").json()
    for key in ("balance", "balance_change_30d", "avg_monthly_inflows", "avg_monthly_outflows"):
        assert_money(k[key])
    assert k["as_of"] == "2025-11-30"
    assert k["balance"]["amount"] == "23552.09"  # running sum of the statement (T015 report)
    assert amount(k["avg_monthly_inflows"]) > 0 > amount(k["avg_monthly_outflows"])
    assert k["undocumented_payments"]["count"] >= 6  # 3+2+1 history invoices only on the workstation or nowhere
    assert amount(k["undocumented_payments"]["total"]) > 0
    assert k["pending_approvals"] == 0 and k["open_tasks"] is None  # no Scout tables in this schema
    assert k["open_cost_alerts"] == 0  # the cloud spike arrives in December


def test_kpis_forecast_summary_and_buffer(nov):
    f = nov.get("/v1/kpis").json()["forecast"]
    assert f["horizon_days"] == 365
    assert f["lowest_date"].startswith("2025-12")  # lowest of the horizon: the start, before December income
    assert f["safety_buffer"]["amount"] == "17730.00"  # one month of fixed costs, D-013
    assert f["first_negative_date"] is None  # D-013: the real forecast stays above zero
    assert amount(f["lowest_balance"]) > amount(f["safety_buffer"])
    assert f["first_below_buffer_date"] is None


def test_kpis_without_data_is_a_clean_error():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    from cfo.db.migrate import SCHEMA_PATH
    from cfo.db.repo import Repository

    repo = Repository.connect(url)
    try:
        repo.conn.execute(f"DROP SCHEMA IF EXISTS {EMPTY} CASCADE")
        repo.conn.execute(f"CREATE SCHEMA {EMPTY}")
        repo.conn.execute(f"SET search_path TO {EMPTY}")
        repo.conn.commit()
        repo.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        repo.conn.commit()
        r = _client(repo).get("/v1/kpis")
        assert r.status_code == 503 and r.json()["error"]["code"] == "no_data"
    finally:
        repo.conn.rollback()
        repo.conn.execute(f"DROP SCHEMA {EMPTY} CASCADE")
        repo.conn.commit()
        repo.conn.close()


def test_kpis_count_scout_approvals_and_open_tasks():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    from cfo.db.migrate import SCHEMA_PATH
    from cfo.db.repo import Repository
    from scout.db import SCOUT_SCHEMA

    repo = Repository.connect(url)
    try:
        repo.conn.execute(f"DROP SCHEMA IF EXISTS {SCOUT} CASCADE")
        repo.conn.execute(f"CREATE SCHEMA {SCOUT}")
        repo.conn.execute(f"SET search_path TO {SCOUT}")
        repo.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        repo.conn.commit()
        assert services._count_if_table(repo, "approvals", "status = 'pending'") is None
        repo.conn.execute(SCOUT_SCHEMA.read_text(encoding="utf-8"))
        for i, status in enumerate(["waiting", "escalated", "done", "in_progress", "waiting"]):
            repo.conn.execute("INSERT INTO agent_tasks (id, dedupe_key, loop, title, status) "
                              "VALUES (%s, %s, 'invoicing', 't', %s)", (f"SC-{i}", f"k{i}", status))
        for i, status in enumerate(["pending", "approved", "pending"]):
            repo.conn.execute("INSERT INTO approvals (id, task_id, approver_id, question, status) "
                              "VALUES (%s, 'SC-0', 'vincenzo', 'q', %s)", (f"apr_{i}", status))
        repo.conn.commit()
        assert services._count_if_table(repo, "approvals", "status = 'pending'") == 2
        assert services._count_if_table(repo, "agent_tasks", "status IN ('waiting', 'escalated')") == 3
    finally:
        repo.conn.rollback()
        repo.conn.execute(f"DROP SCHEMA {SCOUT} CASCADE")
        repo.conn.commit()
        repo.conn.close()


# --- forecast --------------------------------------------------------------------------------------------


def test_forecast_default_90_days_weekly(nov):
    f = nov.get("/v1/forecast/liquidity").json()
    assert f["horizon_days"] == 90 and f["granularity"] == "week" and f["currency"] == "EUR"
    assert f["as_of"] == "2025-11-30"
    assert f["opening_balance"]["amount"] == "23552.09"
    assert f["safety_buffer"]["amount"] == "17730.00"
    assert len(f["points"]) in (13, 14)  # one per week, the trough is added when it falls between
    assert f["points"][0]["date"] > f["as_of"]
    for p in f["points"]:
        assert_money(p["balance"])
        assert amount(p["lower"]) < amount(p["balance"]) < amount(p["upper"])
    assert f["gaps"] == []
    assert f["model"]["name"] == "xgboost-liquidity" and f["model"]["trained_at"].endswith("Z")
    assert f["model"]["version"] == "trained-until-2025-11-30"


def test_forecast_year_shows_the_june_dip_and_stays_above_the_buffer(nov):
    f = nov.get("/v1/forecast/liquidity", params={"horizon_days": 365, "granularity": "day"}).json()
    assert len(f["points"]) == 365
    by_day = {p["date"]: amount(p["balance"]) for p in f["points"]}
    assert by_day["2026-06-30"] < by_day["2026-05-31"] - 10000  # the shutdown month (T015: net about -14k)
    low = amount(f["lowest_point"]["balance"])
    assert low == min(by_day.values())
    assert amount(f["safety_buffer"]) < low  # D-013: balance never below the buffer, so no gap
    assert f["gaps"] == []
    june = [v for d, v in by_day.items() if d.startswith("2026-06")]
    assert min(june) > amount(f["safety_buffer"]) and min(june) > 0  # T015: the dip stays at about 45k


def test_forecast_gap_appears_when_the_buffer_is_above_the_dip(nov, monkeypatch):
    from cfo.api import settings

    monkeypatch.setenv("SAFETY_BUFFER_EUR", "50000")
    settings.get_api_settings.cache_clear()
    try:
        f = nov.get("/v1/forecast/liquidity", params={"horizon_days": 365}).json()
    finally:
        monkeypatch.undo()
        settings.get_api_settings.cache_clear()
    assert f["safety_buffer"]["amount"] == "50000.00"
    june = [g for g in f["gaps"] if g["start"] <= "2026-06-30" and g["end"] >= "2026-06-01"]
    assert len(june) == 1  # the June dip goes below a 50k buffer
    assert all(amount(g["lowest_balance"]) < Decimal(50000) for g in f["gaps"])
    gap = f["gaps"][0]
    assert f["lowest_point"]["date"] == gap["lowest_date"]  # the first gap holds the lowest point (start of horizon)
    assert any(p["date"] == gap["lowest_date"] for p in f["points"])  # the trough is never sampled away


def test_forecast_model_is_cached_on_disk_after_first_request(nov):
    from cfo.forecast.model import default_model_path

    nov.get("/v1/forecast/liquidity")
    assert default_model_path().is_file()
    assert default_model_path().with_name(default_model_path().name + ".meta.json").is_file()


def test_forecast_validation(nov):
    for params in ({"horizon_days": 1}, {"horizon_days": 400}, {"granularity": "hour"}):
        r = nov.get("/v1/forecast/liquidity", params=params)
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


# --- costs by team ---------------------------------------------------------------------------------------


def test_costs_by_team_defaults(nov):
    c = nov.get("/v1/costs/by-team").json()
    assert c["from"] == "2025-01-01" and c["to"] == "2025-11-30"
    assert_money(c["total"])
    teams = {t["team_id"]: t for t in c["items"]}
    assert {"kitchen", "front", "marketing", "it", "equipment"} <= set(teams)
    assert c["items"][0]["team_id"] == "kitchen"
    assert len(teams["kitchen"]["by_month"]) == 11
    assert teams["kitchen"]["owner_id"] == "rosa" and teams["kitchen"]["monthly_budget"] is not None
    assert teams["company"]["owner_id"] is None and teams["company"]["monthly_budget"] is None
    assert sum(amount(t["total"]) for t in c["items"]) == amount(c["total"])
    assert sum(Decimal(t["share"]) for t in c["items"]) == pytest.approx(Decimal(1), abs=Decimal("0.001"))
    assert all(amount(t["total"]) == sum(amount(m["cost"]) for m in t["by_month"]) for t in c["items"])
    assert teams["kitchen"]["by_month"][5]["month"] == "2025-06-01"


def test_costs_by_team_range_and_equipment_oven(nov):
    c = nov.get("/v1/costs/by-team", params={"from": "2025-10-01", "to": "2025-11-30"}).json()
    teams = {t["team_id"]: t for t in c["items"]}
    assert [m["month"] for m in teams["kitchen"]["by_month"]] == ["2025-10-01", "2025-11-01"]
    assert teams["equipment"]["total"]["amount"] == "3581.90"  # the oven exists only as an invoice (T014)


def test_costs_by_team_bad_range(nov):
    r = nov.get("/v1/costs/by-team", params={"from": "2025-05-01", "to": "2025-01-01"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert nov.get("/v1/costs/by-team", params={"from": "yesterday"}).status_code == 422


# --- cost metrics ----------------------------------------------------------------------------------------


def test_cost_metrics_november_has_no_anomaly(nov):
    items = nov.get("/v1/cost-metrics").json()["items"]
    assert [s["kind"] for s in items] == ["food_cost", "ads_cac", "cloud_per_order"]
    assert all(len(s["points"]) == 11 for s in items)
    assert not [p for s in items for p in s["points"] if p["anomaly"]]
    assert items[0]["points"][5]["cost_per_unit"] is None  # June shutdown


def test_cost_metrics_filter(nov):
    items = nov.get("/v1/cost-metrics", params={"kind": "ads_cac"}).json()["items"]
    assert [s["kind"] for s in items] == ["ads_cac"]
    assert nov.get("/v1/cost-metrics", params={"kind": "nope"}).status_code == 422
