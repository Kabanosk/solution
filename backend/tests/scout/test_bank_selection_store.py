from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant, bind_tenant
from scout.connectors.bank import StoredDemoBank
from scout.mock_bank.store import BankStore


def test_demo_worker_reads_stored_payments_without_an_http_session(conn_factory, monkeypatch):
    monkeypatch.setenv("SCOUT_LEGACY_CONNECTORS_ORGANIZATION", LEGACY_ID)
    tenant = Tenant(id=LEGACY_ID, slug=LEGACY_SLUG, schema_name="public", employee_id="vincenzo", role="worker", legacy=True)
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    with bind_tenant(tenant):
        payment = BankStore(conn_factory).post("Test supplier", Decimal("-12.50"), "demo-only")
        [found] = StoredDemoBank(conn_factory).list_transactions(since)
    assert found.id == payment.id
    assert found.amount == Decimal("-12.50")
