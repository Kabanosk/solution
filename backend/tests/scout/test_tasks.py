from scout.tasks import TaskRecorder


def test_open_is_idempotent(conn_factory):
    rec = TaskRecorder(conn_factory)
    a = rec.open("txn:1", "invoicing", "Invoice X")
    b = rec.open("txn:1", "invoicing", "Invoice X again")
    assert a.id == b.id == "SC-100"
    assert rec.open("txn:2", "invoicing", "Y").id == "SC-101"


def test_step_order_and_listener(conn_factory):
    rec = TaskRecorder(conn_factory)
    seen = []
    rec.add_listener(lambda task, step: seen.append((task.id, step.kind)))
    t = rec.open("txn:1", "invoicing", "Invoice X")
    rec.step(t.id, "picked_up", "Scout picked up the transfer from Revolut Business", source="revolut_mock")
    rec.step(t.id, "checked_system", "Searched the invoicing system: 0 matches", result_count=0)
    assert [s.kind for s in rec.steps(t.id)] == ["picked_up", "checked_system"]
    assert seen == [(t.id, "picked_up"), (t.id, "checked_system")]


def test_listener_error_does_not_break(conn_factory):
    rec = TaskRecorder(conn_factory)
    rec.add_listener(lambda task, step: 1 / 0)
    t = rec.open("k", "chat", "x")
    rec.step(t.id, "note", "hello")
    assert len(rec.steps(t.id)) == 1


def test_done_sets_closed_at(conn_factory):
    rec = TaskRecorder(conn_factory)
    t = rec.open("k", "invoicing", "x")
    assert t.closed_at is None
    done = rec.set_status(t.id, "done", outcome="Filed", resolved_via="files")
    assert done.closed_at is not None and done.resolved_via == "files"


def test_evidence_accumulates(conn_factory):
    rec = TaskRecorder(conn_factory)
    t = rec.open("k", "invoicing", "x")
    rec.step(t.id, "matched", "found", evidence={"name": "AS-1.pdf", "kind": "pdf", "ref": "files:Downloads/AS-1.pdf"})
    assert rec.get(t.id).evidence[0]["name"] == "AS-1.pdf"
