"""The registry persists renewed signed bank sessions without replacing newer owner credentials."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from bunq_fixtures import page, payment, script_bunq, stored_blob
from onboarding_helpers import MASTER_KEY, tenant_rows
from test_registry import build, store


def test_registry_persists_a_verified_renewed_bunq_session(build, db_url, org, fakes, monkeypatch):
    from scout.onboarding import http as provider_http
    from scout.connectors.crypto import derive_key, open_

    store(db_url, org, "bank", {"mode": "sandbox"}, json.dumps(stored_blob(session_token="stale")))
    script_bunq(fakes, payments=lambda request: page([payment(1, "5.00")]))
    original = fakes.handler

    def expiring(request):
        if request.method == "GET" and request.headers.get("X-Bunq-Client-Authentication") == "stale":
            return httpx.Response(401, json={"Error": [{"error_description": "expired"}]})
        return original(request)

    monkeypatch.setattr(provider_http, "TRANSPORT", httpx.MockTransport(expiring))
    bank = build()[0].bank
    assert len(bank.list_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))) == 1
    [row] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='bank'")
    saved = json.loads(open_(row["ciphertext"], derive_key(MASTER_KEY, org.id, "bank")))
    assert saved["session_token"] == "session-token-xyz"
    assert b"session-token-xyz" not in bytes(row["ciphertext"])


def test_logout_without_a_verified_response_does_not_claim_confirmed_revocation(api, org, fakes, db_url):
    from onboarding_helpers import step

    script_bunq(fakes, unsigned_paths={"/v1/session/5"})
    assert api.post("/v1/onboarding/connect/bunq", headers=org.headers(), json={}).status_code == 200
    removed = api.delete("/v1/onboarding/connect/bunq", headers=org.headers())
    assert removed.status_code == 200
    assert step(removed.json(), "bank")["display"].get("revocation_unconfirmed") is True
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials WHERE connection_id='bank'") == []


def test_old_refresh_callback_cannot_restore_a_disconnected_bank(build, db_url, org):
    from onboarding_helpers import admin

    store(db_url, org, "bank", {"mode": "sandbox"}, json.dumps(stored_blob()))
    bank = build()[0].bank
    assert callable(bank._on_refresh)
    admin(db_url).execute(f'DELETE FROM "{org.schema}".connections WHERE id=\'bank\'')
    bank._on_refresh(stored_blob(session_token="must-not-be-restored"))
    assert tenant_rows(db_url, org, "SELECT 1 FROM connection_credentials WHERE connection_id='bank'") == []


def test_old_refresh_callback_cannot_update_a_disabled_bank(build, db_url, org):
    from onboarding_helpers import admin
    from scout.connectors.crypto import derive_key, open_

    store(db_url, org, "bank", {"mode": "sandbox"}, json.dumps(stored_blob(session_token="original")))
    bank = build()[0].bank
    admin(db_url).execute(f'UPDATE "{org.schema}".connections SET enabled=false WHERE id=\'bank\'')
    bank._on_refresh(stored_blob(session_token="must-not-be-saved"))
    [row] = tenant_rows(db_url, org, "SELECT ciphertext FROM connection_credentials WHERE connection_id='bank'")
    saved = json.loads(open_(row["ciphertext"], derive_key(MASTER_KEY, org.id, "bank")))
    assert saved["session_token"] == "original"
