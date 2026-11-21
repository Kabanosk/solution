"""Slack tenant installation (OAuth v2). Read-only scopes, identity verification with `auth.test`; no message is ever
sent as a connection test and no global Socket Mode credential is involved."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from scout.onboarding import http as provider_http

SCOPES = ["channels:read", "channels:history", "groups:read", "groups:history", "users:read"]
AUTH_URL = provider_http.BASES["slack"] + "/oauth/v2/authorize"


class SlackAuthError(RuntimeError):
    """`code`: unavailable | denied | scope_missing | identity_mismatch | unexpected."""

    def __init__(self, code: str, token: str | None = None):
        super().__init__(code)
        self.code = code
        self.token = token  # an installation we received but will not use: the caller revokes it


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({"client_id": client_id, "scope": ",".join(SCOPES),
                                       "redirect_uri": redirect_uri, "state": state})


def _post(path: str, *, data: dict[str, str] | None = None, token: str | None = None,
          timeout: httpx.Timeout = provider_http.TIMEOUT) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with provider_http.client("slack", timeout=timeout) as http:
            r = http.post(path, data=data, headers=headers)
    except httpx.HTTPError:
        raise SlackAuthError("unavailable") from None
    if r.status_code >= 500:
        raise SlackAuthError("unavailable")
    try:
        body = r.json()
    except ValueError:
        raise SlackAuthError("unexpected") from None
    if not isinstance(body, dict):
        raise SlackAuthError("unexpected")
    return body


def install(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange the code, then verify the identity read-only. Returns token, team, scopes, bot user."""
    body = _post("/api/oauth.v2.access", data={"client_id": client_id, "client_secret": client_secret, "code": code,
                                               "redirect_uri": redirect_uri})
    if not body.get("ok"):
        raise SlackAuthError("denied")
    token = body.get("access_token")
    team = body.get("team") or {}
    if not isinstance(token, str) or not team.get("id"):
        raise SlackAuthError("unexpected")
    granted = {s for s in str(body.get("scope", "")).split(",") if s}
    if not set(SCOPES) <= granted:
        raise SlackAuthError("scope_missing", token)
    try:
        identity = _post("/api/auth.test", token=token)
    except SlackAuthError as exc:  # the installation exists even though we could not verify it: the caller revokes it
        raise SlackAuthError(exc.code, token) from None
    if not identity.get("ok"):
        raise SlackAuthError("unexpected", token)
    if identity.get("team_id") != team["id"]:
        raise SlackAuthError("identity_mismatch", token)
    return {"token": token, "team_id": str(team["id"]), "team": str(team.get("name") or identity.get("team") or ""),
            "scopes": sorted(granted), "bot_user_id": body.get("bot_user_id")}


def check_channel(token: str, channel_id: str) -> None:
    """Read-only proof that the installation can read one channel: `conversations.info` and a one-message
    `conversations.history`. Message text is discarded unread. Never joins and never posts.
    Raises SlackAuthError: unavailable | not_in_channel | missing_scope | channel_not_found | unexpected."""
    for method, data in (("conversations.info", {"channel": channel_id}),
                         ("conversations.history", {"channel": channel_id, "limit": "1"})):
        body = _post(f"/api/{method}", data=data, token=token)
        if body.get("ok"):
            continue
        error = str(body.get("error", ""))
        if error == "not_in_channel":
            raise SlackAuthError("not_in_channel")
        if error in ("missing_scope", "no_permission", "invalid_auth", "token_revoked", "account_inactive"):
            raise SlackAuthError("missing_scope")
        if error == "channel_not_found":
            raise SlackAuthError("channel_not_found")
        raise SlackAuthError("unexpected")


def revoke(token: str) -> None:
    """Short timeout. Raises unless Slack confirmed (HTTP 200 and ok:true; an already revoked token counts)."""
    try:
        body = _post("/api/auth.revoke", token=token, timeout=provider_http.REVOKE_TIMEOUT)
    except SlackAuthError:
        raise SlackAuthError("revoke_failed") from None
    if not (body.get("ok") or body.get("error") == "token_revoked"):
        raise SlackAuthError("revoke_failed")
