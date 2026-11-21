"""bunq sandbox responses shaped like the official swagger (github.com/bunq/doc swagger.json, 'Response' envelope).

Every response after `/installation` is signed like bunq does it: `X-Bunq-Server-Signature` = base64 RSA PKCS#1 v1.5 /
SHA-256 over the response body (the documented form) or, in `signature="head"` mode, over the SDK head form
(`status\\n` + sorted X-Bunq-* headers + `\\n` + body). Keys are real generated RSA-2048 keys."""
from __future__ import annotations

import base64
import json

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HOST = "public-api.sandbox.bunq.com"
API_KEY = "sandbox_" + "a1" * 28
USER_ID = 42


def _pem_pair() -> tuple[rsa.RSAPrivateKey, str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, private_pem, public_pem


SERVER_PRIVATE, _server_private_pem, SERVER_PUBLIC_PEM = _pem_pair()
OTHER_PRIVATE, _other_private_pem, OTHER_PUBLIC_PEM = _pem_pair()  # an attacker / unrelated key
CLIENT_PRIVATE, CLIENT_PRIVATE_PEM, CLIENT_PUBLIC_PEM = _pem_pair()


def stored_blob(**overrides) -> dict:
    """A sealed credential blob as the connector stores it, with real key material."""
    blob = {"api_key": API_KEY, "installation_token": "installation-token-xyz", "private_key_pem": CLIENT_PRIVATE_PEM,
            "server_public_key": SERVER_PUBLIC_PEM, "session_token": "session-token-xyz", "session_id": 5,
            "user_id": USER_ID}
    return {**blob, **overrides}


def envelope(*items: dict) -> dict:
    return {"Response": list(items)}


def sign(key: rsa.RSAPrivateKey, data: bytes) -> str:
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


def signed_response(status: int, body: dict, signature: str = "body", key: rsa.RSAPrivateKey = SERVER_PRIVATE,
                    extra_headers: dict | None = None) -> httpx.Response:
    """`signature`: body | head | missing | tampered (signed body, then altered) | wrong_key | malformed."""
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "X-Bunq-Client-Response-Id": "11111111-2222-4333-8444-555555555555",
               **(extra_headers or {})}
    signing_key = OTHER_PRIVATE if signature == "wrong_key" else key
    if signature == "head":
        pairs = sorted((k, v) for k, v in headers.items() if k.startswith("X-Bunq-"))
        head = f"{status}\n" + "".join(f"{k}: {v}\n" for k, v in pairs) + "\n"
        headers["X-Bunq-Server-Signature"] = sign(signing_key, head.encode() + raw)
    elif signature in ("body", "wrong_key", "tampered"):
        headers["X-Bunq-Server-Signature"] = sign(signing_key, raw)
    elif signature == "malformed":
        headers["X-Bunq-Server-Signature"] = "not base64 !!"
    if signature == "tampered":
        raw = raw.replace(b"1250.10", b"9999.99") if b"1250.10" in raw else raw.replace(b'"id":', b'"id": ', 1)
    return httpx.Response(status, content=raw, headers=headers)


def script_bunq(fakes, *, accounts=None, payments=None, fail: dict | None = None, user_id: int = USER_ID,
                signature: str = "body", unsigned_paths: set[str] | None = None) -> None:
    """Script the whole handshake. `fail` maps a path to an (status, body) tuple. `signature` is the signing mode for
    every response after the installation; `unsigned_paths` lists paths that are served without a signature."""
    fail = fail or {}
    unsigned_paths = unsigned_paths or set()
    accounts = [{"id": 7, "status": "ACTIVE", "currency": "EUR", "description": "Operating",
                 "balance": {"value": "1250.10", "currency": "EUR"}}] if accounts is None else accounts

    def signed(path: str, body):
        mode = "missing" if path in unsigned_paths else signature

        def respond(request: httpx.Request):
            result = body(request) if callable(body) else body
            if isinstance(result, httpx.Response):
                payload = json.loads(result.content or b"{}")
                return signed_response(result.status_code, payload, mode)
            return signed_response(200, result, mode)
        return respond

    def route(method: str, path: str, body, *, sign_it: bool = True):
        chosen = fail.get(path, body)
        fakes.route(method, HOST, path, signed(path, chosen) if sign_it and path not in fail else chosen)

    route("POST", "/v1/sandbox-user-company", envelope({"ApiKey": {"api_key": API_KEY}}), sign_it=False)
    route("POST", "/v1/installation", envelope(
        {"Id": {"id": 1}}, {"Token": {"id": 2, "token": "installation-token-xyz"}},
        {"ServerPublicKey": {"server_public_key": SERVER_PUBLIC_PEM}}), sign_it=False)
    route("POST", "/v1/device-server", envelope({"Id": {"id": 3}}))
    route("POST", "/v1/session-server", envelope(
        {"Id": {"id": 4}}, {"Token": {"id": 5, "token": "session-token-xyz"}},
        {"UserCompany": {"id": user_id, "display_name": "Acme Sandbox"}}))
    route("DELETE", "/v1/session/5", envelope())
    route("GET", f"/v1/user/{user_id}/monetary-account",
          envelope(*[{"MonetaryAccountBank": a} for a in accounts]))
    for account in accounts:
        route("GET", f"/v1/user/{user_id}/monetary-account/{account['id']}/payment",
              payments if payments is not None else (lambda r: envelope(*[{"Payment": p} for p in []])))


def payment(pid: int, value: str, created: str = "2026-11-20 10:00:00.000000", description: str = "Invoice 1",
            counterparty: str = "Supplier BV", currency: str = "EUR") -> dict:
    return {"id": pid, "created": created, "updated": created, "monetary_account_id": 7,
            "amount": {"value": value, "currency": currency}, "description": description, "type": "BUNQ",
            "counterparty_alias": {"display_name": counterparty, "iban": "NL00BUNQ0000000000"}}


def page(items: list[dict], older_url: str | None = None) -> httpx.Response:
    body = envelope(*[{"Payment": p} for p in items])
    body["Pagination"] = {"future_url": None, "newer_url": None, "older_url": older_url}
    return httpx.Response(200, json=body)
