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


def _age(conn_factory, jid, seconds):  # noqa: ANN001, ANN202
    with conn_factory() as conn:
        conn.execute("UPDATE agent_jobs SET updated_at = now() - make_interval(secs => %s) WHERE id = %s",
                     (seconds, jid))
        conn.commit()


def _exhaust(conn_factory, jid):  # noqa: ANN001, ANN202
    from scout.jobs import MAX_ATTEMPTS

    with conn_factory() as conn:
        conn.execute("UPDATE agent_jobs SET attempts = %s WHERE id = %s", (MAX_ATTEMPTS, jid))
        conn.commit()


def test_stranded_running_job_is_reclaimed_after_the_lease(conn_factory):
    """A worker that died after claiming leaves the job `running`; once its lease expires another worker takes it."""
    from scout.jobs import LEASE_SECONDS

    q = JobQueue(conn_factory)
    jid = q.enqueue("ksef_connect", {}, "k")
    assert q.claim("dead-pod").id == jid
    assert q.claim("w2") is None  # still within the lease: nobody steals it
    _age(conn_factory, jid, LEASE_SECONDS + 5)
    job = q.claim("w2")
    assert job is not None and job.id == jid and job.attempts == 2


def test_stranded_job_out_of_attempts_is_not_reclaimed_but_reaped(conn_factory):
    from scout.jobs import LEASE_SECONDS

    q = JobQueue(conn_factory)
    jid = q.enqueue("ksef_connect", {}, "k")
    q.claim("w")
    _exhaust(conn_factory, jid)
    _age(conn_factory, jid, LEASE_SECONDS + 5)
    assert q.claim("w2") is None
    reaped = q.reap_expired()
    assert [j.id for j in reaped] == [jid] and reaped[0].kind == "ksef_connect"
    assert q.reap_expired() == []  # leased to the reaper until it records the failure
    assert q.get(jid).status == "running"
    assert q.fail(jid, "lease expired", attempt=reaped[0].attempts) is True
    assert q.get(jid).status == "failed"


def test_reap_leaves_a_live_job_alone(conn_factory):
    q = JobQueue(conn_factory)
    jid = q.enqueue("a", {}, "k")
    q.claim("w")
    _exhaust(conn_factory, jid)
    assert q.reap_expired() == []


def test_a_slack_fallback_has_a_shorter_lease(conn_factory):
    from scout.jobs import LEASE_SECONDS, lease_seconds

    q = JobQueue(conn_factory)
    jid = q.enqueue("dm_reply_notify", {}, "k")
    q.claim("w")
    _age(conn_factory, jid, lease_seconds("dm_reply_notify") + 5)
    assert lease_seconds("dm_reply_notify") < LEASE_SECONDS
    assert q.claim("w2").id == jid


def test_a_worker_that_lost_its_lease_cannot_finish_the_job(conn_factory):
    """The stalled worker resumes after another one reclaimed the job: its done/fail change nothing."""
    from scout.jobs import LEASE_SECONDS

    q = JobQueue(conn_factory)
    jid = q.enqueue("a", {}, "k")
    stale = q.claim("slow")
    _age(conn_factory, jid, LEASE_SECONDS + 5)
    fresh = q.claim("w2")
    assert fresh.attempts == stale.attempts + 1
    assert q.done(stale.id, "SC-1", attempt=stale.attempts) is False
    assert q.fail(stale.id, "late boom", attempt=stale.attempts) is False
    assert not q.renew(stale.id, stale.attempts)
    job = q.get(jid)
    assert job.status == "running" and job.task_id is None and job.error is None
    assert q.renew(fresh.id, fresh.attempts)
    assert q.done(fresh.id, "SC-2", attempt=fresh.attempts) is True
    assert q.get(jid).status == "done" and q.get(jid).task_id == "SC-2"


def test_renewing_the_lease_keeps_a_live_job_from_being_reclaimed(conn_factory):
    from scout.jobs import LEASE_SECONDS

    q = JobQueue(conn_factory)
    jid = q.enqueue("a", {}, "k")
    job = q.claim("w")
    _age(conn_factory, jid, LEASE_SECONDS + 5)
    assert q.renew(job.id, job.attempts)
    assert q.claim("thief") is None


def test_reaping_changes_the_fencing_token_of_the_expired_worker(conn_factory):
    """After the reaper leased the dead last attempt, the expired worker can no longer renew or finish it."""
    from scout.jobs import LEASE_SECONDS

    q = JobQueue(conn_factory)
    jid = q.enqueue("ksef_connect", {}, "k")
    stale = q.claim("slow")
    _exhaust(conn_factory, jid)
    _age(conn_factory, jid, LEASE_SECONDS + 5)
    (reaped,) = q.reap_expired()
    old = stale.attempts + 2  # MAX_ATTEMPTS (3): the token the expired worker holds
    assert old == 3
    assert reaped.attempts > old
    assert not q.renew(jid, old)
    assert q.done(jid, "SC-1", attempt=old) is False
    assert q.fail(jid, "late", attempt=old) is False
    assert q.get(jid).status == "running"
    assert q.fail(jid, "lease expired", attempt=reaped.attempts) is True and q.get(jid).status == "failed"
