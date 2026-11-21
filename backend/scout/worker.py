"""The agent pool: `python -m scout.worker`.

N worker threads claim jobs from `agent_jobs` (FOR UPDATE SKIP LOCKED, so several pods can run side by side) and run
the loop the job belongs to. One more thread runs the watchers (bank, mail), the scheduler and the Slack status
flush; Slack itself runs over Socket Mode. A failing job is retried with backoff; after the last attempt its task gets
an `error` step and is escalated, so nothing fails silently.
"""

from __future__ import annotations

import logging
import signal
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from cfo.observability import span

from scout import db as scout_db
from scout import dm_replies, guardrails
from scout.audit import Audit
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors import registry
from scout.jobs import MAX_ATTEMPTS, JobQueue
from scout.loops import chat, cost_control, invoicing, liquidity
from scout.loops.context import HR, JobContext, LogNotifier
from scout.models import Job
from scout.tasks import TaskRecorder
from scout.watchers import BankWatcher, MailWatcher, Scheduler

log = logging.getLogger("scout.worker")
IDLE_SLEEP = 0.5


def _approval(ctx: JobContext, job: Job) -> Any:
    p = job.payload
    try:
        return liquidity.resume(ctx, p["approval_id"], bool(p["approved"]), p["by"])
    except PermissionError:
        return None  # audited in resume; nothing to retry


def _ksef_connect(ctx: JobContext, job: Job) -> Any:
    from scout import ksef_sync
    from scout.connectors.ksef import KsefClient

    return ksef_sync.run(ctx.conn_factory, ctx.audit, KsefClient, base_url=ctx.settings.ksef_api_url, ctx=ctx)


HANDLERS: dict[str, Callable[[JobContext, Job], Any]] = {
    "ksef_connect": _ksef_connect,
    "bank_txn": invoicing.run, "reminder": invoicing.run, "reply": invoicing.run,
    "cost_check": cost_control.run,
    "what_if": liquidity.run, "liquidity_review": liquidity.run,
    "chat_forecast": liquidity.run, "hiring_scenario": liquidity.run,
    "dm": chat.run,
    "dm_reply_notify": dm_replies.notify,
    "approval": _approval,
}


def parse_user_map(raw: str) -> dict[str, str]:
    """`"vincenzo=U01,grazyna=U02"` -> `{"vincenzo": "U01", "grazyna": "U02"}`."""
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        emp, sep, sid = part.strip().partition("=")
        if sep and emp.strip() and sid.strip():
            out[emp.strip()] = sid.strip()
    return out


def tenant_user_map(cf: Any, installation: Any) -> dict[str, str]:
    """Only explicit Slack IDs are trusted; never infer an employee from a display name."""
    with cf() as conn:
        rows = conn.execute("SELECT id, slack_user_id FROM employees WHERE slack_user_id IS NOT NULL").fetchall()
    links = {r["id"]: r["slack_user_id"] for r in rows}
    installer = installation.config.get("installer_user_id")
    employee = installation.config.get("installer_employee_id")
    if installer and employee:
        with cf() as conn:
            owner = conn.execute("SELECT 1 FROM scout_auth.memberships WHERE organization_id=%s "
                                 "AND employee_id=%s AND role='owner'", (installation.tenant.id, employee)).fetchone()
            exists = conn.execute("SELECT 1 FROM employees WHERE id=%s", (employee,)).fetchone()
        if owner and exists:
            links[employee] = installer
    counts: dict[str, int] = {}
    for uid in links.values():
        counts[uid] = counts.get(uid, 0) + 1
    return {employee_id: uid for employee_id, uid in links.items() if counts[uid] == 1}


def organization_of(database_url: str) -> tuple[str, str] | None:
    """(slug, name) of the bound organization, for the "Join <name> workspace" links Scout sends on Slack."""
    from cfo.tenancy import current_tenant
    tenant = current_tenant(required=False)
    if tenant is None or not tenant.slug:
        return None
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            row = conn.execute("SELECT name FROM scout_auth.organizations WHERE id = %s", (tenant.id,)).fetchone()
        return (tenant.slug, row[0] if row else tenant.slug)
    except Exception:
        log.warning("could not read the organization name", exc_info=True)
        return (tenant.slug, tenant.slug)


def _task_of(ctx: JobContext, job: Job) -> str | None:
    if job.task_id:
        return job.task_id
    p = job.payload
    if p.get("task_id"):
        return p["task_id"]
    if job.kind == "dm_reply_notify":  # the chat that asked: a notification that could not be sent must be seen
        with ctx.conn_factory() as conn:
            row = conn.execute("SELECT r.task_id FROM dm_replies p JOIN dm_requests r ON r.id = p.request_id "
                               "WHERE p.id = %s", (p.get("reply_id"),)).fetchone()
        return row["task_id"] if row else None
    if job.kind == "bank_txn":
        t = ctx.recorder.by_key(f"txn:{p['txn']['id']}")
        return t.id if t else None
    for key in (f"dm:{job.dedupe_key}", f"whatif:{job.dedupe_key}"):
        t = ctx.recorder.by_key(key)
        if t:
            return t.id
    return None


class Worker:
    def __init__(self, ctx: JobContext, n_threads: int = 4, *, watchers: list[tuple[float, Callable[[], Any]]] = ()):
        from cfo.tenancy import current_tenant
        self.tenant = current_tenant(required=False)
        self.ctx, self.n = ctx, n_threads
        self.watchers = list(watchers)
        self._id = f"{socket.gethostname()}"

    def run_one(self, worker_id: str = "w") -> bool:
        """Claim and run one job. False when the queue had nothing ready."""
        if self.tenant is not None:
            from cfo.tenancy import bind_tenant
            with bind_tenant(self.tenant):
                return self._run_one(worker_id)
        return self._run_one(worker_id)

    def _run_one(self, worker_id: str) -> bool:
        job = self.ctx.jobs.claim(f"{self._id}:{worker_id}")
        if job is None:
            return False
        installation = getattr(self, "installation", None)
        if installation is not None and job.kind in ("dm", "approval", "dm_reply_notify"):
            from scout.slack_runtime import installation_current
            generation = job.payload.get("_slack_generation")
            if (generation and generation != installation.generation) or not installation_current(installation, self.ctx.settings):
                self.ctx.jobs.done(job.id)
                return True  # revoked/replaced installation: never execute an old Slack job
        try:
            if job.attempts > MAX_ATTEMPTS:
                raise RuntimeError("Retry limit reached after an interrupted worker")
            with span(f"scout.{job.kind}", inputs={"job": job.id, "kind": job.kind}):  # one trace per job
                result = HANDLERS[job.kind](self.ctx, job)
        except Exception as e:
            log.exception("job %s (%s) failed", job.id, job.kind)
            final = self.ctx.jobs.fail(job.id, f"{type(e).__name__}: {e}")
            if final:
                self._give_up(job, e)
            return True
        task_id = getattr(result, "id", None) if not isinstance(result, list) else None
        self.ctx.jobs.done(job.id, task_id)
        return True

    def _give_up(self, job: Job, e: Exception) -> None:
        try:
            task_id = _task_of(self.ctx, job)
            if task_id:
                self.ctx.recorder.step(task_id, "error", f"Something went wrong: {type(e).__name__}. A person needs to "
                                                         "look at this.", detail={"error": str(e)[:500]})
                self.ctx.recorder.update(task_id, status="escalated", owner_id=self.ctx.settings.ceo_id)
            if job.payload.get("status_ref"):
                self.ctx.notifier.update(job.payload["status_ref"], "Sorry, something went wrong on my side. "
                                                                    "I've flagged it for a person.", "NOTIFY")
        except Exception:
            log.exception("could not record the failure of job %s", job.id)

    def _loop(self, i: int, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                if not self.run_one(str(i)):
                    stop.wait(IDLE_SLEEP)
            except Exception:
                log.exception("worker %s loop error", i)
                stop.wait(2)

    def _watch(self, stop: threading.Event) -> None:
        if self.tenant is not None:
            from cfo.tenancy import bind_tenant
            with bind_tenant(self.tenant):
                return self._watch_bound(stop)
        return self._watch_bound(stop)

    def _watch_bound(self, stop: threading.Event) -> None:
        last = [0.0] * len(self.watchers)
        while not stop.is_set():
            now = time.monotonic()
            for k, (every, fn) in enumerate(self.watchers):
                if now - last[k] >= every:
                    last[k] = now
                    try:
                        fn()
                    except Exception:
                        log.exception("watcher %s failed", getattr(fn, "__qualname__", fn))
            stop.wait(0.25)

    def run(self, stop: threading.Event) -> None:
        threads = [threading.Thread(target=self._loop, args=(i, stop), name=f"scout-worker-{i}", daemon=True)
                   for i in range(self.n)]
        threads.append(threading.Thread(target=self._watch, args=(stop,), name="scout-watchers", daemon=True))
        for t in threads:
            t.start()
        log.info("Scout worker pool: %d workers, %d watchers", self.n, len(self.watchers))
        stop.wait()
        for t in threads:
            t.join(timeout=5)


def build(settings: ScoutSettings | None = None, *, socket_enabled: bool = True) -> tuple[Worker, Any]:
    """Everything wired from the environment. Returns the worker and the Slack socket handler (or None)."""
    from cfo.observability import init_tracing
    from cfo.tenancy import bind_tenant, worker_tenant

    init_tracing()  # MLflow traces of Scout's LLM calls and guardrail checks; a no-op without MLFLOW_TRACKING_URI
    s = settings or get_scout_settings()
    tenant = worker_tenant(s.database_url)
    with bind_tenant(tenant):
        return _build(s, socket_enabled=socket_enabled)


def _build(s: ScoutSettings, *, installation: Any = None, socket_enabled: bool = True) -> tuple[Worker, Any]:
    from cfo.llm import get_llm
    from cfo.observability import traced_llm

    from scout.config import tenant_settings
    s = tenant_settings(s)
    cf = scout_db.factory(s.database_url)
    audit, recorder, jobs = Audit(cf), TaskRecorder(cf), JobQueue(cf)
    user_map = parse_user_map(s.slack_user_map)
    slack_client, socket_handler, notifier, names = None, None, LogNotifier(), {}
    if installation is not None:
        from scout.slack_runtime import GuardedSlackClient, installation_current
        from scout.slack_ui import SlackUI, slack_names
        slack_client = GuardedSlackClient(installation, s)
        user_map = tenant_user_map(cf, installation)
        names = slack_names(slack_client, user_map)
        icon = s.scout_icon_url or (f"{s.dashboard_url.rstrip('/')}/scout-icon.png"
                                    if s.dashboard_url.startswith("https://") else None)
        notifier = SlackUI(slack_client, user_map, jobs, recorder, conn_factory=cf, audit=audit,
                           bot_token=installation.token, dashboard_url=s.dashboard_url, icon_url=icon, names=names,
                           generation=installation.generation,
                           installation_valid=lambda: installation_current(installation, s),
                           workspace=organization_of(s.database_url))
    elif s.slack_bot_token:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        from scout.slack_ui import SlackUI, slack_names

        app = App(token=s.slack_bot_token)
        slack_client = app.client
        names = slack_names(slack_client, user_map)
        icon = s.scout_icon_url or (f"{s.dashboard_url.rstrip('/')}/scout-icon.png"
                                    if s.dashboard_url.startswith("https://") else None)
        notifier = SlackUI(slack_client, user_map, jobs, recorder, conn_factory=cf, audit=audit,
                           bot_token=s.slack_bot_token, dashboard_url=s.dashboard_url, icon_url=icon, names=names,
                           workspace=organization_of(s.database_url))
        notifier.register(app)
        if s.slack_app_token and socket_enabled:
            socket_handler = SocketModeHandler(app, s.slack_app_token)
    connectors = registry.build(cf, s, slack_client, audit)
    ctx = JobContext(conn_factory=cf, recorder=recorder, audit=audit, connectors=connectors, llm=traced_llm(get_llm()),
                     notifier=notifier, jobs=jobs, settings=s, hr=HR(cf, user_map, names), guard=guardrails.from_settings(s),
                     checkpointer=scout_db.postgres_checkpointer(s.database_url))
    from cfo.tenancy import legacy_connectors_enabled
    if legacy_connectors_enabled():
        try:
            ctx.hr.link_slack(user_map, names)
        except Exception:
            log.warning("could not write the Slack names to HR", exc_info=True)
    if ctx.guard.enabled:  # the first check after a NeMo restart is slow: warm it up without blocking the start
        threading.Thread(target=ctx.guard.warm_up, name="guardrails-warmup", daemon=True).start()
    bank = BankWatcher(cf, connectors, jobs)
    mail = MailWatcher(cf, connectors, jobs, recorder)

    def refresh_connectors() -> None:  # a Google account connected in the dashboard shows up within a minute
        c = registry.build(cf, s, slack_client, audit)
        ctx.connectors = bank.connectors = mail.connectors = c

    watchers: list[tuple[float, Callable[[], Any]]] = [
        (float(s.bank_poll_seconds), bank.poll), (float(s.mail_poll_seconds), mail.poll),
        (30.0, Scheduler(jobs).tick), (60.0, refresh_connectors)]
    if hasattr(notifier, "flush"):
        watchers.append((0.5, notifier.flush))
    if hasattr(notifier, "sweep"):  # Slack is the source of truth: ask every member without an account (once)
        watchers.append((float(s.slack_sweep_seconds), notifier.sweep))
    worker = Worker(ctx, s.scout_workers, watchers=watchers)
    worker.installation = installation
    return worker, socket_handler


def should_run_primary(tenant: Any, app_token: str) -> bool:
    """Never run a second log-only executor against a tenant already managed by the Slack supervisor."""
    return bool(tenant and tenant.legacy) or not app_token


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # the bank poll every 5 s is not news
    settings = get_scout_settings()
    worker, socket_handler = build(settings, socket_enabled=not bool(settings.slack_app_token))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    supervisor = None
    if settings.slack_app_token:
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        from scout.slack_runtime import SlackSupervisor
        supervisor = SlackSupervisor(settings)
        supervisor.reconcile()
        # A single Socket Mode client receives events for every installed workspace; never create one per tenant.
        socket_handler = SocketModeHandler(supervisor.bolt_app(), settings.slack_app_token)
        socket_handler.connect()
        log.info("Slack Socket Mode connected for tenant routing")
    elif socket_handler is not None:
        socket_handler.connect()
        log.info("Slack Socket Mode connected")
    else:
        log.warning("Slack Socket Mode is not configured: messages go to the log")
    poller = None
    if supervisor is not None:
        poller = threading.Thread(target=supervisor.run, args=(stop,), name="scout-slack-supervisor", daemon=True)
        poller.start()
    try:
        if should_run_primary(worker.tenant, settings.slack_app_token):
            worker.run(stop)
        else:
            log.info("Tenant jobs run in isolated installation workers, not the configured primary worker")
            stop.wait()
    finally:
        stop.set()
        if socket_handler is not None:
            socket_handler.close()
        if poller is not None:
            poller.join(timeout=5)
        if supervisor is not None:
            supervisor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
