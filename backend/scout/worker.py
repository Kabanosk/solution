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

from scout import db as scout_db
from scout.audit import Audit
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors import registry
from scout.jobs import JobQueue
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


HANDLERS: dict[str, Callable[[JobContext, Job], Any]] = {
    "bank_txn": invoicing.run, "reminder": invoicing.run, "reply": invoicing.run,
    "cost_check": cost_control.run,
    "what_if": liquidity.run, "liquidity_review": liquidity.run,
    "dm": chat.run,
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


def _task_of(ctx: JobContext, job: Job) -> str | None:
    if job.task_id:
        return job.task_id
    p = job.payload
    if p.get("task_id"):
        return p["task_id"]
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
        self.ctx, self.n = ctx, n_threads
        self.watchers = list(watchers)
        self._id = f"{socket.gethostname()}"

    def run_one(self, worker_id: str = "w") -> bool:
        """Claim and run one job. False when the queue had nothing ready."""
        job = self.ctx.jobs.claim(f"{self._id}:{worker_id}")
        if job is None:
            return False
        try:
            result = HANDLERS[job.kind](self.ctx, job)
        except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            log.exception("could not record the failure of job %s", job.id)

    def _loop(self, i: int, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                if not self.run_one(str(i)):
                    stop.wait(IDLE_SLEEP)
            except Exception:  # noqa: BLE001 - a database blip must not kill the thread
                log.exception("worker %s loop error", i)
                stop.wait(2)

    def _watch(self, stop: threading.Event) -> None:
        last = [0.0] * len(self.watchers)
        while not stop.is_set():
            now = time.monotonic()
            for k, (every, fn) in enumerate(self.watchers):
                if now - last[k] >= every:
                    last[k] = now
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
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


def build(settings: ScoutSettings | None = None) -> tuple[Worker, Any]:
    """Everything wired from the environment. Returns the worker and the Slack socket handler (or None)."""
    from cfo.llm import get_llm

    s = settings or get_scout_settings()
    cf = scout_db.factory(s.database_url)
    conn = cf()
    scout_db.apply_schema(conn)
    conn.close()
    audit, recorder, jobs = Audit(cf), TaskRecorder(cf), JobQueue(cf)
    user_map = parse_user_map(s.slack_user_map)
    slack_client, socket_handler, notifier, names = None, None, LogNotifier(), {}
    if s.slack_bot_token:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        from scout.slack_ui import SlackUI, slack_names

        app = App(token=s.slack_bot_token)
        slack_client = app.client
        names = slack_names(slack_client, user_map)
        icon = s.scout_icon_url or (f"{s.dashboard_url.rstrip('/')}/scout-icon.png"
                                    if s.dashboard_url.startswith("https://") else None)
        notifier = SlackUI(slack_client, user_map, jobs, recorder, conn_factory=cf, audit=audit,
                           bot_token=s.slack_bot_token, dashboard_url=s.dashboard_url, icon_url=icon, names=names)
        notifier.register(app)
        if s.slack_app_token:
            socket_handler = SocketModeHandler(app, s.slack_app_token)
    connectors = registry.build(cf, s, slack_client, audit)
    ctx = JobContext(conn_factory=cf, recorder=recorder, audit=audit, connectors=connectors, llm=get_llm(),
                     notifier=notifier, jobs=jobs, settings=s, hr=HR(cf, user_map, names),
                     checkpointer=scout_db.postgres_checkpointer(s.database_url))
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
    return Worker(ctx, s.scout_workers, watchers=watchers), socket_handler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # the bank poll every 5 s is not news
    worker, socket_handler = build()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    if socket_handler is not None:
        socket_handler.connect()
        log.info("Slack Socket Mode connected")
    else:
        log.warning("Slack is not configured: messages go to the log")
    worker.run(stop)
    if socket_handler is not None:
        socket_handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
