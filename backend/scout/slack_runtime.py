"""Singleton Socket Mode routing to tenant-bound Slack installations.

The app-level xapp token only transports events. Bot credentials always come from a verified,
connected onboarding installation, sealed with that organization's key.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from cfo.tenancy import Tenant, bind_tenant, current_tenant
from psycopg.rows import dict_row
from slack_bolt.authorization import AuthorizeResult
from slack_sdk import WebClient

from scout import db as scout_db
from scout.config import ScoutSettings
from scout.connectors.crypto import derive_key, open_

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveInstallation:
    tenant: Tenant
    team_id: str
    bot_user_id: str
    token: str
    generation: str
    config: dict[str, Any]
    # When this organization saved its Slack token: with two organizations on one Slack team, the first one keeps it.
    connected_at: datetime | None = field(default=None, compare=False)


def select_installation(
    installs: Iterable[ActiveInstallation], team_id: str | None
) -> ActiveInstallation | None:
    """The organization that owns a Slack team. If a second organization connected the same Slack workspace,
    the one that connected first keeps it (deterministic: earliest token, then organization id); new duplicate
    connections are refused at install time (`team_owner`)."""
    if not team_id:
        return None
    matches = [i for i in installs if i.team_id == team_id]
    if not matches:
        return None
    return min(matches, key=lambda i: (i.connected_at is None, i.connected_at or datetime.max, i.tenant.id))


def dispatch(
    installs: Iterable[ActiveInstallation],
    team_id: str | None,
    handler: Callable[..., Any],
    *args: Any,
) -> bool:
    installation = select_installation(installs, team_id)
    if installation is None:
        return False
    with bind_tenant(installation.tenant):
        handler(*args)
    return True


def _row(conn_factory: Callable[..., Any]) -> dict[str, Any] | None:
    with conn_factory() as conn:
        return conn.execute(
            "SELECT c.config, cc.ciphertext, cc.updated_at FROM connections c "
            "JOIN connection_credentials cc ON cc.connection_id=c.id "
            "WHERE c.id='slack' AND c.provider='slack_oauth' AND c.enabled "
            "AND c.status='connected' AND c.config->>'managed_by'='onboarding' "
            "AND c.config->>'authorized'='true' "
            "AND EXISTS (SELECT 1 FROM scout_auth.memberships m WHERE m.organization_id=%s "
            "AND m.employee_id=c.config->>'installer_employee_id' AND m.role='owner')",
            (current_tenant().id,),
        ).fetchone()


def installation_current(
    installation: ActiveInstallation, settings: ScoutSettings
) -> bool:
    """Check the installed token generation before any remote Slack API effect."""
    try:
        with bind_tenant(installation.tenant):
            row = _row(scout_db.factory(settings.database_url))
        return bool(
            row
            and row["config"] == installation.config
            and hashlib.sha256(bytes(row["ciphertext"])).hexdigest()
            == installation.generation
        )
    except Exception:
        log.warning("could not revalidate Slack installation", exc_info=True)
        return False


class GuardedSlackClient(WebClient):
    """A cached client must stop working immediately on local disconnect or token rotation."""

    def __init__(self, installation: ActiveInstallation, settings: ScoutSettings):
        super().__init__(token=installation.token, timeout=10)
        self._installation, self._settings = installation, settings

    def api_call(self, *args: Any, **kwargs: Any) -> Any:
        if not installation_current(self._installation, self._settings):
            raise PermissionError("Slack installation is no longer active")
        return super().api_call(*args, **kwargs)


def load_installations(settings: ScoutSettings) -> list[ActiveInstallation]:
    """Discover explicitly enrolled organizations, then read each connection *inside* its validated schema.

    No global bot token and no fallback tenant. Invalid or incomplete rows are ignored.
    Duplicate team IDs are retained here so select_installation can fail closed.
    """
    if not settings.connections_key:
        return []
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, connect_timeout=5
    ) as conn:
        organizations = conn.execute(
            "SELECT o.id,o.slug,o.schema_name,o.legacy, "
            "(SELECT m.employee_id FROM scout_auth.memberships m WHERE m.organization_id=o.id "
            "AND m.role='owner' ORDER BY m.user_id LIMIT 1) AS employee_id "
            "FROM scout_auth.organizations o WHERE NOT o.legacy"
        ).fetchall()
    installs = []
    for org in organizations:
        if not org["employee_id"]:
            continue
        try:
            tenant = Tenant(**org, role="worker")
            with bind_tenant(tenant):
                row = _row(scout_db.factory(settings.database_url))
            if row is None:
                continue
            config, ciphertext = row["config"], bytes(row["ciphertext"])
            team_id, bot_user_id = config.get("team_id"), config.get("bot_user_id")
            if not (
                team_id
                and bot_user_id
                and config.get("installer_user_id")
                and config.get("installer_employee_id")
            ):
                continue
            token = open_(
                ciphertext, derive_key(settings.connections_key, tenant.id, "slack")
            )
            installs.append(
                ActiveInstallation(
                    tenant,
                    team_id,
                    bot_user_id,
                    token,
                    hashlib.sha256(ciphertext).hexdigest(),
                    config,
                    row.get("updated_at"),
                )
            )
        except Exception:
            log.warning("ignoring unusable tenant Slack installation", exc_info=True)
    return installs


def team_owner(database_url: str, team_id: str, exclude_organization: str) -> dict[str, Any] | None:
    """The organization that owns this Slack team by the same rule as routing (a usable onboarding installation,
    earliest token first), other than `exclude_organization`: {id, slug, name, schema_name}, or None."""
    from psycopg import sql

    found: list[tuple[Any, dict[str, Any]]] = []
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as conn:
        organizations = conn.execute(
            "SELECT id::text AS id, slug, name, schema_name FROM scout_auth.organizations WHERE id <> %s",
            (exclude_organization,)).fetchall()
        for org in organizations:
            try:
                row = conn.execute(sql.SQL(
                    "SELECT cc.updated_at FROM {s}.connections c JOIN {s}.connection_credentials cc "
                    "ON cc.connection_id = c.id WHERE c.id='slack' AND c.enabled AND c.status='connected' "
                    "AND c.config->>'team_id' = %s AND c.config->>'authorized' = 'true' "
                    "AND c.config->>'installer_user_id' IS NOT NULL").format(s=sql.Identifier(org["schema_name"])),
                    (team_id,)).fetchone()
            except Exception:  # noqa: BLE001 - an uninitialized schema
                conn.rollback()
                continue
            if row:
                found.append((row["updated_at"], org))
    if not found:
        return None
    return min(found, key=lambda f: (f[0] is None, f[0] or datetime.max, f[1]["id"]))[1]


def join_organization(database_url: str, org: dict[str, Any], session_hash: str, slack_user_id: str | None) -> bool:
    """Add the signed-in account (by session) to `org` as a member, with an employee linked to its Slack user.
    True when they are a member afterwards. Used when a second organization connects an already-owned Slack team."""
    from psycopg import sql

    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as conn:
        user = conn.execute("SELECT u.id, u.name FROM scout_auth.sessions s JOIN scout_auth.users u ON u.id = s.user_id "
                            "WHERE s.token_hash = %s AND s.expires_at > now()", (session_hash,)).fetchone()
        if user is None:
            return False
        if conn.execute("SELECT 1 FROM scout_auth.memberships WHERE organization_id = %s AND user_id = %s",
                        (org["id"], user["id"])).fetchone():
            return True
        schema = sql.Identifier(org["schema_name"])
        emp = None
        if slack_user_id:
            row = conn.execute(sql.SQL("SELECT id FROM {}.employees WHERE slack_user_id = %s ORDER BY id LIMIT 1")
                               .format(schema), (slack_user_id,)).fetchone()
            emp = row["id"] if row else None
        if emp is None:
            emp = str(user["id"])
            conn.execute(sql.SQL("INSERT INTO {}.employees (id, name, role, slack_user_id, slack_name) "
                                 "VALUES (%s, %s, 'Staff', %s, %s) ON CONFLICT (id) DO NOTHING").format(schema),
                         (emp, user["name"], slack_user_id, user["name"]))
        conn.execute("INSERT INTO scout_auth.memberships (organization_id, user_id, role, employee_id) "
                     "VALUES (%s, %s, 'member', %s) ON CONFLICT DO NOTHING", (org["id"], user["id"], emp))
        if slack_user_id:
            try:
                conn.execute(sql.SQL("INSERT INTO {}.slack_members (slack_user_id, status, employee_id, registered_at) "
                                     "VALUES (%s, 'registered', %s, now()) ON CONFLICT (slack_user_id) DO UPDATE SET "
                                     "status='registered', employee_id=EXCLUDED.employee_id").format(schema),
                             (slack_user_id, emp))
            except Exception:  # noqa: BLE001 - table not created yet in that organization
                conn.rollback()
                conn.execute("INSERT INTO scout_auth.memberships (organization_id, user_id, role, employee_id) "
                             "VALUES (%s, %s, 'member', %s) ON CONFLICT DO NOTHING", (org["id"], user["id"], emp))
        conn.commit()
    log.info("added a second organization's owner to %s, which owns the Slack team", org["slug"])
    return True


def authorize(
    installs: Iterable[ActiveInstallation], team_id: str | None
) -> AuthorizeResult | None:
    install = select_installation(installs, team_id)
    if install is None:
        return None
    return AuthorizeResult(
        enterprise_id=None,
        team_id=install.team_id,
        bot_user_id=install.bot_user_id,
        bot_token=install.token,
    )


class SlackSupervisor:
    """One app-wide event receiver and one isolated worker per unambiguous installation.

    A new installation becomes active on the next reconciliation; disconnect/reconnect replaces the
    worker and cached SlackUI. All callbacks revalidate the connection before contacting Slack.
    """

    def __init__(
        self,
        settings: ScoutSettings,
        *,
        loader: Callable[
            [ScoutSettings], list[ActiveInstallation]
        ] = load_installations,
        start: Callable[[ActiveInstallation, Any], Any] | None = None,
    ):
        import threading

        self.settings, self._loader = settings, loader
        self._start = start or self._start_worker
        self._lock = threading.RLock()
        self._active: list[ActiveInstallation] = []
        self._members: dict[tuple[str, str], tuple[bool, float]] = {}  # (tenant, channel) -> (is member, checked)
        self._running: dict[
            str, tuple[ActiveInstallation, Any, Callable[[], None]]
        ] = {}

    def _start_worker(
        self, installation: ActiveInstallation, stop: Any
    ) -> tuple[Any, Callable[[], None]]:
        import threading

        from scout.worker import _build

        worker, _ = _build(self.settings, installation=installation)
        thread = threading.Thread(
            target=worker.run,
            args=(stop,),
            name=f"scout-{installation.tenant.id[:8]}",
            daemon=True,
        )
        thread.start()
        return worker.ctx.notifier, lambda: (stop.set(), thread.join(timeout=5))

    def reconcile(self) -> None:
        import threading

        installations = self._loader(self.settings)
        # Never start a listener/worker whose Slack team is shared by two active tenants.
        unique = [
            i
            for i in installations
            if select_installation(installations, i.team_id) is i
        ]
        desired = {i.tenant.id: i for i in unique}
        with self._lock:
            for oid, (old, _, close) in list(self._running.items()):
                new = desired.get(oid)
                if (
                    new
                    and old.generation == new.generation
                    and old.config == new.config
                ):
                    continue
                close()
                del self._running[oid]
            for oid, install in desired.items():
                if oid in self._running:
                    continue
                try:
                    stop = threading.Event()
                    with bind_tenant(install.tenant):
                        started = self._start(install, stop)
                    ui, close = (
                        started if isinstance(started, tuple) else (None, started)
                    )
                    self._running[oid] = (install, ui, close)
                except Exception:
                    log.exception("could not start tenant Slack worker for %s", oid)
            self._active = [i for i in unique if i.tenant.id in self._running]

    def current(self, team_id: str | None) -> ActiveInstallation | None:
        with self._lock:
            install = select_installation(self._active, team_id)
        return (
            install
            if install and installation_current(install, self.settings)
            else None
        )

    def _channel_allowed(self, install: ActiveInstallation, channel: str | None) -> bool:
        """A channel chosen at install, or one someone added Scout to later (checked live with Slack, cached)."""
        import time

        if not channel:
            return False
        if channel in (install.config.get("channels") or []):
            return True
        key = (install.tenant.id, channel)
        hit = self._members.get(key)
        if hit and time.monotonic() - hit[1] < (300 if hit[0] else 30):
            return hit[0]
        try:
            info = WebClient(token=install.token, timeout=10).conversations_info(channel=channel)
            member = bool((info.get("channel") or {}).get("is_member"))
        except Exception:
            log.warning("could not check Scout's membership of a Slack channel", exc_info=True)
            member = False
        self._members[key] = (member, time.monotonic())
        return member

    def route(
        self,
        team_id: str | None,
        kind: str,
        event: dict[str, Any],
        handler: Callable[[Any, dict[str, Any]], None],
    ) -> bool:
        install = self.current(team_id)
        if not install:
            return False
        in_channel = kind == "mention" or (
            kind == "message" and event.get("channel_type") in ("channel", "group")
        )
        if in_channel and not self._channel_allowed(install, event.get("channel")):
            return False
        with self._lock:
            entry = self._running.get(install.tenant.id)
            if not entry or entry[0].generation != install.generation:
                return False
            ui = entry[1]
        with bind_tenant(install.tenant):
            handler(ui, event)
        return True

    def bolt_app(self) -> Any:
        from slack_bolt import App

        def authorize_team(
            enterprise_id: str | None = None, team_id: str | None = None, **_: Any
        ) -> AuthorizeResult | None:
            if not select_installation(self._active, team_id):
                self.reconcile()  # a freshly connected workspace need not wait for the background refresh
            install = self.current(team_id)
            return authorize([install], team_id) if install else None

        app = App(
            authorize=authorize_team,
            client=WebClient(token=""),
            request_verification_enabled=False,
        )

        @app.event("message")
        def message(event: dict[str, Any], context: Any) -> None:
            self.route(
                context.get("team_id"),
                "message",
                event,
                lambda ui, data: ui.on_message(data),
            )

        @app.event("app_mention")
        def mention(event: dict[str, Any], context: Any) -> None:
            self.route(
                context.get("team_id"),
                "mention",
                event,
                lambda ui, data: ui.on_mention(data),
            )

        @app.event("team_join")
        def team_join(event: dict[str, Any], context: Any) -> None:
            self.route(
                context.get("team_id"),
                "team_join",
                event,
                lambda ui, data: ui.on_team_join(data),
            )

        @app.action("scout_join")
        def join_click(ack: Callable[[], None]) -> None:
            ack()  # the join button is a link; nothing to route

        @app.action("scout_approve")
        @app.action("scout_reject")
        def action(ack: Callable[[], None], body: dict[str, Any], context: Any) -> None:
            ack()
            self.route(
                context.get("team_id"),
                "action",
                body,
                lambda ui, data: ui.on_action(lambda: None, data),
            )

        return app

    def run(self, stop: Any) -> None:
        while not stop.is_set():
            try:
                self.reconcile()
            except Exception:
                log.exception("could not refresh tenant Slack installations")
            stop.wait(2)
        self.close()

    def close(self) -> None:
        with self._lock:
            self._active = []
            for _, _, close in self._running.values():
                close()
            self._running.clear()
