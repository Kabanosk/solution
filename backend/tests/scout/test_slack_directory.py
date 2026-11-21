"""Slack is the source of truth for people: members without an account are asked once, their name creates it, and
anyone in the workspace can be messaged."""

from __future__ import annotations

from typing import Any

from scout import slack_directory as directory
from scout.jobs import JobQueue
from scout.slack_ui import SlackUI
from scout.tasks import TaskRecorder

MEMBERS = [
    {"id": "U_VIN", "real_name": "Wojciech Fiołka", "profile": {}},
    {"id": "U_ANNA", "real_name": "Anna Kowalska", "profile": {"display_name": "anka"}},
    {"id": "U_PIOTR", "real_name": "Piotr Nowak", "profile": {}},
    {"id": "U_BOT", "real_name": "Some Bot", "is_bot": True},
    {"id": "U_GONE", "real_name": "Old Timer", "deleted": True},
    {"id": "USLACKBOT", "real_name": "Slackbot"},
]


def with_members(fake_slack: Any, members: list[dict[str, Any]] = MEMBERS) -> Any:
    def users_list(**kw: Any) -> dict[str, Any]:
        fake_slack.calls.append(("users_list", kw))
        return {"ok": True, "members": members, "response_metadata": {"next_cursor": ""}}
    fake_slack.users_list = users_list
    return fake_slack


def make_ui(conn_factory: Any, fake_slack: Any) -> SlackUI:
    with conn_factory() as conn:
        conn.execute("INSERT INTO employees (id, name, role, slack_user_id) VALUES "
                     "('vincenzo', 'Vincenzo Amalfi', 'CEO', 'U_VIN'), ('rosa', 'Rosa Bianchi', 'Baker', NULL)")
        conn.commit()
    return SlackUI(with_members(fake_slack), {"vincenzo": "U_VIN"}, JobQueue(conn_factory), TaskRecorder(conn_factory),
                   conn_factory=conn_factory)


def asks(fake_slack: Any) -> list[str]:
    return [p["channel"] for p in fake_slack.named("chat_postMessage") if "first and last name" in p["text"]]


def test_parse_name():
    assert directory.parse_name("anna kowalska") == "Anna Kowalska"
    assert directory.parse_name("My name is Grażyna Brzęczyszczykiewicz.") == "Grażyna Brzęczyszczykiewicz"
    assert directory.parse_name("Jean-Luc O'Neill") == "Jean-Luc O'Neill"
    for no in ("Anna", "hello there", "what is my cash position", "123 456", "hi scout", "", "a b c d e"):
        assert directory.parse_name(no) is None, no


def test_match_by_name_handle_and_mention():
    people = [m for m in MEMBERS if directory.is_human(m)]
    assert directory.match(people, "Anna")["id"] == "U_ANNA"
    assert directory.match(people, "anna kowalska")["id"] == "U_ANNA"
    assert directory.match(people, "@anka")["id"] == "U_ANNA"
    assert directory.match(people, "<@U_PIOTR>")["id"] == "U_PIOTR"
    assert directory.match(people, "Nobody") is None
    twins = people + [{"id": "U_A2", "real_name": "Anna Zielińska"}]
    assert directory.match(twins, "Anna") is None and directory.match(twins, "Anna Zielińska")["id"] == "U_A2"


def test_sweep_asks_every_member_without_an_account_once(conn_factory, fake_slack):
    ui = make_ui(conn_factory, fake_slack)
    assert ui.sweep() == 2
    assert sorted(asks(fake_slack)) == ["D_U_ANNA", "D_U_PIOTR"]  # not the linked CEO, bots, deleted or Slackbot
    assert ui.sweep() == 0 and len(asks(fake_slack)) == 2  # nobody is asked twice


def test_answering_with_a_name_creates_the_account_and_dms_then_work(conn_factory, db, fake_slack):
    ui = make_ui(conn_factory, fake_slack)
    ui.sweep()
    ui.on_message({"type": "message", "channel_type": "im", "channel": "D_U_ANNA", "user": "U_ANNA", "ts": "1.1",
                   "text": "hello?"})
    assert "still need your" in fake_slack.named("chat_postMessage")[-1]["text"]
    ui.on_message({"type": "message", "channel_type": "im", "channel": "D_U_ANNA", "user": "U_ANNA", "ts": "1.2",
                   "text": "anna kowalska"})
    emp = db.execute("SELECT * FROM employees WHERE slack_user_id = 'U_ANNA'").fetchone()
    assert emp["id"] == "anna.kowalska" and emp["name"] == "Anna Kowalska" and emp["role"] == "Staff"
    assert "Thanks, Anna" in fake_slack.named("chat_postMessage")[-1]["text"]
    assert db.execute("SELECT status FROM slack_members WHERE slack_user_id='U_ANNA'").fetchone()["status"] == "registered"
    assert not db.execute("SELECT 1 FROM agent_jobs").fetchall()
    # From now on her DMs are Scout jobs, and Scout can DM her
    ui.on_message({"type": "message", "channel_type": "im", "channel": "D_U_ANNA", "user": "U_ANNA", "ts": "1.3",
                   "text": "what is our cash?"})
    job = db.execute("SELECT * FROM agent_jobs WHERE kind = 'dm'").fetchone()
    assert job["payload"]["from_id"] == "anna.kowalska"
    assert ui.dm("anna.kowalska", "hi", "NOTIFY") is not None


def test_an_unknown_member_who_dms_first_is_asked_for_their_name(conn_factory, db, fake_slack):
    ui = make_ui(conn_factory, fake_slack)
    ui.on_message({"type": "message", "channel_type": "im", "channel": "D_U_PIOTR", "user": "U_PIOTR", "ts": "2.1",
                   "text": "Piotr Nowak"})  # a name before being asked is not taken as registration
    assert asks(fake_slack) == ["D_U_PIOTR"]
    ui.on_message({"type": "message", "channel_type": "im", "channel": "D_U_PIOTR", "user": "U_PIOTR", "ts": "2.2",
                   "text": "Piotr Nowak"})
    assert db.execute("SELECT id FROM employees WHERE slack_user_id = 'U_PIOTR'").fetchone()["id"] == "piotr.nowak"
    ui.sweep()
    assert asks(fake_slack) == ["D_U_PIOTR", "D_U_ANNA"]


def test_team_join_asks_the_new_member(conn_factory, fake_slack):
    ui = make_ui(conn_factory, fake_slack)
    ui.on_team_join({"type": "team_join", "user": {"id": "U_NEW", "real_name": "New Person"}})
    ui.on_team_join({"type": "team_join", "user": {"id": "U_NEWBOT", "is_bot": True}})
    assert asks(fake_slack) == ["D_U_NEW"]


def test_slack_person_registers_or_links_any_workspace_member(conn_factory, db, fake_slack):
    ui = make_ui(conn_factory, fake_slack)
    emp = ui.slack_person("Piotr")  # never registered: an account from his Slack profile
    assert emp == "piotr.nowak"
    assert db.execute("SELECT name, slack_user_id FROM employees WHERE id = %s", (emp,)).fetchone() == \
        {"name": "Piotr Nowak", "slack_user_id": "U_PIOTR"}
    assert ui.slack_person("Wojciech") == "vincenzo"  # already linked
    assert ui.slack_person("Anna", "rosa") == "rosa"  # an HR record without a Slack id gets linked
    assert db.execute("SELECT slack_user_id FROM employees WHERE id='rosa'").fetchone()["slack_user_id"] == "U_ANNA"
    assert ui.slack_person("Nobody") is None


def test_link_slack_keeps_people_who_registered_themselves(conn_factory, db, fake_slack):
    from scout.loops.context import HR

    ui = make_ui(conn_factory, fake_slack)
    ui.slack_person("Piotr")
    HR(conn_factory).link_slack({"vincenzo": "U_VIN"}, {"vincenzo": "Wojciech Fiołka"})
    assert db.execute("SELECT slack_user_id FROM employees WHERE id='piotr.nowak'").fetchone()["slack_user_id"] == "U_PIOTR"


def test_send_dm_reaches_a_member_who_never_registered(conn_factory, db, fake_slack, tmp_path):
    from test_invoicing_loop import make_world
    from test_toolbox import box

    ctx = make_world(conn_factory, tmp_path)
    ctx.notifier = SlackUI(with_members(fake_slack), {}, ctx.jobs, ctx.recorder, conn_factory=conn_factory)
    out = box(ctx, "grazyna").call("send_dm", {"person": "Piotr Nowak", "text": "Can you send the receipt?"})
    assert out["sent"] is True and out["to"] == "Piotr Nowak"
    post = fake_slack.named("chat_postMessage")[-1]
    assert post["channel"] == "D_U_PIOTR" and "Can you send the receipt?" in post["text"]
    req = db.execute("SELECT recipient_id FROM dm_requests").fetchone()
    assert req["recipient_id"] == "piotr.nowak"


def test_invite_links_to_join_the_named_workspace(conn_factory, db, fake_slack):
    import hashlib
    import re

    with conn_factory() as conn:
        conn.execute("INSERT INTO employees (id, name, role, slack_user_id) VALUES ('vincenzo', 'V', 'CEO', 'U_VIN')")
        conn.commit()
    ui = SlackUI(with_members(fake_slack), {"vincenzo": "U_VIN"}, JobQueue(conn_factory), TaskRecorder(conn_factory),
                 conn_factory=conn_factory, dashboard_url="https://scout.test",
                 workspace=("pizzeria-amalfi", "Pizzeria Amalfi"))
    assert ui.sweep() == 2
    post = next(p for p in fake_slack.named("chat_postMessage") if p["channel"] == "D_U_ANNA")
    assert "Join Pizzeria Amalfi workspace" in post["text"]
    token = re.search(r"https://scout\.test/join/pizzeria-amalfi/([\w-]+)", post["text"]).group(1)
    row = db.execute("SELECT * FROM slack_members WHERE slack_user_id = 'U_ANNA'").fetchone()
    assert row["invite_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["slack_name"] == "Anna Kowalska" and row["status"] == "invited"
