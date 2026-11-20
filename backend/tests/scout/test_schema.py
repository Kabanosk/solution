from scout import db as scout_db


def test_apply_twice(db):
    scout_db.apply_schema(db)
    scout_db.apply_schema(db)


def test_task_id_sequence(db):
    row = db.execute(
        "INSERT INTO agent_tasks (dedupe_key, loop, title) VALUES ('k', 'invoicing', 't') RETURNING id"
    ).fetchone()
    assert row["id"] == "SC-100"


def test_replay_tables_are_gone(db):
    names = {r["table_name"] for r in db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    ).fetchall()}
    assert {"agent_tasks", "agent_task_steps", "agent_jobs", "audit_log", "connections"} <= names
    assert not names & {"events", "sim_state", "decisions", "approval_requests"}
