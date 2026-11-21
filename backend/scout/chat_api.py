"""`POST /v1/chat`: the dashboard's Scout chat. Same toolbox and access policy as a Slack DM, asked as the CEO (the
write token stands for the dashboard owner). Streams NDJSON so the page can show each tool call as it happens:

    {"type": "compacted", "summary": "<the earlier turns>", "replaced": 6}   (long chats: the first 6 messages)
    {"type": "step", "text": "Looked at missing invoices(days=30): 1 result(s)"}
    {"type": "file", "name": "invoices-2026-11-21.csv", "mime": "text/csv", "data_b64": "...", "sent_to_slack": true}
    {"type": "delta", "text": "<the answer>"}
    {"type": "done", "truncated": false, "task_id": "SC-123", "followups": false}
        (followups: something started in this turn, or a message Scout sent that is still unanswered, reports back
        later as a `followup` step on the task)

`POST /v1/chat/followups/ack` {task_id, step_ids}: the open dashboard chat showed these followup steps (a colleague's
reply to a message Scout sent); only then is the Slack fallback for them skipped.
    {"type": "error", "message": "..."}

Each conversation turn is a `chat` task with its step log and audit trail, like any other Scout task.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
from collections.abc import Callable, Iterator
from typing import Annotated, Any

from cfo.api.auth import require_write_token
from cfo.api.errors import ApiError
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from scout import db as scout_db
from scout import dm_replies, guardrails
from scout.api import Conn, WriteAuth
from scout.audit import Audit
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors import registry
from scout.forecast_bridge import ForecastBridgeError, ForecastRequest
from scout.hiring_bridge import HiringBridgeError, HiringRequest
from scout.jobs import JobQueue
from scout.loops import chat
from scout.loops.context import HR, JobContext, LogNotifier
from scout.loops.toolbox import EXTERNAL_TOOLS
from scout.tasks import TaskRecorder

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])
TIMEOUT_S = 150.0


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=16000)


# The dashboard's external model choices. The base URL is fixed per provider here, never taken from the request.
EXTERNAL_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "xai": "https://api.x.ai/v1",
}


class ExternalModel(BaseModel):
    """An external provider chosen in the dashboard chat: it runs the same agent loop as Granite, with only the
    small allowlist of tools that never return company data (`toolbox.EXTERNAL_TOOLS`)."""

    provider: str = Field(pattern="^(openai|mistral|groq|xai)$")
    model: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$")
    api_key: str = Field(pattern=r"^[\x21-\x7e]{8,512}$", repr=False)

    def llm(self) -> Any:
        from cfo.llm import OpenAICompatLLM

        return OpenAICompatLLM(EXTERNAL_BASE_URLS[self.provider], self.model, self.api_key)


class ChatRequest(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=64)
    # One dashboard conversation = one chat task; every question is a step on it. Absent: a task per question.
    conversation_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{8,64}$")
    # Absent: the configured on-premise model (Granite, every tool). Present: this provider answers, allowlisted tools only.
    external: ExternalModel | None = None


def build_context(settings: ScoutSettings) -> JobContext:
    from cfo.llm import get_llm
    from cfo.observability import traced_llm

    from scout.config import tenant_settings
    settings = tenant_settings(settings)
    cf = scout_db.factory(settings.database_url)
    audit = Audit(cf)
    from scout.worker import parse_user_map

    slack, user_map = None, parse_user_map(settings.slack_user_map)
    names: dict[str, str] = {}
    bot_token = settings.slack_bot_token
    generation = None
    if bot_token:
        from slack_sdk import WebClient

        slack = WebClient(token=bot_token, timeout=10)
        names = _names(slack, settings.slack_user_map, user_map)
    else:
        from cfo.tenancy import current_tenant

        from scout.slack_runtime import (
            GuardedSlackClient,
            installation_current,
            load_installations,
            select_installation,
        )
        from scout.slack_ui import slack_names
        from scout.worker import tenant_user_map

        tenant = current_tenant(required=False)
        if tenant is not None:
            installs = load_installations(settings)
            active = next((i for i in installs if i.tenant.id == tenant.id and
                           select_installation(installs, i.team_id) is i), None)
            if active is not None:
                slack = GuardedSlackClient(active, settings)
                user_map = tenant_user_map(cf, active)
                names = slack_names(slack, user_map)
                bot_token, generation = active.token, active.generation
    recorder, jobs = TaskRecorder(cf), JobQueue(cf)
    notifier: Any = LogNotifier()
    if slack is not None:  # files and messages Scout sends from the dashboard chat land in the asker's Slack DM
        from scout.slack_ui import SlackUI

        icon = settings.scout_icon_url or (f"{settings.dashboard_url.rstrip('/')}/scout-icon.png"
                                           if settings.dashboard_url.startswith("https://") else None)
        notifier = SlackUI(slack, user_map, jobs, recorder, conn_factory=cf, audit=audit,
                           bot_token=bot_token, dashboard_url=settings.dashboard_url, icon_url=icon,
                           names=names, generation=generation,
                           installation_valid=(lambda: installation_current(active, settings)) if generation else None)
    return JobContext(conn_factory=cf, recorder=recorder, audit=audit,
                      connectors=registry.build(cf, settings, slack, audit), llm=traced_llm(get_llm()), notifier=notifier,
                      jobs=jobs, settings=settings, hr=HR(cf, user_map, names),
                      guard=guardrails.from_settings(settings))


_NAMES: dict[str, dict[str, str]] = {}


def _names(slack: Any, raw: str, user_map: dict[str, str]) -> dict[str, str]:
    """Slack names change rarely: look them up once per process, not on every chat question."""
    if raw not in _NAMES:
        from scout.slack_ui import slack_names

        _NAMES[raw] = slack_names(slack, user_map)
    return _NAMES[raw]


ContextFactory = Callable[[ScoutSettings], JobContext]


def get_context_factory() -> ContextFactory:
    """Tests override this (fake LLM, schema per test)."""
    return build_context


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


@router.post("/chat", summary="Ask Scout (as the CEO) with its tools; NDJSON stream of steps and the answer")
def chat_endpoint(body: ChatRequest, by: Annotated[str | None, Depends(require_write_token)],
                  settings: Annotated[ScoutSettings, Depends(get_scout_settings)],
                  make_ctx: Annotated[ContextFactory, Depends(get_context_factory)]) -> StreamingResponse:
    turns = [t.model_dump() for t in body.messages]
    if turns[-1]["role"] != "user":
        raise ApiError(400, "bad_conversation", "The last message must be the user's.")
    try:
        ctx = make_ctx(settings)
    except Exception as e:
        log.exception("could not build the chat context")
        raise ApiError(503, "scout_unavailable", f"Scout could not start: {type(e).__name__}") from None
    if body.external is not None:
        from cfo.observability import traced_llm

        ctx.llm = traced_llm(body.external.llm())
    asker = by or settings.ceo_id
    question = turns[-1]["content"]
    role = ctx.hr.role(asker)
    # the current turn is screened first and alone (the strike is decided on it), before anything is stored: the task
    # API is readable by anyone, so a blocked prompt gets a neutral title and no preview
    blocked = guardrails.screen_human(ctx, asker, question, role=role)
    preview = question if len(question) <= 60 else question[:57] + "…"
    key = f"web:{body.conversation_id}" if body.conversation_id else f"web:{secrets.token_hex(8)}"
    task = ctx.recorder.open(key, "chat", "Dashboard chat: blocked message" if blocked else f"Dashboard chat: “{preview}”",
                             requested_by=asker, owner_id=asker)
    if task.status != "in_progress":
        ctx.recorder.set_status(task.id, "in_progress")
    ctx.recorder.step(task.id, "picked_up", f"{ctx.hr.name(asker)} sent a message that the safety policy blocked"
                      if blocked else f"{ctx.hr.name(asker)} asked: “{preview}”", source="dashboard")
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    files: list[dict[str, Any]] = []
    awaiting: list[bool] = []  # a what-if started in this turn: its result arrives later as a `followup` step

    def on_step(t: Any, step: Any) -> None:
        if t.id != task.id:
            return
        if step.kind == "looked_up":
            events.put({"type": "step", "text": step.text})
            if (step.detail or {}).get("tool") == "start_what_if" and "started" in step.text.lower():
                awaiting.append(True)
        elif step.kind == "sent_file" and step.detail:
            files.append({"type": "file", **{k: step.detail.get(k) for k in ("name", "mime", "data_b64",
                                                                              "sent_to_slack")}})

    ctx.recorder.add_listener(on_step)

    def run() -> None:
        try:
            def compacted(summary: str, replaced: int) -> None:
                ctx.recorder.step(task.id, "compacted", f"Summarized the first {replaced} messages to fit the model",
                                  source="scout")
                events.put({"type": "compacted", "summary": summary, "replaced": replaced})

            reply = blocked
            if reply is None:  # the current turn passed; the history the client resent is screened next
                history = guardrails.screen_history(ctx, turns[:-1], task_id=task.id, requester=asker)
            if reply is not None:  # blocked by the safety policy: no model call at all
                ctx.recorder.step(task.id, "denied", "Blocked by the safety policy before Scout read it",
                                  source="guardrails")
                ctx.recorder.set_status(task.id, "cancelled", outcome=reply[:300], resolved_via="auto",
                                        level="NOTIFY")
            else:
                reply = chat.answer(ctx, task, question, asker, role, history=history, on_compact=compacted,
                                    allowed_tools=EXTERNAL_TOOLS if body.external is not None else None)
                reply = guardrails.screen_output(ctx, task.id, reply, requester=asker, source="dashboard reply", role=role)
                ctx.recorder.step(task.id, "answered", "Answered in the dashboard chat", source="scout")
                waiting_on = dm_replies.pending_count(ctx.conn_factory, task.id)
                if waiting_on:  # a colleague still owes an answer to a message sent for this chat
                    ctx.recorder.set_status(task.id, "waiting", outcome=reply.split("\n")[0][:300])
                    awaiting.append(True)
                else:
                    ctx.recorder.set_status(task.id, "done", outcome=reply.split("\n")[0][:300], resolved_via="auto",
                                            level="AUTO")
            for f in files:  # sent before the answer that mentions them
                events.put(f)
            events.put({"type": "delta", "text": reply})
            events.put({"type": "done", "truncated": False, "task_id": task.id, "followups": bool(awaiting)})
        except Exception as e:
            log.exception("dashboard chat failed")
            ctx.recorder.step(task.id, "error", f"Something went wrong: {type(e).__name__}")
            ctx.recorder.set_status(task.id, "cancelled", outcome="Chat failed")
            events.put({"type": "error", "message": "Scout could not answer. Try again in a moment."})
        finally:
            events.put(None)

    from contextvars import copy_context
    bound_context = copy_context()
    threading.Thread(target=bound_context.run, args=(run,), name=f"chat-{task.id}", daemon=True).start()

    def stream() -> Iterator[str]:
        while True:
            try:
                ev = events.get(timeout=TIMEOUT_S)
            except queue.Empty:
                yield _line({"type": "error", "message": "Scout took too long. Try a narrower question."})
                return
            if ev is None:
                return
            yield _line(ev)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})


@router.post("/chat/forecast", summary="Hand a structured forecast intent to on-premise Granite; results stay local")
def forecast_endpoint(body: ForecastRequest, by: Annotated[str | None, Depends(require_write_token)],
                      settings: Annotated[ScoutSettings, Depends(get_scout_settings)],
                      make_ctx: Annotated[ContextFactory, Depends(get_context_factory)]) -> StreamingResponse:
    from scout import forecast_bridge
    from scout.audit import EXEC_ROLES

    try:
        ctx = make_ctx(settings)
    except Exception:
        log.exception("could not build the forecast context")
        raise ApiError(503, "scout_unavailable", "Scout could not start the local forecast.") from None
    requester = by or settings.ceo_id
    role = ctx.hr.role(requester)
    allowed = role in EXEC_ROLES
    ctx.audit.record("agent", "access_check", "bank", "external-chat forecast handoff",
                     "ALLOW" if allowed else "DENY", requester=requester, role=role,
                     policy="forecast-bridge: owner-only")
    if not allowed:
        raise ApiError(403, "forbidden", "Only the company owner may request this forecast.")

    conversation = body.conversation_id or secrets.token_hex(8)
    intent = body.intent()
    title = (f"Cash forecast: {intent.horizon_days} days" if intent.amount_eur is None else
             f"What if: €{intent.amount_eur:,.2f} {'in' if intent.direction == 'in' else 'out'} in {intent.month}")
    task = ctx.recorder.open(forecast_bridge.task_key(intent, requester, conversation), "liquidity", title,
                             requested_by=requester, owner_id=requester, simulated=True,
                             amount=(intent.amount_eur if intent.direction == "in" else -intent.amount_eur)
                             if intent.amount_eur is not None else None)
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def run() -> None:
        try:
            reply = forecast_bridge.run(ctx, task, intent, requester,
                                        lambda text: events.put({"type": "step", "text": text}))
            reply = guardrails.screen_output(ctx, task.id, reply, requester=requester,
                                             source="local forecast reply", role=role)
            events.put({"type": "delta", "text": reply})
            events.put({"type": "done", "truncated": False, "task_id": task.id})
        except Exception as error:
            log.exception("local forecast handoff failed")
            ctx.recorder.step(task.id, "error", f"Local forecast failed: {type(error).__name__}", source="granite")
            # A failure after enqueueing must not cancel an independently running job.
            if not any(step.kind == "queued" for step in ctx.recorder.steps(task.id)):
                ctx.recorder.set_status(task.id, "cancelled", outcome="Forecast handoff failed")
            events.put({"type": "error", "message": str(error) if isinstance(error, ForecastBridgeError)
                        else "The local forecast could not run. Check the scenario date and try again."})
        finally:
            events.put(None)

    from contextvars import copy_context
    bound_context = copy_context()
    threading.Thread(target=bound_context.run, args=(run,), name=f"forecast-{task.id}", daemon=True).start()

    def stream() -> Iterator[str]:
        while True:
            try:
                event = events.get(timeout=TIMEOUT_S)
            except queue.Empty:
                yield _line({"type": "error", "message": "The local forecast took too long. Try again."})
                return
            if event is None:
                return
            yield _line(event)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})


@router.post("/chat/hiring", summary="Review public hiring research with Granite and simulate recurring payroll in Liquidity")
def hiring_endpoint(body: HiringRequest, by: Annotated[str | None, Depends(require_write_token)],
                    settings: Annotated[ScoutSettings, Depends(get_scout_settings)],
                    make_ctx: Annotated[ContextFactory, Depends(get_context_factory)]) -> StreamingResponse:
    from scout import hiring_bridge
    from scout.audit import EXEC_ROLES

    try:
        ctx = make_ctx(settings)
    except Exception:
        log.exception("could not build the hiring context")
        raise ApiError(503, "scout_unavailable", "Scout could not start the hiring simulation.") from None
    requester = by or settings.ceo_id
    role = ctx.hr.role(requester)
    allowed = role in EXEC_ROLES
    ctx.audit.record("agent", "access_check", "bank", "external-chat hiring simulation",
                     "ALLOW" if allowed else "DENY", requester=requester, role=role,
                     policy="hiring-bridge: owner-only")
    if not allowed:
        raise ApiError(403, "forbidden", "Only the company owner may request a hiring simulation.")
    conversation = body.conversation_id or secrets.token_hex(8)
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def run() -> None:
        try:
            task, reply = hiring_bridge.run(ctx, body.intent(), requester, conversation,
                                            lambda text: events.put({"type": "step", "text": text}))
            reply = guardrails.screen_output(ctx, task.id, reply, requester=requester,
                                             source="local hiring forecast reply", role=role)
            events.put({"type": "delta", "text": reply})
            events.put({"type": "done", "truncated": False, "task_id": task.id})
        except Exception as error:
            log.exception("local hiring handoff failed")
            events.put({"type": "error", "message": str(error) if isinstance(error, HiringBridgeError)
                        else "The hiring simulation could not run. Try again."})
        finally:
            events.put(None)

    from contextvars import copy_context
    bound_context = copy_context()
    threading.Thread(target=bound_context.run, args=(run,), name="hiring-simulation", daemon=True).start()

    def stream() -> Iterator[str]:
        while True:
            try:
                event = events.get(timeout=TIMEOUT_S)
            except queue.Empty:
                yield _line({"type": "error", "message": "The hiring simulation took too long. Try again."})
                return
            if event is None:
                return
            yield _line(event)

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})


class AckRequest(BaseModel):
    task_id: str = Field(pattern=r"^SC-\d{1,12}$")
    step_ids: list[int] = Field(min_length=1, max_length=50)


@router.post("/chat/followups/ack", summary="The open dashboard chat showed these followup steps")
def ack_followups(body: AckRequest, conn: Conn, by: WriteAuth,
                  settings: Annotated[ScoutSettings, Depends(get_scout_settings)]) -> dict[str, Any]:
    owner = by or settings.ceo_id
    return {"acknowledged": dm_replies.acknowledge(conn, body.task_id, owner, body.step_ids)}
