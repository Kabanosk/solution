from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from api_test_helpers import TestClient

from cfo.domain import Transaction
from scout.mock_bank.app import create_app
from scout.mock_bank.store import BankStore, week_shift


def test_week_shift_46():
    assert week_shift(date(2025, 12, 31), date(2026, 11, 20)) == 46
    assert date(2025, 12, 31) + timedelta(weeks=46) < date(2026, 11, 20)


def test_week_shift_lands_strictly_before_today():
    assert date(2025, 12, 31) + timedelta(weeks=week_shift(date(2025, 12, 31), date(2026, 11, 18))) < date(2026, 11, 18)


def _txn(i: int, day: date) -> Transaction:
    return Transaction(id=f"txn_{i}", booking_date=day, type="SEPA TRANSFER", reference=f"r{i}", counterparty="X",
                       amount=Decimal("-10.00"))


def test_future_rows_hidden(conn_factory):
    store = BankStore(conn_factory)
    today = datetime.now(timezone.utc).date()
    n = store.load_history([_txn(1, today - timedelta(days=3)), _txn(2, today + timedelta(days=3))])
    assert n == 1
    assert [t.id for t in store.list()] == ["txn_1"]


def test_post_then_list(conn_factory):
    client = TestClient(create_app(conn_factory))
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    r = client.post("/mock/transactions", json={"counterparty": "Lunch Lab", "amount": "-1250.00",
                                                "reference": "Team lunch", "type": "card_payment",
                                                "holder_employee_id": "vanessa", "card_last4": "4417"})
    assert r.status_code == 201
    new_id = r.json()["id"]
    items = client.get("/api/1.0/transactions", params={"from": since}).json()
    match = [i for i in items if i["id"] == new_id]
    assert match and match[0]["legs"][0]["amount"] == -1250.0
    assert match[0]["card"]["card_number"] == "****4417"
    assert client.get("/api/1.0/accounts").json()[0]["currency"] == "EUR"
