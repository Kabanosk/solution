"""T057: unused grants are cleaned up on every failed verification path, revocation is verified and honest, completed
setup reopens, a stored credential is never silently replaced, and KSeF is bounded by wall time."""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

import test_slack as slack
from google_fixtures import PROOF, REDIRECT, REFRESH, callback, parse_start, script_google, start
from onboarding_helpers import admin, step, tenant_rows

UNCONFIRMED = "revocation_unconfirmed"


def google_begin(api, org, fakes, **script):
    params = parse_start(start(api, org).json()["url"])
    script_google(fakes, params["code_challenge"], **script)
    return params


def google_revokes(fakes):
    return [parse_qs(r.content.decode())["token"][0] for r in fakes.requests("POST", "oauth2.googleapis.com")
            if r.url.path == "/revoke"]


def connect_google(api, org, fakes, **script):
    params = google_begin(api, org, fakes, **script)
    return step(callback(api, org, params["state"]).json(), "email")


def connect_slack(api, org, fakes, **script):
    state, _ = slack.begin(api, org, fakes, **script)
    return step(slack.callback(api, org, state, code="good-code").json(), "slack")


# -- 1. every failed post-token path cleans up the grant we will not use ---------------------------------------------
@pytest.mark.parametrize("script", [{"profile_status": 503}, {"refresh_status": 503}, {"refresh_status": 400},
                                    {"profile_status": 403}, {"scope": "openid email"}, {"labels": []}])
def test_google_grant_is_revoked_on_every_failed_verification(api, org, fakes, db_url, script):
    assert connect_google(api, org, fakes, **script)["state"] == "failed"
    assert google_revokes(fakes) == [REFRESH]
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_google_without_refresh_token_revokes_nothing_because_that_could_kill_an_existing_grant(api, org, fakes):
    assert connect_google(api, org, fakes, refresh_token=None)["state"] == "failed"
    assert google_revokes(fakes) == []


def test_a_failed_google_cleanup_does_not_leak_or_change_the_user_facing_failure(api, org, fakes):
    result = connect_google(api, org, fakes, profile_status=503, revoke_status=500)
    assert result["state"] == "failed" and "could not be reached" in result["message"]
    assert REFRESH not in str(result)


def test_slack_installation_is_revoked_when_auth_test_is_unavailable(api, org, fakes, db_url):
    state, _ = slack.begin(api, org, fakes)
    fakes.route("POST", "slack.com", "/api/auth.test", (503, {}))
    result = step(slack.callback(api, org, state, code="good-code").json(), "slack")
    assert result["state"] == "failed" and "could not be reached" in result["message"]
    [revoke] = [c for c in fakes.calls if c.url.path == "/api/auth.revoke"]
    assert revoke.headers["authorization"] == f"Bearer {slack.BOT}"
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_slack_installation_is_revoked_when_auth_test_answers_not_ok_or_garbage(api, org, fakes):
    state, _ = slack.begin(api, org, fakes)
    fakes.route("POST", "slack.com", "/api/auth.test", (200, {"ok": False, "error": "invalid_auth"}))
    slack.callback(api, org, state, code="good-code")
    assert [c.url.path for c in fakes.calls].count("/api/auth.revoke") == 1


# -- 2. revocation is verified; local removal is kept; the owner is told honestly ----------------------------------
def revoke_cases():
    return [("google", dict(revoke_status=500), connect_google, "email", "bank"),
            ("slack", dict(revoke_status=500), connect_slack, "slack", "bank")]


@pytest.mark.parametrize("provider,script,connect,step_id,_", revoke_cases())
def test_disconnect_with_provider_outage_removes_locally_and_warns(api, org, fakes, db_url, provider, script, connect,
                                                                   step_id, _):
    assert connect(api, org, fakes, **script)["state"] in ("connected", "needs_configuration")  # Slack: authorized only
    body = api.delete(f"/v1/onboarding/connect/{provider}", headers=org.headers()).json()
    result = step(body, step_id)
    assert result["state"] == "not_started" and result["display"] == {UNCONFIRMED: True}
    assert "removed" in result["message"] and "could not be confirmed" in result["message"]
    assert provider.capitalize() in result["message"]
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []
    audit = tenant_rows(db_url, org, "SELECT resource FROM audit_log WHERE action='disconnect'")
    assert audit and "unconfirmed" in audit[0]["resource"]
    assert REFRESH not in str(body) and slack.BOT not in str(body)


def test_slack_ok_false_on_revoke_is_not_a_confirmed_revocation(api, org, fakes, db_url):
    connect_slack(api, org, fakes)
    fakes.route("POST", "slack.com", "/api/auth.revoke", (200, {"ok": False, "error": "internal_error"}))
    result = step(api.delete("/v1/onboarding/connect/slack", headers=org.headers()).json(), "slack")
    assert result["display"] == {UNCONFIRMED: True}
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_google_error_status_on_revoke_is_not_a_confirmed_revocation(api, org, fakes):
    connect_google(api, org, fakes)
    fakes.route("POST", "oauth2.googleapis.com", "/revoke", (400, {"error": "invalid_token"}))
    result = step(api.delete("/v1/onboarding/connect/google", headers=org.headers()).json(), "email")
    assert result["display"] == {UNCONFIRMED: True}


def test_confirmed_revocation_has_no_warning(api, org, fakes):
    connect_google(api, org, fakes)
    result = step(api.delete("/v1/onboarding/connect/google", headers=org.headers()).json(), "email")
    assert result["state"] == "not_started" and result["message"] is None and result["display"] == {}


def test_without_the_server_key_the_credential_is_still_removed_and_the_owner_is_warned(api, org, fakes, db_url,
                                                                                          settings_factory):
    connect_google(api, org, fakes)
    api.state["settings"] = settings_factory(database_url=db_url, connections_key="")
    fakes.calls.clear()
    result = step(api.delete("/v1/onboarding/connect/google", headers=org.headers()).json(), "email")
    assert result["display"] == {UNCONFIRMED: True} and fakes.calls == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_unconfirmed_warning_clears_on_the_next_connect_attempt(api, org, fakes):
    connect_google(api, org, fakes, revoke_status=500)
    api.delete("/v1/onboarding/connect/google", headers=org.headers())
    start(api, org)
    email = step(api.get("/v1/onboarding", headers=org.headers()).json(), "email")
    assert email["display"] == {} and email["state"] == "connecting"


def test_disconnecting_a_never_connected_provider_gives_no_false_warning(api, org):
    result = step(api.delete("/v1/onboarding/connect/slack", headers=org.headers()).json(), "slack")
    assert result["display"] == {} and result["message"] is None


# -- 3. a completed setup reopens when it stops being complete -------------------------------------------------------
IDS = ("bank", "invoicing", "ksef", "email", "slack", "network")


def complete_everything(api, org):
    revision = api.get("/v1/onboarding", headers=org.headers()).json()["revision"]
    for sid in IDS:
        if step(api.get("/v1/onboarding", headers=org.headers()).json(), sid)["state"] == "connected":
            continue
        revision = api.patch("/v1/onboarding", headers=org.headers(),
                             json={"revision": revision, "action": "step", "step": sid, "state": "skipped"}).json()["revision"]
    done = api.patch("/v1/onboarding", headers=org.headers(), json={"revision": revision, "action": "complete"}).json()
    assert done["status"] == "completed"


def test_disconnect_reopens_a_completed_setup(api, org, fakes, db_url):
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    complete_everything(api, org)
    body = api.delete("/v1/onboarding/connect/builtin", headers=org.headers()).json()
    assert step(body, "invoicing")["state"] == "not_started" and body["status"] == "in_progress"
    assert admin(db_url).execute("SELECT status FROM scout_auth.workspace_setup WHERE organization_id=%s",
                                 (org.id,)).fetchone()["status"] == "in_progress"


def test_a_runtime_connection_error_is_reported_as_incomplete_without_writing(api, org, db_url):
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    complete_everything(api, org)
    tenant_rows(db_url, org, "UPDATE connections SET status='error', last_error='boom' WHERE id='invoicing' RETURNING 1")
    body = api.get("/v1/onboarding", headers=org.headers()).json()
    assert step(body, "invoicing")["state"] == "failed" and body["status"] == "in_progress"


def test_dismissed_stays_dismissed_when_a_step_fails(api, org, db_url):
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    revision = api.get("/v1/onboarding", headers=org.headers()).json()["revision"]
    api.patch("/v1/onboarding", headers=org.headers(), json={"revision": revision, "action": "skip"})
    body = api.delete("/v1/onboarding/connect/builtin", headers=org.headers()).json()
    assert body["status"] == "dismissed"


# -- 4. a stored credential is never silently replaced ---------------------------------------------------------------
@pytest.mark.parametrize("provider", ["google", "slack"])
def test_connect_is_refused_while_a_credential_is_stored(api, org, fakes, provider):
    (connect_google if provider == "google" else connect_slack)(api, org, fakes)
    fakes.calls.clear()
    body = {"redirect_uri": REDIRECT.replace("google", provider), "browser_proof": PROOF}
    response = api.post(f"/v1/onboarding/connect/{provider}", headers=org.headers(), json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_connected"
    assert "Disconnect" in response.json()["error"]["message"]
    assert fakes.calls == []
    api.delete(f"/v1/onboarding/connect/{provider}", headers=org.headers())
    assert api.post(f"/v1/onboarding/connect/{provider}", headers=org.headers(), json=body).status_code == 200


def test_a_callback_for_a_state_issued_before_a_connection_existed_cannot_replace_it(api, org, fakes, db_url):
    params = google_begin(api, org, fakes)
    with admin(db_url) as conn:
        conn.execute(f'SET search_path = "{org.schema}"')
        conn.execute("INSERT INTO connections (id, kind, provider, display_name, config, status) VALUES "
                     "('gmail','mail','gmail_oauth','Company email (Gmail)', '{\"managed_by\":\"onboarding\"}', 'connected')")
        conn.execute("INSERT INTO connection_credentials (connection_id, ciphertext) VALUES ('gmail', '\\x01')")
    fakes.calls.clear()
    result = step(callback(api, org, params["state"]).json(), "email")
    assert fakes.calls == []  # nothing exchanged, nothing revoked: a revoke would kill the working grant
    assert tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials")[0]["ciphertext"] == b"\x01"
    assert result["state"] == "connected"  # the working connection is untouched and still reported


# -- 5. KSeF is bounded by wall time ---------------------------------------------------------------------------------
def test_ksef_budget_counts_wall_time_not_only_sleeps(api, org, fakes, monkeypatch, db_url):
    import test_onboarding_ksef as ksef
    from scout.onboarding import service
    ksef.script_ksef(fakes)
    clock = iter([0.0, 0.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
    monkeypatch.setattr(service, "_monotonic", lambda: next(clock, 99.0))
    result = step(ksef.connect(api, org).json(), "ksef")
    assert result["state"] == "failed"
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert len(fakes.calls) < 5  # stopped as soon as the budget was exceeded


def test_ksef_success_path_is_unchanged(api, org, fakes):
    import test_onboarding_ksef as ksef
    ksef.script_ksef(fakes)
    assert step(ksef.connect(api, org).json(), "ksef")["state"] == "sandbox_connected"
    assert [r.url.path for r in fakes.calls][-1] == "/v2/invoices/query/metadata"
