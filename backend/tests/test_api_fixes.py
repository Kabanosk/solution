"""T046: API hardening (write token, HTTP edge cases).

Runs in an isolated Postgres schema `t046_test_<pid>` (dropped afterwards). Skips when Postgres is not reachable.
The approvals/simulator/SSE parts went with the December replay agent; Scout's own endpoints are tested in
`tests/scout/test_api.py`.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
from api_test_helpers import operator_repository
import pytest
from api_test_helpers import TestClient

from cfo.api.app import create_app
from cfo.api.deps import get_repo
from cfo.api.settings import get_api_settings
from cfo.config import get_settings
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import Invoice, Transaction

SCHEMA = f"t046_test_{os.getpid()}"
TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE_PATHS = ("/v1/approvals/{approval_id}:approve", "/v1/approvals/{approval_id}:reject",
               "/v1/connections/{connection_id}:enable", "/v1/connections/{connection_id}:disable",
               "/v1/demo/play/{scenario}")


@pytest.fixture
def db_url():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{SCHEMA}"
    with psycopg.connect(isolated) as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield isolated
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture
def repo(db_url):
    r = operator_repository(db_url)
    yield r
    r.conn.close()


def make_client(db_url: str) -> TestClient:
    app = create_app()

    def repo_override():
        with operator_repository(db_url) as r:
            yield r

    app.dependency_overrides[get_repo] = repo_override
    return TestClient(app)


# --- (22) write token --------------------------------------------------------------------------------------


def test_openapi_requires_session_and_organization_for_all_data():
    spec = create_app().openapi()
    assert spec["components"]["securitySchemes"]["SessionToken"]["scheme"] == "bearer"
    assert spec["components"]["securitySchemes"]["Organization"]["name"] == "X-Scout-Organization"
    assert spec["security"] == [{"SessionToken": [], "Organization": []}]
    assert spec["paths"]["/healthz"]["get"]["security"] == []


def test_token_with_a_trailing_newline_still_protects(monkeypatch):
    monkeypatch.setenv("API_WRITE_TOKEN", TOKEN + "\n")  # `oc create secret --from-file` keeps the newline
    get_api_settings.cache_clear()
    try:
        client = TestClient(create_app(), raise_server_exceptions=False)
        assert client.post("/v1/demo/play/nope", headers={"Authorization": ""}).status_code == 401
        assert client.post("/v1/demo/play/nope", headers=AUTH).status_code == 401
    finally:
        monkeypatch.delenv("API_WRITE_TOKEN")
        get_api_settings.cache_clear()


# --- (P3) HTTP edge cases ---------------------------------------------------------------------------------


def test_500_carries_cors_headers(db_url):
    app = create_app()

    def broken():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    app.dependency_overrides[get_repo] = broken
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/v1/kpis", headers={"Origin": "https://dashboard.example"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert r.headers["access-control-allow-origin"] == "*"
    assert "x-stub" in r.headers.get("access-control-expose-headers", "").lower()
    plain = client.get("/v1/kpis")  # no Origin: no CORS headers needed
    assert plain.status_code == 500 and "access-control-allow-origin" not in plain.headers


def test_500_cors_follows_the_configured_origins(db_url, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://ok.example")
    get_api_settings.cache_clear()
    try:
        app = create_app()

        def broken():
            raise RuntimeError("boom")
            yield  # pragma: no cover

        app.dependency_overrides[get_repo] = broken
        client = TestClient(app, raise_server_exceptions=False)
        ok = client.get("/v1/kpis", headers={"Origin": "https://ok.example"})
        assert ok.headers["access-control-allow-origin"] == "https://ok.example"
        other = client.get("/v1/kpis", headers={"Origin": "https://evil.example"})
        assert other.status_code == 500 and "access-control-allow-origin" not in other.headers
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        get_api_settings.cache_clear()


def test_405_keeps_the_allow_header(db_url):
    r = make_client(db_url).post("/v1/kpis")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in r.headers["allow"]


def add_january_booking(repo: Repository) -> None:
    repo.add_transaction(Transaction(id="t1", booking_date=date(2025, 1, 10), type="CARD", reference="x",
                                     counterparty="Supplier", amount=Decimal("-50.00"), category="food"))


def add_unlinked_invoice(repo: Repository, issued: date, total: str = "100.00") -> None:
    repo.add_invoice(Invoice(number="FLV-1", supplier="Flavio", issue_date=issued, total=Decimal(total),
                             category="food", team_id="kitchen"))


def test_half_open_and_inverted_date_ranges_are_422(db_url, repo):
    # a statement with bookings only in January 2025
    add_january_booking(repo)
    client = make_client(db_url)
    assert client.get("/v1/costs/by-team").status_code == 200
    for params in ({"from": "2026-06-01"}, {"to": "2024-12-31"}, {"from": "2025-03-01", "to": "2025-01-01"}):
        r = client.get("/v1/costs/by-team", params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "validation_error"


def test_far_future_range_is_422_not_500(db_url, repo):
    add_january_booking(repo)
    client = make_client(db_url)
    for params in ({"to": "9999-12-31"}, {"from": "0001-01-01", "to": "2025-01-31"}):
        r = client.get("/v1/costs/by-team", params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "validation_error"


def test_by_team_includes_unlinked_invoices_without_any_bank_transactions(db_url, repo):
    add_unlinked_invoice(repo, date(2025, 12, 5), "100.00")
    body = make_client(db_url).get("/v1/costs/by-team").json()
    assert body["total"]["amount"] == "100.00"
    assert [(i["team_id"], i["total"]["amount"]) for i in body["items"] if i["total"]["amount"] != "0.00"] == [
        ("kitchen", "100.00")]
    assert body["from"] == "2025-12-01" and body["to"] == "2025-12-05"


def test_cors_preflight_allows_the_authorization_header(db_url):
    r = make_client(db_url).options("/v1/approvals/apr_1:approve", headers={
        "Origin": "https://dashboard.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_range_ending_in_year_9999_is_not_a_500(db_url, repo):
    add_january_booking(repo)
    r = make_client(db_url).get("/v1/costs/by-team", params={"from": "9999-12-01", "to": "9999-12-31"})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["total"]["amount"] == "0.00"
