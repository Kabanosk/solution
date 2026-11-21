"""Cycle 5: Gmail mailbox OAuth, separate from identity sign-in. Start, bound callback, token storage, verification."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs

import pytest

from google_fixtures import PROOF, REDIRECT, REFRESH, SCOPE, callback, extra_session, parse_start, script_google, start
from onboarding_helpers import APP_ORIGIN, MASTER_KEY, admin, dump, step, tenant_rows

SECRETS = ("rt-secret-1", "at-secret-1", "at-secret-2", "good-code", "google-client-secret")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def begin(api, org, fakes, **script):
    started = start(api, org, label=script.pop("label", None))
    assert started.status_code == 200, started.text
    params = parse_start(started.json()["url"])
    script_google(fakes, params["code_challenge"], **script)
    return params


def state_rows(db_url):
    with admin(db_url) as conn:
        return conn.execute("SELECT * FROM scout_auth.setup_oauth_states").fetchall()


def test_start_builds_a_gmail_readonly_offline_consent_url_and_stores_only_hashes(api, org, fakes, db_url):
    response = start(api, org)
    assert response.status_code == 200 and set(response.json()) == {"url"}
    params = parse_start(response.json()["url"])
    assert params["client_id"] == "google-client-id" and params["redirect_uri"] == REDIRECT
    assert params["response_type"] == "code" and params["scope"] == SCOPE  # no openid/email: this is not sign-in
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    assert params["code_challenge_method"] == "S256" and "include_granted_scopes" not in params
    assert fakes.calls == []  # starting contacts no provider
    [row] = [r for r in state_rows(db_url) if str(r["organization_id"]) == org.id]
    assert row["state_hash"] == sha(params["state"]) and row["browser_hash"] == sha(PROOF)
    assert row["session_hash"] == sha(org.token) and row["provider"] == "google" and row["redirect_uri"] == REDIRECT
    assert row["label"] == "INBOX"
    stored = json.dumps(row, default=str)
    assert params["state"] not in stored and PROOF not in stored and org.token not in stored
    window = admin(db_url).execute("SELECT expires_at - now() AS ttl FROM scout_auth.setup_oauth_states "
                                   "WHERE organization_id=%s", (org.id,)).fetchone()["ttl"]
    assert 0 < window.total_seconds() <= 600
    assert step(api.get("/v1/onboarding", headers=org.headers()).json(), "email")["state"] == "connecting"


def test_start_without_provider_configuration_is_needs_configuration(api, org, fakes, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, google_client_id="", google_client_secret="")
    response = start(api, org)
    assert response.status_code == 200 and "url" not in response.json()
    email = step(response.json(), "email")
    assert email["state"] == "needs_configuration" and "GOOGLE_CLIENT_ID" in email["message"]
    assert [r for r in state_rows(db_url) if str(r["organization_id"]) == org.id] == [] and fakes.calls == []


@pytest.mark.parametrize("origin", ["http://evil.test", "ftp://x", "https://user:pw@x.test", "not a url"])
def test_an_unsafe_configured_origin_is_a_missing_prerequisite(api, org, settings_factory, db_url, origin):
    api.state["settings"] = settings_factory(database_url=db_url, scout_app_origin=origin)
    response = start(api, org, redirect=origin + "/api/onboarding/callback/google")
    assert response.status_code == 200 and "url" not in response.json()
    assert "SCOUT_APP_ORIGIN" in step(response.json(), "email")["message"]


def test_loopback_http_is_allowed_for_development(api, org, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, scout_app_origin="http://localhost:3000")
    redirect = "http://localhost:3000/api/onboarding/callback/google"
    assert start(api, org, redirect=redirect).status_code == 200


@pytest.mark.parametrize("redirect", [APP_ORIGIN + "/api/onboarding/callback/slack", APP_ORIGIN + "/other",
                                      "https://evil.test/api/onboarding/callback/google",
                                      APP_ORIGIN + "/api/onboarding/callback/google?x=1",
                                      APP_ORIGIN + "/api/onboarding/callback/google/",
                                      "http://scout.example.test/api/onboarding/callback/google"])
def test_start_accepts_only_the_exact_configured_redirect(api, org, redirect):
    response = start(api, org, redirect=redirect)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_redirect"


def test_start_validates_proof_and_label(api, org):
    assert start(api, org, proof="short").status_code == 422
    assert start(api, org, label="x" * 101).status_code == 422
    assert start(api, org, label="bad\nlabel").status_code == 422


def test_a_second_start_replaces_the_first_in_flight_state(api, org, db_url):
    first = parse_start(start(api, org).json()["url"])["state"]
    second = parse_start(start(api, org).json()["url"])["state"]
    assert [r["state_hash"] for r in state_rows(db_url) if str(r["organization_id"]) == org.id] == [sha(second)]
    assert first != second


def test_successful_callback_verifies_the_mailbox_and_seals_the_refresh_token(api, org, fakes, db_url):
    params = begin(api, org, fakes, label="Invoices")
    response = callback(api, org, params["state"])
    assert response.status_code == 200, response.text
    body = response.json()
    email = step(body, "email")
    assert email["state"] == "connected" and email["provider"] == "google"
    assert email["display"] == {"mailbox": "boss@acme.example", "label": "Invoices", "scope": "gmail.readonly",
                                "read_only": True}
    for secret in SECRETS:
        assert secret not in response.text
    # exchange with the code, then a refresh with the stored token (proves it works), then read with the refreshed one
    assert [(r.method, r.url.host, r.url.path) for r in fakes.calls] == [
        ("POST", "oauth2.googleapis.com", "/token"), ("POST", "oauth2.googleapis.com", "/token"),
        ("GET", "gmail.googleapis.com", "/gmail/v1/users/me/profile"),
        ("GET", "gmail.googleapis.com", "/gmail/v1/users/me/labels")]
    assert fakes.hosts() == {"oauth2.googleapis.com", "gmail.googleapis.com"}
    from scout.connectors.crypto import derive_key, open_
    [cred] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='gmail'")
    assert open_(cred["ciphertext"], derive_key(MASTER_KEY, org.id, "gmail")) == REFRESH
    assert REFRESH.encode() not in bytes(cred["ciphertext"])
    [row] = tenant_rows(db_url, org, "SELECT * FROM connections WHERE id='gmail'")
    assert (row["kind"], row["provider"], row["status"]) == ("mail", "gmail_oauth", "connected")
    assert row["config"]["email"] == "boss@acme.example" and row["config"]["label"] == "Invoices"
    for secret in SECRETS:
        assert secret not in dump(row)
    assert state_rows(db_url) == [] or all(str(r["organization_id"]) != org.id for r in state_rows(db_url))
    assert body["revision"] == 1 and body["status"] == "in_progress"


def test_default_label_is_inbox(api, org, fakes, db_url):
    params = begin(api, org, fakes)
    assert step(callback(api, org, params["state"]).json(), "email")["display"]["label"] == "INBOX"


def test_state_is_single_use(api, org, fakes):
    params = begin(api, org, fakes)
    assert callback(api, org, params["state"]).status_code == 200
    calls = len(fakes.calls)
    replay = callback(api, org, params["state"])
    assert replay.status_code == 400 and replay.json()["error"]["code"] == "invalid_state"
    assert len(fakes.calls) == calls


@pytest.mark.parametrize("change", [{"proof": "q" * 40}, {"redirect": APP_ORIGIN + "/api/onboarding/callback/slack"},
                                    {"provider": "slack"}])
def test_callback_is_bound_to_browser_redirect_and_provider_and_a_wrong_try_keeps_the_state(api, org, fakes, change):
    params = begin(api, org, fakes)
    wrong = callback(api, org, params["state"], **change)
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_state"
    assert fakes.calls == []
    assert callback(api, org, params["state"]).status_code == 200  # the rightful browser can still finish


def test_callback_is_bound_to_the_initiating_session_and_organization(api, org, other_org, fakes, db_url):
    params = begin(api, org, fakes)
    other_device = extra_session(db_url, org)
    headers = {"Authorization": "Bearer " + other_device, "X-Scout-Organization": org.slug}
    body = {"state": params["state"], "code": "good-code", "redirect_uri": REDIRECT, "browser_proof": PROOF}
    assert api.post("/v1/onboarding/callback/google", headers=headers, json=body).status_code == 400
    assert api.post("/v1/onboarding/callback/google", headers=other_org.headers(), json=body).status_code == 400
    assert fakes.calls == []
    assert step(api.get("/v1/onboarding", headers=other_org.headers()).json(), "email")["state"] == "not_started"
    assert callback(api, org, params["state"]).status_code == 200


def test_only_the_owner_may_finish_the_flow(api, org, fakes):
    params = begin(api, org, fakes)
    denied = callback(api, org, params["state"], member=True)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "owner_required"
    assert fakes.calls == []


def test_expired_state_is_refused_and_cleaned(api, org, fakes, db_url):
    params = begin(api, org, fakes)
    admin(db_url).execute("UPDATE scout_auth.setup_oauth_states SET expires_at = now() - interval '1 second' "
                          "WHERE organization_id=%s", (org.id,))
    assert step(api.get("/v1/onboarding", headers=org.headers()).json(), "email")["state"] == "not_started"
    expired = callback(api, org, params["state"])
    assert expired.status_code == 400 and expired.json()["error"]["code"] == "invalid_state"
    assert fakes.calls == []
    assert [r for r in state_rows(db_url) if str(r["organization_id"]) == org.id] == []


def test_cancel_consumes_the_state_and_marks_the_step_failed_without_provider_calls(api, org, fakes, db_url):
    params = begin(api, org, fakes)
    response = callback(api, org, params["state"], error="access_denied")
    assert response.status_code == 200
    email = step(response.json(), "email")
    assert email["state"] == "failed" and "cancel" in email["message"].lower()
    assert fakes.calls == [] and tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert callback(api, org, params["state"], error="access_denied").status_code == 400
    assert start(api, org).status_code == 200  # retry
    assert step(api.get("/v1/onboarding", headers=org.headers()).json(), "email")["state"] == "connecting"


@pytest.mark.parametrize("script,needle", [
    ({"refresh_token": None}, "offline"),
    ({"scope": "openid email"}, "read-only"),
    ({"exchange_status": 400}, "could not be completed"),
    ({"exchange_status": 503}, "could not be reached"),
    ({"refresh_status": 400}, "rejected"),
    ({"profile_status": 403}, "unexpected"),
])
def test_provider_problems_are_failed_steps_with_nothing_stored_and_no_secrets_echoed(api, org, fakes, db_url, script, needle):
    params = begin(api, org, fakes, **script)
    response = callback(api, org, params["state"])
    assert response.status_code == 200
    email = step(response.json(), "email")
    assert email["state"] == "failed" and needle in email["message"].lower()
    for secret in SECRETS + ("secret-detail", "expired or revoked"):
        assert secret not in response.text
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_a_missing_label_fails_and_revokes_the_grant_we_just_received(api, org, fakes, db_url):
    params = begin(api, org, fakes, label="DoesNotExist")
    email = step(callback(api, org, params["state"]).json(), "email")
    assert email["state"] == "failed" and "label" in email["message"].lower()
    revokes = fakes.requests("POST", "oauth2.googleapis.com")
    assert [r.url.path for r in revokes][-1] == "/revoke" and parse_qs(revokes[-1].content.decode())["token"] == [REFRESH]
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_missing_scope_revokes_what_was_granted(api, org, fakes):
    params = begin(api, org, fakes, scope="openid email")
    callback(api, org, params["state"])
    assert fakes.requests("POST", "oauth2.googleapis.com")[-1].url.path == "/revoke"


def test_missing_refresh_token_asks_for_reconsent_without_revoking_anything(api, org, fakes):
    params = begin(api, org, fakes, refresh_token=None)
    callback(api, org, params["state"])
    assert "/revoke" not in [r.url.path for r in fakes.calls]


def test_disconnect_revokes_at_google_and_removes_the_credential_even_if_revoke_fails(api, org, fakes, db_url):
    params = begin(api, org, fakes, revoke_status=500)
    callback(api, org, params["state"])
    fakes.calls.clear()
    body = api.delete("/v1/onboarding/connect/google", headers=org.headers()).json()
    email = step(body, "email")
    assert email["state"] == "not_started" and email["display"] == {"revocation_unconfirmed": True}
    assert "could not be confirmed" in email["message"]
    [revoke] = fakes.requests("POST", "oauth2.googleapis.com")
    assert revoke.url.path == "/revoke" and parse_qs(revoke.content.decode())["token"] == [REFRESH]
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_disconnect_cancels_a_pending_authorization(api, org, fakes, db_url):
    start(api, org)
    api.delete("/v1/onboarding/connect/google", headers=org.headers())
    assert [r for r in state_rows(db_url) if str(r["organization_id"]) == org.id] == []
