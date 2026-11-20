"""Scout in Slack: DMs as "Scout" (own name and icon), a live "Working on it…" status, files and approval buttons.

Inbound (Socket Mode, slack_bolt):
* a DM from a known employee gets 👀, a threaded "[WORKING] Working on it…" reply, and becomes a `dm` job (files are
  downloaded with the bot token first); bots, edits, channels and strangers are ignored;
* Approve / Reject clicks: only the approver may click (anyone else gets an ephemeral note and an audit DENY); the
  click becomes an `approval` job that the worker resumes (the LangGraph thread lives there).

Outbound (`Notifier`): `dm`, `update`, `track`. `track(task, ref)` makes every new step of the task edit the status
message with the last 8 step lines (at most one edit per second per message; `flush()` sends what was held back).
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from scout.audit import Audit
from scout.jobs import JobQueue
from scout.loops.context import Button
from scout.models import Step, Task
from scout.tasks import TaskRecorder

log = logging.getLogger(__name__)

APPROVE, REJECT = "scout_approve", "scout_reject"
MAX_TEXT = 2900
MAX_FILE = 10 * 1024 * 1024
LIVE_LINES = 8
THROTTLE_S = 1.0
FINAL_KINDS = {"resolved", "asked_person", "asked_approval", "denied", "answered", "escalated", "error"}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_names(client: Any, user_map: Mapping[str, str]) -> dict[str, str]:
    """Employee id -> the real name of the Slack user linked to it (skipped when Slack can't tell)."""
    out: dict[str, str] = {}
    for emp, uid in user_map.items():
        if not uid:
            continue
        try:
            u = client.users_info(user=uid)["user"]
        except Exception:  # noqa: BLE001 - missing users:read or a deleted user: keep the persona name
            log.warning("could not look up Slack user %s", uid, exc_info=True)
            continue
        name = u.get("real_name") or (u.get("profile") or {}).get("real_name") or u.get("name")
        if name:
            out[emp] = name
    return out


def _split(ref: str) -> tuple[str, str]:
    channel, _, ts = ref.partition(":")
    return channel, ts


class SlackUI:
    def __init__(self, client: Any, user_map: Mapping[str, str], jobs: JobQueue, recorder: TaskRecorder, *,
                 conn_factory: Any = None, audit: Audit | None = None, bot_token: str = "", dashboard_url: str = "",
                 icon_url: str | None = None, username: str = "Scout", download: Callable[[str], bytes] | None = None,
                 clock: Callable[[], float] = time.monotonic, names: Mapping[str, str] | None = None):
        self.client = client
        self._slack = dict(user_map)  # employee id -> Slack user id
        self._emp = {v: k for k, v in self._slack.items()}
        # "Wojciech Fiołka" in a message -> <@U…>, longest names first so one name never eats another
        self._mentions = sorted(((_escape(n), self._slack[e]) for e, n in (names or {}).items()
                                 if n and self._slack.get(e)), key=lambda p: -len(p[0]))
        self._jobs, self._recorder, self._cf, self._audit = jobs, recorder, conn_factory, audit
        self._token, self._dash = bot_token, dashboard_url.rstrip("/")
        self._identity = {"username": username, **({"icon_url": icon_url} if icon_url else {})}
        self._download = download or self._http_download
        self._clock = clock
        self._lock = threading.Lock()
        self._tracked: dict[str, str] = {}  # task id -> status message ref
        self._last_edit: dict[str, float] = {}
        self._pending: dict[str, tuple[str, str]] = {}  # ref -> (text, level) held back by the throttle
        self._dm_channels: dict[str, str] = {}
        self._origin: dict[str, tuple[str, str]] = {}  # status ref -> the DM it answers (channel, ts)
        recorder.add_listener(self.on_step)

    # -- outbound -------------------------------------------------------------------------------------------
    def _blocks(self, text: str, level: str, task_id: str | None, buttons: Sequence[Button]) -> list[dict[str, Any]]:
        body = _escape(text)
        for name, uid in self._mentions:
            body = body.replace(name, f"<@{uid}>")
        body = body if len(body) <= MAX_TEXT else body[: MAX_TEXT - 1] + "…"
        blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*[{level}]* {body}"}}]
        if buttons:
            blocks.append({"type": "actions", "elements": [
                {"type": "button", "action_id": b.action_id, "text": {"type": "plain_text", "text": b.label},
                 "style": "primary" if b.action_id == APPROVE else "danger", "value": b.value} for b in buttons]})
        if task_id:
            link = f"<{self._dash}/tasks/{task_id}|Open {task_id} in Scout>" if self._dash else task_id
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": link}]})
        return blocks

    def _channel_for(self, employee_id: str) -> str | None:
        slack_id = self._slack.get(employee_id)
        if not slack_id:
            return None
        if slack_id not in self._dm_channels:
            self._dm_channels[slack_id] = self.client.conversations_open(users=slack_id)["channel"]["id"]
        return self._dm_channels[slack_id]

    def dm(self, employee_id: str, text: str, level: str, *, task_id: str | None = None,
           buttons: Sequence[Button] = (), thread: str | None = None) -> str | None:
        if thread:
            channel, thread_ts = _split(thread)
        else:
            channel, thread_ts = self._channel_for(employee_id), None
        if not channel:
            log.info("no Slack id for %s; message not sent: %s", employee_id, text[:120])
            return None
        res = self.client.chat_postMessage(channel=channel, text=f"[{level}] {text}"[:MAX_TEXT],
                                           blocks=self._blocks(text, level, task_id, buttons),
                                           thread_ts=thread_ts, unfurl_links=False, **self._identity)
        return f"{res['channel']}:{res['ts']}"

    def upload(self, employee_id: str, name: str, content: bytes, comment: str) -> str | None:
        channel = self._channel_for(employee_id)
        if not channel:
            log.info("no Slack id for %s; file %s not sent", employee_id, name)
            return None
        res = self.client.files_upload_v2(channel=channel, filename=name, title=name, content=content,
                                          initial_comment=comment)
        f = res.get("file") or {}
        return f.get("permalink") or "sent"

    def update(self, ref: str, text: str, level: str) -> None:
        """Final text of a message: the throttle forgets it, tracking of it stops."""
        with self._lock:
            self._pending.pop(ref, None)
            for t in [t for t, r in self._tracked.items() if r == ref]:
                del self._tracked[t]
        self._edit(ref, text, level)
        with self._lock:
            origin = self._origin.pop(ref, None)
        if origin:
            self._mark_done(*origin)

    def _mark_done(self, channel: str, ts: str) -> None:
        """👀 -> ✅ on the DM once its final answer is out."""
        for call, name in ((self.client.reactions_remove, "eyes"), (self.client.reactions_add, "white_check_mark")):
            try:
                call(channel=channel, timestamp=ts, name=name)
            except Exception:  # noqa: BLE001 - a missing reaction or scope must not fail the answer
                log.warning("could not set the %s reaction", name, exc_info=True)

    def track(self, task_id: str, status_ref: str) -> None:
        with self._lock:
            self._tracked[task_id] = status_ref

    def _edit(self, ref: str, text: str, level: str) -> None:
        channel, ts = _split(ref)
        self.client.chat_update(channel=channel, ts=ts, text=f"[{level}] {text}"[:MAX_TEXT],
                                blocks=self._blocks(text, level, None, ()))
        self._last_edit[ref] = self._clock()

    # -- live status ----------------------------------------------------------------------------------------
    def on_step(self, task: Task, step: Step) -> None:
        with self._lock:
            ref = self._tracked.get(task.id)
        if not ref:
            return
        lines = [s.text for s in self._recorder.steps(task.id)][-LIVE_LINES:]
        text = "Working on it…\n" + "\n".join(f"• {ln}" for ln in lines)
        now = self._clock()
        if step.kind in FINAL_KINDS or now - self._last_edit.get(ref, -1e9) >= THROTTLE_S:
            with self._lock:
                self._pending.pop(ref, None)
            self._edit(ref, text, "WORKING")
        else:
            with self._lock:
                self._pending[ref] = (text, "WORKING")

    def flush(self) -> None:
        now = self._clock()
        with self._lock:
            due = {r: v for r, v in self._pending.items() if now - self._last_edit.get(r, -1e9) >= THROTTLE_S}
            for r in due:
                del self._pending[r]
        for ref, (text, level) in due.items():
            self._edit(ref, text, level)

    # -- inbound --------------------------------------------------------------------------------------------
    def _http_download(self, url: str) -> bytes:
        with httpx.Client(timeout=10, follow_redirects=True) as http:
            r = http.get(url, headers={"Authorization": f"Bearer {self._token}"})
            r.raise_for_status()
            return r.content

    def on_message(self, event: dict[str, Any]) -> None:
        if event.get("bot_id") or event.get("subtype") not in (None, "file_share"):
            return
        if event.get("channel_type") != "im":
            return
        employee = self._emp.get(event.get("user", ""))
        if not employee:
            log.info("ignoring a DM from an unknown Slack user")
            return
        channel, ts = event["channel"], event["ts"]
        try:
            self.client.reactions_add(channel=channel, timestamp=ts, name="eyes")
        except Exception:  # noqa: BLE001 - already reacted, missing scope: the status message still shows
            log.warning("could not add the eyes reaction", exc_info=True)
        res = self.client.chat_postMessage(channel=channel, thread_ts=event.get("thread_ts") or ts,
                                           text="[WORKING] Working on it…",
                                           blocks=self._blocks("Working on it…", "WORKING", None, ()), **self._identity)
        status_ref = f"{res['channel']}:{res['ts']}"
        with self._lock:
            self._origin[status_ref] = (channel, ts)
        files = []
        for f in event.get("files") or []:
            url = f.get("url_private_download") or f.get("url_private")
            if not url or (f.get("size") or 0) > MAX_FILE:
                continue
            try:
                data = self._download(url)
            except Exception:  # noqa: BLE001
                log.exception("could not download %s", f.get("name"))
                continue
            files.append({"name": f.get("name") or "file", "mimetype": f.get("mimetype"),
                          "data_b64": base64.b64encode(data).decode()})
        thread = f"{channel}:{event['thread_ts']}" if event.get("thread_ts") else None
        self._jobs.enqueue("dm", {"from_id": employee, "text": event.get("text") or "", "files": files,
                                  "channel": channel, "ts": ts, "thread": thread, "status_ref": status_ref},
                           f"dm:{channel}:{ts}")

    def on_action(self, ack: Callable[[], None], body: dict[str, Any]) -> None:
        ack()
        action = (body.get("actions") or [{}])[0]
        apr_id, approved = action.get("value"), action.get("action_id") == APPROVE
        slack_user = (body.get("user") or {}).get("id", "")
        by = self._emp.get(slack_user)
        channel = (body.get("channel") or body.get("container") or {}).get("id") or \
            (body.get("container") or {}).get("channel_id")
        approver = None
        if self._cf is not None:
            with self._cf() as conn:
                row = conn.execute("SELECT approver_id, task_id, status FROM approvals WHERE id = %s",
                                   (apr_id,)).fetchone()
            approver = row["approver_id"] if row else None
            if row and row["status"] != "pending":
                return
        if by is None or (approver and by != approver):
            if self._audit:
                self._audit.record("agent", "approve", f"approval:{apr_id}", f"click by {by or slack_user}", "DENY",
                                   requester=by or slack_user, policy="approvals: approver-only")
            if channel:
                self.client.chat_postEphemeral(channel=channel, user=slack_user,
                                               text="[NOTIFY] Only the approver can answer this request.")
            return
        self._jobs.enqueue("approval", {"approval_id": apr_id, "approved": approved, "by": by}, f"approval:{apr_id}")
        msg = body.get("message") or {}
        if channel and msg.get("ts"):
            question = (msg.get("text") or "").removeprefix("[APPROVAL] ")
            self.client.chat_update(channel=channel, ts=msg["ts"], text=f"[APPROVAL] {question}",
                                    blocks=self._blocks(f"{question}\n\n⏳ {'Approving' if approved else 'Rejecting'}…",
                                                        "APPROVAL", None, ()))

    def register(self, app: Any) -> None:
        app.event("message")(self.on_message)
        app.action(APPROVE)(self.on_action)
        app.action(REJECT)(self.on_action)
