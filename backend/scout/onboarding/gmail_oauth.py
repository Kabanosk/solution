"""Google mailbox authorization (gmail.readonly, offline). Entirely separate from dashboard identity sign-in: its own
OAuth client (GOOGLE_CLIENT_ID/SECRET), its own scope, its own redirect. Endpoints are the documented Google ones."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from scout.onboarding import http as provider_http

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = provider_http.BASES["google_auth"] + "/o/oauth2/v2/auth"


class MailAuthError(RuntimeError):
    """`code`: unavailable | denied | no_refresh_token | scope_missing | label_not_found | revoked | unexpected."""

    def __init__(self, code: str, refresh_token: str | None = None):
        super().__init__(code)
        self.code = code
        self.refresh_token = refresh_token  # a grant we received but will not use: the caller revokes it


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
              "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256"}
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_call(form: dict[str, str]) -> dict[str, Any]:
    try:
        with provider_http.client("google_token") as http:
            r = http.post("/token", data=form)
    except httpx.HTTPError:
        raise MailAuthError("unavailable") from None
    if r.status_code >= 500:
        raise MailAuthError("unavailable")
    try:
        body = r.json()
    except ValueError:
        raise MailAuthError("unexpected") from None
    if r.status_code >= 400:
        raise MailAuthError("denied" if form["grant_type"] == "authorization_code" else "revoked")
    if not isinstance(body, dict) or not body.get("access_token"):
        raise MailAuthError("unexpected")
    return body


def _scopes(body: dict[str, Any]) -> set[str]:
    return set(str(body.get("scope", "")).split())


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
    body = _token_call({"grant_type": "authorization_code", "code": code, "client_id": client_id,
                        "client_secret": client_secret, "redirect_uri": redirect_uri, "code_verifier": verifier})
    if SCOPE not in _scopes(body):
        raise MailAuthError("scope_missing", body.get("refresh_token"))
    if not body.get("refresh_token"):
        raise MailAuthError("no_refresh_token")
    return body


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """A fresh access token from the stored refresh token. `revoked` covers expired/revoked/invalid_grant."""
    body = _token_call({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id,
                        "client_secret": client_secret})
    if SCOPE not in _scopes(body):
        raise MailAuthError("scope_missing")
    return str(body["access_token"])


def read_mailbox(access_token: str, label: str) -> tuple[str, str]:
    """Read-only verification: the authorized mailbox address and the selected label (must exist)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with provider_http.client("gmail") as http:
            profile = http.get("/gmail/v1/users/me/profile", headers=headers)
            if profile.status_code >= 500:
                raise MailAuthError("unavailable")
            if profile.status_code >= 400:
                raise MailAuthError("unexpected")
            labels = http.get("/gmail/v1/users/me/labels", headers=headers)
            if labels.status_code >= 500:
                raise MailAuthError("unavailable")
            if labels.status_code >= 400:
                raise MailAuthError("unexpected")
        email = profile.json().get("emailAddress")
        found = [x for x in labels.json().get("labels", []) if str(x.get("name", "")).casefold() == label.casefold()]
    except httpx.HTTPError:
        raise MailAuthError("unavailable") from None
    except (ValueError, AttributeError):
        raise MailAuthError("unexpected") from None
    if not email:
        raise MailAuthError("unexpected")
    if not found:
        raise MailAuthError("label_not_found")
    return str(email), str(found[0]["name"])


def revoke(token: str) -> None:
    """Short timeout. Raises unless Google confirmed the revocation (HTTP 200); callers decide what a failure means.
    Revoking a refresh token ends the whole authorization of that Google account for this OAuth client."""
    with provider_http.client("google_token", timeout=provider_http.REVOKE_TIMEOUT) as http:
        r = http.post("/revoke", data={"token": token})
    if r.status_code != 200:
        raise MailAuthError("revoke_failed")
