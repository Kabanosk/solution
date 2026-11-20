"""Job log persistence and approval checkpoint behavior without external services."""

import copy
import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from cfo.agent.job_log import MAX_ENTRIES, MAX_MESSAGE_CHARS, recorded_node


class MemoryRepo:
    def __init__(self):
        self.logs = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def merge_decision_job_log(self, ids, entries):
        for decision_id in ids:
            history = self.logs.get(decision_id, []).copy()
            for entry in entries:
                if entry not in history:
                    history.append(copy.deepcopy(entry))
            self.logs[decision_id] = history[-MAX_ENTRIES:]


def test_records_work_before_decision_and_keeps_jobs_separate():
    repo = MemoryRepo()
    first = recorded_node("find_docs", lambda state: {"steps": ["Found two candidate documents"]}, lambda: repo)({})
    assert repo.logs == {}
    result = recorded_node("attach", lambda state: {"steps": ["Invoice attached"], "decision_ids": ["dec-1"]}, lambda: repo)(first)
    assert [entry["message"] for entry in repo.logs["dec-1"]] == ["Found two candidate documents", "Invoice attached"]
    assert result["decision_ids"] == ["dec-1"]
    recorded_node("alert", lambda state: {"steps": ["Separate action"], "decision_ids": ["dec-2"]}, lambda: repo)({})
    assert [entry["message"] for entry in repo.logs["dec-2"]] == ["Separate action"]


def test_approval_interrupt_is_not_an_error_and_resume_keeps_history():
    repo = MemoryRepo()

    class State(TypedDict, total=False):
        job_log: Annotated[list[dict], operator.add]
        decision_ids: Annotated[list[str], operator.add]
        steps: Annotated[list[str], operator.add]

    def request(state):
        return {"decision_ids": ["dec-approval"], "steps": ["Waiting for approval"]}

    def answer(state):
        approved = interrupt("Proceed?")
        return {"steps": ["Approval accepted" if approved else "Approval rejected"]}

    graph = StateGraph(State)
    graph.add_node("request", recorded_node("request_approval", request, lambda: repo))
    graph.add_node("answer", recorded_node("await_approval", answer, lambda: repo))
    graph.add_edge(START, "request")
    graph.add_edge("request", "answer")
    graph.add_edge("answer", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "job-log-test"}}
    agent.invoke({}, config)
    assert [entry["message"] for entry in repo.logs["dec-approval"]] == ["Waiting for approval"]
    result = agent.invoke(Command(resume=True), config)
    assert result["steps"] == ["Waiting for approval", "Approval accepted"]
    assert [entry["message"] for entry in repo.logs["dec-approval"]] == result["steps"]
    assert all(entry["level"] == "info" and entry["ts"].endswith("Z") for entry in repo.logs["dec-approval"])


def test_failure_is_recorded_without_leaking_exception_details():
    repo = MemoryRepo()

    def fail(state):
        raise RuntimeError("private-connection-string")

    with pytest.raises(RuntimeError, match="private-connection-string"):
        recorded_node("attach", fail, lambda: repo)({"decision_ids": ["dec-1"]})
    entry = repo.logs["dec-1"][-1]
    assert entry["level"] == "error"
    assert "private-connection-string" not in entry["message"]


def test_logging_failure_does_not_repeat_or_prevent_business_action():
    calls = []

    def action(state):
        calls.append("executed")
        return {"decision_ids": ["dec-1"], "steps": ["Done"]}

    def unavailable():
        raise ConnectionError("database offline")

    result = recorded_node("action", action, unavailable)({})
    assert calls == ["executed"]
    assert result["steps"] == ["Done"]


def test_persisted_history_and_messages_are_bounded():
    repo = MemoryRepo()
    old = [{"message": str(index)} for index in range(MAX_ENTRIES + 10)]
    recorded_node("check", lambda state: {"steps": ["x" * (MAX_MESSAGE_CHARS + 20)]}, lambda: repo)(
        {"decision_ids": ["dec-1"], "job_log": old}
    )
    assert len(repo.logs["dec-1"]) == MAX_ENTRIES
    assert len(repo.logs["dec-1"][-1]["message"]) == MAX_MESSAGE_CHARS


def test_retry_retains_failure_outside_checkpoint_without_duplicating_history():
    repo = MemoryRepo()
    state = recorded_node("request", lambda state: {"decision_ids": ["dec-1"], "steps": ["Request recorded"]}, lambda: repo)({})

    def fail(state):
        raise RuntimeError("temporary failure")

    with pytest.raises(RuntimeError):
        recorded_node("finish", fail, lambda: repo)(state)
    recorded_node("finish", lambda state: {"steps": ["Completed after retry"]}, lambda: repo)(state)
    history = repo.logs["dec-1"]
    assert [entry["level"] for entry in history] == ["info", "error", "info"]
    assert history[0]["message"] == "Request recorded"
    assert history[-1]["message"] == "Completed after retry"
    assert len({entry["id"] for entry in history}) == 3
