"""A reply to a message Scout sent for someone: matched by Slack thread and sender, stored once, passed on once."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cfo.api.errors import install_error_handlers
from cfo.api.settings import get_api_settings
from cfo.llm import FakeLLM
from scout import chat_api, dm_replies
from scout import api as scout_api
from scout.config import get_scout_settings
from scout.loops import chat
from scout.loops.toolbox import Toolbox
from scout.worker import Worker
from test_invoicing_loop import job as make_job, make_world
from test_other_loops import dm


def no_llm(messages, tools):  # noqa: ANN001
    raise AssertionError("a reply to a delegated message must never reach the language model")


def world(conn_factory, tmp_path, llm=None):  # noqa: ANN001, ANN201
    ctx = make_world(conn_factory, tmp_path, llm=llm or FakeLLM(handler=no_llm))
    ctx.hr.link_slack({"vincenzo": "U_VIN", "grazyna": "U_GRA", "vanessa": "U_VAN"},
                      {"vincenzo": "Wojciech Fiołka", "grazyna": "Grazyna K", "vanessa": "Vanessa M"})
    return ctx


def asker(ctx, who="vincenzo", key="web:conv_test0001"):  # noqa: ANN001, ANN201
    task = ctx.recorder.open(key, "chat", "Dashboard chat", requested_by=who, owner_id=who)
    return Toolbox(ctx, who, ctx.hr.role(who), task)


def send(box, person="Grazyna", text="Is the Asimex invoice filed?"):  # noqa: ANN001, ANN201
    assert box.call("send_dm", {"person": person, "text": text})["sent"] is True


def ref_of(ctx):  # noqa: ANN001, ANN201
    """The thread ref the LogNotifier returned for the last message it sent."""
    return f"D_{ctx.notifier.sent[-1]['to']}:{len(ctx.notifier.sent)}.0"


def reply(ctx, thread, who="grazyna", text="Yes, filed this morning.", n=50, **extra):  # noqa: ANN001, ANN201
    return chat.run(ctx, dm(who, text, n=n, thread=thread, **extra))


def rows(ctx, sql):  # noqa: ANN001, ANN201
    with ctx.conn_factory() as conn:
        return conn.execute(sql).fetchall()


def followups(ctx, task_id):  # noqa: ANN001, ANN201
    return [s for s in ctx.recorder.steps(task_id) if s.kind == "followup"]


def run_notify(ctx):  # noqa: ANN001, ANN201
    """Make every queued fallback due and run it the way the worker would."""
    with ctx.conn_factory() as conn:
        conn.execute("UPDATE agent_jobs SET run_after = now() WHERE kind = 'dm_reply_notify' AND status = 'queued'")
        conn.commit()
    w = Worker(ctx, 1)
    n = 0
    while w.run_one():
        n += 1
    return n


def test_send_registers_the_thread_and_never_claims_a_failed_send(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    out = box.call("send_dm", {"person": "Grazyna", "text": "Is it filed?"})
    assert out["sent"] and out["awaiting_reply"]
    assert ctx.notifier.sent[-1]["level"] == "MESSAGE"
    [req] = rows(ctx, "SELECT * FROM dm_requests")
    assert (req["task_id"], req["requester_id"], req["recipient_id"], req["origin"]) == (
        box.task.id, "vincenzo", "grazyna", "dashboard")
    assert req["thread_ref"] == ref_of(ctx)
    assert dm_replies.pending_count(ctx.conn_factory, box.task.id) == 1

    ctx.notifier.dm = lambda *a, **k: None  # Slack did not deliver
    failed = box.call("send_dm", {"person": "Vanessa", "text": "hello"})
    assert failed["error"] == "not_sent" and "sent" not in failed

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("slack down")

    ctx.notifier.dm = boom
    assert box.call("send_dm", {"person": "Vanessa", "text": "hello"})["error"] == "not_sent"
    assert len(rows(ctx, "SELECT * FROM dm_requests")) == 1
    assert len(rows(ctx, "SELECT * FROM audit_log WHERE action = 'send_dm'")) == 1


def test_recipient_reply_in_the_thread_is_the_answer_not_smalltalk(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)  # the LLM raises if anything routes the reply
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    for text in ("ok", "thanks, invoice FLV-42026 is attached", "Yes, filed this morning."):
        out = reply(ctx, thread, text=text, n=60 + len(text), status_ref="D_grazyna:s1")
        assert out.id == box.task.id
    steps = followups(ctx, box.task.id)
    assert len(steps) == 3
    assert steps[0].text == "Grazyna replied to your message “Is the Asimex invoice filed?”: ok"
    assert steps[1].detail["from"] == "grazyna" and steps[1].detail["thread"] == thread
    assert ctx.notifier.updates[-1]["text"] == "Passed your reply on to Vincenzo."
    assert not [t for t in ctx.recorder.find_open(owner_id="grazyna")] and len(rows(ctx, "SELECT * FROM agent_tasks")) == 1


def test_foreign_sender_in_the_thread_is_not_a_reply(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path, llm=FakeLLM())  # default handler: a normal answer
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    out = reply(ctx, thread, who="vanessa", text="what is the cash balance?", n=70)
    assert out.id != box.task.id
    assert rows(ctx, "SELECT * FROM dm_replies") == []
    assert followups(ctx, box.task.id) == []
    # the same text in another thread of the recipient is not a reply either
    out = reply(ctx, "D_grazyna:999.0", text="hello", n=71)
    assert rows(ctx, "SELECT * FROM dm_replies") == []
    assert out.id != box.task.id


def test_duplicate_events_and_retries_store_one_reply_and_one_notification(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    reply(ctx, thread, n=80, channel="D_grazyna", ts="1.1")
    reply(ctx, thread, n=80, channel="D_grazyna", ts="1.1")  # the same job retried
    reply(ctx, thread, n=81, channel="D_grazyna", ts="1.1")  # Slack redelivers the event under another job
    assert len(rows(ctx, "SELECT * FROM dm_replies")) == 1
    assert len(followups(ctx, box.task.id)) == 1
    assert len(rows(ctx, "SELECT * FROM agent_jobs WHERE kind = 'dm_reply_notify'")) == 1
    assert run_notify(ctx) == 1 and run_notify(ctx) == 0
    assert [m["to"] for m in ctx.notifier.sent].count("vincenzo") == 1


def test_a_crash_between_storing_and_stepping_is_healed_by_the_retry(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    with ctx.conn_factory() as conn:  # the reply row exists, the step and job do not
        req = conn.execute("SELECT id FROM dm_requests").fetchone()
        conn.execute("INSERT INTO dm_replies (request_id, event_key, sender_id, text) VALUES (%s, 'dm:x:1', 'grazyna', 'yes')",
                     (req["id"],))
        conn.commit()
    reply(ctx, thread, n=82, channel="x", ts="1")
    assert len(followups(ctx, box.task.id)) == 1
    assert len(rows(ctx, "SELECT * FROM agent_jobs WHERE kind = 'dm_reply_notify'")) == 1


def test_several_requests_keep_their_own_attribution(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box, "Grazyna", "Question for Grazyna")
    t1 = ref_of(ctx)
    send(box, "Vanessa", "Question for Vanessa")
    t2 = ref_of(ctx)
    other = asker(ctx, "vincenzo", "web:conv_test0002")
    send(other, "Grazyna", "Another chat, same person")
    t3 = ref_of(ctx)
    assert len({t1, t2, t3}) == 3 and dm_replies.pending_count(ctx.conn_factory, box.task.id) == 2
    reply(ctx, t2, who="vanessa", text="Vanessa here", n=90)
    reply(ctx, t3, who="grazyna", text="Third answer", n=91)
    assert [s.text for s in followups(ctx, box.task.id)] == [
        "Vanessa replied to your message “Question for Vanessa”: Vanessa here"]
    assert [s.text for s in followups(ctx, other.task.id)] == [
        "Grazyna replied to your message “Another chat, same person”: Third answer"]
    assert dm_replies.pending_count(ctx.conn_factory, box.task.id) == 1  # Grazyna still owes an answer
    reply(ctx, t1, who="grazyna", text="First answer", n=93)
    assert dm_replies.pending_count(ctx.conn_factory, box.task.id) == 0
    run_notify(ctx)
    sent = [m["text"] for m in ctx.notifier.sent if m["to"] == "vincenzo"]
    assert sorted(sent) == sorted([
        "Vanessa replied to your message “Question for Vanessa”: Vanessa here",
        "Grazyna replied to your message “Another chat, same person”: Third answer",
        "Grazyna replied to your message “Question for Grazyna”: First answer"])
    assert all(m["level"] == "MESSAGE" for m in ctx.notifier.sent if m["to"] == "vincenzo")


def test_acknowledged_replies_are_not_sent_to_slack(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    reply(ctx, ref_of(ctx), n=100)
    [step] = followups(ctx, box.task.id)
    with ctx.conn_factory() as conn:
        assert dm_replies.acknowledge(conn, box.task.id, "vanessa", [step.id]) == 0  # not the owner of that chat
        assert dm_replies.acknowledge(conn, box.task.id, "vincenzo", [step.id + 99]) == 0  # not a step of a reply
    assert run_notify(ctx) == 1  # nothing acknowledged: the fallback sends it
    assert [m["to"] for m in ctx.notifier.sent].count("vincenzo") == 1


def test_open_dashboard_acknowledgement_suppresses_the_fallback(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    reply(ctx, ref_of(ctx), n=101)
    [step] = followups(ctx, box.task.id)
    with ctx.conn_factory() as conn:
        assert dm_replies.acknowledge(conn, box.task.id, "vincenzo", [step.id]) == 1
        assert dm_replies.acknowledge(conn, box.task.id, "vincenzo", [step.id]) == 0
    run_notify(ctx)
    assert "vincenzo" not in [m["to"] for m in ctx.notifier.sent]
    assert rows(ctx, "SELECT status FROM dm_replies")[0]["status"] == "acked"


def test_closed_tab_falls_back_to_slack_once_even_when_slack_fails_first(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    reply(ctx, thread, n=102)
    job = rows(ctx, "SELECT * FROM agent_jobs WHERE kind = 'dm_reply_notify'")[0]
    assert job["run_after"] > rows(ctx, "SELECT now() AS n")[0]["n"]  # waits for a visible dashboard to acknowledge
    real, calls = ctx.notifier.dm, []

    def flaky(*a, **k):  # noqa: ANN002, ANN003
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("slack 500")
        return real(*a, **k)

    ctx.notifier.dm = flaky
    with pytest.raises(RuntimeError):
        dm_replies.notify(ctx, make_job("dm_reply_notify", job["payload"]))
    assert rows(ctx, "SELECT status FROM dm_replies")[0]["status"] == "pending"  # nothing was delivered
    dm_replies.notify(ctx, make_job("dm_reply_notify", job["payload"]))
    dm_replies.notify(ctx, make_job("dm_reply_notify", job["payload"]))
    sent = [m for m in ctx.notifier.sent if m["to"] == "vincenzo"]
    assert len(sent) == 1 and sent[0]["level"] == "MESSAGE" and sent[0]["task_id"] == box.task.id
    assert sent[0]["text"] == "Grazyna replied to your message “Is the Asimex invoice filed?”: Yes, filed this morning."
    assert rows(ctx, "SELECT status FROM dm_replies")[0]["status"] == "slacked"

    ctx.notifier.dm = lambda *a, **k: None  # the requester has no Slack DM: not "delivered"
    reply(ctx, thread, n=103, channel="D", ts="2")
    [pending] = rows(ctx, "SELECT * FROM dm_replies WHERE status = 'pending'")
    with pytest.raises(RuntimeError):
        dm_replies.notify(ctx, make_job("dm_reply_notify", {"reply_id": pending["id"]}, 2))
    assert rows(ctx, "SELECT status FROM dm_replies WHERE id = %d" % pending["id"])[0]["status"] == "pending"


def test_replies_after_the_chat_completed_are_still_routed_and_slack_origin_is_immediate(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx, "vincenzo", "dm:slack:origin")  # asked in a Slack DM, not the dashboard
    send(box)
    thread = ref_of(ctx)
    ctx.recorder.set_status(box.task.id, "done", outcome="x")
    reply(ctx, thread, n=110, channel="D", ts="3")
    reply(ctx, thread, text="one more thing", n=111, channel="D", ts="4")
    assert len(followups(ctx, box.task.id)) == 2
    assert run_notify(ctx) == 2  # no dashboard to wait for
    assert len([m for m in ctx.notifier.sent if m["to"] == "vincenzo"]) == 2
    assert ctx.recorder.get(box.task.id).status == "done"


def test_chat_stays_waiting_until_the_reply_and_tells_the_page(conn_factory, tmp_path, monkeypatch):
    def handler(messages, tools):  # noqa: ANN001
        if not any(m.role == "tool" for m in messages):
            return FakeLLM.tool_call("send_dm", person="Grazyna", text="Is the Asimex invoice filed?")
        return "I sent Grazyna your message and will pass her reply on."

    ctx = world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda s: ctx)
    app.dependency_overrides[get_scout_settings] = lambda: ctx.settings
    conn = ctx.conn_factory()
    app.dependency_overrides[scout_api.get_conn] = lambda: conn
    monkeypatch.setenv("API_WRITE_TOKEN", "tok")
    get_api_settings.cache_clear()
    auth = {"Authorization": "Bearer tok"}
    c = TestClient(app)
    try:
        r = c.post("/v1/chat", json={"messages": [{"role": "user", "content": "ask Grazyna"}],
                                     "conversation_id": "conv_dmreply1"}, headers=auth)
        done = [json.loads(x) for x in r.text.splitlines()][-1]
        assert done["type"] == "done" and done["followups"] is True
        task = ctx.recorder.get(done["task_id"])
        assert task.status == "waiting" and task.owner_id == "vincenzo"

        reply(ctx, ref_of(ctx), n=120)
        assert ctx.recorder.get(task.id).status == "done"  # answered: nothing pending any more
        [step] = followups(ctx, task.id)

        body = {"task_id": task.id, "step_ids": [step.id]}
        assert c.post("/v1/chat/followups/ack", json=body).status_code == 401
        assert c.post("/v1/chat/followups/ack", json={"task_id": "x", "step_ids": [1]}, headers=auth).status_code == 422
        assert c.post("/v1/chat/followups/ack", json=body, headers=auth).json() == {"acknowledged": 1}
        assert c.post("/v1/chat/followups/ack", json=body, headers=auth).json() == {"acknowledged": 0}
        run_notify(ctx)
        assert "vincenzo" not in [m["to"] for m in ctx.notifier.sent]
    finally:
        conn.close()
        get_api_settings.cache_clear()


def test_crash_after_the_step_committed_but_before_it_was_linked_adds_no_second_followup(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    thread = ref_of(ctx)
    with ctx.conn_factory() as conn:
        req = conn.execute("SELECT id FROM dm_requests").fetchone()
        rid = conn.execute("INSERT INTO dm_replies (request_id, event_key, sender_id, text) "
                           "VALUES (%s, 'dm:c:1', 'grazyna', 'yes') RETURNING id", (req["id"],)).fetchone()["id"]
        conn.commit()
    made = ctx.recorder.step(box.task.id, "followup", "Grazyna replied to your message: yes",
                             detail={"dm_reply": rid})  # committed; the process died before the link
    assert rows(ctx, "SELECT step_id FROM dm_replies")[0]["step_id"] is None
    reply(ctx, thread, text="yes", n=130, channel="c", ts="1")
    reply(ctx, thread, text="yes", n=131, channel="c", ts="1")
    assert [s.id for s in followups(ctx, box.task.id)] == [made.id]
    assert rows(ctx, "SELECT step_id FROM dm_replies")[0]["step_id"] == made.id


def test_a_crashed_fallback_claim_expires_but_a_live_one_is_respected(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    reply(ctx, ref_of(ctx), n=140)
    rid = rows(ctx, "SELECT id FROM dm_replies")[0]["id"]
    j = make_job("dm_reply_notify", {"reply_id": rid})

    def claim(ago):  # noqa: ANN001, ANN202
        with ctx.conn_factory() as conn:
            conn.execute("UPDATE dm_replies SET status = 'slacking', claimed_at = now() - make_interval(secs => %s) "
                         "WHERE id = %s", (ago, rid))
            conn.commit()

    claim(5)  # another worker is sending right now
    dm_replies.notify(ctx, j)
    assert "vincenzo" not in [m["to"] for m in ctx.notifier.sent]
    claim(dm_replies.LEASE_S + 5)  # that worker died after claiming
    dm_replies.notify(ctx, j)
    assert [m["to"] for m in ctx.notifier.sent].count("vincenzo") == 1
    dm_replies.notify(ctx, j)
    assert [m["to"] for m in ctx.notifier.sent].count("vincenzo") == 1
    assert rows(ctx, "SELECT status FROM dm_replies")[0]["status"] == "slacked"


def test_a_notify_job_left_running_by_a_dead_worker_is_retried_but_bounded(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    reply(ctx, ref_of(ctx), n=150)

    def age(seconds, attempts=None):  # noqa: ANN001, ANN202
        with ctx.conn_factory() as conn:
            conn.execute("UPDATE agent_jobs SET updated_at = now() - make_interval(secs => %s), "
                         "attempts = coalesce(%s, attempts) WHERE kind = 'dm_reply_notify'", (seconds, attempts))
            conn.commit()

    with ctx.conn_factory() as conn:
        conn.execute("UPDATE agent_jobs SET run_after = now() WHERE kind = 'dm_reply_notify'")
        conn.commit()
    first = ctx.jobs.claim("w1")
    assert first and first.kind == "dm_reply_notify" and first.attempts == 1
    assert ctx.jobs.claim("w2") is None  # still running, not stale
    age(600)
    again = ctx.jobs.claim("w2")  # worker w1 restarted without finishing
    assert again and again.id == first.id and again.attempts == 2
    age(600, attempts=3)
    assert Worker(ctx, 1).run_one("w3")  # reclaim only to record terminal failure; do not send a fourth time
    assert rows(ctx, "SELECT status FROM agent_jobs WHERE kind = 'dm_reply_notify'")[0]["status"] == "failed"
    assert ctx.recorder.get(box.task.id).status == "escalated"
    assert [m["to"] for m in ctx.notifier.sent].count("vincenzo") == 0
    assert ctx.jobs.claim("w4") is None


def test_exhausted_fallback_attempts_escalate_the_chat_visibly(conn_factory, tmp_path):
    ctx = world(conn_factory, tmp_path)
    box = asker(ctx)
    send(box)
    reply(ctx, ref_of(ctx), n=160)

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("slack down")

    ctx.notifier.dm = boom
    w = Worker(ctx, 1)
    for _ in range(3):
        with ctx.conn_factory() as conn:
            conn.execute("UPDATE agent_jobs SET run_after = now() WHERE kind = 'dm_reply_notify'")
            conn.commit()
        assert w.run_one()
    task = ctx.recorder.get(box.task.id)
    assert task.status == "escalated"
    assert any(s.kind == "error" for s in ctx.recorder.steps(box.task.id))
    assert rows(ctx, "SELECT status FROM agent_jobs WHERE kind = 'dm_reply_notify'")[0]["status"] == "failed"
    assert rows(ctx, "SELECT status FROM dm_replies")[0]["status"] == "pending"  # never claimed as sent
