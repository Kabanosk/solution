"""Agent evaluation (T028): scenario set, deterministic scorers, MLflow run.

The scoring and scenario-file tests run everywhere. The evaluation itself needs the optional `mlflow` extra
(`uv run --extra mlflow ...`) and the compose Postgres; it logs to a temporary `sqlite:///` store and keeps the
company of the scenarios in a schema of this process (`t028_test_<pid>`, D-016).
"""

from __future__ import annotations

import importlib.util
import os

import psycopg
import pytest

from cfo import observability as obs
from cfo.config import get_settings
from cfo.eval import scoring
from cfo.eval.__main__ import main
from cfo.eval.scenarios import Scenario, load_scenarios

HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
needs_mlflow = pytest.mark.skipif(not HAS_MLFLOW, reason="mlflow extra not installed")
SCHEMA = f"t028_test_{os.getpid()}"
PASS_FAIL = ("policy_level_correct", "action_correct", "no_execution_without_approval", "explanation_cites_amount")


# -- scenario file ------------------------------------------------------------------------------------------
def test_scenario_file_covers_the_golden_path_and_the_autonomy_rules():
    scenarios = load_scenarios()
    assert 10 <= len(scenarios) <= 15
    assert len({s.id for s in scenarios}) == len(scenarios)
    assert all(s.source and s.title and s.expect.level for s in scenarios)
    levels = {s.id: s.expect.level for s in scenarios}
    assert levels["routine_payment_is_auto"] == "AUTO"
    assert levels["unusual_payment_up_to_5_percent_is_notify"] == "NOTIFY"
    assert levels["payment_above_5_percent_needs_approval"] == "APPROVAL"
    assert levels["new_vendor_small_payment_is_notify"] == "NOTIFY"  # D-011
    actions = [a for s in scenarios for a in s.expect.actions or []]
    assert {"attach_invoice", "request_clarification", "escalation_exhausted", "make_investment",
            "request_credit_line", "alert_cost_anomaly"} <= set(actions)
    assert any(s.expect.executed_simulated and "SIMULATED" in (s.expect.says or []) for s in scenarios)
    assert {s.target for s in scenarios} == {"agent", "policy"}


def test_scenario_file_errors_are_reported(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text("scenarios:\n  - {id: a, title: t, steps: [{dance: {}}], expect: {level: AUTO}}\n")
    with pytest.raises(ValueError, match="exactly one of"):
        load_scenarios(bad)
    with pytest.raises(ValueError, match="unknown scenario ids"):
        load_scenarios(only=["nope"])
    assert [s.id for s in load_scenarios(only=["routine_payment_is_auto"])] == ["routine_payment_is_auto"]


# -- scorers (pure functions) ---------------------------------------------------------------------------------
def test_cites_compares_numbers_not_strings():
    text = "Invest 23,000.00 EUR ahead of 2026-06; cost rose by +50.0% on 2025-12-03, orders by 2.9%."
    assert scoring.cites(text, "23000") and scoring.cites(text, "23000.00") and scoring.cites(text, "50")
    assert scoring.cites(text, "2.9") and scoring.cites(text, "3")  # 2.9 is "3" at the precision asked for
    assert not scoring.cites(text, "2300") and not scoring.cites(text, "23000.01")
    assert not scoring.cites(text, "12")  # part of a date
    assert scoring.cites("about 49.97% more", "50") and not scoring.cites("about 49.2% more", "50")


def test_explanation_scorer_names_the_missing_number():
    out = {"texts": ["Payment of 80.00 EUR has no invoice."]}
    assert scoring.explanation_cites_amount(out, {"cites": ["80.00"]}).value is True
    miss = scoring.explanation_cites_amount(out, {"cites": ["80.00", "412.30"]})
    assert miss.value is False and "412.30" in miss.rationale
    assert scoring.explanation_cites_amount(out, {}).value is None  # not asked: left out of the aggregate


def test_action_scorer_checks_order_recipient_and_state():
    out = {"actions": ["request_invoice", "remind_invoice"], "outcome": "done", "documented": {"t1": False},
           "decisions": [{"recipient": "grazyna", "status": "executed", "executed_simulated": False}],
           "texts": ["Reminder: ... tax risk (Belegpflicht)."]}
    ok = {"actions": ["request_invoice", "remind_invoice"], "recipient": "grazyna", "documented": {"t1": False},
          "says": ["belegpflicht"]}
    assert scoring.action_correct(out, ok).value is True
    wrong = scoring.action_correct(out, {**ok, "actions": ["remind_invoice", "request_invoice"], "recipient": "rosa"})
    assert wrong.value is False and "actions" in wrong.rationale and "recipient" in wrong.rationale
    assert scoring.action_correct(out, {"says": ["SIMULATED"]}).value is False
    assert scoring.action_correct(out, {"level": "AUTO"}).value is None
    assert scoring.action_correct({**out, "error": "RuntimeError: boom"}, ok).value is False


def test_execution_before_approval_is_a_violation():
    pending = {"action_type": "request_credit_line", "level": "APPROVAL", "status": "pending",
               "executed_simulated": False, "approval_status": "pending"}
    approved = {**pending, "status": "executed", "executed_simulated": True, "approval_status": "approved"}
    assert scoring.no_execution_without_approval({"snapshots": [[pending], [approved]]}).value is True
    early = {**pending, "status": "executed", "executed_simulated": True}  # executed while the CEO has not answered
    bad = scoring.no_execution_without_approval({"snapshots": [[early], [approved]]})
    assert bad.value is False and "step 1" in bad.rationale
    rejected_but_run = {**approved, "approval_status": "rejected"}
    assert scoring.no_execution_without_approval({"snapshots": [[rejected_but_run]]}).value is False
    for_real = {"action_type": "request_credit_line", "level": "NOTIFY", "status": "executed", "executed_simulated": False}
    assert "SIMULATED" in scoring.no_execution_without_approval({"snapshots": [[for_real]]}).rationale
    assert scoring.no_execution_without_approval({"target": "policy", "snapshots": []}).value is None


# -- the evaluation in MLflow -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mlflow_store(tmp_path_factory):
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    import mlflow

    from cfo.eval import runner
    from cfo.usage import recorder

    class NullSink:
        def write(self, record):
            pass

    tmp = tmp_path_factory.mktemp("mlflow")
    uri = f"sqlite:///{tmp / 'mlflow.db'}"
    obs.reset_tracing()
    recorder.set_sink(NullSink())  # module scope: the per-test fixture of conftest is not active yet

    def run(**kw):
        return runner.run_eval(tracking_uri=uri, llm_mode="fake", schema=SCHEMA,
                               artifact_location=(tmp / "artifacts").as_uri(), **kw)

    yield mlflow, run
    obs.reset_tracing()
    recorder.set_sink(None)
    with psycopg.connect(url, autocommit=True) as conn:  # the run drops it; this is for a run that crashed
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture(scope="module")
def full_run(mlflow_store):
    mlflow, run = mlflow_store
    return mlflow, run()


@needs_mlflow
def test_run_exists_with_tags_and_aggregate_metrics(full_run):
    mlflow, report = full_run
    exp = mlflow.get_experiment_by_name("cfo-agent-eval")
    assert exp is not None and report.experiment_id == exp.experiment_id
    run = mlflow.get_run(report.run_id)
    assert run.info.status == "FINISHED"
    assert run.data.tags["llm_model"] == "fake" and run.data.tags["llm_mode"] == "fake"
    assert run.data.tags["git_sha"] and run.data.tags["scenarios"] == str(len(report.rows))
    metrics = run.data.metrics
    for name in PASS_FAIL:
        assert 0.0 <= metrics[f"{name}/mean"] <= 1.0
    assert metrics["latency_s/mean"] > 0 and metrics["latency_s/max"] >= metrics["latency_s/mean"]
    assert metrics["scenarios"] == len(load_scenarios()) == len(report.rows)
    assert metrics["scenarios_passed"] == sum(r["passed"] for r in report.rows)
    assert metrics["pass_rate"] == pytest.approx(metrics["scenarios_passed"] / metrics["scenarios"])
    assert report.metrics["policy_level_correct/mean"] == metrics["policy_level_correct/mean"]


@needs_mlflow
def test_scripted_model_passes_every_scenario_on_the_current_agent(full_run):
    """With the scripted model a failure is a finding about the agent code (or a stale scenario), never noise."""
    _, report = full_run
    assert [(r["scenario"], r["why_failed"]) for r in report.failed] == []
    by_id = {r["scenario"]: r for r in report.rows}
    found = by_id["invoice_found_on_workstation_attached"]["outputs"]
    assert found["level"] == "AUTO" and found["documented"] == {"txn_as": True} and found["llm_calls"] == 1
    chain = by_id["nothing_found_full_escalation_chain"]["outputs"]
    assert [d["recipient"] for d in chain["decisions"]] == ["grazyna", "grazyna", "rosa", "rosa", "vincenzo",
                                                            "vincenzo", "vincenzo"]
    approved = by_id["what_if_credit_line_approved_is_simulated"]["outputs"]
    assert [s[0]["approval_status"] for s in approved["snapshots"]] == ["pending", "approved"]
    assert approved["snapshots"][0][0]["executed_simulated"] is False and approved["decisions"][0]["executed_simulated"]
    assert approved["messages"][0]["buttons"] == ["approve", "reject"] and approved["messages"][-1]["level"] == "SIMULATED"
    assert by_id["cost_follows_metric_no_alert"]["outputs"]["messages"] == []
    assert by_id["routine_payment_is_auto"]["action_correct"] is None  # policy-only: no action to check


@needs_mlflow
def test_every_scenario_is_one_trace_linked_to_the_run_with_its_scores(full_run):
    mlflow, report = full_run
    traces = mlflow.search_traces(locations=[report.experiment_id], run_id=report.run_id, return_type="list")
    by_scenario = {t.info.tags["cfo.eval.scenario"]: t for t in traces}
    assert set(by_scenario) == {r["scenario"] for r in report.rows} and len(traces) == len(report.rows)
    assert {r["trace_id"] for r in report.rows} == {t.info.trace_id for t in traces}

    trace = by_scenario["what_if_credit_line_approved_is_simulated"]
    names = [s.name for s in trace.data.spans]
    (root,) = [s for s in trace.data.spans if s.parent_id is None]
    assert root.name == "eval.scenario"
    # the agent spans of T027 hang below the scenario: both the proposal and the resumed approval
    assert {"agent.handle", "propose_credit_line", "request_approval", "agent.resume_approval"} <= set(names)
    scores = {a.name: a.value for a in trace.info.assessments}
    assert scores["policy_level_correct"] is True and scores["no_execution_without_approval"] is True
    assert scores["action_correct"] is True and scores["explanation_cites_amount"] is True
    assert scores["latency_s"] > 0

    found = by_scenario["invoice_found_on_workstation_attached"]
    assert "llm.chat" in [s.name for s in found.data.spans] and found.info.tags["cfo.eval.llm"] == "fake"
    policy = by_scenario["new_vendor_small_payment_is_notify"]
    assert "policy.classify" in [s.name for s in policy.data.spans]
    assert "action_correct" not in {a.name for a in policy.info.assessments}


@needs_mlflow
def test_per_scenario_table_is_logged(full_run):
    mlflow, report = full_run
    table = mlflow.load_table("eval_scenarios.json", run_ids=[report.run_id])
    assert len(table) == len(report.rows)
    assert {"scenario", "title", "source", "passed", "expected_level", "level", "actions", "trace_id", "why_failed",
            *PASS_FAIL, "latency_s"} <= set(table.columns)
    row = table[table["scenario"] == "payment_above_5_percent_needs_approval"].iloc[0]
    assert row["level"] == "APPROVAL" and row["policy_level_correct"] == 1 and bool(row["passed"])


@needs_mlflow
def test_a_deliberately_wrong_expectation_scores_zero(mlflow_store):
    mlflow, run = mlflow_store
    (found,) = load_scenarios(only=["invoice_found_on_workstation_attached"])
    wrong = Scenario.model_validate({**found.model_dump(), "expect": {
        **found.expect.model_dump(), "level": "APPROVAL", "actions": ["request_invoice"], "cites": ["999.99"]}})
    report = run(scenarios=[wrong], run_name="wrong-expectation")
    metrics = mlflow.get_run(report.run_id).data.metrics
    assert metrics["policy_level_correct/mean"] == 0.0
    assert metrics["action_correct/mean"] == 0.0
    assert metrics["explanation_cites_amount/mean"] == 0.0
    assert metrics["no_execution_without_approval/mean"] == 1.0  # the agent itself behaved
    assert metrics["scenarios_passed"] == 0 and metrics["pass_rate"] == 0.0
    (row,) = report.rows
    assert not row["passed"] and "level AUTO, expected APPROVAL" in row["why_failed"]
    (trace,) = mlflow.search_traces(locations=[report.experiment_id], run_id=report.run_id, return_type="list")
    assert {a.name: a.value for a in trace.info.assessments}["policy_level_correct"] is False


@needs_mlflow
def test_a_crashing_scenario_fails_its_scores_and_the_run_still_finishes(mlflow_store):
    mlflow, run = mlflow_store
    broken = Scenario.model_validate({"id": "answer_without_approval", "title": "t", "steps": [
        {"answer": {"approved": True}}], "expect": {"level": "APPROVAL", "actions": ["request_credit_line"]}})
    (ok,) = load_scenarios(only=["routine_payment_is_auto"])
    report = run(scenarios=[broken, ok], run_name="crash")
    assert mlflow.get_run(report.run_id).info.status == "FINISHED"
    assert [r["passed"] for r in report.rows] == [False, True]
    assert "no approval is pending" in report.rows[0]["why_failed"]
    assert report.metrics["policy_level_correct/mean"] == 0.5


@needs_mlflow
def test_optional_llm_judge_is_one_more_score(mlflow_store):
    from cfo.eval.harness import scripted_llm

    _, run = mlflow_store
    inner = scripted_llm()

    class Judging:
        calls = inner.calls

        def chat(self, messages, tools=None, **kw):
            if "review a message" in (messages[0].content or ""):
                return type("R", (), {"content": "YES, it says what was found and where."})()
            return inner.chat(messages, tools, **kw)

    report = run(scenarios=load_scenarios(only=["invoice_found_on_workstation_attached", "cost_follows_metric_no_alert"]),
                 llm=Judging(), judge=True, run_name="judge")
    assert report.metrics["explanation_clear_judge/mean"] == 1.0
    assert [r.get("explanation_clear_judge") for r in report.rows] == [1, None]  # nothing was said in the second


# -- command line -------------------------------------------------------------------------------------------
def test_cli_help_and_missing_tracking_uri(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    if HAS_MLFLOW:
        assert main(["--only", "routine_payment_is_auto"]) == 2
        assert "tracking URI" in capsys.readouterr().err
    assert main(["--only", "no_such_scenario"]) == 2


@needs_mlflow
def test_cli_runs_and_prints_the_summary(mlflow_store, capsys):
    mlflow, _ = mlflow_store
    code = main(["--tracking-uri", mlflow.get_tracking_uri(), "--schema", SCHEMA, "--strict",
                 "--only", "routine_payment_is_auto,payment_above_5_percent_needs_approval"])
    out = capsys.readouterr().out
    assert code == 0 and "2/2 scenarios passed" in out and "policy_level_correct/mean=1.000" in out
    assert "MLflow run" in out and "cfo-agent-eval" in out
