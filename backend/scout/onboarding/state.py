"""Setup persistence and the derived wire model.

`scout_auth.workspace_setup` stores only decisions (skipped), last failure messages and the non-secret network answers.
What is actually connected is always derived from the tenant-local `connections` rows, so the two can never disagree
and a stale decision can never fake a connector status."""

from __future__ import annotations

from typing import Any

from cfo.api.errors import ApiError
from cfo.tenancy import Tenant
from psycopg.types.json import Jsonb

from scout.config import ScoutSettings
from scout.onboarding.catalog import (
    CONNECTION,
    MANAGED_BY,
    STEP_ORDER,
    STEP_PROVIDER,
    SetupUpdate,
    app_origin,
)

CONFIGURED = {"connected", "sandbox_connected", "skipped"}
SANDBOX_STEPS = {"bank", "ksef"}


# -- server prerequisites ---------------------------------------------------------------------------------------
def missing_config(step_id: str, s: ScoutSettings) -> list[str]:
    """Environment variable NAMES (never values) the operator must still provide for this step."""
    key = [] if len(s.connections_key) >= 32 else ["CONNECTIONS_KEY"]
    if step_id == "bank":
        return key
    if step_id == "email":
        need = [("GOOGLE_CLIENT_ID", s.google_client_id), ("GOOGLE_CLIENT_SECRET", s.google_client_secret),
                ("SCOUT_APP_ORIGIN", app_origin(s.scout_app_origin))]
        return [n for n, v in need if not v] + key
    if step_id == "slack":
        need = [("SLACK_CLIENT_ID", s.slack_client_id), ("SLACK_CLIENT_SECRET", s.slack_client_secret),
                ("SLACK_APP_TOKEN", s.slack_app_token),
                ("SCOUT_APP_ORIGIN", app_origin(s.scout_app_origin))]
        return [n for n, v in need if not v] + key
    return []


# -- persistence --------------------------------------------------------------------------------------------------
def fetch(conn: Any, organization_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT revision, status, steps, network FROM scout_auth.workspace_setup WHERE organization_id=%s"
        + (" FOR UPDATE" if lock else ""), (organization_id,)).fetchone()


def _managed_connections(conn: Any) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT id, provider, status, config, last_error FROM connections "
                        "WHERE id = ANY(%s) AND config->>'managed_by' = %s",
                        ([c[0] for c in CONNECTION.values()], MANAGED_BY)).fetchall()
    return {r["id"]: r for r in rows}


def record(conn: Any, organization_id: str, step_id: str, result: dict[str, str] | None = None,
           reopen: bool = False) -> None:
    """Server-driven change (connect, callback, disconnect): clear or set the step's last result, bump the revision.
    Creates the setup row for an existing organization that never had one."""
    conn.execute("INSERT INTO scout_auth.workspace_setup(organization_id, status) VALUES (%s, 'in_progress') "
                 "ON CONFLICT (organization_id) DO NOTHING", (organization_id,))
    row = fetch(conn, organization_id, lock=True)
    steps = dict(row["steps"])
    if result:
        steps[step_id] = {"result": result}
    else:
        steps.pop(step_id, None)
    status = "in_progress" if row["status"] == "pending" else row["status"]
    if status == "completed" and (reopen or (result and result.get("state") != "not_started")):
        status = "in_progress"  # a completed setup stops being complete; "dismissed" stays the owner's decision
    conn.execute("UPDATE scout_auth.workspace_setup SET revision = revision + 1, status = %s, steps = %s, "
                 "updated_at = now() WHERE organization_id = %s", (status, Jsonb(steps), organization_id))


# -- derivation ---------------------------------------------------------------------------------------------------
def _derive(step_id: str, meta: dict[str, Any], row: dict[str, Any] | None, pending: set[str], s: ScoutSettings,
            docs: int, network: dict[str, Any] | None) -> dict[str, Any]:
    provider = STEP_PROVIDER[step_id]
    out = {"id": step_id, "state": "not_started", "provider": provider, "message": None, "display": {}}
    decision, result = meta.get("decision"), meta.get("result")
    if step_id == "network":
        return _derive_network(out, decision, network)
    cfg = (row or {}).get("config") or {}
    if step_id == "slack" and row and row["status"] == "connected" and not s.slack_app_token:
        out["display"] = {"authorized": True, "workspace": cfg.get("team"), "team_id": cfg.get("team_id")}
        out.update(state="needs_configuration", message="Slack events are unavailable: configure SLACK_APP_TOKEN.")
    elif step_id == "slack" and row and row["status"] == "connected" and not (
            cfg.get("installer_user_id") and cfg.get("installer_employee_id")):
        out["display"] = {"authorized": True, "workspace": cfg.get("team"), "team_id": cfg.get("team_id")}
        out.update(state="needs_configuration", message="Disconnect, then reconnect Slack to link your account and activate messages.")
    elif row and row["status"] == "connected":
        out["state"] = "sandbox_connected" if step_id in SANDBOX_STEPS else "connected"
        out["message"], out["display"] = _connected(step_id, cfg, docs)
        if step_id == "slack" and result and result.get("message"):
            out["message"] = result["message"]
    elif step_id == "slack" and row and cfg.get("authorized") and row["status"] == "disconnected":
        # installed and identity-verified, but no channel is readable yet: honest prerequisite, never "connected"
        out["display"] = {"authorized": True, "workspace": cfg.get("team"), "team_id": cfg.get("team_id")}
        out.update(state="needs_configuration", message="Slack is authorized, but Scout could not join any channel. Disconnect and connect Slack again, or add channel IDs below.")
        if result:
            out.update(state=result.get("state", "failed"), message=result.get("message"))
    elif row and row["status"] == "error":
        out.update(state="failed", message=row.get("last_error") or "The connection failed.")
    elif provider in pending:
        out.update(state="connecting", message="Waiting for authorization to finish.")
    elif result:
        out.update(state=result.get("state", "failed"), message=result.get("message"))
        if result.get("code") == "revocation_unconfirmed":
            out["display"] = {"revocation_unconfirmed": True}
    elif decision == "skipped":
        out.update(state="skipped", message="Skipped. You can set this up later from Connections.")
    else:
        missing = missing_config(step_id, s)
        if missing:
            out.update(state="needs_configuration",
                       message="Not configured on this server: " + ", ".join(missing) + ".")
    return out


def _connected(step_id: str, cfg: dict[str, Any], docs: int) -> tuple[str, dict[str, str | bool | None]]:
    if step_id == "bank":
        return ("Read-only bunq sandbox account. Scout cannot make transfers.",
                {"environment": "sandbox", "accounts": str(cfg.get("accounts", 0)), "read_only": True})
    if step_id == "invoicing":
        return ("Built-in invoicing is active. It stays empty until documents exist.",
                {"system": "built-in", "documents": str(docs)})
    if step_id == "ksef":
        return ("KSeF test environment authenticated read-only. No production tax submission.",
                {"environment": "test", "nip": cfg.get("nip"), "read_only": True})
    if step_id == "email":
        return ("Mailbox authorized read-only with Google.",
                {"mailbox": cfg.get("email"), "label": cfg.get("label"), "scope": "gmail.readonly", "read_only": True})
    channels = cfg.get("channels") or []
    message = ("Slack DMs are enabled. No channels were joined; reconnect to add channels." if not channels else
               "Scout can respond to linked people in these channels and DMs. Reconnect for channels created later.")
    return (message,
            {"authorized": True, "workspace": cfg.get("team"), "team_id": cfg.get("team_id"),
             "channels": str(len(channels)), "channel_ids": ", ".join(channels)})


def _derive_network(out: dict[str, Any], decision: str | None, network: dict[str, Any] | None) -> dict[str, Any]:
    if network:
        out["display"] = {"required": network["required"], "provider": network["provider"] or None,
                          "protocol": network["protocol"] or None, "systems": ", ".join(network["systems"]),
                          "administrator": network["administrator"] or None, "company_only": network["company_only"]}
        out["provider"] = network["provider"] or None
    if decision == "skipped":
        out.update(state="skipped", message="Skipped. You can answer these questions later from Connections.")
    elif network and not network["required"]:
        out.update(state="skipped", message="No private network access is needed.")
    elif network:
        out.update(state="needs_configuration",
                   message="Your administrator must set up private access. Scout does not install VPNs or change routing.")
    return out


def build(conn: Any, tenant: Tenant, s: ScoutSettings, row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:  # never onboarded: no write, no auto-onboarding
        return {"version": 1, "status": "not_started", "revision": 0, "network": None,
                "steps": [{"id": sid, "state": "not_started", "provider": STEP_PROVIDER[sid], "message": None,
                           "display": {}} for sid in STEP_ORDER]}
    connections = {} if tenant.legacy else _managed_connections(conn)
    pending = {r["provider"] for r in conn.execute(
        "SELECT provider FROM scout_auth.setup_oauth_states WHERE organization_id=%s AND expires_at>now()",
        (tenant.id,)).fetchall()}
    docs = 0
    if "invoicing" in connections:
        docs = conn.execute("SELECT count(*) AS n FROM invoices").fetchone()["n"]
    steps = [_derive(sid, row["steps"].get(sid) or {}, connections.get(CONNECTION[sid][0]) if sid in CONNECTION else None,
                     pending, s, docs, row["network"]) for sid in STEP_ORDER]
    status = row["status"]
    if status == "completed" and any(x["state"] not in CONFIGURED for x in steps):
        status = "in_progress"  # derived honestly: a runtime error or a changed connection reopens a finished setup
    return {"version": 1, "status": status, "revision": row["revision"], "steps": steps,
            "network": row["network"]}


# -- PATCH --------------------------------------------------------------------------------------------------------
def update(conn: Any, tenant: Tenant, s: ScoutSettings, body: SetupUpdate) -> dict[str, Any]:
    current = fetch(conn, tenant.id, lock=True)
    exists = current is not None
    if not exists:
        if body.revision != 0:
            raise ApiError(409, "conflict", "The setup changed elsewhere. Reload and try again.")
        current = {"revision": 0, "status": "not_started", "steps": {}, "network": None}
    elif current["revision"] != body.revision:
        raise ApiError(409, "conflict", "The setup changed elsewhere. Reload and try again.")
    status, steps, network = current["status"], dict(current["steps"]), current["network"]
    engaged = "in_progress" if status in ("pending", "not_started") else status
    if body.action == "skip":
        status = "dismissed"
    elif body.action == "resume":
        status = "in_progress"
    elif body.action == "step":
        assert body.step and body.state
        if body.state == "skipped":
            steps[body.step] = {"decision": "skipped"}
        else:
            steps.pop(body.step, None)
        status = engaged
    elif body.action == "network":
        assert body.network
        network = body.network.model_dump()
        steps.pop("network", None)
        status = engaged
    else:  # complete
        candidate = build(conn, tenant, s, {"revision": body.revision, "status": status, "steps": steps,
                                            "network": network})
        open_steps = [x["id"] for x in candidate["steps"] if x["state"] not in CONFIGURED]
        if open_steps:
            raise ApiError(422, "setup_incomplete",
                           "Every step must be connected or skipped first: " + ", ".join(open_steps) + ".")
        status = "completed"
    if exists:
        conn.execute("UPDATE scout_auth.workspace_setup SET revision = revision + 1, status = %s, steps = %s, "
                     "network = %s, updated_at = now() WHERE organization_id = %s",
                     (status, Jsonb(steps), Jsonb(network) if network is not None else None, tenant.id))
    else:
        inserted = conn.execute(
            "INSERT INTO scout_auth.workspace_setup(organization_id, revision, status, steps, network) "
            "VALUES (%s, 1, %s, %s, %s) ON CONFLICT (organization_id) DO NOTHING RETURNING revision",
            (tenant.id, status if status != "not_started" else "in_progress", Jsonb(steps),
             Jsonb(network) if network is not None else None)).fetchone()
        if inserted is None:
            raise ApiError(409, "conflict", "The setup changed elsewhere. Reload and try again.")
    return build(conn, tenant, s, fetch(conn, tenant.id))
