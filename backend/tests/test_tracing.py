"""MLflow tracing of the agent (T027).

The MLflow tests need the optional extra (`uv run --extra mlflow ...`) and are skipped without it; they log to a
temporary `sqlite:///` store. The agent tests also need the compose Postgres (fixture `make_env` of test_agent).
The first two tests run in every install: without `MLFLOW_TRACKING_URI` nothing imports mlflow.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cfo import observability as obs
from cfo.domain import Event, EventKind

from test_agent import DOC, TS, bank, make_env  # noqa: F401  (make_env is a fixture)

BACKEND = Path(__file__).resolve().parents[1]
HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
needs_mlflow = pytest.mark.skipif(not HAS_MLFLOW, reason="mlflow extra not installed")
EXPERIMENT = "cfo-agent-test"

NO_MLFLOW_SCRIPT = """
import sys
from cfo import observability as obs
import cfo.agent.graph, cfo.agent.investigate, cfo.api.app
assert obs.init_tracing(obs.TracingSettings(_env_file=None)) is False
assert obs.enabled() is False
with obs.span("x", inputs={"a": 1}) as s:
    s.set_outputs({"b": 2})
    obs.set_trace_tags({"k": "v"})
assert obs.traced_node("n", lambda state: {"ok": state})(1) == {"ok": 1}
loaded = sorted(m for m in sys.modules if m == "mlflow" or m.startswith("mlflow."))
assert not loaded, loaded
print("clean")
"""


# -- off by default ---------------------------------------------------------------------------------
def test_without_tracking_uri_nothing_imports_mlflow():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MLFLOW_")}
    out = subprocess.run([sys.executable, "-c", NO_MLFLOW_SCRIPT], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("clean")


def test_disabled_helpers_are_transparent():
    assert not obs.enabled()

    class Llm:
        calls = 0

        def chat(self, messages, tools=None, **kw):
            self.calls += 1
            return "answer"

    llm = Llm()
    wrapped = obs.traced_llm(llm)
    assert wrapped.chat([]) == "answer" and wrapped.calls == 1
    with pytest.raises(ValueError):
        with obs.span("boom"):
            raise ValueError("propagates")


# -- with MLflow ------------------------------------------------------------------------------------
@pytest.fixture
def tracing(tmp_path):
    import mlflow

    settings = obs.TracingSettings(_env_file=None, mlflow_tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
                                   mlflow_agent_experiment=EXPERIMENT)
    assert obs.init_tracing(settings) is True
    yield mlflow
    obs.reset_tracing()


def traces(mlflow):
    mlflow.flush_trace_async_logging()
    exp = mlflow.get_experiment_by_name(EXPERIMENT)
    assert exp is not None
    return mlflow.search_traces(experiment_ids=[exp.experiment_id], return_type="list")


def children(trace, parent_name):
    spans = trace.data.spans
    (parent,) = [s for s in spans if s.name == parent_name]
    return [s for s in spans if s.parent_id == parent.span_id]


@needs_mlflow
def test_init_is_idempotent_and_spans_nest(tracing):
    settings = obs.TracingSettings(_env_file=None, mlflow_tracking_uri="sqlite:///ignored-second-call.db")
    assert obs.init_tracing(settings) is True and obs.enabled()
    with obs.span("root", inputs={"q": 1}) as root:
        obs.set_trace_tags({"cfo.outcome": "done", "skipped": None})
        assert obs.traced_node("child", lambda state: {"steps": ["x"]})({"event": {}}) == {"steps": ["x"]}
        root.set_outputs({"a": 2})
    (trace,) = traces(tracing)
    assert trace.info.tags["cfo.outcome"] == "done" and "skipped" not in trace.info.tags
    assert [s.name for s in children(trace, "root")] == ["child"]
    assert not Path("ignored-second-call.db").exists()


@needs_mlflow
def test_interrupt_is_not_an_error_span_but_real_errors_are(tracing):
    from langgraph.errors import GraphInterrupt

    def waiting(state):
        raise GraphInterrupt()

    def broken(state):
        raise RuntimeError("bug")

    with obs.span("root"):
        with pytest.raises(GraphInterrupt):
            obs.traced_node("waiting", waiting)({})
        with pytest.raises(RuntimeError):
            obs.traced_node("broken", broken)({})
    (trace,) = traces(tracing)
    status = {s.name: s.status.status_code.value for s in trace.data.spans}
    assert status["waiting"] == "OK" and status["broken"] == "ERROR"
    (w,) = [s for s in trace.data.spans if s.name == "waiting"]
    assert w.attributes.get("cfo.interrupted") is True


@needs_mlflow
def test_init_never_raises(monkeypatch):
    import mlflow

    def boom(*a, **kw):
        raise RuntimeError("server down")

    monkeypatch.setattr(mlflow, "set_experiment", boom)
    try:
        assert obs.init_tracing(obs.TracingSettings(_env_file=None, mlflow_tracking_uri="sqlite:///:memory:")) is False
        assert not obs.enabled()
    finally:
        obs.reset_tracing()


@needs_mlflow
def test_golden_path_event_is_one_trace_with_node_and_llm_spans(tracing, make_env):
    env = make_env(files={"Downloads/AS-69664.txt": DOC})
    res = env.agent.handle(bank("txn_0"))
    assert res.status == "done"

    (trace,) = traces(tracing)
    tags = trace.info.tags
    assert tags["cfo.event_kind"] == "bank_transaction"
    assert tags["cfo.transaction_id"] == "txn_0"
    assert tags["cfo.policy_level"] == "AUTO"
    assert tags["cfo.outcome"] == "done"
    assert tags["cfo.simulated"] == "no"

    spans = trace.data.spans
    (root,) = [s for s in spans if s.parent_id is None]
    assert root.name == "agent.handle"
    node_names = [s.name for s in children(trace, "agent.handle")]
    assert node_names == ["load_txn", "find_docs", "match", "attach"]  # one child span per visited node
    # the LLM call (invoice extraction) sits under the node that made it
    llm_spans = children(trace, "match")
    assert [s.name for s in llm_spans] == ["llm.chat"]
    assert "submit_invoice" in str(llm_spans[0].outputs)
    assert env.llm.calls  # the FakeLLM itself was called through the tracing proxy


@needs_mlflow
def test_approval_and_resume_are_traced_with_level_and_simulation(tracing, make_env):
    env = make_env()
    ev = Event(ts=TS, kind=EventKind.METRIC, payload={
        "signal": "liquidity_forecast", "month": "2026-02", "min_balance": "-5000", "requested_amount": "20000"})
    res = env.agent.handle(ev)
    assert res.status == "awaiting_approval"
    done = env.agent.resume_approval(res.approval_id, True, "vincenzo")
    assert done.status == "done"

    by_root = {next(s.name for s in t.data.spans if s.parent_id is None): t for t in traces(tracing)}
    assert set(by_root) == {"agent.handle", "agent.resume_approval"}
    first, second = by_root["agent.handle"], by_root["agent.resume_approval"]
    assert first.info.tags["cfo.outcome"] == "awaiting_approval"
    assert first.info.tags["cfo.policy_level"] == "APPROVAL"
    assert first.info.tags["cfo.simulated"] == "no"
    assert {s.name: s.status.status_code.value for s in first.data.spans}["await_approval"] == "OK"
    assert second.info.tags["cfo.approval_id"] == res.approval_id
    assert second.info.tags["cfo.policy_level"] == "APPROVAL"
    assert second.info.tags["cfo.simulated"] == "yes"
    assert second.info.tags["cfo.outcome"] == "done"


@needs_mlflow
def test_sub_agent_investigation_spans_are_children_of_the_investigate_node(tracing, make_env):
    env = make_env()
    env.agent.handle(Event(ts=TS, kind=EventKind.METRIC, payload={"signal": "cost_metrics_updated"}))
    (trace,) = traces(tracing)
    assert [s.name for s in children(trace, "agent.handle")] == ["investigate", "alert"]
    assert [s.name for s in children(trace, "investigate")][:2] == ["investigate.gather", "investigate.detect"]
