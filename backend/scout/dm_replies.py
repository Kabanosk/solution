"""Waiting for the answer to a message Scout sent for someone.

`t_send_dm` registers the Slack thread of the message (`dm_requests`). A later DM in exactly that thread from the
recipient is the reply to it: `intercept` runs before any intent routing, stores it once (`dm_replies`, keyed by the
Slack event) and appends a `followup` step to the chat task that asked. Delivery uses dashboard receipts and a durable Slack fallback:

* the open, visible dashboard chat shows the step and acknowledges it (`acknowledge`, via the authenticated API);
* otherwise a delayed `dm_reply_notify` job sends it to the requester's Slack DM, labelled MESSAGE and attributed.

Replies from anyone else in that thread are not replies to the request and fall through to the normal DM handling.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from scout.db import ConnFactory
from scout.loops.context import JobContext
from scout.models import Job, Task

log = logging.getLogger(__name__)
LEVEL = "MESSAGE"
FALLBACK_DELAY_S = 12  # long enough for an open dashboard (it polls every 4 s) to show and acknowledge the reply
PREVIEW = 80
LEASE_S = 120  # a Slack fallback claimed longer ago than this was abandoned (crashed worker): another may take it


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def register(ctx: JobContext, task: Task, requester: str, recipient: str, ref: str, text: str) -> None:
    origin = "dashboard" if task.dedupe_key.startswith("web:") else "slack"
    with ctx.conn_factory() as conn:
        conn.execute("INSERT INTO dm_requests (task_id, requester_id, recipient_id, thread_ref, origin, text) "
                     "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (thread_ref) DO NOTHING",
                     (task.id, requester, recipient, ref, origin, text))
        conn.commit()


def pending_count(cf: ConnFactory, task_id: str) -> int:
    """Messages sent from this task that nobody has answered yet."""
    with cf() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM dm_requests r WHERE r.task_id = %s "
            "AND NOT EXISTS (SELECT 1 FROM dm_replies p WHERE p.request_id = r.id)", (task_id,)).fetchone()["n"]


def _wording(ctx: JobContext, req: dict[str, Any], reply_text: str) -> str:
    return (f"{ctx.hr.name(req['recipient_id'])} replied to your message “{_clip(req['text'], PREVIEW)}”: "
            f"{reply_text}")


def intercept(ctx: JobContext, job: Job) -> Task | None:
    """The parent task when this DM is the recipient's reply in a delegated message's thread; else None."""
    p = job.payload
    thread = p.get("thread")
    if not thread:
        return None
    with ctx.conn_factory() as conn:
        req = conn.execute("SELECT * FROM dm_requests WHERE thread_ref = %s", (thread,)).fetchone()
    if req is None or p.get("from_id") != req["recipient_id"]:
        return None
    text = (p.get("text") or "").strip()
    if p.get("files"):
        names = ", ".join(f.get("name") or "file" for f in p["files"])
        text = f"{text}\n[attached: {names}]".strip()
    text = text or "(empty message)"
    event_key = f"dm:{p['channel']}:{p['ts']}" if p.get("channel") and p.get("ts") else job.dedupe_key
    with ctx.conn_factory() as conn:
        conn.execute("INSERT INTO dm_replies (request_id, event_key, sender_id, text) VALUES (%s, %s, %s, %s) "
                     "ON CONFLICT (event_key) DO NOTHING", (req["id"], event_key, p["from_id"], text))
        conn.commit()
    with ctx.conn_factory() as conn:  # the row lock makes "add the step once" safe across workers and retries
        row = conn.execute("SELECT * FROM dm_replies WHERE event_key = %s FOR UPDATE", (event_key,)).fetchone()
        if row["step_id"] is None:
            # The step commits on the recorder's own connection, so a crash can leave it unlinked: look for the step
            # this reply already made (correlated by detail.dm_reply) before making one. The row lock serialises workers.
            found = conn.execute(
                "SELECT id FROM agent_task_steps WHERE task_id = %s AND kind = 'followup' "
                "AND detail->>'dm_reply' = %s ORDER BY id LIMIT 1", (req["task_id"], str(row["id"]))).fetchone()
            step_id = found["id"] if found else ctx.recorder.step(
                req["task_id"], "followup", _wording(ctx, req, row["text"])[:1900], source="slack:dm",
                detail={"dm_reply": row["id"], "request": req["id"], "from": row["sender_id"], "thread": thread}).id
            conn.execute("UPDATE dm_replies SET step_id = %s WHERE id = %s", (step_id, row["id"]))
        conn.commit()
    delay = FALLBACK_DELAY_S if req["origin"] == "dashboard" else 0
    ctx.jobs.enqueue("dm_reply_notify", {"reply_id": row["id"]}, f"dm_reply_notify:{row['id']}",
                     run_after=datetime.now(timezone.utc) + timedelta(seconds=delay))
    task = ctx.recorder.get(req["task_id"])
    if task.status == "waiting" and pending_count(ctx.conn_factory, task.id) == 0:
        task = ctx.recorder.set_status(task.id, "done", outcome=f"{ctx.hr.name(req['recipient_id'])} replied",
                                       resolved_via="slack_dm")
    if p.get("status_ref"):
        ctx.notifier.update(p["status_ref"], f"Passed your reply on to {ctx.hr.name(req['requester_id'])}.", "AUTO")
    return task


def acknowledge(conn: Any, task_id: str, owner: str, step_ids: list[int]) -> int:
    """The owner's open dashboard chat showed these followup steps. Only the owner of the task can say so."""
    task = conn.execute("SELECT owner_id FROM agent_tasks WHERE id = %s", (task_id,)).fetchone()
    if not step_ids or task is None or task["owner_id"] != owner:
        return 0
    rows = conn.execute(
        "UPDATE dm_replies p SET status = 'acked', notified_at = now() FROM dm_requests r "
        "WHERE p.request_id = r.id AND r.task_id = %s AND r.requester_id = %s AND r.origin = 'dashboard' "
        "AND p.step_id = ANY(%s) AND p.status = 'pending' RETURNING p.id", (task_id, owner, step_ids)).fetchall()
    conn.commit()
    return len(rows)


def notify(ctx: JobContext, job: Job) -> None:
    """Slack fallback: send the reply to the requester unless the dashboard already showed it (or it was sent).

    The claim is a lease (`slacking` + `claimed_at`): a worker that dies mid-send leaves a claim that expires after
    LEASE_S, so the retried job can send it. Slack delivery and the database mark are not one transaction: a crash
    between them can repeat the notification. Exhausted attempts are escalated on the original task."""
    with ctx.conn_factory() as conn:
        row = conn.execute(
            "UPDATE dm_replies SET status = 'slacking', claimed_at = now() WHERE id = %s AND (status = 'pending' "
            "OR (status = 'slacking' AND claimed_at < now() - make_interval(secs => %s))) RETURNING *",
            (job.payload["reply_id"], LEASE_S)).fetchone()
        conn.commit()
        if row is None:
            return  # acked in the dashboard, already sent, or being sent by another worker
        req = conn.execute("SELECT * FROM dm_requests WHERE id = %s", (row["request_id"],)).fetchone()
    try:
        ref = ctx.notifier.dm(req["requester_id"], _wording(ctx, req, row["text"]), LEVEL, task_id=req["task_id"])
        if not ref:
            raise RuntimeError("the requester has no Slack DM")
    except Exception:
        with ctx.conn_factory() as conn:  # nothing was delivered: release the claim so the retry sends it
            conn.execute("UPDATE dm_replies SET status = 'pending', claimed_at = NULL "
                         "WHERE id = %s AND status = 'slacking' AND claimed_at = %s", (row["id"], row["claimed_at"]))
            conn.commit()
        raise
    with ctx.conn_factory() as conn:
        conn.execute("UPDATE dm_replies SET status = 'slacked', notified_at = now() "
                     "WHERE id = %s AND status = 'slacking' AND claimed_at = %s", (row["id"], row["claimed_at"]))
        conn.commit()
    ctx.recorder.step(req["task_id"], "note", f"Also sent {ctx.hr.name(req['recipient_id'])}'s reply to "
                      f"{ctx.hr.name(req['requester_id'])} on Slack", source="slack:dm",
                      detail={"dm_reply": row["id"], "delivered": True})
