"""The worker: simulator -> agent -> people, in one process.

    python -m cfo.run [--data data/generated] [--start] [--speed N]

`build_runtime(...)` wires the parts every process needs: the LangGraph agent, the chat channel (adapted to the
agent's `Notifier` port), the CEO chat router, reminders and the scheduled checks. `Worker` adds the simulator
loop. `main` builds both from the environment (see `RunSettings`).

What happens to an event:

* the simulator writes it to Postgres and calls `Runtime.on_event`, which gives it to `Agent.handle`;
* if the agent asked somebody for a document it returns `next_reminder_hours`; the runtime stores a reminder in
  the table `agent_reminders` (due in simulated time), and `pump` turns due reminders into REMINDER events;
* `pump` also runs the two scheduled checks on the simulated clock: cost vs metric (every simulated day) and
  the liquidity forecast review (every `LIQUIDITY_CHECK_HOURS` and once when the replay has finished). Both go
  through `Agent.handle` as METRIC events and are deduplicated against the decision history.

Approve / Reject clicks go to `Agent.resume_approval`; free text from the CEO goes to `cfo.chat.ChatRouter`.
Reminders live in Postgres and approvals in the LangGraph Postgres checkpointer, so the API process may build
the same runtime (`attach_api`, `AGENT_IN_API=1`): `POST /v1/approvals/{id}:approve` and `POST /v1/sim:step`
then reach an agent there, while this worker keeps the clock, the reminders and the Slack socket.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from cfo.agent.graph import (Agent, AgentConfig, HandleResult, build_agent,
                             postgres_checkpointer)
from cfo.agent.policy import context_from_transactions
from cfo.agent.ports import ActionButton
from cfo.agent.tools import (COMPANY_DB, HR, WORKSTATIONS, InProcessToolBox,
                             McpToolBox, ToolBox)
from cfo.channels.base import (Action, ActionKind, ApprovalAnswer, ChatMessage,
                               ChatReply, Level)
from cfo.channels.base import Notifier as Channel
from cfo.chat import ChatRouter
from cfo.config import REPO_ROOT, Settings, get_settings
from cfo.db.repo import Repository
from cfo.domain import Decision, Employee, Event, EventKind
from cfo.forecast import features as feat
from cfo.forecast import liquidity as liq
from cfo.llm import LLM, get_llm
from cfo.sim.engine import Simulator
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

COST_CHECK_SIGNAL = "cost_metrics_check"
LIQUIDITY_ACTION_TYPES = (liq.CREDIT_LINE, liq.INVESTMENT)
MAX_REMINDERS_PER_PUMP = 20

REMINDERS_SQL = """
CREATE TABLE IF NOT EXISTS agent_reminders (
    transaction_id TEXT PRIMARY KEY,
    due            TIMESTAMPTZ NOT NULL  -- simulated time
)
"""


class RunSettings(BaseSettings):
    """Worker-only settings (same `.env` as `cfo.config`, kept apart so tasks do not collide on config.py)."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # MCP servers over streamable HTTP. All three set: the agent calls tools through MCP. Otherwise in-process.
    mcp_company_db_url: str = ""
    mcp_workstations_url: str = ""
    mcp_hr_url: str = ""
    # Generated data (`events/december.jsonl` for the replay). Env var: DATA_DIR.
    data_dir: Path = Field(default=Path("data/generated"))
    # Scheduled checks, in simulated hours.
    cost_check_hours: float = 24.0
    liquidity_check_hours: float = (
        744.0  # 31 days: in the December replay it runs once, when the month is closed
    )
    reminder_after_hours: float = 24.0
    # Build the agent inside the API process too (see `attach_api`).
    agent_in_api: bool = False


@lru_cache
def get_run_settings() -> RunSettings:
    return RunSettings()


# -- channel adapters -----------------------------------------------------------------------------------------
class LogChannel:
    """Channel used when Slack is not configured: messages go to the log, nothing comes back."""

    def __init__(self) -> None:
        self._n = 0

    def send_dm(
        self,
        employee: Employee,
        text: str,
        level: Level,
        actions: Sequence[Action] = (),
    ) -> str:
        self._n += 1
        buttons = f" [{' / '.join(a.kind.value for a in actions)}]" if actions else ""
        log.info("DM to %s [%s]%s: %s", employee.id, level, buttons, text)
        return f"log:{self._n}"

    def on_approval(self, handler: Any) -> None:
        pass

    def on_chat(self, handler: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class AgentNotifier:
    """The agent's `cfo.agent.ports.Notifier` on top of a chat channel (`cfo.channels.base.Notifier`):
    employee id -> `Employee`, `ActionButton` -> `Action`."""

    def __init__(self, channel: Channel, repo_factory: Callable[[], Repository]):
        self.channel = channel
        self._repo_factory = repo_factory
        self._employees: dict[str, Employee] = {}

    def _employee(self, employee_id: str) -> Employee:
        if employee_id not in self._employees:
            with self._repo_factory() as repo:
                self._employees = {e.id: e for e in repo.list_employees()}
        if employee_id not in self._employees:
            raise ValueError(f"unknown employee {employee_id!r}")
        return self._employees[employee_id]

    def send_dm(
        self,
        employee_id: str,
        text: str,
        level: Level,
        actions: Sequence[ActionButton] = (),
    ) -> str:
        buttons = [
            Action(kind=ActionKind(a.action_id), request_id=a.value, label=a.label)
            for a in actions
        ]
        return self.channel.send_dm(self._employee(employee_id), text, level, buttons)


def normalize_event(event: Event) -> Event:
    """The replay carries the whole bank line (`payload.transaction`); the agent looks it up by
    `payload.transaction_id`."""
    if (
        event.kind == EventKind.BANK_TRANSACTION
        and "transaction_id" not in event.payload
    ):
        txn_id = (event.payload.get("transaction") or {}).get("id")
        if txn_id:
            return event.model_copy(
                update={"payload": {**event.payload, "transaction_id": txn_id}}
            )
    return event


def _decision_month(dec: Decision) -> str | None:
    facts = (
        dec.payload.get("facts")
        or (dec.payload.get("pending") or {}).get("facts")
        or {}
    )
    return facts.get("forecast_month")


# -- runtime ------------------------------------------------------------------------------------------------
class Runtime:
    """Agent + channel + chat + reminders + scheduled checks. Thread-safe where the agent is (its own lock)."""

    def __init__(
        self,
        *,
        agent: Agent,
        channel: Channel,
        chat: ChatRouter,
        repo_factory: Callable[[], Repository],
        cost_check_hours: float = 24.0,
        liquidity_check_hours: float = 744.0,
    ):
        self.agent = agent
        self.channel = channel
        self.chat = chat
        self.repo_factory = repo_factory
        self.cost_check_every = timedelta(hours=cost_check_hours)
        self.liquidity_check_every = timedelta(hours=liquidity_check_hours)
        self._next_cost_check: datetime | None = None
        self._next_liquidity_check: datetime | None = None
        self._checked_at_finish = False
        self._pump_lock = threading.Lock()
        with repo_factory() as repo:
            repo.conn.execute(REMINDERS_SQL)
            repo.conn.commit()

    # -- inbound ------------------------------------------------------------------------------------------
    def on_event(self, event: Event) -> HandleResult:
        """Simulator subscriber: let the agent handle the event, remember the reminder it asks for."""
        event = normalize_event(event)
        result = self.agent.handle(event)
        txn_id = event.payload.get("transaction_id")
        if result.next_reminder_hours and txn_id:
            self._schedule_reminder(
                txn_id, event.ts + timedelta(hours=result.next_reminder_hours)
            )
        if result.decision_ids or result.status == "awaiting_approval":
            log.info(
                "event %s (%s): %s", event.id, event.kind.value, "; ".join(result.steps)
            )
        return result

    def on_approval(self, answer: ApprovalAnswer) -> None:
        """Channel handler for Approve / Reject. Raises (PermissionError, KeyError) when the click is not valid,
        so the channel leaves the buttons in place."""
        result = self.agent.resume_approval(
            answer.request_id, answer.approved, answer.employee_id
        )
        log.info(
            "approval %s %s by %s: %s",
            answer.request_id,
            "approved" if answer.approved else "rejected",
            answer.employee_id,
            result.status,
        )

    def on_chat(self, message: ChatMessage) -> ChatReply:
        return self.chat.handle(message)

    # -- reminders (simulated time, stored in Postgres) ------------------------------------------------------
    def _schedule_reminder(self, transaction_id: str, due: datetime) -> None:
        with self.repo_factory() as repo:
            repo.conn.execute(
                "INSERT INTO agent_reminders (transaction_id, due) VALUES (%s, %s) "
                "ON CONFLICT (transaction_id) DO UPDATE SET due = EXCLUDED.due",
                (transaction_id, due),
            )
            repo.conn.commit()

    def _pop_due_reminder(self, sim_now: datetime) -> tuple[str, datetime] | None:
        with self.repo_factory() as repo:
            row = repo.conn.execute(
                "DELETE FROM agent_reminders WHERE transaction_id = (SELECT transaction_id FROM agent_reminders "
                "WHERE due <= %s ORDER BY due, transaction_id LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING transaction_id, due",
                (sim_now,),
            ).fetchone()
            repo.conn.commit()
        return (row["transaction_id"], row["due"]) if row else None

    def fire_due_reminders(self, sim_now: datetime) -> int:
        fired = 0
        while fired < MAX_REMINDERS_PER_PUMP and (
            due := self._pop_due_reminder(sim_now)
        ):
            txn_id, when = due
            self.on_event(
                Event(
                    ts=when, kind=EventKind.REMINDER, payload={"transaction_id": txn_id}
                )
            )
            fired += 1
        return fired

    # -- scheduled checks ----------------------------------------------------------------------------------
    def check_cost_metrics(self, sim_now: datetime) -> HandleResult:
        """Cost vs metric (D-006): the agent's sub-agent looks at the newest month of each pair; an anomaly is
        alerted once per pair and month."""
        return self.agent.handle(
            Event(
                ts=sim_now, kind=EventKind.METRIC, payload={"signal": COST_CHECK_SIGNAL}
            )
        )

    def review_liquidity(self, outflows: Iterable[Any] = ()) -> liq.LiquidityReview:
        with self.repo_factory() as repo:
            tx = feat.fetch_transactions(repo.conn)
            context = context_from_transactions(
                repo.list_transactions(), repo.list_rules()
            )
        return liq.review_liquidity(outflows, transactions=tx, policy_context=context)

    def check_liquidity(self, sim_now: datetime) -> list[HandleResult]:
        """Forecast review (D-013): hand a new proposal to the agent. A proposal of the same kind for the same
        month that is already in the history (pending, executed or rejected) is not repeated.
        """
        review = self.review_liquidity()
        with self.repo_factory() as repo:
            known = {
                (d.action_type, _decision_month(d))
                for d in repo.list_decisions(limit=500)
                if d.action_type in LIQUIDITY_ACTION_TYPES
            }
        results = []
        for proposal in review.proposals:
            if (proposal.action_type, proposal.month) in known:
                continue
            event = proposal.to_event()
            results.append(self.agent.handle(event.model_copy(update={"ts": sim_now})))
            log.info(
                "liquidity review: %s %s for %s",
                proposal.action_type,
                proposal.amount,
                proposal.month,
            )
        return results

    def run_checks(self, sim_now: datetime) -> None:
        """Both checks now, whatever the schedule says (a failing check is logged, never raised)."""
        self._guarded("cost metrics", self.check_cost_metrics, sim_now)
        self._guarded("liquidity", self.check_liquidity, sim_now)

    def pump(self, sim_now: datetime, *, finished: bool = False) -> None:
        """Do what became due at simulated time `sim_now`: reminders, then the scheduled checks."""
        with self._pump_lock:
            self.fire_due_reminders(sim_now)
            if self._next_cost_check is None:
                self._next_cost_check = sim_now + self.cost_check_every
                self._next_liquidity_check = sim_now + self.liquidity_check_every
            at_finish = finished and not self._checked_at_finish
            if at_finish or sim_now >= self._next_cost_check:
                self._guarded("cost metrics", self.check_cost_metrics, sim_now)
                self._next_cost_check = sim_now + self.cost_check_every
            if at_finish or sim_now >= self._next_liquidity_check:
                self._guarded("liquidity", self.check_liquidity, sim_now)
                self._next_liquidity_check = sim_now + self.liquidity_check_every
            self._checked_at_finish = self._checked_at_finish or finished

    @staticmethod
    def _guarded(
        name: str, check: Callable[[datetime], Any], sim_now: datetime
    ) -> None:
        try:
            check(sim_now)
        except Exception:  # noqa: BLE001
            log.exception("scheduled %s check failed", name)


def build_runtime(
    *,
    repo_factory: Callable[[], Repository],
    tools: ToolBox,
    llm: LLM,
    channel: Channel,
    config: AgentConfig | None = None,
    checkpointer: Any = None,
    cost_check_hours: float = 24.0,
    liquidity_check_hours: float = 744.0,
    register: bool = True,
) -> Runtime:
    """Wire agent, channel and chat. With `register` the runtime becomes the handler of the channel's clicks
    and chat messages, and its agent the one the REST API resumes approvals on (`cfo.api.deps.set_agent`).
    """
    config = config or AgentConfig()
    agent = build_agent(
        tools,
        repo_factory,
        llm,
        AgentNotifier(channel, repo_factory),
        config,
        checkpointer,
    )
    runtime: Runtime
    chat = ChatRouter(
        agent=agent,
        llm=llm,
        repo_factory=repo_factory,
        ceo_id=config.ceo_id,
        review=lambda outflows: runtime.review_liquidity(outflows),
    )
    runtime = Runtime(
        agent=agent,
        channel=channel,
        chat=chat,
        repo_factory=repo_factory,
        cost_check_hours=cost_check_hours,
        liquidity_check_hours=liquidity_check_hours,
    )
    if register:
        channel.on_approval(runtime.on_approval)
        channel.on_chat(runtime.on_chat)
        from cfo.api.deps import set_agent

        set_agent(agent)
    return runtime


# -- worker -------------------------------------------------------------------------------------------------
class Worker:
    """The loop: tick the simulator (its subscriber is `Runtime.on_event`), then `Runtime.pump`."""

    def __init__(self, runtime: Runtime, simulator: Simulator):
        self.runtime = runtime
        self.sim = simulator

    def pump(self) -> None:
        status = self.sim.status()
        self.runtime.pump(status.sim_time, finished=status.finished)

    def tick(self) -> None:
        self.sim.tick()
        self.pump()

    def run(
        self,
        stop: threading.Event,
        *,
        max_wait: float = 1.0,
        wait: Callable[[float], Any] | None = None,
    ) -> None:
        """Start the channel, tick until `stop` is set, stop the channel. `wait(seconds)` defaults to
        `stop.wait`; tests pass a function that advances a fake clock."""
        wait = wait or stop.wait
        try:
            self.runtime.channel.start()
        except (
            Exception
        ):  # noqa: BLE001 - without the socket the agent still works and sends
            log.exception(
                "channel did not start; approvals then only work through the API"
            )
        try:
            while not stop.is_set():
                try:
                    self.tick()
                    status = self.sim.status()
                except (
                    Exception
                ):  # noqa: BLE001 - e.g. the database restarting: keep the worker alive
                    log.exception("worker tick failed")
                    wait(max_wait)
                    continue
                delay = max_wait
                if not status.paused and status.next_event_ts is not None:
                    delay = min(
                        max_wait,
                        max(
                            0.01,
                            (status.next_event_ts - status.sim_time).total_seconds()
                            / status.speed,
                        ),
                    )
                wait(delay)
        finally:
            self.runtime.channel.stop()


# -- from the environment ---------------------------------------------------------------------------------
def tools_from_settings(
    settings: Settings, run: RunSettings, repo_factory: Callable[[], Repository]
) -> ToolBox:
    urls = {
        COMPANY_DB: run.mcp_company_db_url,
        WORKSTATIONS: run.mcp_workstations_url,
        HR: run.mcp_hr_url,
    }
    if all(urls.values()):
        log.info("tools: MCP over HTTP (%s)", ", ".join(urls.values()))
        return McpToolBox(urls)
    from cfo.mcp_servers.company_db import CompanyDbTools
    from cfo.mcp_servers.hr import HrTools
    from cfo.mcp_servers.workstations import DbAccessLog, WorkstationTools

    log.info(
        "tools: in-process (set MCP_COMPANY_DB_URL, MCP_WORKSTATIONS_URL and MCP_HR_URL to use MCP servers)"
    )
    return InProcessToolBox(
        CompanyDbTools(repo_factory),
        WorkstationTools(settings.workstations_root, DbAccessLog(repo_factory)),
        HrTools(repo_factory),
    )


def channel_from_settings(
    settings: Settings, repo_factory: Callable[[], Repository], *, receive: bool = True
) -> Channel:
    """Slack when the tokens are there (`receive` also needs the app-level token for Socket Mode), else the log."""
    if settings.slack_bot_token and (settings.slack_app_token or not receive):
        from cfo.channels.slack import SlackNotifier

        with repo_factory() as repo:
            employees = repo.list_employees()
        log.info("channel: Slack")
        return SlackNotifier.from_settings(employees)
    log.warning("channel: log only (SLACK_BOT_TOKEN / SLACK_APP_TOKEN not set)")
    return LogChannel()


def runtime_from_settings(*, receive: bool = True) -> Runtime:
    settings, run = get_settings(), get_run_settings()

    def repo_factory() -> Repository:
        return Repository.connect(settings.database_url)

    return build_runtime(
        repo_factory=repo_factory,
        tools=tools_from_settings(settings, run, repo_factory),
        llm=get_llm(settings),
        channel=channel_from_settings(settings, repo_factory, receive=receive),
        config=AgentConfig(reminder_after_hours=run.reminder_after_hours),
        checkpointer=postgres_checkpointer(settings.database_url),
        cost_check_hours=run.cost_check_hours,
        liquidity_check_hours=run.liquidity_check_hours,
    )


def attach_api() -> Runtime | None:
    """For the API process (`AGENT_IN_API=1`): an agent that answers approvals from the dashboard and handles
    events emitted by `POST /v1/sim:step`. It sends DMs but does not open the Slack socket or tick the clock;
    the worker does that. Returns None when switched off."""
    if not get_run_settings().agent_in_api:
        return None
    from cfo.sim import subscribe

    runtime = runtime_from_settings(receive=False)
    subscribe(runtime.on_event)
    log.info("agent attached to the API process")
    return runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cfo.run", description="Agent worker: simulator -> agent -> notifier."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="generated data dir (default: DATA_DIR or data/generated)",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="resume the simulated clock at startup (default: "
        "leave it as it is; start it with `python -m cfo.sim start` or POST /v1/sim:start)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="with --start: sim-seconds per real second",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from cfo.data.load import load_events
    from cfo.db.migrate import apply_schema

    settings, run = get_settings(), get_run_settings()
    apply_schema(settings.database_url)
    from cfo.observability import init_tracing

    init_tracing(
        settings
    )  # MLflow agent traces; a no-op unless MLFLOW_TRACKING_URI is set
    runtime = runtime_from_settings()
    data_dir = args.data or run.data_dir
    simulator = Simulator(
        Repository.connect(settings.database_url),
        load_events(data_dir),
        speed=settings.sim_speed,
        subscribers=[runtime.on_event],
    )
    if args.start:
        simulator.start(args.speed)
    status = simulator.status()
    log.info(
        "worker up: sim time %s, %s, speed %g, %d/%d events",
        status.sim_time.astimezone(UTC).isoformat(),
        "paused" if status.paused else "running",
        status.speed,
        status.emitted,
        status.total,
    )

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        Worker(runtime, simulator).run(stop)
    finally:
        simulator.repo.conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
