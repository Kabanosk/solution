"""Cycle 3: read-only bunq sandbox connection and the BankConnector adapter."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from bunq_fixtures import (API_KEY, HOST, OTHER_PUBLIC_PEM, USER_ID, envelope, page, payment, script_bunq,
                           signed_response, stored_blob)
from onboarding_helpers import MASTER_KEY, dump, step, tenant_rows


def connect(api, org, body=None):
    return api.post("/v1/onboarding/connect/bunq", headers=org.headers(), json=body if body is not None else {})


def sealed_blob(db_url, org):
    from scout.connectors.crypto import derive_key, open_
    [row] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='bank'")
    return json.loads(open_(row["ciphertext"], derive_key(MASTER_KEY, org.id, "bank")))


def test_empty_body_creates_a_sandbox_company_and_reads_accounts(api, org, fakes, db_url):
    script_bunq(fakes)
    response = connect(api, org)
    assert response.status_code == 200
    bank = step(response.json(), "bank")
    assert bank["state"] == "sandbox_connected" and bank["provider"] == "bunq"
    assert bank["display"] == {"environment": "sandbox", "accounts": "1", "read_only": True}
    posts = [r.url.path for r in fakes.requests("POST")]
    assert posts == ["/v1/sandbox-user-company", "/v1/installation", "/v1/device-server", "/v1/session-server"]
    assert [r.url.path for r in fakes.requests("GET")][0] == f"/v1/user/{USER_ID}/monetary-account"
    # Only the fixed sandbox host; never a payment write or any other mutation.
    assert fakes.hosts() == {HOST}
    assert all("/payment" not in r.url.path for r in fakes.requests("POST"))
    assert {r.method for r in fakes.calls} <= {"GET", "POST"}


def test_credentials_are_sealed_per_organization_and_never_echoed(api, org, fakes, db_url):
    script_bunq(fakes)
    body = connect(api, org).json()
    blob = sealed_blob(db_url, org)
    assert blob["api_key"] == API_KEY and blob["installation_token"] == "installation-token-xyz"
    assert blob["private_key_pem"].startswith("-----BEGIN") and blob["session_token"] == "session-token-xyz"
    [row] = tenant_rows(db_url, org, "SELECT * FROM connections WHERE id='bank'")
    assert row["provider"] == "bunq_sandbox" and row["status"] == "connected" and row["kind"] == "bank"
    visible = dump([body, row])
    for secret in (API_KEY, "installation-token-xyz", "session-token-xyz", "PRIVATE KEY"):
        assert secret not in visible
    [cipher] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials")
    assert API_KEY.encode() not in bytes(cipher["ciphertext"])
    from scout.connectors.crypto import derive_key, open_
    with pytest.raises(Exception):
        open_(cipher["ciphertext"], derive_key(MASTER_KEY, "00000000-0000-4000-8000-000000000099", "bank"))


def test_a_supplied_sandbox_key_is_used_and_nothing_is_created(api, org, fakes):
    script_bunq(fakes)
    assert connect(api, org, {"api_key": API_KEY}).status_code == 200
    assert "/v1/sandbox-user-company" not in [r.url.path for r in fakes.requests("POST")]
    secret_calls = [r for r in fakes.requests("POST") if API_KEY.encode() in r.content]
    assert [r.url.path for r in secret_calls] == ["/v1/device-server", "/v1/session-server"]


def test_a_non_sandbox_key_is_refused_before_any_request(api, org, fakes):
    script_bunq(fakes)
    response = connect(api, org, {"api_key": "ab" * 32})
    assert response.status_code == 422
    assert fakes.calls == []


@pytest.mark.parametrize("broken", ["/v1/installation", "/v1/device-server", "/v1/session-server",
                                    f"/v1/user/{USER_ID}/monetary-account"])
def test_provider_failure_is_a_failed_step_not_a_connection(api, org, fakes, db_url, broken):
    script_bunq(fakes, fail={broken: (503, {"Error": [{"error_description": "maintenance " + API_KEY}]})})
    response = connect(api, org)
    assert response.status_code == 200
    bank = step(response.json(), "bank")
    assert bank["state"] == "failed" and bank["message"]
    assert API_KEY not in dump(response.json())
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_a_user_without_an_active_account_is_not_connected(api, org, fakes, db_url):
    script_bunq(fakes, accounts=[{"id": 7, "status": "CANCELLED", "currency": "EUR"}])
    bank = step(connect(api, org).json(), "bank")
    assert bank["state"] == "failed" and "account" in bank["message"].lower()
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_a_timeout_is_reported_honestly(api, org, fakes):
    def boom(request):
        raise httpx.ConnectTimeout("slow", request=request)
    script_bunq(fakes, fail={"/v1/installation": boom})
    assert step(connect(api, org).json(), "bank")["state"] == "failed"


def test_missing_master_key_is_needs_configuration_with_no_provider_traffic(api, org, fakes, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, connections_key="")
    bank = step(connect(api, org).json(), "bank")
    assert bank["state"] == "needs_configuration" and "CONNECTIONS_KEY" in bank["message"]
    assert fakes.calls == []


def test_disconnect_removes_the_credential_even_if_the_provider_is_down(api, org, fakes, db_url):
    script_bunq(fakes, fail={"/v1/session/5": (503, {})})
    connect(api, org)
    body = api.delete("/v1/onboarding/connect/bunq", headers=org.headers()).json()
    bank = step(body, "bank")
    assert bank["state"] == "not_started" and bank["display"] == {"revocation_unconfirmed": True}
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []
    assert [r.url.path for r in fakes.requests("DELETE")] == ["/v1/session/5"]


def _client(handler):
    return httpx.Client(base_url="https://public-api.sandbox.bunq.com/v1", transport=httpx.MockTransport(handler))


def _bank(fakes, handler=None, session_token="session-token-xyz", on_refresh=None, **blob):
    from scout.connectors.bunq import BunqSandboxBank
    return BunqSandboxBank(stored_blob(session_token=session_token, **blob), http=_client(handler or fakes.handler),
                           on_refresh=on_refresh)


def test_adapter_maps_exact_money_and_follows_pagination(fakes):
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    newest = [payment(30, "-12.50"), payment(29, "1234567.89", description="Big")]
    oldest = [payment(28, "-0.01", created="2026-11-05 08:00:00.000000"),
              payment(27, "-99.00", created="2026-10-01 08:00:00.000000")]  # before `since`: excluded, stops paging
    seen = []

    def payments_route(request):
        seen.append(dict(request.url.params))
        return page(oldest) if request.url.params.get("older_id") == "29" else page(
            newest, older_url=f"/v1/user/{USER_ID}/monetary-account/7/payment?count=200&older_id=29")

    script_bunq(fakes, payments=payments_route)
    txns = _bank(fakes).list_transactions(since)
    assert [t.id for t in txns] == ["bunq:7:30", "bunq:7:29", "bunq:7:28"]
    assert [t.amount for t in txns] == [Decimal("-12.50"), Decimal("1234567.89"), Decimal("-0.01")]
    assert all(isinstance(t.amount, Decimal) for t in txns) and txns[0].currency == "EUR"
    assert txns[0].counterparty == "Supplier BV" and txns[0].reference == "Invoice 1"
    assert txns[0].booked_at == datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc) and txns[0].outgoing
    assert seen[0].get("count") == "200" and seen[1].get("older_id") == "29"
    assert {r.method for r in fakes.calls} == {"GET"} and fakes.hosts() == {HOST}


def _expiring(fakes):
    state = {"first": True}
    original = fakes.handler

    def expiring(request):
        if request.method == "GET" and request.headers.get("X-Bunq-Client-Authentication") == "stale" and state["first"]:
            state["first"] = False
            return httpx.Response(401, json={"Error": [{"error_description": "expired"}]})
        return original(request)
    return expiring


def test_adapter_logs_in_again_once_when_the_session_expired(fakes):
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]))
    bank = _bank(fakes, _expiring(fakes), session_token="stale")
    assert [t.id for t in bank.list_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))] == ["bunq:7:1"]
    assert [r.url.path for r in fakes.requests("POST")] == ["/v1/session-server"]


def test_a_renewed_session_is_handed_to_on_refresh_with_the_complete_blob(fakes):
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]))
    handed = []
    bank = _bank(fakes, _expiring(fakes), session_token="stale", on_refresh=handed.append)
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bank.list_transactions(since)
    assert handed == [stored_blob(session_token="session-token-xyz")]  # every key, new session, nothing dropped
    handed[0]["session_token"] = "mutated"
    fakes.calls.clear()
    bank.list_transactions(since)  # the live session was not touched by the callee, and no second login happens
    assert fakes.requests("POST") == []
    # persisted blob reused later: no login at all
    from scout.connectors.bunq import BunqSandboxBank
    again = BunqSandboxBank(stored_blob(session_token="session-token-xyz"), http=_client(fakes.handler))
    assert [t.id for t in again.list_transactions(since)] == ["bunq:7:1"] and fakes.requests("POST") == []


def test_on_refresh_is_not_called_without_a_renewal_and_its_failure_never_loses_the_read(fakes, caplog):
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]))
    calls = []
    _bank(fakes, on_refresh=calls.append).list_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert calls == []

    def broken(blob):
        raise RuntimeError("db down " + blob["api_key"])
    bank = _bank(fakes, _expiring(fakes), session_token="stale", on_refresh=broken)
    assert len(bank.list_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))) == 1
    assert API_KEY not in caplog.text


def test_an_unsigned_renewal_is_refused_and_not_persisted(fakes):
    from scout.connectors.bunq import BunqError
    script_bunq(fakes, unsigned_paths={"/v1/session-server"})
    handed = []
    bank = _bank(fakes, _expiring(fakes), session_token="stale", on_refresh=handed.append)
    with pytest.raises(BunqError) as err:
        bank.list_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert err.value.kind == "unexpected" and err.value.reason == "signature_missing" and handed == []


SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("mode", ["body", "head"])
def test_both_documented_server_signature_forms_verify(fakes, mode):
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]), signature=mode)
    assert [t.id for t in _bank(fakes).list_transactions(SINCE)] == ["bunq:7:1"]


@pytest.mark.parametrize("mode,reason", [("missing", "signature_missing"), ("tampered", "signature_invalid"),
                                         ("wrong_key", "signature_invalid"), ("malformed", "signature_malformed")])
@pytest.mark.parametrize("target", ["accounts", "payments"])
def test_unsigned_tampered_or_foreign_signed_responses_fail_closed(fakes, mode, reason, target):
    from scout.connectors.bunq import BunqError
    paths = {"accounts": f"/v1/user/{USER_ID}/monetary-account", "payments": f"/v1/user/{USER_ID}/monetary-account/7/payment"}
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]), unsigned_paths=set())
    # break exactly one authenticated read; the rest stay correctly signed
    original = fakes.handler
    broken_path = paths[target]

    def handler(request):
        response = original(request)
        if request.url.path == broken_path:
            return signed_response(200, json.loads(response.content), mode)
        return response
    with pytest.raises(BunqError) as err:
        _bank(fakes, handler).list_transactions(SINCE)
    assert err.value.kind == "unexpected" and err.value.reason == reason


def test_a_head_signature_does_not_cover_a_tampered_signed_header(fakes):
    from scout.connectors.bunq import BunqError
    script_bunq(fakes, payments=lambda r: page([payment(1, "5.00")]), signature="head")
    original = fakes.handler

    def handler(request):
        response = original(request)
        if request.url.path.endswith("/monetary-account"):
            headers = dict(response.headers)
            headers["x-bunq-client-response-id"] = "99999999-2222-4333-8444-555555555555"
            return httpx.Response(200, content=response.content, headers=headers)
        return response
    with pytest.raises(BunqError) as err:
        _bank(fakes, handler).list_transactions(SINCE)
    assert err.value.reason == "signature_invalid"


@pytest.mark.parametrize("server_key", [None, "", "MIIB", OTHER_PUBLIC_PEM.replace("MII", "XXX", 1)])
def test_missing_or_unusable_pinned_key_fails_before_any_request(fakes, server_key):
    from scout.connectors.bunq import BunqError
    script_bunq(fakes)
    blob = stored_blob()
    blob["server_public_key"] = server_key
    from scout.connectors.bunq import BunqSandboxBank
    with pytest.raises(BunqError) as err:
        BunqSandboxBank(blob, http=_client(fakes.handler)).list_transactions(SINCE)
    assert err.value.reason == "bad_key_material" and fakes.calls == []


def test_a_response_signed_by_a_different_key_than_the_pinned_one_is_refused(fakes):
    from scout.connectors.bunq import BunqError
    script_bunq(fakes)
    with pytest.raises(BunqError) as err:
        _bank(fakes, server_public_key=OTHER_PUBLIC_PEM).list_transactions(SINCE)
    assert err.value.reason == "signature_invalid"


def test_a_weak_server_key_is_not_pinned(fakes):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from scout.connectors.bunq import BunqError, BunqSandboxClient
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    script_bunq(fakes)
    original = fakes.handler

    def handler(request):
        response = original(request)
        if request.url.path == "/v1/installation":
            return signed_response(200, envelope({"Token": {"id": 2, "token": "t"}},
                                                 {"ServerPublicKey": {"server_public_key": weak}}), "missing")
        return response
    with pytest.raises(BunqError) as err:
        BunqSandboxClient(_client(handler)).register(API_KEY)
    assert err.value.reason == "bad_key_material"
    assert [r.url.path for r in fakes.calls] == ["/v1/installation"]  # stopped before /device-server


def test_requests_are_signed_with_the_installation_key_over_the_body_only(fakes):
    """session-server: documented mandatory body signature. GETs: signed over an empty body like both SDKs do."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from scout.connectors.bunq import BunqSandboxClient
    script_bunq(fakes, payments=lambda r: page([]))
    client = BunqSandboxClient(_client(fakes.handler))
    creds = client.register(API_KEY)
    session = client.login(creds)
    client.accounts(session)
    installation = next(r for r in fakes.requests("POST") if r.url.path == "/v1/installation")
    assert "X-Bunq-Client-Signature" not in installation.headers  # nothing to sign with yet
    public = serialization.load_pem_public_key(json.loads(installation.content)["client_public_key"].encode())
    for r in fakes.calls:
        if "X-Bunq-Client-Authentication" in r.headers:
            public.verify(base64.b64decode(r.headers["X-Bunq-Client-Signature"]), r.content or b"",
                          padding.PKCS1v15(), hashes.SHA256())  # raises if wrong
    assert {r.url.path for r in fakes.calls if "X-Bunq-Client-Signature" in r.headers} == {
        "/v1/device-server", "/v1/session-server", f"/v1/user/{USER_ID}/monetary-account"}


@pytest.mark.parametrize("mode", ["missing", "tampered", "wrong_key", "malformed"])
@pytest.mark.parametrize("path", ["/v1/device-server", "/v1/session-server", "/v1/user/42/monetary-account"])
def test_setup_fails_when_any_handshake_response_is_not_correctly_signed(api, org, fakes, db_url, mode, path,
                                                                        monkeypatch):
    script_bunq(fakes, signature="body")
    original = fakes.handler

    def handler(request):
        response = original(request)
        if request.url.path == path:
            return signed_response(200, json.loads(response.content), mode)
        return response
    from scout.onboarding import http as onboarding_http
    monkeypatch.setattr(onboarding_http, "TRANSPORT", httpx.MockTransport(handler))
    bank = step(connect(api, org).json(), "bank")
    assert bank["state"] == "failed" and bank["message"] == "bunq sandbox returned an unexpected response."
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials") == []


def test_close_verifies_with_pinned_keys_and_still_works_without_them(fakes):
    from scout.connectors.bunq import BunqSandboxClient
    script_bunq(fakes)
    session = {"session_token": "session-token-xyz", "session_id": 5, "user_id": USER_ID}
    BunqSandboxClient(_client(fakes.handler)).close(session)  # no pinned keys: best effort, reply ignored
    pinned = BunqSandboxClient(_client(fakes.handler))
    pinned.use(stored_blob())
    pinned.close(session)
    assert [r.method for r in fakes.calls] == ["DELETE", "DELETE"]
    assert "X-Bunq-Client-Signature" not in fakes.calls[0].headers and "X-Bunq-Client-Signature" in fakes.calls[1].headers


def test_adapter_has_no_write_surface():
    from scout.connectors.bunq import BunqSandboxBank
    public = {n for n in dir(BunqSandboxBank) if not n.startswith("_")}
    assert public == {"name", "label", "list_transactions"}
