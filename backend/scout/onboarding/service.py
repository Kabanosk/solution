"""Provider actions. Every write is tenant-scoped through the request's bound connection and recorded in the setup row.

Application prerequisites and provider failures never raise: they become a step result (`needs_configuration` /
`failed`) in an ordinary 2xx setup response. Only genuine request/authorization errors are 4xx. Messages shown to the
user are fixed strings of ours; provider response bodies are never copied into them."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from cfo.tenancy import Tenant
from scout.config import ScoutSettings
from scout.connectors.crypto import derive_key, open_, seal
from scout.onboarding import state
from scout.onboarding.catalog import CONNECTION, MANAGED_BY, PROVIDER_STEP

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Actor:
    """The verified owner session. `session_hash` is sha256 of the bearer token; the token itself is never kept."""
    tenant: Tenant
    session_hash: str


def seal_secret(s: ScoutSettings, organization_id: str, step_id: str, payload: str) -> bytes:
    return seal(payload, derive_key(s.connections_key, organization_id, CONNECTION[step_id][0]))


def open_secret(conn: Any, s: ScoutSettings, organization_id: str, step_id: str) -> str | None:
    cid = CONNECTION[step_id][0]
    row = conn.execute("SELECT ciphertext FROM connection_credentials WHERE connection_id = %s", (cid,)).fetchone()
    if not row:
        return None
    try:
        return open_(row["ciphertext"], derive_key(s.connections_key, organization_id, cid))
    except Exception:  # noqa: BLE001 - wrong key or foreign ciphertext: unusable, never a crash
        return None


def upsert_connection(conn: Any, step_id: str, display_name: str, config: dict[str, Any],
                      ciphertext: bytes | None = None, status: str = "connected") -> None:
    cid, kind, provider = CONNECTION[step_id]
    conn.execute(
        "INSERT INTO connections (id, kind, provider, display_name, config, status, enabled, last_error, last_sync_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, TRUE, NULL, now()) ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, "
        "provider = EXCLUDED.provider, display_name = EXCLUDED.display_name, config = EXCLUDED.config, "
        "status = EXCLUDED.status, enabled = TRUE, last_error = NULL, last_sync_at = now()",
        (cid, kind, provider, display_name, Jsonb({**config, "managed_by": MANAGED_BY}), status))
    if ciphertext is None:
        conn.execute("DELETE FROM connection_credentials WHERE connection_id = %s", (cid,))
    else:
        conn.execute("INSERT INTO connection_credentials (connection_id, ciphertext) VALUES (%s, %s) "
                     "ON CONFLICT (connection_id) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, updated_at = now()",
                     (cid, ciphertext))


PROVIDER_NAMES = {"google": "Google", "slack": "Slack", "bunq": "bunq"}


def has_credential(conn: Any, step_id: str) -> bool:
    """A stored credential is never silently replaced: a provider revocation can end the whole grant (Google revokes the
    authorization for the account, Slack the installation), including a newer token, so the owner disconnects first."""
    return conn.execute("SELECT 1 FROM connection_credentials WHERE connection_id = %s",
                        (CONNECTION[step_id][0],)).fetchone() is not None


def audit(conn: Any, action: str, target: str, resource: str) -> None:
    conn.execute("INSERT INTO audit_log (actor, action, target, resource, decision) VALUES ('dashboard', %s, %s, %s, 'ALLOW')",
                 (action, target, resource))


def fail(conn: Any, actor: Actor, step_id: str, message: str, code: str = "failed") -> dict[str, Any]:
    state.record(conn, actor.tenant.id, step_id, {"state": "failed", "message": message, "code": code})
    return {}


def needs_configuration(conn: Any, actor: Actor, step_id: str, missing: list[str]) -> dict[str, Any]:
    state.record(conn, actor.tenant.id, step_id)  # drops a stale skip/failure; the state is derived from the config
    return {}


# -- built-in invoicing ---------------------------------------------------------------------------------------------
def connect_builtin(conn: Any, actor: Actor, s: ScoutSettings, body: Any) -> dict[str, Any]:
    upsert_connection(conn, "invoicing", "Built-in invoicing", {"system": "built-in"})
    audit(conn, "connect", "invoicing:builtin", "Built-in invoicing")
    state.record(conn, actor.tenant.id, "invoicing")
    return {}


# -- bunq sandbox ----------------------------------------------------------------------------------------------------
BUNQ_MESSAGES = {"unavailable": "bunq sandbox could not be reached. Try again later.",
                 "rejected": "bunq sandbox did not accept the request. Check the sandbox API key and try again.",
                 "unexpected": "bunq sandbox returned an unexpected response."}


def connect_bunq(conn: Any, actor: Actor, s: ScoutSettings, body: Any) -> dict[str, Any]:
    from scout.connectors.bunq import BunqError, BunqSandboxClient
    missing = state.missing_config("bank", s)
    if missing:
        return needs_configuration(conn, actor, "bank", missing)
    client = BunqSandboxClient()
    try:
        api_key = body.api_key or client.create_sandbox_api_key()
        creds = client.register(api_key)
        session = client.login(creds)
        accounts = client.accounts(session)
        if not accounts:
            return fail(conn, actor, "bank", "The bunq sandbox user has no active account.", "no_account")
        client.payments(session, accounts[0]["id"], None)  # prove payment read access, nothing is stored
    except BunqError as exc:
        log.warning("bunq sandbox connect failed: %s", exc)
        return fail(conn, actor, "bank", BUNQ_MESSAGES[exc.kind], exc.kind)
    blob = json.dumps({**creds, **session})
    upsert_connection(conn, "bank", "bunq sandbox", {"mode": "sandbox", "environment": "sandbox",
                                                    "accounts": len(accounts), "read_only": True},
                      seal_secret(s, actor.tenant.id, "bank", blob))
    audit(conn, "connect", "bank:bunq_sandbox", "bunq sandbox (read-only)")
    state.record(conn, actor.tenant.id, "bank")
    return {}


def revoke_bunq(secret: str) -> None:
    from scout.connectors.bunq import BunqSandboxClient
    blob = json.loads(secret)
    BunqSandboxClient().close(blob)


# -- KSeF test environment ---------------------------------------------------------------------------------------------
KSEF_FAILED = ("KSeF test environment did not accept authentication for this NIP, or is unavailable. "
               "Nothing was connected.")


KSEF_WALL_BUDGET = 20.0  # seconds of real elapsed time for the whole login + read, whatever the provider does


def _monotonic() -> float:
    import time
    return time.monotonic()


def _ksef_deadline(budget: float = KSEF_WALL_BUDGET):
    """(sleep, before_request): both stop the flow once the real elapsed time exceeds `budget`. Each socket operation
    is still capped at 10 seconds by the provider client."""
    from scout.connectors.ksef import KsefError
    started = _monotonic()

    def check() -> None:
        if _monotonic() - started > budget:
            raise KsefError("authentication timed out")

    def sleep(seconds: float) -> None:
        import time
        check()
        time.sleep(seconds)
        check()

    return sleep, lambda request: check()


def connect_ksef(conn: Any, actor: Actor, s: ScoutSettings, body: Any) -> dict[str, Any]:
    """TEST only (fixed base URL, `ksef_api_url` is ignored). Reuses KsefClient's self-signed seal login and one read
    query. Never issues invoices, never creates suppliers, never reuses another organization's identity."""
    import datetime as dt

    import httpx

    from cfo.api.errors import ApiError
    from scout.connectors.ksef import TEST_URL, KsefClient, KsefError, valid_nip
    from scout.onboarding import http as provider_http
    if not valid_nip(body.nip):
        raise ApiError(422, "invalid_nip", "That is not a valid NIP (ten digits with a correct check digit).")
    http = provider_http.client("ksef")
    http.headers["X-Error-Format"] = "problem-details"
    sleep, before_request = _ksef_deadline()
    http.event_hooks["request"].append(before_request)
    client = KsefClient(TEST_URL, http=http, sleep=sleep)
    try:
        token = client.login(body.nip, "Scout test company")
        client.verify_read(token, dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
    except (KsefError, httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        log.warning("KSeF test authentication failed: %s", type(exc).__name__)
        return fail(conn, actor, "ksef", KSEF_FAILED, "auth_failed")
    finally:
        http.close()
    upsert_connection(conn, "ksef", "KSeF · test environment",
                      {"env": "test", "api": TEST_URL, "nip": body.nip, "phase": "done", "steps": [], "read_only": True})
    audit(conn, "connect", "invoicing:ksef", "KSeF test environment (read-only)")
    state.record(conn, actor.tenant.id, "ksef")
    return {}


# -- OAuth start (shared) ----------------------------------------------------------------------------------------------
def _start_oauth(conn: Any, actor: Actor, s: ScoutSettings, body: Any, provider: str) -> dict[str, Any] | None:
    """Validate and issue state. Returns None when a prerequisite is missing (the step shows needs_configuration)."""
    from cfo.api.errors import ApiError
    from scout.onboarding.catalog import expected_redirect
    step_id = PROVIDER_STEP[provider]
    if state.missing_config(step_id, s):
        needs_configuration(conn, actor, step_id, state.missing_config(step_id, s))
        return None
    if body.redirect_uri != expected_redirect(s.scout_app_origin, provider):
        raise ApiError(422, "invalid_redirect", "The redirect address is not the configured Scout callback.")
    if has_credential(conn, step_id):
        raise ApiError(409, "already_connected", f"{PROVIDER_NAMES[provider]} is already connected. Disconnect it first, "
                                                 "then connect again.")
    return {}


def connect_google(conn: Any, actor: Actor, s: ScoutSettings, body: Any) -> dict[str, Any]:
    from scout.onboarding import gmail_oauth, oauth
    if _start_oauth(conn, actor, s, body, "google") is None:
        return {}
    verifier, challenge = gmail_oauth.new_pkce()
    label = body.label or "INBOX"
    raw = oauth.issue(conn, s, actor.tenant.id, actor.session_hash, "google", body.redirect_uri, body.browser_proof,
                      label, verifier)
    return {"url": gmail_oauth.authorize_url(s.google_client_id, body.redirect_uri, raw, challenge)}


GOOGLE_MESSAGES = {
    "unavailable": "Google could not be reached. Try again later.",
    "denied": "Google authorization was cancelled or could not be completed.",
    "no_refresh_token": "Google did not grant offline access. Remove Scout in your Google account permissions, then connect again.",
    "scope_missing": "Gmail read-only access was not granted. Connect again and allow it.",
    "label_not_found": "The selected Gmail label was not found in this mailbox.",
    "revoked": "Google rejected the stored authorization. Connect again.",
    "unexpected": "Google returned an unexpected response.",
}


def callback_google(conn: Any, actor: Actor, s: ScoutSettings, body: Any, bound: dict[str, Any]) -> dict[str, Any]:
    from scout.onboarding import gmail_oauth
    if body.error or not body.code:
        return fail(conn, actor, "email", "Google authorization was cancelled or denied.", "cancelled")
    if state.missing_config("email", s):
        return needs_configuration(conn, actor, "email", state.missing_config("email", s))
    if has_credential(conn, "email"):  # connected after this state was issued: exchange nothing, revoke nothing
        return {}
    refresh = None
    try:
        tokens = gmail_oauth.exchange_code(s.google_client_id, s.google_client_secret, body.code, body.redirect_uri,
                                           bound["verifier"] or "")
        refresh = str(tokens["refresh_token"])
        access = gmail_oauth.refresh_access_token(s.google_client_id, s.google_client_secret, refresh)
        mailbox, label = gmail_oauth.read_mailbox(access, bound["label"] or "INBOX")
    except gmail_oauth.MailAuthError as exc:
        refresh = refresh or exc.refresh_token
        if refresh:  # any failure after the exchange: never leave a grant behind that we will not store or use
            try:
                gmail_oauth.revoke(refresh)
            except Exception:  # noqa: BLE001
                log.warning("could not revoke an unused Google grant")
        # No refresh token (the account already holds a grant for this client): revoking the returned access token
        # could end that existing grant, so it is deliberately left alone.
        log.warning("google mailbox authorization failed: %s", exc.code)
        return fail(conn, actor, "email", GOOGLE_MESSAGES[exc.code], exc.code)
    upsert_connection(conn, "email", "Company email (Gmail)",
                      {"email": mailbox, "label": label, "scope": "gmail.readonly", "read_only": True},
                      seal_secret(s, actor.tenant.id, "email", refresh))
    audit(conn, "connect", "mail:gmail", f"Google mailbox {mailbox} (read-only, label {label})")
    state.record(conn, actor.tenant.id, "email")
    return {}


def revoke_google(secret: str) -> None:
    from scout.onboarding import gmail_oauth
    gmail_oauth.revoke(secret)


# -- Slack ---------------------------------------------------------------------------------------------------------------
SLACK_MESSAGES = {
    "unavailable": "Slack could not be reached. Try again later.",
    "denied": "Slack authorization could not be completed.",
    "scope_missing": "Slack did not grant the required read permissions. Install again and allow them.",
    "identity_mismatch": "Slack workspace identity could not be verified. Nothing was connected.",
    "unexpected": "Slack returned an unexpected response.",
}


def connect_slack(conn: Any, actor: Actor, s: ScoutSettings, body: Any) -> dict[str, Any]:
    from scout.onboarding import oauth, slack_oauth
    if _start_oauth(conn, actor, s, body, "slack") is None:
        return {}
    raw = oauth.issue(conn, s, actor.tenant.id, actor.session_hash, "slack", body.redirect_uri, body.browser_proof)
    return {"url": slack_oauth.authorize_url(s.slack_client_id, body.redirect_uri, raw)}


def callback_slack(conn: Any, actor: Actor, s: ScoutSettings, body: Any, bound: dict[str, Any]) -> dict[str, Any]:
    from scout.onboarding import slack_oauth
    if body.error or not body.code:
        return fail(conn, actor, "slack", "Slack authorization was cancelled or denied.", "cancelled")
    if state.missing_config("slack", s):
        return needs_configuration(conn, actor, "slack", state.missing_config("slack", s))
    if has_credential(conn, "slack"):  # connected after this state was issued: exchange nothing, revoke nothing
        return {}
    try:
        found = slack_oauth.install(s.slack_client_id, s.slack_client_secret, body.code, body.redirect_uri)
    except slack_oauth.SlackAuthError as exc:
        if exc.token:
            try:  # do not leave an installation behind that we will not use
                slack_oauth.revoke(exc.token)
            except Exception:  # noqa: BLE001
                log.warning("could not revoke an unused Slack installation")
        log.warning("slack installation failed: %s", exc.code)
        return fail(conn, actor, "slack", SLACK_MESSAGES[exc.code], exc.code)
    # One click: the bot joins every public channel and is invited into the installer's private channels. The
    # installer's user token is only used for those invites and is revoked right after, whatever happened.
    # A fresh installation never inherits an old channel list, so a different workspace starts clean.
    user_token = found.pop("user_token", None)
    try:
        joined = slack_oauth.auto_join(found["token"], found["bot_user_id"], user_token)
    except slack_oauth.SlackAuthError as exc:
        log.warning("slack auto-join failed: %s", exc.code)
        joined = {"channels": [], "private": 0, "skipped": 0}
    finally:
        if user_token:
            try:
                slack_oauth.revoke(user_token)
            except Exception:  # noqa: BLE001
                log.warning("could not revoke the Slack installer token")
    channels = joined["channels"]
    upsert_connection(conn, "slack", "Slack",
                      {"team": found["team"], "team_id": found["team_id"], "channels": channels, "scopes": found["scopes"],
                       "bot_user_id": found["bot_user_id"], "read_only": True, "authorized": True},
                      seal_secret(s, actor.tenant.id, "slack", found["token"]),
                      status="connected" if channels else "disconnected")
    audit(conn, "connect", "chat:slack",
          f"Slack workspace {found['team_id']}: bot joined {len(channels)} channel(s), {joined['private']} private")
    state.record(conn, actor.tenant.id, "slack")
    return {}


SLACK_CHANNEL_MESSAGES = {
    "unavailable": "Slack could not be reached while checking channel {c}. Nothing was changed.",
    "not_in_channel": "Scout's bot is not in channel {c}. Invite the bot to that channel in Slack, or disconnect and connect Slack again to join all channels automatically.",
    "missing_scope": "Slack did not allow reading channel {c}: the installation lacks permission. Install again and allow the read permissions.",
    "channel_not_found": "Channel {c} was not found in this Slack workspace.",
    "unexpected": "Slack returned an unexpected response for channel {c}.",
}


def set_slack_channels(conn: Any, actor: Actor, s: ScoutSettings, channels: list[str]) -> None:
    """Edit the readable channel list by hand. Persisted only after EVERY channel passed a read check; never joins."""
    from scout.onboarding import slack_oauth
    row = conn.execute("SELECT status, config FROM connections WHERE id = 'slack' AND config->>'managed_by' = %s "
                       "AND provider = 'slack_oauth'", (MANAGED_BY,)).fetchone()
    token = open_secret(conn, s, actor.tenant.id, "slack") if row and s.connections_key else None
    if row is None or not token:
        fail(conn, actor, "slack", "Install Slack first, then choose channels.", "not_authorized")
        return
    for channel in channels:
        try:
            slack_oauth.check_channel(token, channel)
        except slack_oauth.SlackAuthError as exc:
            log.warning("slack channel check failed: %s", exc.code)
            state.record(conn, actor.tenant.id, "slack",
                         {"state": "failed" if exc.code == "unavailable" else "needs_configuration",
                          "message": SLACK_CHANNEL_MESSAGES[exc.code].format(c=channel), "code": exc.code})
            return
    config = {**(row["config"] or {}), "channels": channels}
    conn.execute("UPDATE connections SET config = %s, status = %s, last_error = NULL, last_sync_at = now() "
                 "WHERE id = 'slack'", (Jsonb(config), "connected" if channels else "disconnected"))
    audit(conn, "authorize", "chat:slack", f"{len(channels)} Slack channel(s) readable")
    state.record(conn, actor.tenant.id, "slack")


def revoke_slack(secret: str) -> None:
    from scout.onboarding import slack_oauth
    slack_oauth.revoke(secret)


HANDLERS: dict[str, Any] = {"builtin": connect_builtin, "bunq": connect_bunq, "ksef": connect_ksef,
                            "google": connect_google, "slack": connect_slack}
CALLBACKS: dict[str, Any] = {"google": callback_google, "slack": callback_slack}
REVOKERS: dict[str, Any] = {"bunq": revoke_bunq, "google": revoke_google, "slack": revoke_slack}


UNCONFIRMED_MESSAGE = ("Scout's access to {name} was removed from this workspace, but {name} could not be confirmed to have "
                       "revoked it. Remove Scout in your {name} account or workspace settings to be sure it is gone.")


def disconnect(conn: Any, actor: Actor, s: ScoutSettings, provider: str) -> None:
    """Local removal is always done. The provider revocation is verified; when it cannot be confirmed (outage, rejected,
    unreadable credential, missing server key) the owner gets an explicit warning instead of a silent success."""
    step_id = PROVIDER_STEP[provider]
    cid = CONNECTION[step_id][0]
    revoker = REVOKERS.get(provider)
    unconfirmed = False
    if revoker and has_credential(conn, step_id):
        secret = open_secret(conn, s, actor.tenant.id, step_id)
        if not secret:
            unconfirmed = True
        else:
            try:
                revoker(secret)
            except Exception:  # noqa: BLE001 - local removal must survive a provider outage
                log.warning("provider revoke failed for %s; removing local credential anyway", provider)
                unconfirmed = True
    conn.execute("DELETE FROM connections WHERE id = %s AND config->>'managed_by' = %s", (cid, MANAGED_BY))
    conn.execute("DELETE FROM connection_credentials WHERE connection_id = %s", (cid,))
    if provider in ("google", "slack"):
        conn.execute("DELETE FROM scout_auth.setup_oauth_states WHERE organization_id = %s AND provider = %s",
                     (actor.tenant.id, provider))
    audit(conn, "disconnect", f"{CONNECTION[step_id][1]}:{cid}",
          "Connection removed by owner" + ("; provider revocation unconfirmed" if unconfirmed else ""))
    result = ({"state": "not_started", "code": "revocation_unconfirmed",
               "message": UNCONFIRMED_MESSAGE.format(name=PROVIDER_NAMES.get(provider, provider))}
              if unconfirmed else None)
    state.record(conn, actor.tenant.id, step_id, result, reopen=True)
