"""Approvals, live events (SSE) and simulator control of the API (T020).

Everything runs in an isolated Postgres schema `t020_test` (dropped afterwards, the shared public schema is never
touched), with a fake agent and a fake poll wait: no real-time sleeping. Skips when Postgres is not reachable.
"""

from __future__ import annotations

import os
import json
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from cfo.api import stream
from cfo.api.app import create_app
from cfo.api.deps import (
    StreamConfig,
    get_agent,
    get_repo,
    get_sim_control,
    get_stream_config,
    set_agent,
)
from cfo.config import get_settings
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    DecisionStatus,
    Event,
    EventKind,
    PolicyLevel,
)
from cfo.sim.engine import Simulator

SCHEMA = f"t020_test_{os.getpid()}"
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_url():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{SCHEMA}"
    with psycopg.connect(isolated) as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield isolated
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture
def repo(db_url):
    r = Repository.connect(db_url)
    yield r
    r.conn.close()


def add_approval(repo: Repository, n: int, *, approver: str = "vincenzo", at: datetime = T0) -> str:
    ts = at + timedelta(minutes=n)
    dec = Decision(id=f"dec_{n}", ts=ts, action_type="request_credit_line", summary=f"Credit line {n}",
                   level=PolicyLevel.APPROVAL, reason="Credit lines always need CEO approval.",
                   status=DecisionStatus.PENDING, approval_request_id=f"apr_{n}", payload={"amount": "20000.00"})
    repo.add_decision(dec)
    repo.add_approval_request(ApprovalRequest(id=f"apr_{n}", decision_id=dec.id, approver_id=approver,
                                              question=f"Apply for credit line {n}?", created_at=ts))
    return f"apr_{n}"


class FakeAgent:
    """Same contract as `Agent.resume_approval`: PermissionError, KeyError, idempotent, resolves in the database."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.calls: list[tuple[str, bool, str]] = []

    def resume_approval(self, approval_id: str, approved: bool, by: str):
        from cfo.agent.graph import HandleResult

        self.calls.append((approval_id, approved, by))
        with Repository.connect(self.db_url) as repo:
            req = repo.get_approval_request(approval_id)
            if req is None:
                raise KeyError(approval_id)
            if by != req.approver_id:
                raise PermissionError(by)
            if req.status != ApprovalStatus.PENDING:
                return HandleResult(status="already_resolved", approval_id=approval_id)
            dec = repo._all("SELECT * FROM decisions WHERE id = %s", (req.decision_id,))[0]
            dec = Decision(**dec)
            dec.actor = by
            dec.status = DecisionStatus.EXECUTED if approved else DecisionStatus.REJECTED
            dec.executed_simulated = approved
            repo.add_decision(dec)
            repo.resolve_approval(approval_id, ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED)
        return HandleResult(status="done", approval_id=approval_id)


@pytest.fixture
def agent(db_url):
    return FakeAgent(db_url)


@pytest.fixture
def client(db_url, agent):
    app = create_app()

    def repo_override():
        with Repository.connect(db_url) as r:
            yield r

    app.dependency_overrides[get_repo] = repo_override
    app.dependency_overrides[get_agent] = lambda: agent
    return TestClient(app)


# --- list and detail ---------------------------------------------------------------------------------------


def test_list_filters_and_pagination(client, repo):
    for n in (1, 2, 3):
        add_approval(repo, n)
    repo.resolve_approval("apr_1", ApprovalStatus.APPROVED)
    repo.resolve_approval("apr_2", ApprovalStatus.REJECTED)

    body = client.get("/v1/approvals").json()
    assert body["total"] == 3 and body["stub"] is False
    assert [i["id"] for i in body["items"]] == ["apr_3", "apr_2", "apr_1"]  # newest first

    pending = client.get("/v1/approvals?state=pending").json()
    assert [i["id"] for i in pending["items"]] == ["apr_3"] and pending["total"] == 1
    resolved = client.get("/v1/approvals?state=resolved").json()
    assert {i["id"] for i in resolved["items"]} == {"apr_1", "apr_2"}
    assert [i["id"] for i in client.get("/v1/approvals?state=approved").json()["items"]] == ["apr_1"]
    assert [i["id"] for i in client.get("/v1/approvals?state=rejected").json()["items"]] == ["apr_2"]

    page = client.get("/v1/approvals?limit=1&offset=1").json()
    assert page["total"] == 3 and page["limit"] == 1 and page["offset"] == 1
    assert [i["id"] for i in page["items"]] == ["apr_2"]


def test_list_validation(client):
    assert client.get("/v1/approvals?state=bogus").status_code == 422
    r = client.get("/v1/approvals?limit=0")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_detail_embeds_the_decision(client, repo):
    add_approval(repo, 1)
    body = client.get("/v1/approvals/apr_1").json()
    assert body["status"] == "pending" and body["approver_id"] == "vincenzo" and body["resolved_at"] is None
    assert body["decision"]["id"] == "dec_1" and body["decision"]["status"] == "pending"
    assert body["decision"]["payload"] == {"amount": "20000.00"}
    missing = client.get("/v1/approvals/apr_nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


# --- approve / reject ---------------------------------------------------------------------------------------


def test_approve_resolves_and_returns_state(client, repo, agent):
    add_approval(repo, 1)
    r = client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved" and body["already_resolved"] is False and body["resolved_at"]
    assert body["decision"]["status"] == "executed" and body["decision"]["executed_simulated"] is True
    assert body["decision"]["actor"] == "vincenzo"
    assert agent.calls == [("apr_1", True, "vincenzo")]
    assert client.get("/v1/approvals?state=pending").json()["total"] == 0
    assert client.get("/v1/decisions/dec_1").json()["status"] == "executed"


def test_reject(client, repo, agent):
    add_approval(repo, 1)
    body = client.post("/v1/approvals/apr_1:reject", json={"by": "vincenzo"}).json()
    assert body["status"] == "rejected" and body["decision"]["status"] == "rejected"
    assert body["decision"]["executed_simulated"] is False
    assert agent.calls == [("apr_1", False, "vincenzo")]


def test_second_call_is_idempotent(client, repo):
    add_approval(repo, 1)
    first = client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"}).json()
    again = client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"})
    assert again.status_code == 200
    body = again.json()
    assert body["already_resolved"] is True and body["status"] == "approved"
    assert body["resolved_at"] == first["resolved_at"]


def test_answering_the_other_way_after_resolution_is_a_conflict(client, repo):
    add_approval(repo, 1)
    client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"})
    r = client.post("/v1/approvals/apr_1:reject", json={"by": "vincenzo"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert client.get("/v1/approvals/apr_1").json()["status"] == "approved"


def test_wrong_person_gets_403_and_nothing_changes(client, repo):
    add_approval(repo, 1)
    r = client.post("/v1/approvals/apr_1:approve", json={"by": "grazyna"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert client.get("/v1/approvals/apr_1").json()["status"] == "pending"


def test_unknown_approval_is_404(client):
    r = client.post("/v1/approvals/apr_nope:approve", json={"by": "vincenzo"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_body_is_validated(client, repo):
    add_approval(repo, 1)
    assert client.post("/v1/approvals/apr_1:approve").status_code == 422
    assert client.post("/v1/approvals/apr_1:approve", json={"by": ""}).status_code == 422
    assert client.post("/v1/approvals/apr_1:approve", json={}).status_code == 422


def test_without_an_agent_answering_is_503(db_url, repo):
    set_agent(None)
    app = create_app()

    def repo_override():
        with Repository.connect(db_url) as r:
            yield r

    app.dependency_overrides[get_repo] = repo_override
    add_approval(repo, 1)
    r = TestClient(app).post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "agent_unavailable"


# --- SSE --------------------------------------------------------------------------------------------------


def parse_sse(text: str) -> list[dict]:
    """Messages (`id`, `event`, `data` parsed) of an event-stream body; comments are returned as `{"comment": ...}`."""
    out = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        msg: dict = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                msg["comment"] = line[1:].strip()
            elif line.startswith("data: "):
                msg["data"] = json.loads(line[6:])
            elif ": " in line:
                key, value = line.split(": ", 1)
                msg[key] = value
        out.append(msg)
    return out


class Polls:
    """Fake `wait`: runs one scripted action per call instead of sleeping, and records the requested intervals."""

    def __init__(self, *actions):
        self.actions = list(actions)
        self.intervals: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.intervals.append(seconds)
        if self.actions:
            self.actions.pop(0)()


def sse_client(db_url: str, polls: Polls, max_polls: int, **config) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_stream_config] = lambda: StreamConfig(
        repo_factory=lambda: Repository.connect(db_url), wait=polls, max_polls=max_polls, **config)
    return TestClient(app)


def read_stream(client: TestClient, **kwargs) -> list[dict]:
    with client.stream("GET", "/v1/events", **kwargs) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        return parse_sse("".join(response.iter_text()))


def add_event(repo: Repository, n: int) -> None:
    repo.add_event(Event(id=f"evt_{n}", ts=datetime(2025, 12, 30, 9, n, tzinfo=UTC), kind=EventKind.BANK_TRANSACTION,
                         payload={"n": n}))


def test_stream_sends_only_news_in_order_and_never_sleeps(db_url, repo):
    add_approval(repo, 1)  # exists before the client connects: not replayed
    add_event(repo, 0)
    polls = Polls(lambda: (add_approval(repo, 2), add_event(repo, 1)),
                  lambda: repo.add_decision(Decision(id="dec_x", ts=T0 + timedelta(hours=1), action_type="attach_invoice",
                                                     summary="Attached AS-69664", level=PolicyLevel.AUTO, reason="routine")))
    messages = read_stream(sse_client(db_url, polls, max_polls=3))
    assert messages[0] == {"retry": "3000", "comment": "connected"}
    news = messages[1:]
    kinds = [(m["event"], m["data"]["id"]) for m in news]
    # one poll: decisions, then approvals, then events; second poll: the later decision
    assert kinds == [("decision", "dec_2"), ("approval", "apr_2"), ("event", "evt_1"), ("decision", "dec_x")]
    assert news[1]["data"]["decision"]["id"] == "dec_2" and news[1]["data"]["status"] == "pending"
    assert news[3]["data"]["level"] == "AUTO"
    assert polls.intervals == [1.0, 1.0]  # waited between the 3 polls through the injected function


def test_stream_resumes_from_last_event_id(db_url, repo):
    polls = Polls(lambda: (add_event(repo, 1), add_event(repo, 2)))
    first = read_stream(sse_client(db_url, polls, max_polls=2))
    ids = [m["id"] for m in first if "id" in m]
    assert [m["data"]["id"] for m in first if "data" in m] == ["evt_1", "evt_2"]

    add_event(repo, 3)  # written while the client was away
    resumed = read_stream(sse_client(db_url, Polls(), max_polls=1), headers={"Last-Event-ID": ids[0]})
    assert [m["data"]["id"] for m in resumed if "data" in m] == ["evt_2", "evt_3"]
    via_query = read_stream(sse_client(db_url, Polls(), max_polls=1), params={"last_event_id": ids[1]})
    assert [m["data"]["id"] for m in via_query if "data" in m] == ["evt_3"]


def test_resume_cursor_covers_all_tables_and_survives_different_clocks(db_url, repo):
    # events carry simulated time (2025), decisions the wall clock (2026): one cursor per table
    polls = Polls(lambda: (add_event(repo, 1), add_approval(repo, 1)))
    first = read_stream(sse_client(db_url, polls, max_polls=2))
    last_id = [m["id"] for m in first if "id" in m][-1]
    add_event(repo, 2)
    add_approval(repo, 2)
    resumed = read_stream(sse_client(db_url, Polls(), max_polls=1), headers={"Last-Event-ID": last_id})
    assert [(m["event"], m["data"]["id"]) for m in resumed if "data" in m] == [
        ("decision", "dec_2"), ("approval", "apr_2"), ("event", "evt_2")]


def test_answered_approval_is_streamed_again_with_the_resolved_decision(db_url, repo, agent):
    add_approval(repo, 1)
    polls = Polls(lambda: agent.resume_approval("apr_1", True, "vincenzo"))
    messages = [m for m in read_stream(sse_client(db_url, polls, max_polls=2)) if "data" in m]
    assert [m["event"] for m in messages] == ["approval"]
    assert messages[0]["data"]["status"] == "approved"
    assert messages[0]["data"]["decision"]["status"] == "executed"


def test_keepalive_comment_when_idle(db_url):
    messages = read_stream(sse_client(db_url, Polls(), max_polls=5, keepalive_polls=2))
    assert [m.get("comment") for m in messages] == ["connected", "keepalive", "keepalive"]


def test_invalid_last_event_id_starts_from_now(db_url, repo):
    add_event(repo, 1)
    messages = read_stream(sse_client(db_url, Polls(), max_polls=1), headers={"Last-Event-ID": "garbage"})
    assert [m for m in messages if "data" in m] == []


def test_cursor_roundtrip():
    cursor = {"decision": (T0, "dec_1"), "event": (T0 - timedelta(days=300), "evt_9")}
    assert stream.decode_cursor(stream.encode_cursor(cursor)) == cursor
    for bad in (None, "", "x", "decision=nodate|dec", "other=2026-01-01T00:00:00+00:00|a", "decision=2026-01-01T00:00:00+00:00|"):
        assert stream.decode_cursor(bad) is None


# --- simulator control --------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class SimOnSchema:
    """`SimControl` backed by a real `Simulator` in the isolated schema with a fake clock (what control.py does)."""

    def __init__(self, db_url: str, events: list[Event], clock: FakeClock, got: list[Event]):
        self.db_url, self.events, self.clock, self.got = db_url, events, clock, got

    def _run(self, fn):
        repo = Repository.connect(self.db_url)
        try:
            return fn(Simulator(repo, self.events, speed=60.0, clock=self.clock, subscribers=[self.got.append]))
        finally:
            repo.conn.close()

    def start(self, speed=None):
        return self._run(lambda s: s.start(speed))

    def pause(self):
        return self._run(lambda s: s.pause())

    def step(self):
        return self._run(lambda s: s.step())

    def status(self):
        return self._run(lambda s: s.status())


@pytest.fixture
def sim_client(db_url):
    events = [Event(id=f"evt_s{n}", ts=datetime(2025, 12, n, 8, tzinfo=UTC), kind=EventKind.BANK_TRANSACTION,
                    payload={"transaction": {"id": f"txn_s{n}", "booking_date": f"2025-12-0{n}", "type": "SUPPLIER PAYMENT",
                                             "reference": f"Invoice AS-{n}", "counterparty": "Acme", "amount": "-10.00"}})
              for n in (1, 2)]
    got: list[Event] = []
    control = SimOnSchema(db_url, events, FakeClock(), got)
    app = create_app()
    app.dependency_overrides[get_sim_control] = lambda: control
    client = TestClient(app)
    client.got = got  # type: ignore[attr-defined]
    return client


def test_sim_status_start_pause_step(sim_client):
    st = sim_client.get("/v1/sim").json()
    assert st["paused"] is True and st["emitted"] == 0 and st["total"] == 2 and st["finished"] is False
    assert st["next_event_ts"].startswith("2025-12-01T08:00:00")

    started = sim_client.post("/v1/sim:start", json={"speed": 120}).json()
    assert started["paused"] is False and started["speed"] == 120
    assert sim_client.post("/v1/sim:start").json()["speed"] == 120  # body is optional, speed is kept
    assert sim_client.post("/v1/sim:pause").json()["paused"] is True

    step = sim_client.post("/v1/sim:step").json()
    assert step["event"]["id"] == "evt_s1" and step["event"]["kind"] == "bank_transaction"
    assert step["status"]["emitted"] == 1
    assert [e.id for e in sim_client.got] == ["evt_s1"]
    step = sim_client.post("/v1/sim:step").json()
    assert step["event"]["id"] == "evt_s2" and step["status"]["finished"] is True
    assert sim_client.post("/v1/sim:step").json()["event"] is None


def test_sim_validation(sim_client):
    assert sim_client.post("/v1/sim:start", json={"speed": 0}).status_code == 422
    assert sim_client.post("/v1/sim:start", json={"speed": "fast"}).status_code == 422
    assert sim_client.get("/v1/sim:pause").status_code == 405


def test_default_sim_control_uses_the_control_module(monkeypatch):
    from cfo.sim import control as module

    calls: list[str] = []
    monkeypatch.setattr(module, "pause", lambda: calls.append("pause") or module.SimStatus(
        sim_time=T0, paused=True, speed=1.0, emitted=0, total=0, next_event_ts=None, finished=False))
    r = TestClient(create_app()).post("/v1/sim:pause")
    assert r.status_code == 200 and calls == ["pause"]


def test_openapi_documents_the_new_routes():
    paths = create_app().openapi()["paths"]
    for path in ("/v1/approvals", "/v1/approvals/{approval_id}", "/v1/approvals/{approval_id}:approve",
                 "/v1/approvals/{approval_id}:reject", "/v1/events", "/v1/sim", "/v1/sim:start", "/v1/sim:pause",
                 "/v1/sim:step"):
        assert path in paths, path
