"""Cycle 8: tenant and authorization boundaries, closed legacy surfaces, no secret leakage, provider allowlist."""
from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest

from bunq_fixtures import API_KEY, script_bunq
from google_fixtures import PROOF, REDIRECT, callback, parse_start, script_google, start
from onboarding_helpers import admin, setup_row, step, tenant_rows

LEGACY_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def legacy_owner(db_url):
    uid, token = uuid4(), uuid4().hex + uuid4().hex
    with psycopg.connect(db_url) as conn:
        conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,%s,%s)", (uid, f"{uid}@legacy.test", "Legacy"))
        conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner','vincenzo')",
                     (LEGACY_ID, uid))
        conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) VALUES (%s,%s,%s,now()+interval '1 hour')",
                     (hashlib.sha256(token.encode()).hexdigest(), uid, LEGACY_ID))
    yield {"Authorization": "Bearer " + token, "X-Scout-Organization": "pizzeria-amalfi"}
    with psycopg.connect(db_url) as conn:
        conn.execute("DELETE FROM scout_auth.workspace_setup WHERE organization_id=%s", (LEGACY_ID,))
        conn.execute("DELETE FROM scout_auth.users WHERE id=%s", (uid,))


def test_the_legacy_organization_is_never_onboarded_or_mutated(api, legacy_owner, db_url, fakes):
    assert api.get("/v1/onboarding", headers=legacy_owner).json()["status"] == "not_started"
    for response in (
        api.patch("/v1/onboarding", headers=legacy_owner, json={"revision": 0, "action": "resume"}),
        api.post("/v1/onboarding/connect/builtin", headers=legacy_owner, json={}),
        api.delete("/v1/onboarding/connect/builtin", headers=legacy_owner),
        api.post("/v1/onboarding/callback/google", headers=legacy_owner,
                 json={"state": "s" * 30, "code": "c", "redirect_uri": REDIRECT, "browser_proof": PROOF}),
    ):
        assert response.status_code == 403 and response.json()["error"]["code"] == "legacy_organization"
    assert setup_row(db_url, LEGACY_ID) is None and fakes.calls == []


@pytest.mark.parametrize("method,path", [("POST", "/v1/connections/google/start"), ("GET", "/v1/connections/google/callback"),
                                         ("POST", "/v1/connections/ksef:connect"), ("POST", "/v1/connections/gmail:enable"),
                                         ("POST", "/v1/demo/play/ksef"), ("POST", "/v1/demo/play/anything")])
def test_old_global_mutation_and_demo_routes_stay_closed_for_owners(api, org, method, path):
    response = api.request(method, path, headers=org.headers())
    assert response.status_code == 403 and response.json()["error"]["code"] == "not_configured"


def test_revoked_membership_and_expired_sessions_lose_access_immediately(api, org, db_url):
    assert api.get("/v1/onboarding", headers=org.headers()).status_code == 200
    admin(db_url).execute("UPDATE scout_auth.sessions SET expires_at = now() - interval '1 second' WHERE organization_id=%s "
                          "AND token_hash=%s", (org.id, hashlib.sha256(org.token.encode()).hexdigest()))
    assert api.get("/v1/onboarding", headers=org.headers()).status_code == 401
    assert api.patch("/v1/onboarding", headers=org.headers(), json={"revision": 0, "action": "skip"}).status_code == 401
    assert api.get("/v1/onboarding", headers=org.headers(member=True)).status_code == 200
    admin(db_url).execute("DELETE FROM scout_auth.memberships WHERE organization_id=%s AND role='member'", (org.id,))
    assert api.get("/v1/onboarding", headers=org.headers(member=True)).status_code == 401


def test_another_organizations_owner_cannot_touch_or_remove_my_connections(api, org, other_org, db_url):
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    api.delete("/v1/onboarding/connect/builtin", headers=other_org.headers())
    assert [r["id"] for r in tenant_rows(db_url, org, "SELECT id FROM connections")] == ["invoicing"]
    crossed = {"Authorization": other_org.headers()["Authorization"], "X-Scout-Organization": org.slug}
    assert api.delete("/v1/onboarding/connect/builtin", headers=crossed).status_code == 401
    assert setup_row(db_url, org.id)["revision"] == 1  # only my own connect changed my setup


def test_reading_setup_never_calls_a_provider_even_with_everything_connected(api, org, fakes, db_url):
    script_bunq(fakes)
    api.post("/v1/onboarding/connect/bunq", headers=org.headers(), json={})
    fakes.calls.clear()
    body = api.get("/v1/onboarding", headers=org.headers()).json()
    assert step(body, "bank")["state"] == "sandbox_connected"
    assert fakes.calls == []


def test_no_secret_ever_reaches_the_logs(api, org, fakes, caplog):
    caplog.set_level(logging.DEBUG)
    script_bunq(fakes)
    api.post("/v1/onboarding/connect/bunq", headers=org.headers(), json={})
    params = parse_start(start(api, org).json()["url"])
    script_google(fakes, params["code_challenge"], refresh_status=400)
    callback(api, org, params["state"])
    for secret in (API_KEY, "installation-token-xyz", "session-token-xyz", "rt-secret-1", "at-secret-1",
                   "google-client-secret", "good-code", "secret-detail", params["state"], PROOF, org.token):
        assert secret not in caplog.text, secret


def test_provider_clients_are_allowlisted_short_and_never_follow_redirects():
    from scout.onboarding import http
    assert set(http.BASES) == {"bunq", "google_auth", "google_token", "gmail", "slack", "ksef"}
    assert all(url.startswith("https://") for url in http.BASES.values())
    assert http.BASES["bunq"] == "https://public-api.sandbox.bunq.com/v1"  # sandbox host only
    assert http.BASES["ksef"] == "https://api-test.ksef.mf.gov.pl/v2"  # test environment only
    with pytest.raises(KeyError):
        http.client("https://evil.example")
    with pytest.raises(KeyError):
        http.client("api")
    for name in http.BASES:
        c = http.client(name)
        assert c.timeout.read <= 10 and c.timeout.connect <= 10 and c.follow_redirects is False
    assert http.REVOKE_TIMEOUT.read <= 10


def test_a_provider_redirect_is_not_followed(api, org, fakes):
    script_bunq(fakes, fail={"/v1/installation": httpx.Response(302, headers={"location": "https://evil.example/steal"})})
    assert step(api.post("/v1/onboarding/connect/bunq", headers=org.headers(), json={}).json(), "bank")["state"] == "failed"
    assert fakes.hosts() == {"public-api.sandbox.bunq.com"}


def test_concurrent_updates_with_the_same_revision_yield_exactly_one_winner(api, org):
    def attempt(_):
        return api.patch("/v1/onboarding", headers=org.headers(), json={"revision": 0, "action": "skip"}).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = sorted(pool.map(attempt, range(4)))
    assert codes == [200, 409, 409, 409]


def test_existing_organization_without_a_row_is_untouched_until_the_owner_acts(api, make_org, db_url):
    org = make_org(with_setup_row=False)
    for _ in range(3):
        api.get("/v1/onboarding", headers=org.headers())
    assert setup_row(db_url, org.id) is None
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    assert setup_row(db_url, org.id)["status"] == "in_progress"


def test_the_setup_response_never_contains_internal_identifiers(api, org):
    body = api.get("/v1/onboarding", headers=org.headers()).text
    assert org.schema not in body and "scout_auth" not in body and org.id not in body
