"""Watchers, scheduler and the worker pool."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from scout import worker as worker_mod
from scout.jobs import JobQueue
from scout.models import BankTxn, MailHit
from scout.watchers import BankWatcher, MailWatcher, Scheduler
from scout.worker import Worker, parse_user_map
from test_invoicing_loop import NOW, make_world, pay, run_txn


class FakeBank:
    def __init__(self, txns):  # noqa: ANN001
        self.txns = txns

    def list_transactions(self, since):  # noqa: ANN001
        return [t for t in self.txns if t.booked_at >= since]


def test_bank_watcher_only_new_outgoing_once(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.execute("INSERT INTO watcher_state (name, cursor) VALUES ('bank', %s)", ('{"since": "%s"}' % old.isoformat(),))
    db.commit()
    t1 = BankTxn(id="tx_a", booked_at=old + timedelta(minutes=1), amount=Decimal("-50"), counterparty="Metro AG",
                 reference="card")
    t2 = BankTxn(id="tx_b", booked_at=old + timedelta(minutes=2), amount=Decimal("300"), counterparty="Stripe",
                 reference="payout")
    ctx.connectors.bank = FakeBank([t1, t2])
    w = BankWatcher(conn_factory, ctx.connectors, ctx.jobs)
    assert w.poll() == 1
    assert w.poll() == 0
    jobs = db.execute("SELECT kind, dedupe_key FROM agent_jobs").fetchall()
    assert [(j["kind"], j["dedupe_key"]) for j in jobs] == [("bank_txn", "txn:tx_a")]
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 2


def test_mail_watcher_reply_for_waiting_task(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    task = run_txn(ctx, pay(ctx, "nowhere"))
    pdf = (tmp_path / "demo" / "FLV-42026.pdf").read_bytes()

    class Box:
        name = "eml_folder"

        def available(self):
            return True

        def changes(self, cursor):  # noqa: ANN001
            hit = MailHit(id="m1", mailbox="office", sender="billing@fattoria.example", subject="Rechnung FLV-42026",
                          attachments=["FLV-42026.pdf"])
            return ([] if cursor.get("seen") else [hit]), {"seen": ["m1"]}

        def attachment(self, hit_id, name, task_id=None):  # noqa: ANN001
            return pdf

    ctx.connectors.mail = Box()
    w = MailWatcher(conn_factory, ctx.connectors, ctx.jobs, ctx.recorder)
    assert w.poll() == 1 and w.poll() == 0
    job = ctx.jobs.claim("t")
    assert job.kind == "reply" and job.payload["task_id"] == task.id and job.payload["from_id"] == "email"


def test_scheduler_daily_dedupe(conn_factory, db):
    s = Scheduler(JobQueue(conn_factory))
    monday_8 = datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc)  # 08:00 in Frankfurt
    assert s.tick(monday_8) == ["cost_check", "liquidity_review"]
    assert s.tick(monday_8 + timedelta(hours=3)) == []
    assert s.tick(datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)) == []  # 05:00 local: too early
    assert s.tick(datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)) == ["cost_check"]


def test_dispatch_runs_the_loop(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    txn = pay(ctx, "files")
    ctx.jobs.enqueue("bank_txn", {"txn": txn.model_dump(mode="json")}, f"txn:{txn.id}")
    w = Worker(ctx, 1)
    assert w.run_one() is True and w.run_one() is False
    j = db.execute("SELECT status, task_id FROM agent_jobs").fetchone()
    assert j["status"] == "done" and j["task_id"].startswith("SC-")


def test_dispatch_and_retry(conn_factory, db, tmp_path, monkeypatch):
    ctx = make_world(conn_factory, tmp_path)
    txn = pay(ctx, "nowhere")

    def boom(c, job):  # noqa: ANN001
        c.recorder.open(f"txn:{job.payload['txn']['id']}", "invoicing", "x")
        raise RuntimeError("model exploded")

    monkeypatch.setitem(worker_mod.HANDLERS, "bank_txn", boom)
    ctx.jobs.enqueue("bank_txn", {"txn": txn.model_dump(mode="json")}, "txn:x")
    w = Worker(ctx, 1)
    for _ in range(3):
        db.execute("UPDATE agent_jobs SET run_after = now() - interval '1 second'")
        db.commit()
        assert w.run_one()
    j = db.execute("SELECT status, attempts FROM agent_jobs").fetchone()
    assert j["status"] == "failed" and j["attempts"] == 3
    t = db.execute("SELECT * FROM agent_tasks").fetchone()
    steps = db.execute("SELECT kind, text FROM agent_task_steps WHERE task_id = %s", (t["id"],)).fetchall()
    assert t["status"] == "escalated"
    assert steps[-1]["kind"] == "error" and steps[-1]["text"].startswith("Something went wrong: RuntimeError")


def test_pool_runs_in_threads_and_stops(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    for key in ("files", "email"):
        txn = pay(ctx, key)
        ctx.jobs.enqueue("bank_txn", {"txn": txn.model_dump(mode="json")}, f"txn:{txn.id}")
    stop = threading.Event()
    w = Worker(ctx, 2)
    th = threading.Thread(target=w.run, args=(stop,))
    th.start()
    for _ in range(100):
        n = db.execute("SELECT count(*) AS n FROM agent_jobs WHERE status = 'done'").fetchone()["n"]
        db.commit()
        if n == 2:
            break
        stop.wait(0.1)
    stop.set()
    th.join(5)
    assert n == 2 and not th.is_alive()


def test_parse_user_map():
    assert parse_user_map("vincenzo=U1, grazyna=U2,bad") == {"vincenzo": "U1", "grazyna": "U2"}


_ = NOW
