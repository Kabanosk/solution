"""KSeF endpoints, mounted into `cfo.api.app` before Scout's router (so `/v1/demo/play/ksef` wins over the
generic `/v1/demo/play/{scenario}`). The API only starts work; the `ksef_connect` job runs in the worker."""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter

from cfo.api.errors import ApiError
from scout import db as scout_db
from scout import ksef_sync
from scout.api import Conn, Settings, WriteAuth, _jsonable
from scout.connectors.ksef import KsefClient, KsefError
from scout.demo_data import SCENARIOS

router = APIRouter(prefix="/v1")


@router.post("/connections/ksef:connect", tags=["connections"], status_code=202,
             summary="Connect KSeF (test environment): sign in, pull our e-invoices, match payments (worker job)")
def ksef_connect(conn: Conn, _: WriteAuth) -> dict[str, Any]:
    """Starts a `ksef_connect` worker job; progress shows in `GET /v1/connections` (row `ksef`, `config.phase`,
    `config.steps`). 409 while a run is in progress."""
    if not ksef_sync.start(conn, commit=False):  # committed below with the job and the audit row, or not at all
        conn.rollback()
        raise ApiError(409, "already_running", "A KSeF sync is already running.")
    job = conn.execute("INSERT INTO agent_jobs (kind, payload, dedupe_key) VALUES ('ksef_connect', '{}', %s) "
                       "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
                       (f"ksef_connect:{time.time_ns()}",)).fetchone()
    conn.execute("SELECT pg_notify('scout_jobs', 'ksef_connect')")
    conn.execute("INSERT INTO audit_log (actor, action, target, resource, decision) VALUES "
                 "('dashboard', 'connect', 'invoicing:ksef', 'KSeF test environment', 'ALLOW')")
    conn.commit()
    row = conn.execute("SELECT * FROM connections WHERE id = 'ksef'").fetchone()
    return {"connection": _jsonable(row), "job_id": job["id"] if job else None}


@router.post("/demo/play/ksef", tags=["demo"],
             summary="Demo case ksef: the supplier issues HMZ-20417 in KSeF, then the payment arrives (SCOUT_DEMO=1)")
def play_ksef(settings: Settings, _: WriteAuth) -> dict[str, Any]:
    if not settings.scout_demo:
        raise ApiError(404, "not_found", "Demo mode is off.")
    s = SCENARIOS["ksef"]
    try:
        with scout_db.connect() as conn:
            nip = ksef_sync.demo_nip(conn)
        ksef_number = ksef_sync.issue_demo_invoice(nip, KsefClient, base_url=settings.ksef_api_url)
    except LookupError:
        raise ApiError(409, "ksef_not_connected", "Connect KSeF on the Connections page first.") from None
    except (KsefError, RuntimeError, httpx.HTTPError) as e:
        raise ApiError(502, "ksef_unavailable", f"KSeF did not accept the demo invoice: {e}") from None
    body = {"counterparty": s.counterparty, "amount": str(s.amount), "reference": s.reference, "type": s.type,
            "holder_employee_id": s.holder_employee_id, "card_last4": s.card_last4}
    try:
        r = httpx.post(f"{settings.bank_api_url.rstrip('/')}/mock/transactions", json=body, timeout=5)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ApiError(502, "bank_mock_unavailable", f"The bank mock did not accept the payment: {e}") from None
    return {"scenario": "ksef", "ksef_number": ksef_number, "transaction": r.json()}
