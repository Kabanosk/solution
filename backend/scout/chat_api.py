"""`POST /v1/chat`: the dashboard's Scout chat. Same toolbox and access policy as a Slack DM, asked as the CEO (the
write token stands for the dashboard owner). Streams NDJSON so the page can show each tool call as it happens:

    {"type": "compacted", "summary": "<the earlier turns>", "replaced": 6}   (long chats: the first 6 messages)
    {"type": "step", "text": "Looked at missing invoices(days=30): 1 result(s)"}
    {"type": "file", "name": "invoices-2026-11-21.csv", "mime": "text/csv", "data_b64": "...", "sent_to_slack": true}
    {"type": "delta", "text": "<the answer>"}
    {"type": "done", "truncated": false, "task_id": "SC-123", "followups": false}
        (followups: something started in this turn reports back later as a `followup` step on the task)
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

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cfo.api.auth import require_write_token
from cfo.api.errors import ApiError
from scout import db as scout_db
from scout import guardrails
from scout.audit import Audit
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors import registry
from scout.jobs import JobQueue
from scout.loops import chat
from scout.loops.context import HR, JobContext, LogNotifier
from scout.tasks import TaskRecorder

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])
TIMEOUT_S = 150.0


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=16000)


class ChatRequest(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=64)
    # One dashboard conversation = one chat task; every question is a step on it. Absent: a task per question.
    conversation_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


def build_context(settings: ScoutSettings) -> JobContext:
    from cfo.llm import get_llm
    from cfo.observability import traced_llm

    cf = scout_db.factory(settings.database_url)
    audit = Audit(cf)
    from scout.worker import parse_user_map

    slack, user_map = None, parse_user_map(settings.slack_user_map)
    names: dict[str, str] = {}
    if settings.slack_bot_token:
        from slack_sdk import WebClient

        slack = WebClient(token=settings.slack_bot_token, timeout=10)
        names = _names(slack, settings.slack_user_map, user_map)
    recorder, jobs = TaskRecorder(cf), JobQueue(cf)
    notifier: Any = LogNotifier()
    if slack is not None:  # files and messages Scout sends from the dashboard chat land in the asker's Slack DM
        from scout.slack_ui import SlackUI

        icon = settings.scout_icon_url or (f"{settings.dashboard_url.rstrip('/')}/scout-icon.png"
                                           if settings.dashboard_url.startswith("https://") else None)
        notifier = SlackUI(slack, user_map, jobs, recorder, conn_factory=cf, audit=audit,
                           bot_token=settings.slack_bot_token, dashboard_url=settings.dashboard_url, icon_url=icon,
                           names=names)
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
    except Exception as e:  # noqa: BLE001
        log.exception("could not build the chat context")
        raise ApiError(503, "scout_unavailable", f"Scout could not start: {type(e).__name__}") from None
    asker = by or settings.ceo_id
    question = turns[-1]["content"]
    preview = question if len(question) <= 60 else question[:57] + "…"
    key = f"web:{body.conversation_id}" if body.conversation_id else f"web:{secrets.token_hex(8)}"
    task = ctx.recorder.open(key, "chat", f"Dashboard chat: “{preview}”", requested_by=asker, owner_id=asker)
    if task.status != "in_progress":
        ctx.recorder.set_status(task.id, "in_progress")
    ctx.recorder.step(task.id, "picked_up", f"{ctx.hr.name(asker)} asked: “{preview}”", source="dashboard")
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

            role = ctx.hr.role(asker)
            earlier = [t["content"] for t in turns[:-1] if t["role"] == "user"]  # the client supplies the history
            reply = guardrails.screen_human(ctx, asker, question, role=role, task_id=task.id, earlier=earlier)
            if reply is not None:  # blocked by the safety policy: no model call at all
                ctx.recorder.step(task.id, "denied", "Blocked by the safety policy before Scout read it",
                                  source="guardrails")
                ctx.recorder.set_status(task.id, "cancelled", outcome=reply[:300], resolved_via="auto",
                                        level="NOTIFY")
            else:
                reply = chat.answer(ctx, task, question, asker, role, history=turns[:-1], on_compact=compacted)
                reply = guardrails.screen_output(ctx, task.id, reply, requester=asker, source="dashboard reply")
                ctx.recorder.step(task.id, "answered", "Answered in the dashboard chat", source="scout")
                ctx.recorder.set_status(task.id, "done", outcome=reply.split("\n")[0][:300], resolved_via="auto",
                                        level="AUTO")
            for f in files:  # sent before the answer that mentions them
                events.put(f)
            events.put({"type": "delta", "text": reply})
            events.put({"type": "done", "truncated": False, "task_id": task.id, "followups": bool(awaiting)})
        except Exception as e:  # noqa: BLE001
            log.exception("dashboard chat failed")
            ctx.recorder.step(task.id, "error", f"Something went wrong: {type(e).__name__}")
            ctx.recorder.set_status(task.id, "cancelled", outcome="Chat failed")
            events.put({"type": "error", "message": "Scout could not answer. Try again in a moment."})
        finally:
            events.put(None)

    threading.Thread(target=run, name=f"chat-{task.id}", daemon=True).start()

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
