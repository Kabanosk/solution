"""MLflow tracing of the agent (D-015): one trace per handled event, one child span per graph node, LLM calls
under the node that made them.

Off by default. `init_tracing()` turns it on only when `MLFLOW_TRACKING_URI` is set and the optional `mlflow`
extra is installed; until then every helper here is a no-op and `mlflow` is never imported. Call it once at
process start (API app startup, worker entrypoint) before events are handled.

Authentication is MLflow's own business: it reads the standard variables (`MLFLOW_TRACKING_AUTH`,
`MLFLOW_TRACKING_TOKEN`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_INSECURE_TLS`, ...) from the environment. On RHOAI
set `MLFLOW_TRACKING_AUTH=kubernetes-namespaced`; nothing here hardcodes credentials.

Tracing must never break the agent: every MLflow call is guarded, failures are logged as warnings.

    init_tracing()                          # once
    with span("agent.handle", inputs=...) as s:
        ...
        set_trace_tags({"cfo.outcome": "done"})
        s.set_outputs(...)
    g.add_node(name, traced_node(name, fn)) # LangGraph node -> child span
    llm = traced_llm(llm)                   # every `chat` -> span "llm.chat"
"""

from __future__ import annotations

import functools
import importlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from cfo.config import REPO_ROOT

log = logging.getLogger(__name__)

MAX_CHARS = 4000  # span inputs/outputs are clipped: documents can be long, traces should stay readable


class TracingSettings(BaseSettings):
    """Own settings class (same `.env` as cfo.config) so parallel tasks do not collide on config.py."""

    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8",
                                      extra="ignore", env_ignore_empty=True)

    mlflow_tracking_uri: str = ""  # MLFLOW_TRACKING_URI; empty = tracing off
    mlflow_agent_experiment: str = "cfo-agent"  # MLFLOW_AGENT_EXPERIMENT


_lock = threading.Lock()
_mlflow: Any = None  # the mlflow module once tracing is on
_warned = False


def enabled() -> bool:
    return _mlflow is not None


def init_tracing(settings: Any = None) -> bool:
    """Turn tracing on if configured. Returns whether tracing is on. Idempotent; never raises.

    `settings`: anything with `mlflow_tracking_uri` and `mlflow_agent_experiment` (default: `TracingSettings()`).
    """
    global _mlflow
    with _lock:
        if _mlflow is not None:
            return True
        try:
            s = settings if settings is not None else TracingSettings()
            uri = getattr(s, "mlflow_tracking_uri", "") or os.environ.get("MLFLOW_TRACKING_URI", "")
            if not uri:
                return False
            experiment = getattr(s, "mlflow_agent_experiment", "") or "cfo-agent"
            try:
                mlflow = importlib.import_module("mlflow")
            except ImportError:
                log.warning("MLFLOW_TRACKING_URI is set but mlflow is not installed (uv sync --extra mlflow); "
                            "agent tracing is off")
                return False
            mlflow.set_tracking_uri(uri)
            mlflow.set_experiment(experiment)
            try:
                # the real client (cfo.llm.OpenAICompatLLM) uses the `openai` package: token usage, latency, wire data
                importlib.import_module("mlflow.openai").autolog()
            except Exception as exc:  # noqa: BLE001 - spans from our own wrappers still work
                log.warning("mlflow.openai.autolog() failed: %s", exc)
            _mlflow = mlflow
            log.info("MLflow tracing on: %s, experiment %s", uri, experiment)
            return True
        except Exception as exc:  # noqa: BLE001 - observability must not stop the process from starting
            log.warning("MLflow tracing could not be initialised, continuing without it: %s", exc)
            return False


def reset_tracing() -> None:
    """Turn tracing off again (tests)."""
    global _mlflow, _warned
    with _lock:
        mlflow, _mlflow, _warned = _mlflow, None, False
    if mlflow is not None:
        try:
            mlflow.flush_trace_async_logging()
            importlib.import_module("mlflow.openai").autolog(disable=True)
        except Exception:  # noqa: BLE001
            pass


def _warn(what: str, exc: BaseException) -> None:
    global _warned
    if not _warned:  # once: a dead tracking server would otherwise flood the log on every node
        _warned = True
        log.warning("MLflow tracing failed (%s): %s; the agent continues", what, exc)


def _jsonable(obj: Any) -> Any:
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else str(obj)


def _clip(value: Any) -> Any:
    """JSON-friendly and bounded copy of `value` for span inputs/outputs."""
    try:
        text = json.dumps(value, default=_jsonable, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = repr(value)
    if len(text) <= MAX_CHARS:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text[:MAX_CHARS] + f"... [{len(text) - MAX_CHARS} more chars]"


class Span:
    """What `span()` yields. All methods are safe to call whether tracing is on or not."""

    def __init__(self, live: Any = None):
        self._live = live

    def _call(self, method: str, *args: Any) -> None:
        if self._live is None:
            return
        try:
            getattr(self._live, method)(*args)
        except Exception as exc:  # noqa: BLE001
            _warn(method, exc)

    def set_inputs(self, value: Any) -> None:
        self._call("set_inputs", _clip(value))

    def set_outputs(self, value: Any) -> None:
        self._call("set_outputs", _clip(value))

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        self._call("set_attributes", {k: v for k, v in attributes.items() if v is not None})


_NOOP = Span()


@contextmanager
def span(name: str, span_type: str = "CHAIN", inputs: Any = None, attributes: Mapping[str, Any] | None = None,
         not_errors: tuple[type[BaseException], ...] = ()) -> Iterator[Span]:
    """A span under the current one (a new trace if there is none). No-op while tracing is off.

    Exceptions always propagate. They mark the span as failed, except `not_errors` (control flow such as a
    LangGraph interrupt), which close the span normally with attribute `cfo.interrupted`.
    """
    mlflow = _mlflow
    if mlflow is None:
        yield _NOOP
        return
    try:
        cm = mlflow.start_span(name=name, span_type=span_type)
        live = cm.__enter__()
    except Exception as exc:  # noqa: BLE001
        _warn("start_span", exc)
        yield _NOOP
        return
    handle = Span(live)
    if inputs is not None:
        handle.set_inputs(inputs)
    if attributes:
        handle.set_attributes(attributes)

    def close(*exc_info: Any) -> None:
        try:
            cm.__exit__(*exc_info)
        except Exception as exc:  # noqa: BLE001
            _warn("end_span", exc)

    try:
        yield handle
    except not_errors:
        handle.set_attributes({"cfo.interrupted": True})
        close(None, None, None)
        raise
    except BaseException as exc:
        close(type(exc), exc, exc.__traceback__)
        raise
    else:
        close(None, None, None)


def set_trace_tags(tags: Mapping[str, Any]) -> None:
    """Tags on the trace of the current span (searchable in the MLflow UI). None values are skipped."""
    mlflow = _mlflow
    if mlflow is None:
        return
    try:
        mlflow.update_current_trace(tags={k: str(v) for k, v in tags.items() if v is not None})
    except Exception as exc:  # noqa: BLE001
        _warn("update_current_trace", exc)


def _graph_control_flow() -> tuple[type[BaseException], ...]:
    try:
        from langgraph.errors import GraphBubbleUp  # interrupt() and friends: not failures

        return (GraphBubbleUp,)
    except ImportError:
        return ()


def traced_node(name: str, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a LangGraph node function `fn(state) -> update` in a span called `name`."""
    not_errors = _graph_control_flow()

    @functools.wraps(fn)
    def node(state: Any) -> Any:
        if _mlflow is None:
            return fn(state)
        keys = sorted(state) if isinstance(state, Mapping) else None
        with span(name, inputs={"state_keys": keys}, not_errors=not_errors) as s:
            out = fn(state)
            s.set_outputs(out)
            return out

    return node


class _TracedLLM:
    """`cfo.llm.LLM` proxy: each `chat` is a span `llm.chat` (the openai autolog span, if any, nests inside)."""

    def __init__(self, inner: Any):
        self._inner = inner

    def chat(self, messages: Any, tools: Any = None, **kwargs: Any) -> Any:
        if _mlflow is None:
            return self._inner.chat(messages, tools, **kwargs)
        inputs = {"messages": list(messages), "tools": [getattr(t, "name", str(t)) for t in tools or []], **kwargs}
        with span("llm.chat", span_type="CHAT_MODEL", inputs=inputs,
                  attributes={"llm.model": getattr(self._inner, "model", None),
                              "llm.client": type(self._inner).__name__}) as s:
            out = self._inner.chat(messages, tools, **kwargs)
            s.set_outputs(out)
            return out

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


def traced_llm(llm: Any) -> Any:
    """`llm` with every `chat` call traced. Cheap pass-through while tracing is off; safe to apply twice."""
    return llm if isinstance(llm, _TracedLLM) else _TracedLLM(llm)
