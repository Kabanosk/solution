from __future__ import annotations

import base64

from scout.jobs import JobQueue
from scout.loops.context import Button
from scout.slack_ui import SlackUI
from scout.tasks import TaskRecorder

USERS = {"vincenzo": "U_VIN", "grazyna": "U_GRA"}


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def ui(conn_factory, fake_slack, **kw):  # noqa: ANN001, ANN201
    clock = Clock()
    rec = TaskRecorder(conn_factory)
    s = SlackUI(fake_slack, USERS, JobQueue(conn_factory), rec, conn_factory=conn_factory, clock=clock,
                icon_url="https://scout.test/scout-icon.png", dashboard_url="https://scout.test", **kw)
    return s, rec, clock


def jobs(db):  # noqa: ANN001, ANN201
    return db.execute("SELECT * FROM agent_jobs ORDER BY id").fetchall()


def test_dm_adds_eyes_posts_working_and_enqueues(conn_factory, db, fake_slack):
    s, _, _ = ui(conn_factory, fake_slack)
    s.on_message({"type": "message", "channel_type": "im", "channel": "D1", "user": "U_GRA", "ts": "5.1",
                  "text": "here you go"})
    assert fake_slack.named("reactions_add")[0]["name"] == "eyes"
    post = fake_slack.named("chat_postMessage")[0]
    assert post["text"] == "[WORKING] Working on it…" and post["thread_ts"] == "5.1"
    assert post["username"] == "Scout" and post["icon_url"].endswith("scout-icon.png")
    [j] = jobs(db)
    assert j["kind"] == "dm" and j["dedupe_key"] == "dm:D1:5.1"
    assert j["payload"]["from_id"] == "grazyna" and j["payload"]["status_ref"].startswith("D1:100.")


def test_final_update_swaps_eyes_for_a_check_mark(conn_factory, db, fake_slack):
    s, _, _ = ui(conn_factory, fake_slack)
    s.on_message({"type": "message", "channel_type": "im", "channel": "D1", "user": "U_GRA", "ts": "5.1",
                  "text": "hi"})
    ref = jobs(db)[0]["payload"]["status_ref"]
    s.update(ref, "Done", "AUTO")
    assert fake_slack.named("reactions_remove")[0] == {"channel": "D1", "timestamp": "5.1", "name": "eyes"}
    assert fake_slack.named("reactions_add")[-1] == {"channel": "D1", "timestamp": "5.1",
                                                     "name": "white_check_mark"}
    s.update(ref, "Done again", "AUTO")  # only once
    assert len(fake_slack.named("reactions_remove")) == 1


def test_ignores_bots_and_strangers(conn_factory, db, fake_slack):
    s, _, _ = ui(conn_factory, fake_slack)
    base = {"type": "message", "channel_type": "im", "channel": "D1", "ts": "1.0", "text": "x"}
    s.on_message({**base, "user": "U_GRA", "bot_id": "B1"})
    s.on_message({**base, "user": "U_STRANGER"})
    s.on_message({**base, "user": "U_GRA", "subtype": "message_changed"})
    s.on_message({**base, "user": "U_GRA", "channel_type": "channel"})
    assert jobs(db) == [] and fake_slack.calls == []


def test_live_status_edits_last_8_lines_throttled(conn_factory, fake_slack):
    s, rec, clock = ui(conn_factory, fake_slack)
    t = rec.open("k", "invoicing", "t")
    s.track(t.id, "D1:9.9")
    for i in range(10):
        rec.step(t.id, "note", f"line {i}")
    edits = fake_slack.named("chat_update")
    assert len(edits) == 1  # the rest were held back inside the same second
    clock.t += 1.5
    s.flush()
    last = fake_slack.named("chat_update")[-1]["text"]
    assert "line 9" in last and "line 2" in last and "line 1" not in last  # last 8 lines only
    rec.step(t.id, "resolved", "Resolved · done")  # final kinds are never held back
    assert "Resolved · done" in fake_slack.named("chat_update")[-1]["text"]
    s.update("D1:9.9", "the answer", "AUTO")
    rec.step(t.id, "note", "after the end")
    assert fake_slack.named("chat_update")[-1]["text"] == "[AUTO] the answer"  # tracking stopped


def test_pdf_file_downloaded_into_job(conn_factory, db, fake_slack):
    got = []
    s, _, _ = ui(conn_factory, fake_slack, download=lambda url: got.append(url) or b"%PDF-1.4 x")
    s.on_message({"type": "message", "subtype": "file_share", "channel_type": "im", "channel": "D1",
                  "user": "U_GRA", "ts": "6.0", "text": "",
                  "files": [{"name": "FLV-42026.pdf", "mimetype": "application/pdf", "size": 10,
                             "url_private_download": "https://files.slack.test/FLV.pdf"}]})
    [j] = jobs(db)
    f = j["payload"]["files"][0]
    assert got == ["https://files.slack.test/FLV.pdf"]
    assert f["name"] == "FLV-42026.pdf" and base64.b64decode(f["data_b64"]).startswith(b"%PDF")


def _approval(conn_factory, rec):  # noqa: ANN001, ANN202
    t = rec.open("whatif:1", "liquidity", "What if: oven")
    with conn_factory() as conn:
        conn.execute("INSERT INTO approvals (id, task_id, approver_id, question) VALUES ('apr_1', %s, 'vincenzo', 'q')",
                     (t.id,))
        conn.commit()


def click(user: str, action: str = "scout_approve") -> dict:
    return {"user": {"id": user}, "channel": {"id": "D9"}, "message": {"ts": "7.0", "text": "[APPROVAL] q"},
            "actions": [{"action_id": action, "value": "apr_1"}]}


def test_approve_click_by_non_approver_ephemeral(conn_factory, db, fake_slack):
    s, rec, _ = ui(conn_factory, fake_slack, audit=__import__("scout.audit", fromlist=["Audit"]).Audit(conn_factory))
    _approval(conn_factory, rec)
    acks = []
    s.on_action(lambda: acks.append(1), click("U_GRA"))
    assert acks == [1] and jobs(db) == []
    assert fake_slack.named("chat_postEphemeral")[0]["user"] == "U_GRA"
    assert db.execute("SELECT decision FROM audit_log").fetchone()["decision"] == "DENY"
    s.on_action(lambda: None, click("U_VIN"))
    [j] = jobs(db)
    assert j["kind"] == "approval" and j["payload"] == {"approval_id": "apr_1", "approved": True, "by": "vincenzo"}
    assert "Approving" in fake_slack.named("chat_update")[-1]["blocks"][0]["text"]["text"]


def test_dm_with_buttons_and_link(conn_factory, fake_slack):
    s, _, _ = ui(conn_factory, fake_slack)
    ref = s.dm("vincenzo", "Approve?", "APPROVAL", task_id="SC-101",
               buttons=[Button("scout_approve", "Approve", "apr_1"), Button("scout_reject", "Reject", "apr_1")])
    post = fake_slack.named("chat_postMessage")[-1]
    assert ref == f"D_U_VIN:{post and fake_slack.named('chat_postMessage') and '100.0001'}"
    assert post["blocks"][1]["elements"][0]["value"] == "apr_1"
    assert "https://scout.test/tasks/SC-101" in post["blocks"][2]["elements"][0]["text"]
    assert s.dm("rosa", "hi", "NOTIFY") is None  # no Slack id: nothing sent


def test_linked_people_show_as_real_slack_people(conn_factory, db, fake_slack):
    from scout.loops.context import HR
    from scout.slack_ui import slack_names

    names = slack_names(fake_slack, {"vincenzo": "U_VIN", "grazyna": "U_GRA", "rosa": ""})
    assert names == {"vincenzo": "U_VIN", "grazyna": "U_GRA"}  # FakeSlack's real_name is the id
    names = {"vincenzo": "Wojciech Fiołka", "grazyna": "Piotr Warząchowski"}
    hr = HR(conn_factory, USERS, names)
    assert hr.name("vincenzo") == "Wojciech Fiołka" and hr.name("rosa") == "rosa"  # unlinked, not in HR: the id, no crash
    s, _, _ = ui(conn_factory, fake_slack, names=names)
    s.dm("grazyna", "Piotr Warząchowski is on vacation, asked Wojciech Fiołka on Slack", "NOTIFY")
    post = fake_slack.named("chat_postMessage")[-1]
    body = post["blocks"][0]["text"]["text"]
    assert "<@U_GRA> is on vacation, asked <@U_VIN> on Slack" in body
    assert "Wojciech Fiołka" in post["text"]  # the notification text stays readable


def test_upload_sends_the_file_to_the_dm(conn_factory, fake_slack):
    s, _, _ = ui(conn_factory, fake_slack)
    assert s.upload("grazyna", "invoices.csv", b"a,b\n", "3 invoices") == "https://slack.test/files/F1"
    [up] = fake_slack.named("files_upload_v2")
    assert up["channel"] == "D_U_GRA" and up["filename"] == "invoices.csv" and up["content"] == b"a,b\n"
    assert up["initial_comment"] == "3 invoices"


def test_linked_slack_people_are_written_to_hr(conn_factory, db):
    from scout.loops.context import HR

    db.execute("INSERT INTO employees (id, name, role) VALUES ('vincenzo', 'Vincenzo Amalfi', 'CEO / owner'), "
               "('rosa', 'Rosa Pavese', 'Head chef')")
    db.commit()
    HR(conn_factory).link_slack({"vincenzo": "U_VIN"}, {"vincenzo": "Wojciech Fiołka"})
    rows = {r["id"]: r for r in db.execute("SELECT id, slack_user_id, slack_name FROM employees").fetchall()}
    assert rows["vincenzo"]["slack_user_id"] == "U_VIN" and rows["vincenzo"]["slack_name"] == "Wojciech Fiołka"
    assert rows["rosa"]["slack_name"] is None
