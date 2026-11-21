"""task_log / scout_tasks: everyone sees their own tasks, the CEO sees all; denials are audited (D-029)."""

from __future__ import annotations

import scout.loops.toolbox as toolbox_module
from scout.audit import can_see_task
from scout.loops.toolbox import Toolbox
from test_invoicing_loop import make_world


def box(ctx, who: str) -> Toolbox:  # noqa: ANN001
    t = ctx.recorder.open(f"dm:test:{who}", "chat", "q")
    return Toolbox(ctx, who, ctx.hr.role(who), t)


def make_tasks(ctx) -> dict[str, str]:  # noqa: ANN001
    """One task per situation; returns name -> task id."""
    rec = ctx.recorder
    mine = rec.open("k:rosa-asked", "invoicing", "Dinner invoice for Rosa", requested_by="rosa", owner_id="rosa")
    other = rec.open("k:yusuf-asked", "invoicing", "Oven repair receipt", requested_by="yusuf", owner_id="yusuf")
    assigned = rec.open("k:agent-asked-rosa", "invoicing", "Missing flour invoice", owner_id="rosa")
    private = rec.open("k:vincenzo-chat", "chat", "Dashboard chat: salaries", requested_by="vincenzo", owner_id="vincenzo")
    for t in (mine, other, assigned, private):
        rec.step(t.id, "note", f"step of {t.title}")
    return {"mine": mine.id, "other": other.id, "assigned": assigned.id, "private": private.id}


def denials(db):  # noqa: ANN001
    return db.execute("SELECT * FROM audit_log WHERE decision = 'DENY' AND target LIKE 'task%' ORDER BY id").fetchall()


def test_policy():
    assert can_see_task("vincenzo", "ceo", "rosa", "rosa")[0] is True
    assert can_see_task("rosa", "head_chef", "rosa", None)[0] is True
    assert can_see_task("rosa", "head_chef", "agent", "rosa")[0] is True  # assignee
    assert can_see_task("rosa", "head_chef", "yusuf", "yusuf")[0] is False
    assert can_see_task("rosa", "head_chef", "agent", None)[0] is False


def test_task_log_own_and_assigned_allowed(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ids = make_tasks(ctx)
    for key in ("mine", "assigned"):
        out = box(ctx, "rosa").call("task_log", {"task_id": ids[key]})
        assert "error" not in out and out["steps"]
    assert denials(db) == []


def test_task_log_of_someone_else_is_denied_and_audited(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ids = make_tasks(ctx)
    out = box(ctx, "rosa").call("task_log", {"task_id": ids["other"]})
    assert out["error"] == "access_denied" and "steps" not in out and "Oven" not in str(out)
    out = box(ctx, "grazyna").call("task_log", {"task_id": ids["private"].lower()})  # even the office manager
    assert out["error"] == "access_denied"
    rows = denials(db)
    assert [r["requester"] for r in rows] == ["rosa", "grazyna"] and all(r["policy"] for r in rows)
    assert ids["other"] in rows[0]["target"] or ids["other"] in rows[0]["resource"]


def test_ceo_sees_every_task_log(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ids = make_tasks(ctx)
    for key in ids:
        assert "error" not in box(ctx, "vincenzo").call("task_log", {"task_id": ids[key]})
    assert denials(db) == []


def test_unknown_task_is_still_not_found(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    assert box(ctx, "rosa").call("task_log", {"task_id": "SC-9999"})["error"] == "not_found"


def test_scout_tasks_lists_only_own_unless_ceo(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ids = make_tasks(ctx)
    rosa = {r["id"] for r in box(ctx, "rosa").call("scout_tasks", {})["rows"]}
    assert rosa == {ids["mine"], ids["assigned"]}
    yusuf = {r["id"] for r in box(ctx, "yusuf").call("scout_tasks", {})["rows"]}
    assert yusuf == {ids["other"]}
    ceo = {r["id"] for r in box(ctx, "vincenzo").call("scout_tasks", {})["rows"]}
    assert ceo == {ids["mine"], ids["other"], ids["assigned"]}  # chat tasks stay out of this list as before
    # a text search cannot reach tasks of others either
    assert box(ctx, "rosa").call("scout_tasks", {"text": "Oven"})["rows"] == []
    assert box(ctx, "rosa").call("scout_tasks", {"status": "in_progress"})["rows"]
    hidden = db.execute("SELECT * FROM audit_log WHERE decision = 'DENY' AND target = 'tasks'").fetchall()
    assert hidden and all(h["requester"] in ("rosa", "yusuf") for h in hidden)


def test_task_log_is_one_consistent_snapshot(conn_factory, db, tmp_path, monkeypatch):
    """A task that changes hands (even away and back) right after the permission check must not let steps written
    in between into the answer: permission and steps come from one REPEATABLE READ snapshot."""
    ctx = make_world(conn_factory, tmp_path)
    ids = make_tasks(ctx)
    real = toolbox_module.can_see_task
    calls: list[int] = []

    def churn_after_first_check(*args, **kw):  # noqa: ANN002, ANN003
        result = real(*args, **kw)
        if not calls:
            with ctx.conn_factory() as conn:
                conn.execute("UPDATE agent_tasks SET owner_id = 'yusuf', requested_by = 'yusuf' WHERE id = %s", (ids["mine"],))
                conn.commit()
            ctx.recorder.step(ids["mine"], "note", "written while Rosa had no access")
            with ctx.conn_factory() as conn:
                conn.execute("UPDATE agent_tasks SET owner_id = 'rosa', requested_by = 'rosa' WHERE id = %s", (ids["mine"],))
                conn.commit()
        calls.append(1)
        return result

    monkeypatch.setattr(toolbox_module, "can_see_task", churn_after_first_check)
    out = box(ctx, "rosa").call("task_log", {"task_id": ids["mine"]})
    assert "error" not in out and not any("written while" in s for s in out["steps"])
    assert any("step of Dinner invoice" in s for s in out["steps"])
