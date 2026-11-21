"""The legacy opt-in must not silently select the mock bank in a non-demo worker."""
import json
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant, bind_tenant
from scout.config import ScoutSettings
from scout.connectors import registry
from scout.connectors.crypto import derive_key, seal
from scout.mock_bank.app import create_app

LEGACY = Tenant(id=LEGACY_ID, slug=LEGACY_SLUG, schema_name="public", employee_id="vincenzo", role="worker", legacy=True)
OTHER = Tenant(id="12345678-1234-4234-8234-123456789abc", slug="other",
               schema_name="tenant_12345678123442348234123456789abc", employee_id="owner", role="worker")
NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


@pytest.fixture
def build(monkeypatch):
    monkeypatch.setenv("SCOUT_LEGACY_CONNECTORS_ORGANIZATION", LEGACY_ID)
    def forbidden(*args, **kwargs):
        pytest.fail("Selecting the bank must not make network requests or use an unscoped database")
    monkeypatch.setattr("httpx.Client", forbidden)
    def run(row=None, *, tenant=LEGACY, demo=False, key=""):
        rows = {"bank": row} if row else {}
        monkeypatch.setattr(registry, "_rows", lambda cf: rows)
        monkeypatch.setattr(registry, "_authorized_rows", lambda cf: rows)
        with bind_tenant(tenant):
            return registry.build(forbidden, ScoutSettings(_env_file=None, scout_demo=demo, connections_key=key,
                                                          bank_api_url="http://bank-mock:8010"))
    return run


def mock_row(**changes):
    return {"id":"bank", "enabled":True, "status":"connected", "provider":"revolut_mock", "config":{}, **changes}


@pytest.mark.parametrize("row", [None, mock_row(), mock_row(enabled=False), mock_row(status="error")])
def test_non_demo_legacy_worker_never_uses_bank_mock(build, row):
    bank = build(row).bank
    assert bank.name == "not_configured"
    assert bank.list_transactions(NOW) == []


@pytest.mark.parametrize("row", [None, mock_row(enabled=False), mock_row(status="disconnected"),
                                mock_row(provider="unknown"), mock_row(config={"managed_by":"onboarding"})])
def test_demo_still_requires_explicit_enabled_mock_connection(build, row):
    assert build(row, demo=True).bank.name == "not_configured"


def test_explicit_legacy_demo_reads_the_bound_store_without_http(build, monkeypatch):
    bank = build(mock_row(), demo=True).bank
    seen = []
    monkeypatch.setattr(bank._store, "list", lambda **kwargs: seen.append(kwargs) or [])
    with bind_tenant(LEGACY):
        assert bank.list_transactions(NOW) == []
    assert seen == [{"from_ts":NOW}]
    with bind_tenant(OTHER), pytest.raises(PermissionError):
        bank.list_transactions(NOW)


def test_nonlegacy_never_inherits_mock_even_if_demo_enabled(build):
    assert build(mock_row(), tenant=OTHER, demo=True).bank.name == "not_configured"


@pytest.mark.parametrize("demo", [False, True])
def test_legacy_uses_saved_bunq_even_with_demo_enabled(build, monkeypatch, demo):
    from scout.onboarding.catalog import CONNECTION
    key = Fernet.generate_key().decode()
    blob = {"api_key":"private-bank-key", "session_token":"tenant-session"}
    cipher = seal(json.dumps(blob), derive_key(key, LEGACY_ID, "bank"))
    seen = []
    marker = object()
    monkeypatch.setattr("scout.connectors.bunq.BunqSandboxBank", lambda value, **kwargs: seen.append(value) or marker)
    row = mock_row(provider=CONNECTION["bank"][2], config={"managed_by":"onboarding"}, ciphertext=cipher)
    assert build(row, demo=demo, key=key).bank is marker
    assert seen == [blob]
    row["ciphertext"] = seal(json.dumps(blob), derive_key(key, OTHER.id, "bank"))
    assert build(row, demo=demo, key=key).bank.name == "not_configured"


def test_bank_mock_http_still_rejects_anonymous_requests():
    client = TestClient(create_app(lambda: pytest.fail("Unauthenticated DB access")))
    assert client.get("/api/1.0/transactions").status_code == 401
    assert client.get("/healthz").status_code == 200
