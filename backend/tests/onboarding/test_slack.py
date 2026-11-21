"""Cycle 6: tenant-bound Slack installation, one click: identity verification, then the bot joins public channels and
is invited into the installer's private channels. Never sends a message."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from google_fixtures import PROOF
from onboarding_helpers import APP_ORIGIN, MASTER_KEY, admin, dump, step, tenant_rows

REDIRECT = APP_ORIGIN + "/api/onboarding/callback/slack"
BOT = "xoxb-secret-bot-token"
SCOPES = "channels:read,channels:history,channels:join,groups:read,groups:history,users:read"
USER = "xoxp-secret-installer-token"
USER_SCOPES = "groups:read,groups:write"


def start(api, org, redirect=REDIRECT, proof=PROOF):
    return api.post("/v1/onboarding/connect/slack", headers=org.headers(),
                    json={"redirect_uri": redirect, "browser_proof": proof})


def callback(api, org, state, **kw):
    body = {"state": state, "redirect_uri": REDIRECT, "browser_proof": PROOF}
    body.update(kw)
    return api.post("/v1/onboarding/callback/slack", headers=org.headers(), json=body)


def script_slack(fakes, *, ok=True, scope=SCOPES, team="T1", test_team="T1", token=BOT, revoke_status=200,
                 user_scope=USER_SCOPES, public=(), bot_private=(), user_private=(), join_errors=(), invite_errors=()):
    """Default workspace: no channels. `public`: [(id, is_member)]; private lists are channel ids."""
    def access(request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["client_id"] == "slack-client-id" and form["client_secret"] == "slack-client-secret"
        assert form["redirect_uri"] == REDIRECT
        if not ok or form["code"] != "good-code":
            return 200, {"ok": False, "error": "invalid_code"}
        return 200, {"ok": True, "access_token": token, "token_type": "bot", "scope": scope, "bot_user_id": "U1",
                     "app_id": "A1", "team": {"id": team, "name": "Acme Workspace"},
                     "authed_user": {"id": "UOWNER", "access_token": USER, "token_type": "user", "scope": user_scope}}

    def auth_test(request):
        assert request.headers["authorization"] == f"Bearer {token}"
        return 200, {"ok": True, "team_id": test_team, "team": "Acme Workspace", "user_id": "U1",
                     "url": "https://acme.slack.com/"}

    fakes.route("POST", "slack.com", "/api/oauth.v2.access", access)
    fakes.route("POST", "slack.com", "/api/auth.test", auth_test)
    fakes.route("POST", "slack.com", "/api/auth.revoke", (revoke_status, {"ok": True, "revoked": True}))

    def form(request):
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}

    def conversations_list(request):
        assert request.headers["authorization"] == f"Bearer {token}"
        assert (form(request)["types"], form(request)["exclude_archived"]) == ("public_channel", "true")
        return 200, {"ok": True, "channels": [{"id": c, "is_member": m} for c, m in public]}

    def users_conversations(request):
        assert form(request)["types"] == "private_channel"
        ids = bot_private if request.headers["authorization"] == f"Bearer {token}" else user_private
        return 200, {"ok": True, "channels": [{"id": c} for c in ids]}

    def join(request):
        ch = form(request)["channel"]
        return 200, {"ok": False, "error": "method_not_supported_for_channel_type"} if ch in join_errors else {"ok": True}

    def invite(request):
        assert request.headers["authorization"] == f"Bearer {USER}" and form(request)["users"] == "U1"
        ch = form(request)["channel"]
        return 200, {"ok": False, "error": "restricted_action"} if ch in invite_errors else {"ok": True}

    fakes.route("POST", "slack.com", "/api/conversations.list", conversations_list)
    fakes.route("POST", "slack.com", "/api/users.conversations", users_conversations)
    fakes.route("POST", "slack.com", "/api/conversations.join", join)
    fakes.route("POST", "slack.com", "/api/conversations.invite", invite)


def begin(api, org, fakes, **script):
    response = start(api, org)
    assert response.status_code == 200, response.text
    script_slack(fakes, **script)
    return parse_qs(urlparse(response.json()["url"]).query)["state"][0], response.json()["url"]


def test_start_builds_a_read_only_install_url_bound_to_the_session(api, org, fakes, db_url):
    response = start(api, org)
    url = urlparse(response.json()["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "slack.com", "/oauth/v2/authorize")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == "slack-client-id" and q["redirect_uri"] == REDIRECT
    assert set(q["scope"].split(",")) == set(SCOPES.split(",")) and q["user_scope"] == USER_SCOPES
    assert not any(s in q["scope"] for s in ("chat:write", "im:write", "files:write"))
    assert fakes.calls == []
    with admin(db_url) as conn:
        [row] = conn.execute("SELECT * FROM scout_auth.setup_oauth_states WHERE organization_id=%s", (org.id,)).fetchall()
    assert row["provider"] == "slack" and row["redirect_uri"] == REDIRECT
    assert q["state"] not in json.dumps(row, default=str)
    assert step(api.get("/v1/onboarding", headers=org.headers()).json(), "slack")["state"] == "connecting"


def test_missing_slack_configuration_is_needs_configuration(api, org, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, slack_client_id="")
    response = start(api, org)
    assert "url" not in response.json()
    slack = step(response.json(), "slack")
    assert slack["state"] == "needs_configuration" and "SLACK_CLIENT_ID" in slack["message"]


def test_redirect_must_be_the_slack_callback(api, org):
    assert start(api, org, redirect=APP_ORIGIN + "/api/onboarding/callback/google").status_code == 422


def test_callback_verifies_workspace_identity_and_seals_the_bot_token(api, org, fakes, db_url):
    state, _ = begin(api, org, fakes)
    response = callback(api, org, state, code="good-code")
    assert response.status_code == 200, response.text
    slack = step(response.json(), "slack")
    # an empty workspace: nothing to join, so the owner is told no channel could be joined
    assert slack["state"] == "needs_configuration" and "could not join any channel" in slack["message"].lower()
    assert slack["display"] == {"authorized": True, "workspace": "Acme Workspace", "team_id": "T1"}
    assert BOT not in response.text and "slack-client-secret" not in response.text
    assert [r.url.path for r in fakes.calls] == ["/api/oauth.v2.access", "/api/auth.test", "/api/conversations.list",
                                                 "/api/users.conversations", "/api/users.conversations", "/api/auth.revoke"]
    assert fakes.calls[-1].headers["authorization"] == f"Bearer {USER}"  # only the installer token is revoked
    assert not [r for r in fakes.calls if "chat." in r.url.path]  # no message sent
    from scout.connectors.crypto import derive_key, open_
    [cred] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='slack'")
    assert open_(cred["ciphertext"], derive_key(MASTER_KEY, org.id, "slack")) == BOT
    [row] = tenant_rows(db_url, org, "SELECT * FROM connections WHERE id='slack'")
    assert (row["kind"], row["provider"], row["status"]) == ("chat", "slack_oauth", "disconnected")
    assert row["config"]["team_id"] == "T1" and row["config"]["channels"] == []
    assert set(row["config"]["scopes"]) == set(SCOPES.split(","))
    assert BOT not in dump(row)


def test_workspaces_are_isolated_per_organization(api, org, other_org, fakes, db_url):
    state, _ = begin(api, org, fakes)
    callback(api, org, state, code="good-code")
    assert step(api.get("/v1/onboarding", headers=other_org.headers()).json(), "slack")["state"] == "not_started"
    assert tenant_rows(db_url, other_org, "SELECT id FROM connections") == []
    stolen = callback(api, other_org, state, code="good-code")
    assert stolen.status_code == 400


def test_state_is_single_use_and_cancel_is_consumed(api, org, fakes):
    state, _ = begin(api, org, fakes)
    cancelled = callback(api, org, state, error="access_denied")
    assert step(cancelled.json(), "slack")["state"] == "failed" and "cancel" in step(cancelled.json(), "slack")["message"].lower()
    assert callback(api, org, state, code="good-code").status_code == 400
    assert fakes.calls == []


@pytest.mark.parametrize("script,needle", [
    ({"ok": False}, "could not be completed"),
    ({"scope": "channels:read"}, "permissions"),
    ({"test_team": "T999"}, "identity"),
])
def test_failures_store_nothing_and_never_echo_secrets(api, org, fakes, db_url, script, needle):
    state, _ = begin(api, org, fakes, **script)
    response = callback(api, org, state, code="good-code")
    assert response.status_code == 200
    slack = step(response.json(), "slack")
    assert slack["state"] == "failed" and needle in slack["message"].lower()
    assert BOT not in response.text and "invalid_code" not in response.text
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_an_installation_we_will_not_use_is_revoked(api, org, fakes):
    state, _ = begin(api, org, fakes, test_team="T999")
    callback(api, org, state, code="good-code")
    assert fakes.requests("POST", "slack.com")[-1].url.path == "/api/auth.revoke"


def test_disconnect_revokes_and_removes_even_if_slack_is_down(api, org, fakes, db_url):
    state, _ = begin(api, org, fakes, revoke_status=500)
    callback(api, org, state, code="good-code")
    fakes.calls.clear()
    body = api.delete("/v1/onboarding/connect/slack", headers=org.headers()).json()
    result = step(body, "slack")
    assert result["state"] == "not_started" and result["display"] == {"revocation_unconfirmed": True}
    assert "could not be confirmed" in result["message"]
    [revoke] = fakes.calls
    assert revoke.url.path == "/api/auth.revoke" and revoke.headers["authorization"] == f"Bearer {BOT}"
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


# -- channel authorization ---------------------------------------------------------------------------------------
C1, C2, G1 = "C0123456789", "C0987654321", "G0123456789"


def script_channels(fakes, *, errors=None, status=200):
    errors = errors or {}

    def info(request):
        ch = parse_qs(request.content.decode())["channel"][0]
        assert request.headers["authorization"] == f"Bearer {BOT}"
        if status != 200:
            return status, {}
        return 200, {"ok": True, "channel": {"id": ch, "name": "finance"}} if ch not in errors else {"ok": False, "error": errors[ch]}

    def history(request):
        form = parse_qs(request.content.decode())
        ch = form["channel"][0]
        assert form["limit"] == ["1"]
        if ch in errors:
            return 200, {"ok": False, "error": errors[ch]}
        return 200, {"ok": True, "messages": [{"text": "TOP SECRET PAYROLL", "ts": "1.0"}]}

    fakes.route("POST", "slack.com", "/api/conversations.info", info)
    fakes.route("POST", "slack.com", "/api/conversations.history", history)


def authorized(api, org, fakes):
    state, _ = begin(api, org, fakes)
    callback(api, org, state, code="good-code")
    fakes.calls.clear()


def put(api, org, channels, **kw):
    return api.put("/v1/onboarding/slack/channels", headers=org.headers(**kw), json={"channels": channels})


def test_choosing_channels_verifies_read_access_then_connects(api, org, fakes, db_url, caplog):
    authorized(api, org, fakes)
    script_channels(fakes)
    response = put(api, org, [C1, G1, C1])
    assert response.status_code == 200, response.text
    slack = step(response.json(), "slack")
    assert slack["state"] == "connected"
    assert slack["display"] == {"authorized": True, "workspace": "Acme Workspace", "team_id": "T1", "channels": "2",
                                "channel_ids": "C0123456789, G0123456789"}
    assert "TOP SECRET" not in response.text and BOT not in response.text and "TOP SECRET" not in caplog.text
    paths = [r.url.path for r in fakes.calls]
    assert sorted(paths) == sorted(["/api/conversations.info", "/api/conversations.history"] * 2)
    assert not any("join" in p or "chat." in p for p in paths)  # never joins or sends
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["status"] == "connected" and row["config"]["channels"] == [C1, G1]
    from scout.connectors.crypto import derive_key, open_
    [cred] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='slack'")
    assert open_(cred["ciphertext"], derive_key(MASTER_KEY, org.id, "slack")) == BOT


def test_a_channel_the_bot_is_not_in_asks_the_owner_to_invite_it_and_persists_nothing(api, org, fakes, db_url):
    authorized(api, org, fakes)
    script_channels(fakes, errors={C2: "not_in_channel"})
    response = put(api, org, [C1, C2])
    slack = step(response.json(), "slack")
    assert response.status_code == 200 and slack["state"] == "needs_configuration"
    assert "invite" in slack["message"].lower() and C2 in slack["message"]
    assert "TOP SECRET" not in response.text
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["status"] == "disconnected" and row["config"]["channels"] == []
    assert not any("join" in r.url.path for r in fakes.calls)


@pytest.mark.parametrize("error,needle", [("missing_scope", "permission"), ("channel_not_found", "not found")])
def test_other_read_failures_are_safe_prerequisites(api, org, fakes, error, needle):
    authorized(api, org, fakes)
    script_channels(fakes, errors={C1: error})
    slack = step(put(api, org, [C1]).json(), "slack")
    assert slack["state"] in ("needs_configuration", "failed") and needle in slack["message"].lower()
    assert "TOP SECRET" not in dump(slack)


def test_a_slack_outage_is_failed_and_changes_nothing(api, org, fakes, db_url):
    authorized(api, org, fakes)
    script_channels(fakes, status=503)
    assert step(put(api, org, [C1]).json(), "slack")["state"] == "failed"
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["config"]["channels"] == [] and row["status"] == "disconnected"


@pytest.mark.parametrize("channels", [["general"], ["c0123456789"], ["D0123456789"], ["C1"], [C1] * 0 + [f"C{i:010d}" for i in range(101)],
                                      "C0123456789", [123]])
def test_channel_ids_are_validated_and_capped(api, org, fakes, channels):
    authorized(api, org, fakes)
    assert put(api, org, channels).status_code == 422
    assert fakes.calls == []


def test_only_the_owner_may_choose_channels(api, org, fakes):
    authorized(api, org, fakes)
    denied = put(api, org, [C1], member=True)
    assert denied.status_code == 403 and fakes.calls == []


def test_without_an_authorized_workspace_nothing_is_called(api, org, fakes):
    response = put(api, org, [C1])
    assert response.status_code == 200 and step(response.json(), "slack")["state"] != "connected"
    assert fakes.calls == []


def test_choosing_no_channels_turns_chat_reading_off(api, org, fakes, db_url):
    authorized(api, org, fakes)
    script_channels(fakes)
    put(api, org, [C1])
    slack = step(put(api, org, []).json(), "slack")
    assert slack["state"] == "needs_configuration"
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["status"] == "disconnected" and row["config"]["channels"] == []


def test_authorizing_an_installation_again_clears_the_channels(api, org, fakes, db_url):
    authorized(api, org, fakes)
    script_channels(fakes)
    put(api, org, [C1])
    assert start(api, org).status_code == 409  # a stored installation is never silently replaced
    api.delete("/v1/onboarding/connect/slack", headers=org.headers())
    state, _ = begin(api, org, fakes, team="T2", test_team="T2")
    body = callback(api, org, state, code="good-code").json()
    assert step(body, "slack")["state"] == "needs_configuration"
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["config"]["channels"] == [] and row["config"]["team_id"] == "T2" and row["status"] == "disconnected"


def test_unauthenticated_and_other_organizations_cannot_use_the_endpoint(api, org, other_org, fakes):
    authorized(api, org, fakes)
    assert api.put("/v1/onboarding/slack/channels", json={"channels": [C1]}).status_code == 401
    script_channels(fakes)
    put(api, other_org, [C1])
    assert fakes.calls == []  # other org has no authorized workspace: no token to use, org's token untouched


# -- one-click channel access --------------------------------------------------------------------------------------
G2, C3 = "G0987654321", "C0111111111"


def test_one_click_joins_public_channels_and_invites_the_bot_into_private_ones(api, org, fakes, db_url):
    state, _ = begin(api, org, fakes, public=[(C1, False), (C2, True)], bot_private=[G2], user_private=[G1, G2])
    response = callback(api, org, state, code="good-code")
    slack = step(response.json(), "slack")
    assert slack["state"] == "connected" and slack["display"]["channels"] == "4"
    paths = [(r.url.path, parse_qs(r.content.decode()).get("channel", [""])[0]) for r in fakes.calls]
    assert ("/api/conversations.join", C1) in paths and ("/api/conversations.join", C2) not in paths  # already in C2
    assert ("/api/conversations.invite", G1) in paths and ("/api/conversations.invite", G2) not in paths  # already in G2
    assert not [r for r in fakes.calls if "chat." in r.url.path]  # joining posts nothing
    revoke = fakes.calls[-1]
    assert revoke.url.path == "/api/auth.revoke" and revoke.headers["authorization"] == f"Bearer {USER}"
    [row] = tenant_rows(db_url, org, "SELECT status, config FROM connections WHERE id='slack'")
    assert row["status"] == "connected" and row["config"]["channels"] == [C1, C2, G1, G2]
    assert USER not in dump(row) and USER not in response.text
    from scout.connectors.crypto import derive_key, open_
    [cred] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='slack'")
    assert open_(cred["ciphertext"], derive_key(MASTER_KEY, org.id, "slack")) == BOT  # the user token is never stored


def test_one_refused_channel_does_not_block_the_others(api, org, fakes, db_url):
    state, _ = begin(api, org, fakes, public=[(C1, False), (C3, False)], user_private=[G1, G2],
                     join_errors=[C3], invite_errors=[G2])
    assert step(callback(api, org, state, code="good-code").json(), "slack")["state"] == "connected"
    [row] = tenant_rows(db_url, org, "SELECT config FROM connections WHERE id='slack'")
    assert row["config"]["channels"] == [C1, G1]


def test_without_installer_scopes_only_public_channels_are_joined(api, org, fakes, db_url):
    state, _ = begin(api, org, fakes, public=[(C1, False)], user_private=[G1], user_scope="")
    assert step(callback(api, org, state, code="good-code").json(), "slack")["state"] == "connected"
    assert not [r for r in fakes.calls if r.url.path == "/api/conversations.invite"]
    [row] = tenant_rows(db_url, org, "SELECT config FROM connections WHERE id='slack'")
    assert row["config"]["channels"] == [C1]
