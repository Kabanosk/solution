"""Bank: a Revolut Business-shaped API (`scout.mock_bank` in the demo; the real API has the same shape)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

import httpx

from scout.models import BankTxn


class BankConnector(Protocol):
    name: str

    def list_transactions(self, since: datetime) -> list[BankTxn]: ...


def from_revolut(item: dict) -> BankTxn:
    leg = (item.get("legs") or [{}])[0]
    card = item.get("card") or {}
    last4 = (card.get("card_number") or "")[-4:] or None
    return BankTxn(
        id=item["id"],
        booked_at=item.get("completed_at") or item["created_at"],
        amount=Decimal(str(leg.get("amount", 0))),
        currency=leg.get("currency", "EUR"),
        counterparty=(item.get("merchant") or {}).get("name") or leg.get("description") or "",
        reference=item.get("reference") or "",
        type=item.get("type", "transfer"),
        card_last4=last4,
        holder_employee_id=card.get("employee_id"),
    )


class RevolutMockBank:
    name = "revolut_mock"
    label = "Revolut Business"

    def __init__(self, base_url: str, http: httpx.Client | None = None, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = http or httpx.Client(base_url=base_url, timeout=10.0, headers=headers)

    def list_transactions(self, since: datetime) -> list[BankTxn]:
        r = self._http.get("/api/1.0/transactions", params={"from": since.isoformat(), "count": 1000})
        r.raise_for_status()
        return [from_revolut(i) for i in r.json() if i.get("state", "completed") == "completed"]


class StoredDemoBank:
    """Explicit legacy demo only; reads the tenant-bound mock ledger without a browser-session HTTP hop."""

    name = "revolut_mock"
    label = "Demo bank (stored transactions)"

    def __init__(self, conn_factory):
        from scout.mock_bank.store import BankStore
        self._store = BankStore(conn_factory)

    def list_transactions(self, since: datetime) -> list[BankTxn]:
        from cfo.tenancy import legacy_connectors_enabled
        if not legacy_connectors_enabled():
            raise PermissionError("Stored demo bank requires the explicitly authorized legacy organization")
        return self._store.list(from_ts=since)
