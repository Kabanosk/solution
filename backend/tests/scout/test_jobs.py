from scout.jobs import JobQueue


def test_enqueue_is_idempotent(conn_factory):
    q = JobQueue(conn_factory)
    assert q.enqueue("bank_txn", {"a": 1}, "txn:1") is not None
    assert q.enqueue("bank_txn", {"a": 1}, "txn:1") is None


def test_claim_skip_locked(conn_factory):
    q = JobQueue(conn_factory)
    q.enqueue("a", {}, "k1")
    q.enqueue("a", {}, "k2")
    j1, j2 = q.claim("w1"), q.claim("w2")
    assert j1 and j2 and j1.id != j2.id
    assert q.claim("w3") is None


def test_fail_backoff_then_failed(conn_factory):
    q = JobQueue(conn_factory)
    jid = q.enqueue("a", {}, "k")
    afters = []
    for attempt in range(3):
        conn = conn_factory()
        conn.execute("UPDATE agent_jobs SET run_after = now() WHERE id = %s", (jid,))
        conn.commit()
        conn.close()
        job = q.claim("w")
        assert job.attempts == attempt + 1
        final = q.fail(job.id, "boom")
        afters.append(q.get(jid).run_after)
        assert final is (attempt == 2)
    assert q.get(jid).status == "failed"
    assert afters[0] < afters[1] < afters[2]
