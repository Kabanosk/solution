"""Revolut Business-shaped API over `BankStore` (read endpoints as in Revolut's `/api/1.0`, plus a demo POST).

    python -m scout.mock_bank            # :8010
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI, Query
from pydantic import BaseModel

from scout import db as scout_db
from scout.mock_bank.store import BankStore
from scout.models import BankTxn

EMPLOYEE_NAMES = {"vincenzo": ("Vincenzo", "Amalfi"), "grazyna": ("Grazyna", "Nowak"), "rosa": ("Rosa", "Pavese"),
                  "vanessa": ("Vanessa", "Paradi"), "yusuf": ("Yusuf", "Akcit")}


def revolut_shape(t: BankTxn) -> dict:
    out = {
        "id": t.id,
        "type": t.type if t.type in ("card_payment", "transfer") else "transfer",
        "state": "completed",
        "created_at": t.booked_at.isoformat(),
        "completed_at": t.booked_at.isoformat(),
        "reference": t.reference,
        "legs": [{"leg_id": f"{t.id}_l1", "account_id": "acc_amalfi", "amount": float(t.amount),
                  "currency": t.currency, "description": t.counterparty}],
        "merchant": {"name": t.counterparty} if t.type == "card_payment" else None,
    }
    if t.card_last4 or t.holder_employee_id:
        first, last = EMPLOYEE_NAMES.get(t.holder_employee_id or "", ("", ""))
        out["card"] = {"card_number": f"****{t.card_last4 or '0000'}", "first_name": first, "last_name": last,
                       "employee_id": t.holder_employee_id}
    return out


class NewPayment(BaseModel):
    counterparty: str
    amount: Decimal
    reference: str
    type: str = "transfer"
    holder_employee_id: str | None = None
    card_last4: str | None = None


def create_app(conn_factory=None) -> FastAPI:  # noqa: ANN001
    # Resolve the connection only after authentication, never during module import.
    store = BankStore(conn_factory or (lambda: scout_db.connect()))
    app = FastAPI(title="Revolut Business (mock)", version="1.0")
    from cfo.api.auth import OrganizationAuthMiddleware
    from cfo.api.errors import install_error_handlers
    from cfo.tenancy import legacy_connectors_enabled
    from starlette.responses import JSONResponse

    class LegacyBankOnly:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"] != "/healthz" and not legacy_connectors_enabled():
                return await JSONResponse({"error": {"code": "not_configured", "message": "Mock bank is disabled for this organization."}}, status_code=403)(scope, receive, send)
            return await self.app(scope, receive, send)
    app.add_middleware(LegacyBankOnly)
    app.add_middleware(OrganizationAuthMiddleware)
    install_error_handlers(app)

    @app.get("/api/1.0/accounts")
    def accounts() -> list[dict]:
        return [{"id": "acc_amalfi", "name": "Pizzeria Amalfi EUR", "balance": float(store.balance()),
                 "currency": "EUR", "state": "active", "public": False}]

    @app.get("/api/1.0/transactions")
    def transactions(from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
                     count: int = Query(1000, le=1000)) -> list[dict]:
        return [revolut_shape(t) for t in store.list(from_, to, count)]

    @app.post("/mock/transactions", status_code=201)
    def post(body: NewPayment) -> dict:
        return revolut_shape(store.post(body.counterparty, body.amount, body.reference, type=body.type,
                                        holder_employee_id=body.holder_employee_id, card_last4=body.card_last4))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
