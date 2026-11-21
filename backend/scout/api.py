"""Scout's REST API, mounted into `cfo.api.app`: tasks (+ live SSE), approvals, audit, connections, Google OAuth and
the demo trigger. Reads come straight from the tables the loops write; writes only enqueue jobs or change
connection rows, so the API never runs agent code itself (the worker pool does).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from psycopg.types.json import Jsonb

from cfo.api.auth import require_write_token
from cfo.api.errors import ApiError
from scout import db as scout_db
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors.crypto import seal
from scout.demo_data import SCENARIOS

router = APIRouter(prefix="/v1")
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/gmail.readonly"
SLACK_SCOPES = ["chat:write", "chat:write.customize", "im:history", "im:read", "im:write", "reactions:write",
                "files:read", "channels:history", "channels:read", "groups:history", "users:read"]
KEEPALIVE_S = 15.0
Settings = Annotated[ScoutSettings, Depends(get_scout_settings)]
WriteAuth = Annotated[str | None, Depends(require_write_token)]


def get_conn() -> Iterator[Any]:
    """One connection per request. Tests override this dependency (schema per test)."""
    conn = scout_db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[Any, Depends(get_conn)]


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.astimezone(timezone.utc).isoformat()
        elif hasattr(v, "as_tuple"):  # Decimal: money as a string, never a float
            out[k] = str(v)
        elif isinstance(v, (bytes, memoryview)):
            continue
        else:
            out[k] = v
    return out


def _strategy_exec() -> bool:
    from cfo.tenancy import current_tenant
    tenant = current_tenant(required=False)
    return bool(tenant and tenant.role in {"owner", "ceo"})


@router.post("/strategy/review", tags=["tasks"], summary="Queue today's CEO strategy briefing")
def strategy_review(conn: Conn, _: WriteAuth) -> dict[str, Any]:
    from zoneinfo import ZoneInfo
    if not _strategy_exec():
        raise ApiError(403, "forbidden", "Only the organization owner can run a strategy review.")
    day = str(datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Berlin")).date())
    key = f"strategy:{day}"
    row = conn.execute("INSERT INTO agent_jobs(kind,payload,dedupe_key) VALUES ('strategy_review',%s,%s) "
                       "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id", (Jsonb({"day": day}), key)).fetchone()
    task = conn.execute("SELECT id FROM agent_tasks WHERE dedupe_key=%s", (key,)).fetchone()
    scout_db.notify(conn, "scout_jobs", "strategy_review")
    conn.commit()
    return {"status": "queued" if row else "already_scheduled", "day": day, "task_id": task["id"] if task else None}


# -- tasks --------------------------------------------------------------------------------------------------
@router.get("/tasks", tags=["tasks"], summary="Agent tasks, newest activity first, with counts per status")
def list_tasks(conn: Conn, status: str | None = None, loop: str | None = None, owner: str | None = None,
               q: str | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    visible = "TRUE" if _strategy_exec() else "loop <> 'strategy'"
    where, args = [visible], []
    for col, val in (("status", status), ("loop", loop), ("owner_id", owner)):
        if val:
            where.append(f"{col} = ANY(%s)")
            args.append(val.split(","))
    if q:
        where.append("(title ILIKE %s OR counterparty ILIKE %s OR id ILIKE %s OR outcome ILIKE %s)")
        args += [f"%{q}%"] * 4
    rows = conn.execute(f"SELECT * FROM agent_tasks WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT %s",
                        [*args, limit]).fetchall()
    counts = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*)::int AS n FROM agent_tasks WHERE {visible} GROUP BY status").fetchall()}
    return {"items": [_jsonable(r) for r in rows], "counts": counts}


@router.get("/tasks/stream", tags=["tasks"], summary="Server-Sent Events: `task` with the id of a changed task")
def task_stream(settings: Settings) -> StreamingResponse:
    from cfo.tenancy import current_tenant
    from psycopg import sql
    tenant = current_tenant()
    def events() -> Iterator[str]:
        conn = scout_db.connect(settings.database_url, tenant.schema_name, autocommit=True)
        try:
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier("scout_tasks_" + tenant.schema_name)))
            yield "retry: 3000\n\n"
            while True:
                got = False
                for n in conn.notifies(timeout=KEEPALIVE_S, stop_after=50):
                    got = True
                    yield f"event: task\ndata: {json.dumps({'id': n.payload})}\n\n"
                if not got:
                    yield ": keepalive\n\n"
        finally:
            conn.close()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/tasks/{task_id}", tags=["tasks"], summary="One task with its step log, audit trail and approval")
def get_task(task_id: str, conn: Conn) -> dict[str, Any]:
    task = conn.execute("SELECT * FROM agent_tasks WHERE id = %s", (task_id,)).fetchone()
    if task is None:
        raise ApiError(404, "not_found", f"No task {task_id}.")
    if task["loop"] == "strategy" and not _strategy_exec():
        raise ApiError(404, "not_found", f"No task {task_id}.")
    steps = conn.execute("SELECT * FROM agent_task_steps WHERE task_id = %s ORDER BY id", (task_id,)).fetchall()
    audit = conn.execute("SELECT * FROM audit_log WHERE task_id = %s ORDER BY id", (task_id,)).fetchall()
    apr = conn.execute("SELECT * FROM approvals WHERE task_id = %s ORDER BY created_at DESC LIMIT 1",
                       (task_id,)).fetchone()
    return {"task": _jsonable(task), "steps": [_jsonable(s) for s in steps], "audit": [_jsonable(a) for a in audit],
            "approval": _jsonable(apr) if apr else None}


# -- approvals ----------------------------------------------------------------------------------------------
@router.get("/approvals", tags=["approvals"], summary="Approval requests")
def list_approvals(conn: Conn, status: str = "pending") -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM approvals WHERE status = %s ORDER BY created_at DESC LIMIT 100",
                        (status,)).fetchall()
    return {"items": [_jsonable(r) for r in rows]}


def _answer(approval_id: str, approved: bool, conn: Any, by: str | None, settings: ScoutSettings) -> dict[str, Any]:
    apr = conn.execute("SELECT * FROM approvals WHERE id = %s", (approval_id,)).fetchone()
    if apr is None:
        raise ApiError(404, "not_found", f"No approval {approval_id}.")
    if apr["status"] != "pending":
        raise ApiError(409, "already_answered", f"{approval_id} is already {apr['status']}.")
    who = by or settings.ceo_id  # the write token stands for the CEO (the dashboard owner)
    if who != apr["approver_id"]:
        raise ApiError(403, "not_approver", "Only the approver can answer this request.")
    conn.execute("INSERT INTO agent_jobs (kind, payload, dedupe_key) VALUES ('approval', %s, %s) "
                 "ON CONFLICT (dedupe_key) DO NOTHING",
                 (Jsonb({"approval_id": approval_id, "approved": approved, "by": who}), f"approval:{approval_id}"))
    scout_db.notify(conn, "scout_jobs", "approval")
    conn.commit()
    return {"status": "queued", "approval_id": approval_id, "task_id": apr["task_id"], "approved": approved}


@router.post("/approvals/{approval_id}:approve", tags=["approvals"], summary="Approve (the worker resumes the task)")
def approve(approval_id: str, conn: Conn, by: WriteAuth, settings: Settings) -> dict[str, Any]:
    return _answer(approval_id, True, conn, by, settings)


@router.post("/approvals/{approval_id}:reject", tags=["approvals"], summary="Reject (the worker closes the task)")
def reject(approval_id: str, conn: Conn, by: WriteAuth, settings: Settings) -> dict[str, Any]:
    return _answer(approval_id, False, conn, by, settings)


# -- audit --------------------------------------------------------------------------------------------------
@router.get("/audit", tags=["audit"], summary="Every connector read, DM and access decision")
def audit(conn: Conn, task_id: str | None = None, decision: str | None = None,
          limit: Annotated[int, Query(ge=1, le=500)] = 100) -> dict[str, Any]:
    where, args = ["TRUE"], []
    if task_id:
        where.append("task_id = %s")
        args.append(task_id)
    if decision:
        where.append("decision = %s")
        args.append(decision.upper())
    rows = conn.execute(f"SELECT * FROM audit_log WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
                        [*args, limit]).fetchall()
    return {"items": [_jsonable(r) for r in rows]}


# -- invoices -----------------------------------------------------------------------------------------------
@router.get("/transactions/{transaction_id}/invoice", tags=["invoices"],
            summary="The invoice filed for a bank booking: header, line items and the document text Scout read")
def transaction_invoice(transaction_id: str, conn: Conn) -> dict[str, Any]:
    """Read-only, like the other Scout reads. `document` is the latest filing step of an agent task for this
    invoice (text excerpt of the PDF/mail Scout read, its evidence); null when the invoice came from the
    invoicing system without a document."""
    txn = conn.execute(
        "SELECT id, booking_date, type, reference, counterparty, amount, currency, invoice_number, category,"
        " documented, invoice_id FROM transactions WHERE id = %s", (transaction_id,)).fetchone()
    if txn is None:
        raise ApiError(404, "not_found", f"No transaction {transaction_id}.")
    inv = conn.execute(
        "SELECT id, number, supplier, issue_date, due_date, total, currency, source, category, transaction_id"
        " FROM invoices WHERE id = %s OR transaction_id = %s ORDER BY (id = %s) DESC LIMIT 1",
        (txn["invoice_id"], transaction_id, txn["invoice_id"])).fetchone()
    if inv is None:
        raise ApiError(404, "no_invoice", f"No invoice is filed for transaction {transaction_id}.")
    lines = conn.execute(
        "SELECT position, description, quantity, unit_price, amount, category FROM invoice_lines"
        " WHERE invoice_id = %s ORDER BY position, id", (inv["id"],)).fetchall()
    step = conn.execute(
        "SELECT s.task_id, s.ts, s.source, s.detail->>'text_excerpt' AS text_excerpt, s.evidence"
        " FROM agent_task_steps s WHERE s.detail->>'invoice_id' = %s ORDER BY s.id DESC LIMIT 1",
        (inv["id"],)).fetchone()
    task = conn.execute("SELECT id FROM agent_tasks WHERE transaction_id = %s ORDER BY updated_at DESC LIMIT 1",
                        (transaction_id,)).fetchone()
    document = None
    if step:
        document = {"task_id": step["task_id"], "filed_at": _jsonable({"ts": step["ts"]})["ts"],
                    "source": step["source"], "text_excerpt": step["text_excerpt"] or None, "evidence": step["evidence"]}
    return {"transaction": _jsonable(dict(txn)), "invoice": _jsonable(dict(inv)),
            "lines": [_jsonable(dict(line)) for line in lines], "document": document,
            "task_id": task["id"] if task else (step["task_id"] if step else None)}


# -- connections --------------------------------------------------------------------------------------------
_slack_cache: dict[str, Any] = {}


def slack_scopes(token: str) -> list[str] | None:
    if not token:
        return None
    hit = _slack_cache.get("scopes")
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        r = httpx.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        scopes = [s for s in r.headers.get("x-oauth-scopes", "").split(",") if s]
    except httpx.HTTPError:
        return None
    _slack_cache["scopes"] = (time.monotonic(), scopes)
    return scopes


@router.get("/connections", tags=["connections"], summary="Systems Scout reads, their status and Slack scopes")
def connections(conn: Conn, settings: Settings) -> dict[str, Any]:
    rows = conn.execute("SELECT c.*, (cc.connection_id IS NOT NULL) AS has_credentials FROM connections c "
                        "LEFT JOIN connection_credentials cc ON cc.connection_id = c.id ORDER BY c.kind, c.id").fetchall()
    from cfo.tenancy import legacy_connectors_enabled
    items = [_jsonable(r) for r in rows]
    if not legacy_connectors_enabled():
        return {"items": items}
    for it in items:
        if it["id"] == "slack":
            granted = slack_scopes(settings.slack_bot_token)
            it["scopes"] = {"granted": granted, "missing": [s for s in SLACK_SCOPES if granted is not None
                                                            and s not in granted]}
        if it["id"] == "gmail":
            it["oauth_configured"] = bool(settings.google_client_id and settings.google_client_secret
                                          and settings.connections_key and settings.oauth_state_secret)
    return {"items": items}


@router.post("/connections/{connection_id}:enable", tags=["connections"])
def enable(connection_id: str, conn: Conn, _: WriteAuth) -> dict[str, Any]:
    return _set_enabled(conn, connection_id, True)


@router.post("/connections/{connection_id}:disable", tags=["connections"])
def disable(connection_id: str, conn: Conn, _: WriteAuth) -> dict[str, Any]:
    return _set_enabled(conn, connection_id, False)


def _set_enabled(conn: Any, connection_id: str, on: bool) -> dict[str, Any]:
    row = conn.execute("UPDATE connections SET enabled = %s WHERE id = %s RETURNING *", (on, connection_id)).fetchone()
    conn.execute("INSERT INTO audit_log (actor, action, target, resource, decision) VALUES "
                 "('dashboard', %s, %s, 'connection', 'ALLOW')", ("enable" if on else "disable", connection_id))
    conn.commit()
    if row is None:
        raise ApiError(404, "not_found", f"No connection {connection_id}.")
    return _jsonable(row)


# -- Google OAuth (one button: Connect Google) --------------------------------------------------------------
def _sign(secret: str, body: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_state(secret: str, now: float | None = None, ttl: int = 600) -> str:
    body = f"{secrets.token_urlsafe(12)}.{int((time.time() if now is None else now) + ttl)}"
    return f"{body}.{_sign(secret, body)}"


def check_state(secret: str, state: str, now: float | None = None) -> bool:
    try:
        nonce, exp, sig = state.split(".")
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(secret, f"{nonce}.{exp}")):
        return False
    return exp.isdigit() and int(exp) >= (time.time() if now is None else now)


def _need_oauth(settings: ScoutSettings) -> None:
    missing = [n for n, v in (("GOOGLE_CLIENT_ID", settings.google_client_id),
                              ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
                              ("CONNECTIONS_KEY", settings.connections_key),
                              ("OAUTH_STATE_SECRET", settings.oauth_state_secret)) if not v]
    if missing:
        raise ApiError(503, "oauth_not_configured", f"Google sign-in is not configured: set {', '.join(missing)}.")


@router.get("/connections/google/start", tags=["connections"], summary="URL of Google's consent screen")
def google_start(settings: Settings, _: WriteAuth) -> dict[str, str]:
    _need_oauth(settings)
    params = {"client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
              "response_type": "code", "scope": GOOGLE_SCOPES, "access_type": "offline", "prompt": "consent",
              "include_granted_scopes": "true", "state": make_state(settings.oauth_state_secret)}
    return {"url": f"{GOOGLE_AUTH}?{urlencode(params)}"}


def exchange_code(code: str, settings: ScoutSettings) -> dict[str, Any]:
    r = httpx.post(GOOGLE_TOKEN, data={"code": code, "client_id": settings.google_client_id,
                                       "client_secret": settings.google_client_secret,
                                       "redirect_uri": settings.google_redirect_uri,
                                       "grant_type": "authorization_code"}, timeout=10)
    r.raise_for_status()
    return r.json()


def _email_of(id_token: str | None) -> str | None:
    """The account's address from the id_token Google just sent us over TLS (claims only, no signature needed)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
    except Exception:  # noqa: BLE001
        return None


@router.get("/connections/google/callback", tags=["connections"], include_in_schema=False)
def google_callback(conn: Conn, settings: Settings, code: str = "", state: str = "",
                    error: str = "") -> RedirectResponse:
    _need_oauth(settings)
    back = f"{settings.dashboard_url.rstrip('/')}/connections"
    if error:
        return RedirectResponse(f"{back}?google=denied", status_code=302)
    if not check_state(settings.oauth_state_secret, state):
        raise ApiError(400, "bad_state", "The sign-in link expired or was changed. Start again from the dashboard.")
    try:
        tokens = exchange_code(code, settings)
    except httpx.HTTPError:
        return RedirectResponse(f"{back}?google=error", status_code=302)
    refresh = tokens.get("refresh_token")
    if not refresh:
        return RedirectResponse(f"{back}?google=error", status_code=302)
    email = _email_of(tokens.get("id_token"))
    conn.execute("INSERT INTO connection_credentials (connection_id, ciphertext) VALUES ('gmail', %s) "
                 "ON CONFLICT (connection_id) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, updated_at = now()",
                 (seal(refresh, settings.connections_key),))
    conn.execute("UPDATE connections SET status = 'connected', enabled = TRUE, last_error = NULL, "
                 "last_sync_at = now(), config = config || %s WHERE id = 'gmail'",
                 (Jsonb({"email": email or "me", "label": settings.google_mail_label}),))
    conn.execute("INSERT INTO audit_log (actor, action, target, resource, decision) VALUES "
                 "('dashboard', 'connect', 'mail:gmail', %s, 'ALLOW')", (f"Google account {email or '?'}",))
    conn.commit()
    return RedirectResponse(f"{back}?google=connected", status_code=302)


# -- demo -----------------------------------------------------------------------------------------------------
@router.post("/demo/play/{scenario}", tags=["demo"], summary="Post a demo payment to the bank mock (SCOUT_DEMO=1)")
def play(scenario: str, settings: Settings, _: WriteAuth) -> dict[str, Any]:
    if not settings.scout_demo:
        raise ApiError(404, "not_found", "Demo mode is off.")
    s = SCENARIOS.get(scenario)
    if s is None:
        raise ApiError(404, "not_found", f"Unknown scenario {scenario}.")
    body = {"counterparty": s.counterparty, "amount": str(s.amount), "reference": s.reference, "type": s.type,
            "holder_employee_id": s.holder_employee_id, "card_last4": s.card_last4}
    try:
        r = httpx.post(f"{settings.bank_api_url.rstrip('/')}/mock/transactions", json=body, timeout=5)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ApiError(502, "bank_mock_unavailable", f"The bank mock did not accept the payment: {e}") from None
    return {"scenario": scenario, "transaction": r.json()}
