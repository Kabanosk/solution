"""The Socket Mode receiver selects one installed workspace, never a shared global bot or another tenant."""

from __future__ import annotations

from cfo.tenancy import Tenant, bind_tenant, current_tenant
from scout.slack_runtime import ActiveInstallation, dispatch, select_installation


def installation(oid: str, team: str, *, generation: str = "g1") -> ActiveInstallation:
    from uuid import UUID

    tenant = Tenant(
        id=oid,
        slug=f"workspace-{oid[:8]}",
        schema_name="tenant_" + UUID(oid).hex,
        employee_id="owner",
        role="worker",
    )
    return ActiveInstallation(
        tenant=tenant,
        team_id=team,
        bot_user_id="U_BOT",
        token="xoxb-test",
        generation=generation,
        config={"channels": ["C12345678"]},
    )


def test_ambiguous_workspace_is_never_routed():
    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    b = installation("00000000-0000-4000-8000-000000000022", "T12345678")
    assert select_installation([a, b], "T12345678") is None
    assert select_installation([a], "TOTHER") is None
    assert select_installation([a], "T12345678") is a


def test_event_callback_binds_only_selected_tenant_and_restores_prior_scope():
    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    b = installation("00000000-0000-4000-8000-000000000022", "T87654321")
    seen: list[tuple[str, str]] = []

    def record(event: dict):
        seen.append((current_tenant().id, event["text"]))

    with bind_tenant(b.tenant):
        assert dispatch([a, b], "T12345678", record, {"text": "hello"})
        assert current_tenant() == b.tenant
        assert not dispatch([a, b], "UNKNOWN", record, {"text": "wrong"})
    assert seen == [(a.tenant.id, "hello")]
    assert current_tenant(required=False) is None


def test_duplicate_team_never_executes_callback():
    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    b = installation("00000000-0000-4000-8000-000000000022", "T12345678")
    called: list[dict] = []
    assert not dispatch([a, b], "T12345678", called.append, {"text": "ignored"})
    assert not called


def _save(
    url, org, settings, *, team="T12345678", token="xoxb-private", status="connected"
):
    import psycopg
    from psycopg.rows import dict_row
    from scout.onboarding.service import seal_secret, upsert_connection

    with psycopg.connect(
        url, options=f"-csearch_path={org.schema}", row_factory=dict_row
    ) as conn:
        upsert_connection(
            conn,
            "slack",
            "Slack",
            {
                "team_id": team,
                "bot_user_id": "U_BOT",
                "authorized": True,
                "channels": ["C12345678"],
                "installer_user_id": "U_OWNER",
                "installer_employee_id": org.id,
            },
            seal_secret(settings, org.id, "slack", token),
            status=status,
        )


def test_discovery_requires_active_unique_tenant_installation_and_stops_on_disconnect(
    db_url, org, other_org, settings_factory
):
    import psycopg
    from scout.slack_runtime import installation_current, load_installations

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    _save(db_url, other_org, settings, status="disconnected")
    [active] = load_installations(settings)
    assert active.tenant.id == org.id and active.token == "xoxb-private"
    assert select_installation(load_installations(settings), "T12345678") is not None
    assert installation_current(active, settings)
    _save(db_url, other_org, settings, token="xoxb-other")
    assert select_installation(load_installations(settings), "T12345678") is None
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute("DELETE FROM connections WHERE id='slack'")
    assert not installation_current(active, settings)
    [new] = load_installations(settings)
    assert new.tenant.id == other_org.id and new.token == "xoxb-other"


def test_old_cached_client_is_blocked_after_token_rotated(
    db_url, org, settings_factory
):
    import pytest
    from scout.slack_runtime import GuardedSlackClient, load_installations

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    [old] = load_installations(settings)
    client = GuardedSlackClient(old, settings)
    _save(db_url, org, settings, token="xoxb-new")
    with pytest.raises(PermissionError, match="no longer active"):
        client.api_call("auth.test")


def test_installer_is_linked_by_id_without_guessing_employee_names(
    db_url, org, settings_factory
):
    import psycopg
    from cfo.tenancy import bind_tenant
    from scout import db as scout_db
    from scout.slack_runtime import load_installations
    from scout.worker import tenant_user_map

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "INSERT INTO employees (id, name, role) VALUES ('colleague', 'U_OWNER', 'staff')"
        )
    [active] = load_installations(settings)
    with bind_tenant(active.tenant):
        mapping = tenant_user_map(scout_db.factory(db_url), active)
    assert mapping == {
        org.id: "U_OWNER"
    }  # colleague display name is NOT an identity proof


def test_ambiguous_employee_slack_id_is_not_assigned_to_either_person(
    db_url, org, settings_factory
):
    import psycopg
    from cfo.tenancy import bind_tenant
    from scout import db as scout_db
    from scout.slack_runtime import load_installations
    from scout.worker import tenant_user_map

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "INSERT INTO employees (id, name, role, slack_user_id) VALUES ('colleague', 'Colleague', 'staff', 'U_OWNER')"
        )
    [active] = load_installations(settings)
    with bind_tenant(active.tenant):
        mapping = tenant_user_map(scout_db.factory(db_url), active)
    assert mapping == {}  # explicit conflicting links are denied, never overwritten


def test_supervisor_automatically_starts_new_installations_and_stops_removed_ones(
    monkeypatch,
):
    from scout.slack_runtime import SlackSupervisor

    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    b = installation("00000000-0000-4000-8000-000000000022", "T87654321")
    available = [a]
    built: list[str] = []
    stopped: list[str] = []

    def start(install, stop):
        built.append(current_tenant().id)
        return lambda: stopped.append(install.tenant.id)

    manager = SlackSupervisor(object(), loader=lambda _: list(available), start=start)
    manager.reconcile()
    assert built == [a.tenant.id]
    available.append(b)
    manager.reconcile()
    assert built == [a.tenant.id, b.tenant.id]
    available.remove(a)
    manager.reconcile()
    assert stopped == [a.tenant.id]
    manager.close()
    assert stopped == [a.tenant.id, b.tenant.id]


def test_supervisor_rejects_ambiguous_team_and_wrong_channel(monkeypatch):
    from scout.slack_runtime import SlackSupervisor

    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    b = installation("00000000-0000-4000-8000-000000000022", "T12345678")
    available = [a, b]
    seen = []
    manager = SlackSupervisor(
        object(), loader=lambda _: available, start=lambda i, stop: lambda: None
    )
    monkeypatch.setattr(
        "scout.slack_runtime.installation_current", lambda install, settings: True
    )
    manager.reconcile()
    assert not manager.route(
        "T12345678",
        "mention",
        {"channel": "C12345678"},
        lambda ui, event: seen.append(event),
    )
    available.pop()
    manager.reconcile()
    assert not manager.route(
        "T12345678",
        "mention",
        {"channel": "CWRONG"},
        lambda ui, event: seen.append(event),
    )
    assert manager.route(
        "T12345678",
        "mention",
        {"channel": "C12345678"},
        lambda ui, event: seen.append(event),
    )
    assert seen == [{"channel": "C12345678"}]
    manager.close()


def test_installed_tenant_worker_uses_saved_bot_without_global_fallback(
    db_url, org, settings_factory, monkeypatch
):
    import psycopg
    from cfo.tenancy import bind_tenant
    from scout.slack_runtime import load_installations
    from scout.slack_ui import SlackUI
    from scout.worker import _build

    settings = settings_factory(
        database_url=db_url,
        slack_bot_token="xoxb-global-legacy",
        slack_app_token="xapp-global",
    )
    _save(db_url, org, settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "UPDATE employees SET slack_user_id='U_OWNER' WHERE id=%s", (org.id,)
        )

    class SlackClient:
        def __init__(self, active, _):
            self.token = active.token

        def users_info(self, *, user):
            return {"user": {"real_name": "Owner"}}

    monkeypatch.setattr("scout.slack_runtime.GuardedSlackClient", SlackClient)
    monkeypatch.setattr(
        "scout.guardrails.from_settings",
        lambda _: type("Guard", (), {"enabled": False})(),
    )
    [active] = load_installations(settings)
    with bind_tenant(active.tenant):
        worker, socket = _build(settings, installation=active)
    assert socket is None and worker.tenant == active.tenant
    assert isinstance(worker.ctx.notifier, SlackUI)
    assert worker.ctx.notifier.client.token == "xoxb-private"
    assert (
        worker.ctx.settings.slack_bot_token == ""
    )  # global legacy token remains isolated
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        assert (
            conn.execute(
                "SELECT slack_user_id FROM employees WHERE id=%s", (org.id,)
            ).fetchone()[0]
            == "U_OWNER"
        )


def test_single_bolt_socket_routes_app_mention_without_global_bot_token(monkeypatch):
    from scout.slack_runtime import SlackSupervisor
    from slack_bolt.request import BoltRequest

    a = installation("00000000-0000-4000-8000-000000000021", "T12345678")
    calls = []

    class UI:
        def on_mention(self, event):
            calls.append((current_tenant().id, event["text"]))

        def on_message(self, event):
            pass

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-global-must-not-be-used")
    monkeypatch.setattr("scout.slack_runtime.installation_current", lambda i, s: True)
    manager = SlackSupervisor(
        object(), loader=lambda _: [a], start=lambda i, stop: (UI(), lambda: None)
    )
    manager.reconcile()
    app = manager.bolt_app()
    assert (
        app.client.token == ""
    )  # no global bot credential despite its presence in the environment
    response = app.dispatch(
        BoltRequest(
            body={
                "type": "event_callback",
                "team_id": "T12345678",
                "event": {
                    "type": "app_mention",
                    "channel": "C12345678",
                    "user": "U_OWNER",
                    "ts": "1.0",
                    "text": "<@U_BOT> hi",
                },
            },
            mode="socket_mode",
        )
    )
    assert response.status == 200
    import time

    for _ in range(30):
        if calls:
            break
        time.sleep(0.01)
    assert calls == [(a.tenant.id, "<@U_BOT> hi")]
    manager.close()


def test_disconnected_installation_never_processes_queued_slack_job(
    db_url, org, settings_factory, monkeypatch
):
    from cfo.tenancy import bind_tenant
    from scout import db as scout_db
    from scout.jobs import JobQueue
    from scout.slack_runtime import load_installations
    from scout.worker import HANDLERS, Worker

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    [active] = load_installations(settings)
    with bind_tenant(active.tenant):
        jobs = JobQueue(scout_db.factory(db_url))
        job_id = jobs.enqueue(
            "dm", {"from_id": org.id, "_slack_generation": active.generation}, "dm:old"
        )
        ctx = type("Context", (), {"jobs": jobs, "settings": settings})()
        worker = Worker(ctx)
        worker.installation = active
    called = []
    monkeypatch.setitem(HANDLERS, "dm", lambda *_: called.append(True))
    monkeypatch.setattr("scout.slack_runtime.installation_current", lambda *_: False)
    assert worker.run_one()
    assert not called and jobs.get(job_id).status == "done"


def test_full_event_to_job_to_reply_is_tenant_isolated(
    db_url, org, other_org, settings_factory, monkeypatch
):
    """Real Bolt + real tenant DB queue/worker + fake Slack transport; no external Slack messages."""
    import psycopg
    from cfo.tenancy import current_tenant
    from scout.slack_runtime import SlackSupervisor
    from scout.worker import HANDLERS, _build
    from slack_bolt.request import BoltRequest

    settings = settings_factory(
        database_url=db_url, slack_bot_token="xoxb-global-not-for-tenants"
    )
    _save(db_url, org, settings, team="TEAM_A", token="xoxb-team-a")
    _save(db_url, other_org, settings, team="TEAM_B", token="xoxb-team-b")
    calls = []

    class SlackClient:
        def __init__(self, install, _):
            self.token = install.token

        def users_info(self, *, user):
            return {"user": {"real_name": "Owner"}}

        def reactions_add(self, **kwargs):
            calls.append((self.token, "reaction", kwargs["channel"]))

        def chat_postMessage(self, **kwargs):
            calls.append((self.token, "post", kwargs["channel"]))
            return {"channel": kwargs["channel"], "ts": "9.1"}

        def chat_update(self, **kwargs):
            calls.append((self.token, "update", kwargs["channel"]))

        def reactions_remove(self, **kwargs):
            calls.append((self.token, "reaction_remove", kwargs["channel"]))

    monkeypatch.setattr("scout.slack_runtime.GuardedSlackClient", SlackClient)
    monkeypatch.setattr(
        "scout.guardrails.from_settings",
        lambda _: type("Guard", (), {"enabled": False})(),
    )
    workers = {}

    def start(install, _stop):
        worker, socket = _build(settings, installation=install)
        assert socket is None and current_tenant() == install.tenant
        workers[install.tenant.id] = worker
        return worker.ctx.notifier, lambda: None

    manager = SlackSupervisor(settings, start=start)
    manager.reconcile()
    app = manager.bolt_app()

    def event(team, channel, ts):
        return BoltRequest(
            body={
                "type": "event_callback",
                "team_id": team,
                "event": {
                    "type": "app_mention",
                    "channel": channel,
                    "channel_type": "channel",
                    "user": "U_OWNER",
                    "ts": ts,
                    "text": "<@U_BOT> ping",
                },
            },
            mode="socket_mode",
        )

    assert app.dispatch(event("TEAM_A", "C12345678", "1.0")).status == 200
    import time

    for _ in range(100):
        if any(call[0] == "xoxb-team-a" and call[1] == "post" for call in calls):
            break
        time.sleep(0.01)
    pending = None
    for _ in range(100):
        with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
            pending = conn.execute(
                "SELECT payload FROM agent_jobs WHERE kind='dm'"
            ).fetchone()
        if pending:
            break
        time.sleep(0.01)
    assert pending and pending[0]["from_id"] == org.id

    def answer(ctx, job):
        ctx.notifier.update(job.payload["status_ref"], "Pong", "AUTO")

    monkeypatch.setitem(HANDLERS, "dm", answer)
    assert workers[org.id].run_one()
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        assert conn.execute("SELECT status FROM agent_jobs").fetchone()[0] == "done"
    with psycopg.connect(db_url, options=f"-csearch_path={other_org.schema}") as conn:
        assert conn.execute("SELECT count(*) FROM agent_jobs").fetchone()[0] == 0
    assert ("xoxb-team-a", "update", "C12345678") in calls
    assert not any(c[0] == "xoxb-team-b" for c in calls)
    assert app.dispatch(event("TEAM_B", "C12345678", "2.0")).status == 200
    for _ in range(100):
        with psycopg.connect(
            db_url, options=f"-csearch_path={other_org.schema}"
        ) as conn:
            second = conn.execute(
                "SELECT payload FROM agent_jobs WHERE kind='dm'"
            ).fetchone()
        if second:
            break
        time.sleep(0.01)
    assert second and second[0]["from_id"] == other_org.id
    assert workers[other_org.id].run_one()
    assert ("xoxb-team-b", "update", "C12345678") in calls
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        assert conn.execute("SELECT count(*) FROM agent_jobs").fetchone()[0] == 1
    manager.close()


def test_dashboard_chat_uses_installed_workspace_bot_for_outbound_dm(
    db_url, org, settings_factory, monkeypatch
):
    from cfo.tenancy import bind_tenant
    from scout.chat_api import build_context
    from scout.slack_runtime import load_installations
    from scout.slack_ui import SlackUI

    settings = settings_factory(
        database_url=db_url, slack_bot_token="xoxb-global-wrong"
    )
    _save(db_url, org, settings)
    [active] = load_installations(settings)

    class Client:
        def __init__(self, install, _):
            self.token = install.token

        def users_info(self, *, user):
            return {"user": {"real_name": "Owner"}}

    monkeypatch.setattr("scout.slack_runtime.GuardedSlackClient", Client)
    monkeypatch.setattr(
        "scout.guardrails.from_settings",
        lambda _: type("Guard", (), {"enabled": False})(),
    )
    with bind_tenant(active.tenant):
        ctx = build_context(settings)
    assert isinstance(ctx.notifier, SlackUI)
    assert ctx.notifier.client.token == "xoxb-private"
    assert ctx.notifier._slack == {org.id: "U_OWNER"}


def test_old_connected_installation_requires_reconnect_for_verified_identity(
    api, db_url, org, settings_factory
):
    import psycopg
    from onboarding_helpers import step

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "UPDATE connections SET config = config - 'installer_user_id' - 'installer_employee_id' "
            "WHERE id='slack'"
        )
    slack = step(api.get("/v1/onboarding", headers=org.headers()).json(), "slack")
    assert slack["state"] == "needs_configuration"
    assert (
        "disconnect" in slack["message"].lower()
        and "reconnect" in slack["message"].lower()
    )
    from scout.slack_runtime import load_installations

    assert not [i for i in load_installations(settings) if i.tenant.id == org.id]


def test_dm_only_installation_reports_zero_channels_without_claiming_channel_reads(
    api, db_url, org, settings_factory
):
    import psycopg
    from onboarding_helpers import step

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "UPDATE connections SET config = jsonb_set(config, '{channels}', '[]') WHERE id='slack'"
        )
    slack = step(api.get("/v1/onboarding", headers=org.headers()).json(), "slack")
    assert slack["state"] == "connected" and slack["display"]["channels"] == "0"
    assert "DM" in slack["message"]


def test_manual_channel_policy_change_invalidates_stale_listener(
    db_url, org, settings_factory
):
    import psycopg
    from scout.slack_runtime import installation_current, load_installations

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    [old] = load_installations(settings)
    with psycopg.connect(db_url, options=f"-csearch_path={org.schema}") as conn:
        conn.execute(
            "UPDATE connections SET config=jsonb_set(config, '{channels}', '[]') WHERE id='slack'"
        )
    assert not installation_current(old, settings)


def test_slack_setup_requires_app_level_event_transport(settings_factory):
    from scout.onboarding.state import missing_config

    settings = settings_factory(slack_app_token="")
    assert "SLACK_APP_TOKEN" in missing_config("slack", settings)


def test_connected_slack_is_not_ready_without_socket_token(
    api, db_url, org, settings_factory
):
    from onboarding_helpers import step

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    api.state["settings"] = settings_factory(database_url=db_url, slack_app_token="")
    slack = step(api.get("/v1/onboarding", headers=org.headers()).json(), "slack")
    assert slack["state"] == "needs_configuration"
    assert "SLACK_APP_TOKEN" in slack["message"]


def test_single_bolt_socket_routes_approval_action_to_owning_tenant(monkeypatch):
    import time

    from scout.slack_runtime import SlackSupervisor
    from slack_bolt.request import BoltRequest

    a = installation("00000000-0000-4000-8000-000000000021", "TEAM_A")
    called = []

    class UI:
        def on_action(self, ack, body):
            ack()
            called.append((current_tenant().id, body["user"]["id"]))

    monkeypatch.setattr("scout.slack_runtime.installation_current", lambda *_: True)
    manager = SlackSupervisor(
        object(), loader=lambda _: [a], start=lambda i, stop: (UI(), lambda: None)
    )
    manager.reconcile()
    app = manager.bolt_app()
    request = BoltRequest(
        body={
            "type": "block_actions",
            "team": {"id": "TEAM_A"},
            "user": {"id": "U_OWNER"},
            "actions": [{"action_id": "scout_approve", "value": "apr_1"}],
        },
        mode="socket_mode",
    )
    assert app.dispatch(request).status == 200
    for _ in range(100):
        if called:
            break
        time.sleep(0.01)
    assert called == [(a.tenant.id, "U_OWNER")]
    manager.close()


def test_single_bolt_socket_routes_direct_messages_to_installed_tenant(monkeypatch):
    import time

    from scout.slack_runtime import SlackSupervisor
    from slack_bolt.request import BoltRequest

    a = installation("00000000-0000-4000-8000-000000000021", "TEAM_A")
    seen = []

    class UI:
        def on_message(self, event):
            seen.append((current_tenant().id, event["text"]))

    monkeypatch.setattr("scout.slack_runtime.installation_current", lambda *_: True)
    manager = SlackSupervisor(
        object(), loader=lambda _: [a], start=lambda i, stop: (UI(), lambda: None)
    )
    manager.reconcile()
    app = manager.bolt_app()
    request = BoltRequest(
        body={
            "type": "event_callback",
            "team_id": "TEAM_A",
            "event": {
                "type": "message",
                "channel_type": "im",
                "channel": "D12345678",
                "user": "U_OWNER",
                "ts": "3.0",
                "text": "Hi Scout",
            },
        },
        mode="socket_mode",
    )
    assert app.dispatch(request).status == 200
    for _ in range(100):
        if seen:
            break
        time.sleep(0.01)
    assert seen == [(a.tenant.id, "Hi Scout")]
    manager.close()


def test_removed_installer_owner_cannot_keep_using_cached_slack_connection(
    db_url, org, settings_factory
):
    import psycopg
    from scout.slack_runtime import installation_current, load_installations

    settings = settings_factory(database_url=db_url)
    _save(db_url, org, settings)
    [old] = load_installations(settings)
    with psycopg.connect(db_url) as conn:
        conn.execute(
            "UPDATE scout_auth.memberships SET role='member' "
            "WHERE organization_id=%s AND employee_id=%s",
            (org.id, org.id),
        )
    assert not installation_current(old, settings)
    assert not [i for i in load_installations(settings) if i.tenant.id == org.id]


def test_primary_worker_never_races_tenant_slack_worker():
    from scout.worker import should_run_primary

    nonlegacy = installation("00000000-0000-4000-8000-000000000021", "TEAM_A").tenant
    from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant

    legacy = Tenant(
        id=LEGACY_ID,
        slug=LEGACY_SLUG,
        schema_name="public",
        employee_id="owner",
        role="worker",
        legacy=True,
    )
    assert not should_run_primary(nonlegacy, app_token="xapp-connected")
    assert should_run_primary(legacy, app_token="xapp-connected")
    assert should_run_primary(nonlegacy, app_token="")


def test_file_download_rechecks_installation_before_using_cached_bot_token(monkeypatch):
    import pytest
    from scout.slack_ui import SlackUI

    recorder = type("Recorder", (), {"add_listener": lambda *_: None})()
    monkeypatch.setattr(
        "scout.slack_ui.httpx.Client",
        lambda **_: (_ for _ in ()).throw(AssertionError("outbound")),
    )
    ui = SlackUI(
        object(),
        {},
        object(),
        recorder,
        bot_token="xoxb-stale",
        installation_valid=lambda: False,
    )
    with pytest.raises(PermissionError, match="no longer active"):
        ui._http_download("https://files.slack.com/stale")
