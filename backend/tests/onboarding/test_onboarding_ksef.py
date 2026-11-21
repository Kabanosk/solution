"""Cycle 4: KSeF TEST-environment read authentication only. No demo suppliers, invoices or transactions."""
from __future__ import annotations

import random

import pytest

from onboarding_helpers import step, tenant_rows
from scout.connectors.ksef import random_nip

HOST = "api-test.ksef.mf.gov.pl"
NIP = random_nip(random.Random(7))


def script_ksef(fakes, *, fail: dict | None = None, status_code: int = 200) -> None:
    fail = fail or {}

    def route(method, path, body):
        fakes.route(method, HOST, "/v2" + path, fail.get(path, body))

    route("POST", "/auth/challenge", {"challenge": "challenge-1", "timestamp": "2026-11-21T10:00:00Z"})
    route("POST", "/auth/xades-signature", {"referenceNumber": "ref-1", "authenticationToken": {"token": "auth-token"}})
    route("GET", "/auth/ref-1", {"status": {"code": status_code}})
    route("POST", "/auth/token/redeem", {"accessToken": {"token": "access-token"}})
    route("POST", "/invoices/query/metadata", {"invoices": [], "hasMore": False})


def connect(api, org, nip=NIP):
    return api.post("/v1/onboarding/connect/ksef", headers=org.headers(), json={"nip": nip})


def test_valid_test_nip_authenticates_and_runs_one_read_query(api, org, fakes, db_url):
    script_ksef(fakes)
    response = connect(api, org)
    assert response.status_code == 200
    ksef = step(response.json(), "ksef")
    assert ksef["state"] == "sandbox_connected" and ksef["provider"] == "ksef"
    assert ksef["display"] == {"environment": "test", "nip": NIP, "read_only": True}
    assert fakes.hosts() == {HOST}
    paths = [r.url.path for r in fakes.calls]
    assert paths == ["/v2/auth/challenge", "/v2/auth/xades-signature", "/v2/auth/ref-1", "/v2/auth/token/redeem",
                     "/v2/invoices/query/metadata"]
    [row] = tenant_rows(db_url, org, "SELECT * FROM connections WHERE id='ksef'")
    assert row["provider"] == "ksef_test" and row["status"] == "connected"
    assert row["config"]["env"] == "test" and row["config"]["nip"] == NIP
    assert row["config"]["api"] == "https://api-test.ksef.mf.gov.pl/v2"
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []  # nothing secret is kept


def test_nothing_is_manufactured_no_sessions_no_invoices_no_transactions(api, org, fakes, db_url):
    script_ksef(fakes)
    connect(api, org)
    assert not [r for r in fakes.calls if "/sessions" in r.url.path or "public-key" in r.url.path]
    for table in ("invoices", "transactions", "agent_jobs", "bank_mock_transactions"):
        assert tenant_rows(db_url, org, f"SELECT count(*) AS n FROM {table}")[0]["n"] == 0, table


def test_a_production_url_in_settings_is_never_used(api, org, fakes, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, ksef_api_url="https://api.ksef.mf.gov.pl/v2")
    script_ksef(fakes)
    assert step(connect(api, org).json(), "ksef")["state"] == "sandbox_connected"
    assert fakes.hosts() == {HOST}


def test_an_invalid_nip_is_a_validation_error_with_no_provider_traffic(api, org, fakes):
    for bad in ("1234567890", "abc", "123"):
        assert connect(api, org, bad).status_code == 422
    assert fakes.calls == []


@pytest.mark.parametrize("fail,status_code", [({"/auth/xades-signature": (400, {"title": "bad certificate"})}, 200),
                                              ({"/auth/challenge": (503, {})}, 200),
                                              ({}, 450),
                                              ({"/invoices/query/metadata": (403, {"title": "no access"})}, 200)])
def test_unsupported_or_failed_auth_is_failed_with_a_specific_message_never_connected(api, org, fakes, db_url, fail, status_code):
    script_ksef(fakes, fail=fail, status_code=status_code)
    response = connect(api, org)
    assert response.status_code == 200
    ksef = step(response.json(), "ksef")
    assert ksef["state"] == "failed" and "KSeF" in ksef["message"]
    assert "bad certificate" not in response.text and "no access" not in response.text  # provider bodies are not echoed
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_disconnect_removes_the_test_identity(api, org, fakes, db_url):
    script_ksef(fakes)
    connect(api, org)
    body = api.delete("/v1/onboarding/connect/ksef", headers=org.headers()).json()
    assert step(body, "ksef")["state"] == "not_started"
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
