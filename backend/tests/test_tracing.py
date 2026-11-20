"""MLflow tracing of the agent (T027).

The MLflow tests need the optional extra (`uv run --extra mlflow ...`) and are skipped without it; they log to a
temporary `sqlite:///` store. The first two tests run in every install: without `MLFLOW_TRACKING_URI` nothing
imports mlflow. (The traces of the old December agent went with it.)
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cfo import observability as obs

BACKEND = Path(__file__).resolve().parents[1]
HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
needs_mlflow = pytest.mark.skipif(not HAS_MLFLOW, reason="mlflow extra not installed")
EXPERIMENT = "cfo-agent-test"

NO_MLFLOW_SCRIPT = """
import sys
from cfo import observability as obs
import cfo.api.app, scout.worker
assert obs.init_tracing() is False
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

    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)  # keep trace artifacts in tmp_path too (the default is ./mlruns in the cwd)
    mlflow.create_experiment(EXPERIMENT, artifact_location=(tmp_path / "artifacts").as_uri())
    settings = obs.TracingSettings(mlflow_tracking_uri=uri, mlflow_agent_experiment=EXPERIMENT)
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
    settings = obs.TracingSettings(mlflow_tracking_uri="sqlite:///ignored-second-call.db")
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
        assert obs.init_tracing(obs.TracingSettings(mlflow_tracking_uri="sqlite:///:memory:")) is False
        assert not obs.enabled()
    finally:
        obs.reset_tracing()
