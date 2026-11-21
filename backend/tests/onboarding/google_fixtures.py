"""Google OAuth/Gmail HTTP fixtures (documented endpoints). The token fake verifies PKCE for real."""
from __future__ import annotations

import base64
import hashlib
import hashlib as _h
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg

from onboarding_helpers import APP_ORIGIN

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
REFRESH = "rt-secret-1"
REDIRECT = APP_ORIGIN + "/api/onboarding/callback/google"
PROOF = "p" * 40
LABELS = [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "Label_1", "name": "Invoices", "type": "user"}]


def parse_start(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
    return {k: v[0] for k, v in parse_qs(parsed.query).items()}


def start(api, org, *, label=None, redirect=REDIRECT, proof=PROOF):
    body = {"redirect_uri": redirect, "browser_proof": proof}
    if label is not None:
        body["label"] = label
    return api.post("/v1/onboarding/connect/google", headers=org.headers(), json=body)


def callback(api, org, state, *, code="good-code", error=None, redirect=REDIRECT, proof=PROOF, provider="google", member=False):
    body = {"state": state, "redirect_uri": redirect, "browser_proof": proof}
    if error:
        body["error"] = error
    else:
        body["code"] = code
    return api.post(f"/v1/onboarding/callback/{provider}", headers=org.headers(member=member), json=body)


def script_google(fakes, challenge: str | None, *, scope=SCOPE, refresh_token: str | None = REFRESH,
                  exchange_status: int = 200, refresh_status: int = 200, profile_status: int = 200,
                  labels=None, revoke_status: int = 200, email: str = "boss@acme.example") -> None:
    labels = LABELS if labels is None else labels

    def token(request: httpx.Request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["client_id"] == "google-client-id" and form["client_secret"] == "google-client-secret"
        if form["grant_type"] == "authorization_code":
            if exchange_status != 200:
                return exchange_status, {"error": "invalid_grant", "error_description": "secret-detail good-code"}
            verifier = form["code_verifier"]
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if form["code"] != "good-code" or digest != challenge or form["redirect_uri"] != REDIRECT:
                return 400, {"error": "invalid_grant"}
            body = {"access_token": "at-secret-1", "expires_in": 3599, "scope": scope, "token_type": "Bearer"}
            if refresh_token:
                body["refresh_token"] = refresh_token
            return 200, body
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == REFRESH
        if refresh_status != 200:
            return refresh_status, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        return 200, {"access_token": "at-secret-2", "expires_in": 3599, "scope": scope, "token_type": "Bearer"}

    def authorized(body):
        def respond(request: httpx.Request):
            if request.headers.get("authorization") != "Bearer at-secret-2":
                return 401, {"error": {"code": 401}}
            return profile_status, body if profile_status == 200 else {"error": {"code": profile_status}}
        return respond

    fakes.route("POST", "oauth2.googleapis.com", "/token", token)
    fakes.route("POST", "oauth2.googleapis.com", "/revoke", (revoke_status, {}))
    fakes.route("GET", "gmail.googleapis.com", "/gmail/v1/users/me/profile",
                authorized({"emailAddress": email, "messagesTotal": 10, "historyId": "1"}))
    fakes.route("GET", "gmail.googleapis.com", "/gmail/v1/users/me/labels", authorized({"labels": labels}))


def extra_session(db_url: str, org) -> str:
    """A second, different session of the same owner (another browser/device)."""
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with psycopg.connect(db_url) as conn:
        conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) "
                     "VALUES (%s,%s,%s,now()+interval '1 hour')", (_h.sha256(token.encode()).hexdigest(), org.id, org.id))
    return token
