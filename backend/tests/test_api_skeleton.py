"""API skeleton tests: stubs only, no Postgres."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from cfo.api.app import create_app
from cfo.api.export_openapi import main as export_main

MONEY = re.compile(r"^-?\d+\.\d{2}$")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def assert_money(m):
    assert set(m) == {"amount", "currency"}
    assert MONEY.match(m["amount"]), m
    assert m["currency"] == "EUR"


def assert_error(resp, status, code):
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("path", [
    "/v1/kpis", "/v1/forecast/liquidity", "/v1/costs/by-team", "/v1/cost-metrics", "/v1/decisions", "/v1/rules",
])
def test_every_endpoint_is_marked_stub(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.json()["stub"] is True
    assert r.headers["x-stub"] == "true"


def test_kpis_shape(client):
    k = client.get("/v1/kpis").json()
    for key in ("balance", "balance_change_30d", "avg_monthly_inflows", "avg_monthly_outflows"):
        assert_money(k[key])
    assert k["as_of"] == "2025-12-31"
    assert k["forecast"]["lowest_date"].startswith("2026-06")
    assert k["forecast"]["first_negative_date"].startswith("2026-06")
    assert_money(k["forecast"]["lowest_balance"])
    assert k["pending_approvals"] == 1
    assert k["open_cost_alerts"] == 1


def test_forecast_default_is_90_days_weekly(client):
    f = client.get("/v1/forecast/liquidity").json()
    assert f["horizon_days"] == 90 and f["granularity"] == "week"
    assert len(f["points"]) == 13  # one per week, last day of each 7-day block
    assert f["gaps"] == []
    for p in f["points"]:
        assert_money(p["balance"])
        assert float(p["lower"]["amount"]) < float(p["balance"]["amount"]) < float(p["upper"]["amount"])


def test_forecast_full_year_shows_june_gap(client):
    f = client.get("/v1/forecast/liquidity", params={"horizon_days": 365}).json()
    assert len(f["gaps"]) == 1
    gap = f["gaps"][0]
    assert gap["start"].startswith("2026-06") and gap["end"].startswith("2026-07")
    assert float(gap["lowest_balance"]["amount"]) == pytest.approx(-20430.0)
    assert f["lowest_point"]["date"] == gap["lowest_date"]
    assert any(p["date"] == gap["lowest_date"] for p in f["points"])


def test_forecast_daily(client):
    f = client.get("/v1/forecast/liquidity", params={"horizon_days": 30, "granularity": "day"}).json()
    assert len(f["points"]) == 30


def test_forecast_validation_error(client):
    assert_error(client.get("/v1/forecast/liquidity", params={"horizon_days": 1}), 422, "validation_error")
    r = client.get("/v1/forecast/liquidity", params={"granularity": "hour"})
    assert_error(r, 422, "validation_error")
    assert r.json()["error"]["details"][0]["loc"] == ["query", "granularity"]


def test_costs_by_team(client):
    c = client.get("/v1/costs/by-team").json()
    assert c["from"] == "2025-01-01" and c["to"] == "2025-12-31"
    assert_money(c["total"])
    teams = {t["team_id"]: t for t in c["items"]}
    assert set(teams) == {"kitchen", "front", "marketing", "it", "equipment"}
    assert c["items"][0]["team_id"] == "kitchen"
    assert len(teams["kitchen"]["by_month"]) == 12
    assert sum(float(t["total"]["amount"]) for t in c["items"]) == pytest.approx(float(c["total"]["amount"]))
    assert sum(float(t["share"]) for t in c["items"]) == pytest.approx(1.0, abs=0.001)


def test_costs_by_team_range(client):
    c = client.get("/v1/costs/by-team", params={"from": "2025-10-01", "to": "2025-11-30"}).json()
    teams = {t["team_id"]: t for t in c["items"]}
    assert [m["month"] for m in teams["kitchen"]["by_month"]] == ["2025-10-01", "2025-11-01"]
    assert teams["equipment"]["total"]["amount"] == "3590.20"


def test_costs_by_team_bad_range(client):
    assert_error(client.get("/v1/costs/by-team", params={"from": "2025-05-01", "to": "2025-01-01"}), 422,
                 "validation_error")
    assert_error(client.get("/v1/costs/by-team", params={"from": "yesterday"}), 422, "validation_error")


def test_cost_metrics_three_pairs_and_one_anomaly(client):
    items = client.get("/v1/cost-metrics").json()["items"]
    assert [s["kind"] for s in items] == ["food_cost", "ads_cac", "cloud_per_order"]
    flagged = [(s["kind"], p["month"]) for s in items for p in s["points"] if p["anomaly"]]
    assert flagged == [("cloud_per_order", "2025-12-01")]
    cloud = items[2]["points"][-1]
    assert cloud["cost_change_pct"] == "50.0"
    assert cloud["metric_change_pct"] == "2.9"
    assert cloud["cost"]["amount"] == "366.02"
    assert_money(cloud["cost_per_unit"])
    june = items[0]["points"][5]
    assert june["cost_per_unit"] is None


def test_cost_metrics_filter(client):
    items = client.get("/v1/cost-metrics", params={"kind": "ads_cac"}).json()["items"]
    assert [s["kind"] for s in items] == ["ads_cac"]
    assert_error(client.get("/v1/cost-metrics", params={"kind": "nope"}), 422, "validation_error")


def test_decisions_pagination(client):
    page = client.get("/v1/decisions", params={"limit": 2}).json()
    assert page["total"] == 6 and page["limit"] == 2 and page["offset"] == 0 and len(page["items"]) == 2
    nxt = client.get("/v1/decisions", params={"limit": 2, "offset": 2}).json()
    assert {d["id"] for d in page["items"]}.isdisjoint(d["id"] for d in nxt["items"])
    ts = [d["ts"] for d in client.get("/v1/decisions").json()["items"]]
    assert ts == sorted(ts, reverse=True)
    assert all(t.endswith("Z") for t in ts)


def test_decisions_filters_and_simulated(client):
    appr = client.get("/v1/decisions", params={"level": "APPROVAL"}).json()
    assert appr["total"] == 2
    assert {d["status"] for d in appr["items"]} == {"pending", "executed"}
    assert any(d["executed_simulated"] for d in appr["items"])
    pend = client.get("/v1/decisions", params={"status": "pending"}).json()
    assert pend["total"] == 1 and pend["items"][0]["approval_request_id"]


def test_decisions_bad_params(client):
    assert_error(client.get("/v1/decisions", params={"limit": 0}), 422, "validation_error")
    assert_error(client.get("/v1/decisions", params={"limit": 1000}), 422, "validation_error")
    assert_error(client.get("/v1/decisions", params={"level": "MAYBE"}), 422, "validation_error")


def test_decision_by_id_and_404(client):
    d = client.get("/v1/decisions/dec_6f1c0a7e2b11")
    assert d.status_code == 200 and d.json()["action_type"] == "attach_invoice"
    assert_error(client.get("/v1/decisions/dec_nope"), 404, "not_found")


def test_rules(client):
    page = client.get("/v1/rules").json()
    assert page["total"] == 3
    assert page["items"][0]["text"] == "I always approve equipment myself"
    assert page["items"][0]["level"] == "APPROVAL"
    active = client.get("/v1/rules", params={"active": "true"}).json()
    assert active["total"] == 2 and all(r["active"] for r in active["items"])


def test_unknown_route_and_method_use_error_model(client):
    assert_error(client.get("/v1/nothing"), 404, "not_found")
    assert_error(client.post("/v1/kpis"), 405, "method_not_allowed")


def test_unhandled_exception_is_500_envelope(monkeypatch):
    from cfo.api import stubs

    def boom():
        raise RuntimeError("secret detail")

    monkeypatch.setattr(stubs, "kpis", boom)
    r = TestClient(create_app(), raise_server_exceptions=False).get("/v1/kpis")
    assert_error(r, 500, "internal_error")
    assert "secret" not in r.text


def test_cors_headers(client):
    r = client.options("/v1/kpis", headers={"Origin": "http://localhost:5173",
                                            "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")
    g = client.get("/v1/kpis", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" in g.headers
    assert "x-stub" in g.headers["access-control-expose-headers"].lower()


def test_cors_origins_from_env(monkeypatch):
    from cfo.api import settings

    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example")
    settings.get_api_settings.cache_clear()
    try:
        c = TestClient(create_app())
        ok = c.get("/healthz", headers={"Origin": "http://b.example"})
        assert ok.headers["access-control-allow-origin"] == "http://b.example"
        bad = c.get("/healthz", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in bad.headers
    finally:
        settings.get_api_settings.cache_clear()


def test_openapi_document(tmp_path):
    out = tmp_path / "sub" / "openapi.json"
    assert export_main([str(out)]) == 0
    doc = json.loads(out.read_text())
    paths = set(doc["paths"])
    assert {"/healthz", "/v1/kpis", "/v1/forecast/liquidity", "/v1/costs/by-team", "/v1/cost-metrics",
            "/v1/decisions", "/v1/decisions/{decision_id}", "/v1/rules"} <= paths
    assert "ErrorResponse" in doc["components"]["schemas"]
    assert export_main([]) == 2
