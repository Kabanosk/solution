"""API skeleton tests: error model, validation, CORS, OpenAPI. No Postgres (the repo is replaced by a dummy).

The data behind the endpoints is tested in `test_api_read.py`.
"""

import json
import re

import pytest
from api_test_helpers import TestClient

from cfo.api.app import create_app
from cfo.api.deps import get_repo
from cfo.api.export_openapi import main as export_main

MONEY = re.compile(r"^-?\d+\.\d{2}$")


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: None  # validation and routing never reach the database
    return TestClient(app)


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


def test_forecast_validation_error(client):
    assert_error(client.get("/v1/forecast/liquidity", params={"horizon_days": 1}), 422, "validation_error")
    r = client.get("/v1/forecast/liquidity", params={"granularity": "hour"})
    assert_error(r, 422, "validation_error")
    assert r.json()["error"]["details"][0]["loc"] == ["query", "granularity"]


def test_costs_by_team_bad_range(client):
    assert_error(client.get("/v1/costs/by-team", params={"from": "2025-05-01", "to": "2025-01-01"}), 422,
                 "validation_error")
    assert_error(client.get("/v1/costs/by-team", params={"from": "yesterday"}), 422, "validation_error")


def test_cost_metrics_bad_kind(client):
    assert_error(client.get("/v1/cost-metrics", params={"kind": "nope"}), 422, "validation_error")


def test_unknown_route_and_method_use_error_model(client):
    assert_error(client.get("/v1/nothing"), 404, "not_found")
    assert_error(client.post("/v1/kpis"), 405, "method_not_allowed")


def test_unhandled_exception_is_500_envelope():
    def boom():
        raise RuntimeError("secret detail")

    app = create_app()
    app.dependency_overrides[get_repo] = boom
    r = TestClient(app, raise_server_exceptions=False).get("/v1/kpis")
    assert_error(r, 500, "internal_error")
    assert "secret" not in r.text


def test_unreachable_database_is_503_envelope():
    import psycopg

    def down():
        raise psycopg.OperationalError("connection refused at 10.0.0.1")

    app = create_app()
    app.dependency_overrides[get_repo] = down
    r = TestClient(app, raise_server_exceptions=False).get("/v1/kpis")
    assert_error(r, 503, "database_unavailable")
    assert "10.0.0.1" not in r.text


def test_cors_headers(client):
    r = client.options("/v1/kpis", headers={"Origin": "http://localhost:5173",
                                            "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:5173")
    g = client.get("/healthz", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" in g.headers


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
            "/v1/tasks", "/v1/tasks/{task_id}", "/v1/approvals", "/v1/audit", "/v1/connections"} <= paths
    assert not paths & {"/v1/decisions", "/v1/rules", "/v1/events", "/v1/sim"}
    assert doc["info"]["title"] == "Scout API"
    assert "ErrorResponse" in doc["components"]["schemas"]
    assert export_main([]) == 2
