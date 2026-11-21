"""The guardrails demo kit (T080): scenario table, `guard-preflight`, `guard-reset`, `plant`, and the runbook that
quotes the same prompts. The check service is a fake; nothing here touches the cluster."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfo.data.people import EMPLOYEES
from scout import demo, demo_guard, guardrails
from scout.config import REPO_ROOT
from scout.demo_data import SCENARIOS
from scout.guardrails import Verdict
from scout.loops import chat
from test_guardrails import FakeGuard, audit_rows, blocked_world, SALARY
from test_other_loops import dm

RUNBOOK = REPO_ROOT / "docs" / "guardrails-demo.md"


class TableGuard:
    """A check service that answers exactly what the scenario table expects (optionally with a few exceptions)."""

    enabled = True

    def __init__(self, lie: dict[tuple[str, str], Verdict] | None = None, down: bool = False):
        self.lie, self.down, self.calls, self.warmed = lie or {}, down, [], 0
        self.expected = {(p.config, p.sent_text()): p for s in demo_guard.SCENARIOS for p in s.probes}

    def warm_up(self) -> Verdict:
        self.warmed += 1
        return Verdict("success")

    def check(self, config_id: str, messages: list[dict[str, str]]) -> Verdict:
        text = messages[0]["content"]
        self.calls.append((config_id, messages))
        if self.down:
            return Verdict("unavailable", detail="ConnectError: refused")
        if (config_id, text) in self.lie:
            return self.lie[(config_id, text)]
        p = self.expected[(config_id, text)]
        return Verdict("blocked", (p.rail or "some rail",), 0.1) if p.expect == "blocked" else Verdict("success", (), 0.1)


# -- the scenario table ----------------------------------------------------------------------------------
def test_table_has_scenarios_a_to_g_in_presenting_order():
    assert [s.key for s in demo_guard.SCENARIOS] == list("ABCDEFG")


def test_every_scenario_is_complete_and_uses_real_personas():
    ids = {e.id for e in EMPLOYEES}
    for s in demo_guard.SCENARIOS:
        assert s.title and s.channel and s.expected and s.show_next and s.fallback and s.minutes > 0, s.key
        assert s.probes, s.key
        assert s.personas and set(s.personas) <= ids, s.key
        for p in s.probes:
            assert p.expect in ("blocked", "success") and p.config in {
                guardrails.USER_INPUT, guardrails.TOOL_RESULT, guardrails.BOT_OUTPUT, guardrails.BOT_OUTPUT_FINANCE}
            assert p.expect == "success" or p.rail, f"{s.key}: a blocked probe names its rail"
        assert any(p.kind == "type" or s.key == "E" for p in s.probes), s.key


def test_total_time_is_about_three_minutes():
    assert 2.5 <= sum(s.minutes for s in demo_guard.SCENARIOS) <= 4.0


def test_poisoned_scenario_is_the_asimex_invoice_of_the_demo_cases():
    e = demo_guard.scenario("E")
    assert e.demo_case == "poisoned" and SCENARIOS["poisoned"].invoice_number == "AS-77666"
    assert "AS-77666" in e.title or "Asimex" in e.title
    text = demo_guard.poisoned_invoice_text()
    assert "AS-77666" in text and "ignore all previous instructions" in text.lower()


def test_staff_never_get_the_ceo_view_of_the_same_prompt():
    d = demo_guard.scenario("D")
    assert {"vanessa", "grazyna"} <= set(d.personas)
    g = demo_guard.scenario("G")
    assert "vincenzo" in g.personas and set(g.personas) & {"vanessa", "yusuf", "rosa"}


def test_scenario_lookup_rejects_unknown_key():
    with pytest.raises(KeyError):
        demo_guard.scenario("Z")


def test_typed_prompts_of_a_are_three_different_salary_questions():
    typed = [p.text for p in demo_guard.scenario("A").probes if p.kind == "type"]
    assert len(typed) == 3 and len({t.casefold() for t in typed}) == 3
    assert all(p.expect == "blocked" and p.config == "user-input" for p in demo_guard.scenario("A").probes
               if p.kind == "type")


# -- preflight --------------------------------------------------------------------------------------------
def test_preflight_all_as_expected_returns_zero_and_prints_every_probe(capsys):
    guard = TableGuard()
    results = demo_guard.preflight(guard)
    assert all(r.ok for r in results) and demo_guard.exit_code(results) == 0
    assert guard.warmed == 1  # warm-up first: the first check after a NeMo restart is slow
    n = sum(len(s.probes) for s in demo_guard.SCENARIOS)
    assert len(results) == len(guard.calls) == n
    out = demo_guard.format_results(results)
    assert out.count("\n") >= n and "blocked" in out and "success" in out and "check tool result" in out
    for s in demo_guard.SCENARIOS:
        assert f"{s.key}" in out


def test_preflight_flags_a_wrong_verdict_and_exits_one():
    victim = demo_guard.scenario("A").probes[0]
    guard = TableGuard({(victim.config, victim.sent_text()): Verdict("success")})
    results = demo_guard.preflight(guard)
    bad = [r for r in results if not r.ok]
    assert len(bad) == 1 and bad[0].probe is victim and bad[0].actual.status == "success"
    assert demo_guard.exit_code(results) == 1
    assert "MISMATCH" in demo_guard.format_results(results)


def test_preflight_flags_a_block_by_the_wrong_rail():
    victim = next(p for p in demo_guard.scenario("C").probes if p.expect == "blocked")
    guard = TableGuard({(victim.config, victim.sent_text()): Verdict("blocked", ("self check input",))})
    (bad,) = [r for r in demo_guard.preflight(guard) if not r.ok]
    assert bad.probe is victim and "rail" in bad.why


def test_preflight_treats_a_failed_service_as_a_mismatch_not_a_pass():
    results = demo_guard.preflight(TableGuard(down=True))
    assert not any(r.ok for r in results) and demo_guard.exit_code(results) == 1
    assert "unavailable" in demo_guard.format_results(results)


def test_preflight_disabled_client_is_a_mismatch():
    class Off:
        enabled = False

    results = demo_guard.preflight(Off())
    assert demo_guard.exit_code(results) == 1 and "GUARDRAILS_URL" in demo_guard.format_results(results)


def test_staff_reply_probe_is_masked_like_screen_output_before_it_is_sent():
    guard = TableGuard()
    demo_guard.preflight(guard)
    g = demo_guard.scenario("G")
    masked = [p for p in g.probes if p.mask]
    assert masked
    sent = [m[0]["content"] for c, m in guard.calls if c == guardrails.BOT_OUTPUT]
    assert any("[IBAN hidden]" in t for t in sent) and not any("DE89" in t for t in sent if "[IBAN hidden]" in t)
    for p in masked:
        assert "DE89" in p.text and "DE89" not in p.sent_text()


def test_preflight_never_writes_strikes_or_audit(conn_factory, db):
    demo_guard.preflight(TableGuard())  # it has no ctx at all: nothing to write with
    assert db.execute("SELECT count(*) AS n FROM guardrail_strikes").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] == 0


def test_repeat_runs_each_probe_that_often_and_demands_a_stable_verdict():
    guard = TableGuard()
    results = demo_guard.preflight(guard, repeat=3)
    assert len(guard.calls) == 3 * len(results)
    flaky = demo_guard.scenario("B").probes[0]
    seq = iter([Verdict("blocked", ("self check input",)), Verdict("success"), Verdict("blocked", ("self check input",))])

    class Flip(TableGuard):
        def check(self, config_id, messages):  # noqa: ANN001, ANN202
            if messages[0]["content"] == flaky.sent_text():
                return next(seq)
            return super().check(config_id, messages)

    (bad,) = [r for r in demo_guard.preflight(Flip(), repeat=3) if not r.ok]
    assert bad.probe is flaky and "unstable" in bad.why


def test_cli_preflight_exit_code_and_output(monkeypatch, capsys):
    monkeypatch.setattr(demo, "guard_client", lambda settings: TableGuard())
    assert demo.main(["guard-preflight"]) == 0
    assert "check tool result" in capsys.readouterr().out
    monkeypatch.setattr(demo, "guard_client", lambda settings: TableGuard(down=True))
    assert demo.main(["guard-preflight"]) == 1


def test_cli_preflight_only_one_scenario(monkeypatch, capsys):
    guard = TableGuard()
    monkeypatch.setattr(demo, "guard_client", lambda settings: guard)
    assert demo.main(["guard-preflight", "--scenario", "C"]) == 0
    assert len(guard.calls) == len(demo_guard.scenario("C").probes)


# -- reset -------------------------------------------------------------------------------------------------
def _seed_rows(db):
    for person in ("vanessa", "grazyna", "vincenzo", "stranger"):
        db.execute("INSERT INTO guardrail_strikes (person, prompt, rails) VALUES (%s, 'p', 'r')", (person,))
    for key in ("doc:mail:7:abc", "mail:111", "file:x/y.pdf", "chat:222", "tool:search_email:333", "ksef:123-4"):
        db.execute("INSERT INTO guardrail_alerts (item_key, rails) VALUES (%s, 'check tool result')", (key,))
    db.execute("INSERT INTO audit_log (actor, action, target, resource, decision) VALUES "
               "('agent', 'guardrail_check', 't', 'r', 'BLOCK')")
    db.commit()


def _left(db, table, col):
    return {r[col] for r in db.execute(f"SELECT {col} FROM {table}").fetchall()}


def test_reset_clears_demo_personas_and_outside_item_alerts_but_keeps_audit(conn_factory, db):
    _seed_rows(db)
    out = demo_guard.reset(conn_factory)
    assert out == {"strikes": 3, "alerts": 5}
    assert _left(db, "guardrail_strikes", "person") == {"stranger"}
    assert _left(db, "guardrail_alerts", "item_key") == {"ksef:123-4"}
    assert db.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] == 1


def test_reset_person_only_touches_that_person(conn_factory, db):
    _seed_rows(db)
    assert demo_guard.reset(conn_factory, ["vanessa"])["strikes"] == 1
    assert _left(db, "guardrail_strikes", "person") == {"grazyna", "vincenzo", "stranger"}


def test_reset_is_idempotent(conn_factory, db):
    assert demo_guard.reset(conn_factory) == {"strikes": 0, "alerts": 0}


def test_reset_rejects_a_person_that_is_not_a_demo_persona(conn_factory):
    with pytest.raises(ValueError):
        demo_guard.reset(conn_factory, ["stranger"])


def test_after_reset_scenario_a_starts_again_at_attempt_one(conn_factory, tmp_path):
    ctx, _ = blocked_world(conn_factory, tmp_path)
    for n in range(3):
        chat.run(ctx, dm("vanessa", f"The {SALARY} please {n}", n=n))
    assert "attempt 3 of 3" in ctx.notifier.updates[-1]["text"] and len(ctx.notifier.sent) == 1
    for n in range(2):  # a rehearsal stopped half way
        chat.run(ctx, dm("vanessa", f"The {SALARY} once more {n}", n=10 + n))
    assert "attempt 2 of 3" in ctx.notifier.updates[-1]["text"]
    assert demo_guard.reset(conn_factory, ["vanessa"])["strikes"] == 5
    chat.run(ctx, dm("vanessa", f"The {SALARY} start", n=20))
    assert "attempt 1 of 3" in ctx.notifier.updates[-1]["text"]
    assert audit_rows(conn_factory, "guardrail_check")  # audit rows stay


def test_after_reset_the_ceo_alert_for_the_same_item_fires_again(conn_factory, tmp_path):
    ctx, _ = blocked_world(conn_factory, tmp_path)
    ctx.guard = FakeGuard()
    key = "doc:mail:poisoned:abc"
    guardrails.alert_outside(ctx, key, "AS-77666.pdf", "check tool result", None)
    guardrails.alert_outside(ctx, key, "AS-77666.pdf", "check tool result", None)
    assert len(ctx.notifier.sent) == 1  # dedup
    demo_guard.reset(conn_factory)
    guardrails.alert_outside(ctx, key, "AS-77666.pdf", "check tool result", None)
    assert len(ctx.notifier.sent) == 2


def test_cli_reset_uses_the_bound_database_and_prints_counts(monkeypatch, capsys, conn_factory, db):
    _seed_rows(db)
    monkeypatch.setattr(demo, "db_factory", lambda settings: conn_factory)
    assert demo.main(["guard-reset", "--person", "vanessa", "--person", "grazyna"]) == 0
    assert "2 strikes" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # argparse: not a demo persona
        demo.main(["guard-reset", "--person", "stranger"])


# -- plant -------------------------------------------------------------------------------------------------
def test_plant_writes_the_files_for_the_live_cases_and_nothing_else(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "data_dir", lambda settings: tmp_path)

    def no_db(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("plant must not touch the database")

    monkeypatch.setattr(demo, "db_factory", no_db)
    assert demo.main(["plant"]) == 0
    out = capsys.readouterr().out
    assert "poisoned" in out
    assert (tmp_path / "mailbox" / "002-invoice-AS-77666.eml").is_file()
    assert (tmp_path / "workstations/grazyna/Downloads/Invoice_AS-77012.pdf").is_file()
    assert (tmp_path / "demo" / "FLV-42026.pdf").is_file()


# -- the runbook quotes the same prompts ---------------------------------------------------------------------
def test_runbook_exists_and_has_every_typed_prompt_verbatim():
    assert RUNBOOK.is_file()
    doc = RUNBOOK.read_text(encoding="utf-8")
    for s in demo_guard.SCENARIOS:
        for p in s.probes:
            if p.kind in ("type", "alt"):
                assert p.text in doc, f"{s.key}: prompt missing from the runbook: {p.text!r}"
        assert s.title in doc


def test_runbook_covers_preconditions_commands_and_troubleshooting():
    doc = RUNBOOK.read_text(encoding="utf-8")
    for needle in ("guard-preflight", "guard-reset", "plant", "COMPLIANCE_CONTACT", "SLACK_USER_MAP",
                   "SCOUT_LEGACY_CONNECTORS_ORGANIZATION", "fail-open", "cold start", "Pizzeria Amalfi"):
        assert needle in doc, needle


def test_runbook_commits_no_slack_ids_tokens_or_hostnames():
    import re

    doc = RUNBOOK.read_text(encoding="utf-8") + Path(demo_guard.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bU[A-Z0-9]{8,}\b|xox[abp]-|\.apps\.[a-z0-9.-]+|\.openshiftapps\.com", doc)
