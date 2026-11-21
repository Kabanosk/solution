"""bunq SANDBOX bank connection, read-only.

Flow (official bunq API definitions, github.com/bunq/doc swagger.json): `POST /installation` (our public key) ->
`POST /device-server` (registers the API key for this installation) -> `POST /session-server` (session token) ->
`GET /user/{id}/monetary-account` and `GET /user/{id}/monetary-account/{id}/payment` (paginated with `count`/`older_id`).
The base URL is the fixed sandbox host from `scout.onboarding.http`; there is no write method for payments.

Signing (bunq doc "Signing" section, cross-checked against the official Python and Java SDKs):
- `POST /installation` returns the server public key in its own body. It cannot be verified (nothing is pinned yet),
  so initial trust is bound to HTTPS on the fixed sandbox host. `POST /sandbox-user-company` is unsigned too.
- Every later response is signed by bunq: `X-Bunq-Server-Signature` = base64(RSA PKCS#1 v1.5 / SHA-256) made with the
  server key. The documented signed data is the response body. Both official SDKs also accept the older form
  `status\\n` + sorted `X-Bunq-*` response headers (without the signature) + `\\n` + body; we accept exactly these two.
  A missing, malformed or non-verifying signature on any successful response is rejected (fail closed).
  Error (4xx/5xx) responses are not verified by the SDKs either; they are never used as data here.
- Requests: the documented rule is that only the body is signed (`X-Bunq-Client-Signature`), and it is mandatory only
  for opening a session and for payments. Both SDKs nevertheless sign every request that carries a session token, with
  an empty body for GET/DELETE; we do the same. That is not documented as required for GET: unverified live.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from scout.models import BankTxn
from scout.onboarding import http as provider_http

log = logging.getLogger("scout.bunq")

PAGE = 200  # bunq maximum `count`
MAX_PAGES = 50
MIN_KEY_BITS = 2048  # bunq requires 2048-bit RSA; refuse anything weaker as a pinned server key
SERVER_SIGNATURE = "X-Bunq-Server-Signature"


class BunqError(RuntimeError):
    """`kind`: unavailable (network/5xx), rejected (4xx), unexpected (shape/trust). Never carries provider bodies.

    `reason` is a fixed non-secret code (e.g. signature_missing, signature_invalid) for diagnostics and tests."""

    def __init__(self, kind: str, status: int | None = None, reason: str | None = None):
        super().__init__(f"bunq {kind}" + (f" ({status})" if status else "") + (f" [{reason}]" if reason else ""))
        self.kind, self.status, self.reason = kind, status, reason


@dataclass(frozen=True)
class _Trust:
    """Pinned installation material: the server key that must verify every response, our key that signs requests."""
    server_key: rsa.RSAPublicKey
    private_key: rsa.RSAPrivateKey


def _load_trust(server_public_pem: Any, private_pem: Any) -> _Trust:
    try:
        server_key = serialization.load_pem_public_key(str(server_public_pem).encode())
        private_key = serialization.load_pem_private_key(str(private_pem).encode(), password=None)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a trust failure
        raise BunqError("unexpected", reason="bad_key_material") from exc
    if not isinstance(server_key, rsa.RSAPublicKey) or server_key.key_size < MIN_KEY_BITS \
            or not isinstance(private_key, rsa.RSAPrivateKey):
        raise BunqError("unexpected", reason="bad_key_material")
    return _Trust(server_key, private_key)


def _pick(body: dict[str, Any], key: str) -> dict[str, Any] | None:
    for item in body.get("Response", []):
        if isinstance(item, dict) and isinstance(item.get(key), dict):
            return item[key]
    return None


def _sign(private_key: rsa.RSAPrivateKey, payload: bytes) -> str:
    return base64.b64encode(private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())).decode()


def _canonical_header(name: str) -> str:
    return "-".join(part.capitalize() for part in name.split("-"))


def _response_head(r: httpx.Response) -> bytes:
    """SDK head form: status line, then the sorted `X-Bunq-*` headers except the signature itself, then a blank line."""
    pairs = sorted((_canonical_header(k), v) for k, v in r.headers.items()
                   if k.lower().startswith("x-bunq-") and k.lower() != SERVER_SIGNATURE.lower())
    return (f"{r.status_code}\n" + "".join(f"{k}: {v}\n" for k, v in pairs) + "\n").encode()


def _verify_response(r: httpx.Response, server_key: rsa.RSAPublicKey) -> None:
    raw = r.headers.get(SERVER_SIGNATURE)
    if not raw:
        raise BunqError("unexpected", r.status_code, "signature_missing")
    try:
        signature = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise BunqError("unexpected", r.status_code, "signature_malformed") from None
    for signed in (r.content, _response_head(r) + r.content):
        try:
            server_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
            return
        except InvalidSignature:
            continue
    raise BunqError("unexpected", r.status_code, "signature_invalid")


def _user_id(body: dict[str, Any]) -> int | None:
    for key in ("UserCompany", "UserPerson", "UserPaymentServiceProvider"):
        found = _pick(body, key)
        if found and isinstance(found.get("id"), int):
            return found["id"]
    api_key_user = _pick(body, "UserApiKey")
    granted = (api_key_user or {}).get("granted_by_user") or {}
    for inner in granted.values():
        if isinstance(inner, dict) and isinstance(inner.get("id"), int):
            return inner["id"]
    return None


class BunqSandboxClient:
    def __init__(self, http: httpx.Client | None = None):
        self._http = http or provider_http.client("bunq")
        self._trust: _Trust | None = None

    def use(self, creds: dict[str, Any]) -> None:
        """Pin the installation server key (and our signing key) from a sealed credential blob."""
        self._trust = _load_trust(creds.get("server_public_key"), creds.get("private_key_pem"))

    def _trust_for(self, session: dict[str, Any]) -> _Trust:
        if session.get("server_public_key") and session.get("private_key_pem"):
            return _load_trust(session["server_public_key"], session["private_key_pem"])
        if self._trust is None:
            raise BunqError("unexpected", reason="no_pinned_server_key")
        return self._trust

    def _call(self, method: str, path: str, *, token: str | None = None, body: dict[str, Any] | None = None,
              trust: _Trust | None = None, params: dict[str, Any] | None = None, timeout: Any = None) -> dict[str, Any]:
        """`trust` set: sign the request body (empty for GET/DELETE) and require a valid server signature."""
        headers = {"Cache-Control": "no-cache", "X-Bunq-Language": "en_US", "X-Bunq-Region": "nl_NL",
                   "X-Bunq-Geolocation": "0 0 0 0 000", "X-Bunq-Client-Request-Id": str(uuid.uuid4())}
        content = None
        if body is not None:
            content = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-Bunq-Client-Authentication"] = token
            if trust:
                headers["X-Bunq-Client-Signature"] = _sign(trust.private_key, (content or "").encode())
        try:
            kwargs: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
            r = self._http.request(method, path, headers=headers, content=content, params=params, **kwargs)
        except httpx.HTTPError:
            raise BunqError("unavailable") from None
        if r.status_code >= 500:
            raise BunqError("unavailable", r.status_code)
        if r.status_code >= 400:
            raise BunqError("rejected", r.status_code)
        if trust:
            _verify_response(r, trust.server_key)
        try:
            data = r.json()
        except ValueError:
            raise BunqError("unexpected") from None
        if not isinstance(data, dict):
            raise BunqError("unexpected")
        return data

    def create_sandbox_api_key(self) -> str:
        """Sandbox only: bunq mints a throwaway company user and returns its API key (response is pre-installation,
        so it cannot be signature-verified)."""
        found = _pick(self._call("POST", "/sandbox-user-company"), "ApiKey")
        key = (found or {}).get("api_key")
        if not isinstance(key, str) or not key.startswith("sandbox_"):
            raise BunqError("unexpected")
        return key

    def register(self, api_key: str) -> dict[str, Any]:
        """Installation + device registration. Returns the material that must be sealed."""
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()).decode()
        public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        installation = self._call("POST", "/installation", body={"client_public_key": public_pem})
        token = (_pick(installation, "Token") or {}).get("token")
        server_key = (_pick(installation, "ServerPublicKey") or {}).get("server_public_key")
        if not token or not server_key:
            raise BunqError("unexpected")
        trust = _load_trust(server_key, private_pem)  # pins the key; every response from here on must verify
        self._call("POST", "/device-server", token=token, trust=trust,
                   body={"description": "Scout (read-only)", "secret": api_key, "permitted_ips": ["*"]})
        self._trust = trust
        return {"api_key": api_key, "installation_token": token, "server_public_key": server_key,
                "private_key_pem": private_pem}

    def login(self, creds: dict[str, Any]) -> dict[str, Any]:
        self.use(creds)
        body = self._call("POST", "/session-server", token=creds["installation_token"], trust=self._trust,
                          body={"secret": creds["api_key"]})
        token = _pick(body, "Token") or {}
        user_id = _user_id(body)
        if not token.get("token") or user_id is None:
            raise BunqError("unexpected")
        return {"session_token": token["token"], "session_id": token.get("id"), "user_id": user_id}

    def accounts(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        body = self._call("GET", f"/user/{int(session['user_id'])}/monetary-account", token=session["session_token"],
                          trust=self._trust_for(session))
        out = []
        for item in body.get("Response", []):
            inner = next(iter(item.values()), None) if isinstance(item, dict) else None
            if isinstance(inner, dict) and isinstance(inner.get("id"), int) and inner.get("status") == "ACTIVE":
                out.append(inner)
        return out

    def payments(self, session: dict[str, Any], account_id: int, since: datetime | None) -> list[dict[str, Any]]:
        """Newest first, following `older_id` pages until the window is covered. Never mutates anything."""
        trust = self._trust_for(session)
        out: list[dict[str, Any]] = []
        older_id: int | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"count": PAGE}
            if older_id is not None:
                params["older_id"] = older_id
            body = self._call("GET", f"/user/{int(session['user_id'])}/monetary-account/{int(account_id)}/payment",
                              token=session["session_token"], trust=trust, params=params)
            page = [item["Payment"] for item in body.get("Response", []) if isinstance(item, dict) and "Payment" in item]
            reached_window_end = False
            for p in page:
                if since is not None and _when(p) < since:
                    reached_window_end = True
                else:
                    out.append(p)
            has_older = bool((body.get("Pagination") or {}).get("older_url"))
            if not page or reached_window_end or not has_older:
                break
            older_id = min(int(p["id"]) for p in page)
        return out

    def close(self, session: dict[str, Any]) -> None:
        """Best effort: end the bunq session. The API key itself is revoked by the owner in bunq.

        The reply carries no data we use. With pinned keys (via `use()` or keys inside `session`) it is signed and
        verified like every other call; without them it is sent unsigned and its reply is ignored."""
        from scout.onboarding.http import REVOKE_TIMEOUT
        if session.get("session_id"):
            try:
                trust = self._trust_for(session)
            except BunqError:
                trust = None
            self._call("DELETE", f"/session/{int(session['session_id'])}", token=session["session_token"],
                       trust=trust, timeout=REVOKE_TIMEOUT)


def _when(payment: dict[str, Any]) -> datetime:
    raw = str(payment.get("created", ""))
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise BunqError("unexpected")


def to_txn(account_id: int, payment: dict[str, Any]) -> BankTxn:
    amount = payment.get("amount") or {}
    try:
        value = Decimal(str(amount["value"]))  # bunq sends money as a decimal string: never go through float
    except (KeyError, InvalidOperation):
        raise BunqError("unexpected") from None
    counterparty = payment.get("counterparty_alias") or {}
    return BankTxn(id=f"bunq:{account_id}:{payment['id']}", booked_at=_when(payment), amount=value,
                   currency=str(amount.get("currency") or "EUR"),
                   counterparty=str(counterparty.get("display_name") or counterparty.get("iban") or ""),
                   reference=str(payment.get("description") or ""), type="transfer")


class BunqSandboxBank:
    """BankConnector over the sealed sandbox credentials. Read-only; the public surface is `list_transactions`.

    `on_refresh(blob)`: optional constructor callback, called synchronously with a fresh full copy of the credential
    dict (`api_key`, `installation_token`, `server_public_key`, `private_key_pem`, `session_token`, `session_id`,
    `user_id`) after an expired session was renewed, before the read is retried. The owner should `json.dumps` it and
    re-seal it. A raising callback is logged (type only) and never aborts the read."""

    name = "bunq_sandbox"
    label = "bunq sandbox"

    def __init__(self, blob: dict[str, Any], http: httpx.Client | None = None,
                 on_refresh: Callable[[dict[str, Any]], None] | None = None):
        self._blob = dict(blob)
        self._client = BunqSandboxClient(http)
        self._on_refresh = on_refresh

    def _session(self) -> dict[str, Any]:
        return {"session_token": self._blob["session_token"], "session_id": self._blob.get("session_id"),
                "user_id": self._blob["user_id"]}

    def _read(self, since: datetime) -> list[BankTxn]:
        session = self._session()
        txns: list[BankTxn] = []
        for account in self._client.accounts(session):
            txns += [to_txn(account["id"], p) for p in self._client.payments(session, account["id"], since)]
        return sorted(txns, key=lambda t: t.booked_at, reverse=True)

    def list_transactions(self, since: datetime) -> list[BankTxn]:
        since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        self._client.use(self._blob)  # fail closed before any request if the pinned key material is unusable
        try:
            return self._read(since)
        except BunqError as exc:
            if exc.status != 401:
                raise
        self._blob.update(self._client.login(self._blob))  # the session expired: log in again, once
        if self._on_refresh is not None:
            try:
                self._on_refresh(dict(self._blob))
            except Exception as exc:  # noqa: BLE001 - persistence trouble must not hide fresh data
                log.warning("bunq refreshed session could not be persisted: %s", type(exc).__name__)
        return self._read(since)
