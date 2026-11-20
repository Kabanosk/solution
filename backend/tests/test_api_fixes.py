"""T046: API hardening (write token, async SSE, HTTP edge cases).

Runs in an isolated Postgres schema `t046_test_<pid>` (dropped afterwards), with a fake agent and a fake poll wait:
no real-time sleeping. Skips when Postgres is not reachable.
"""

from __future__ import annotations

import inspect
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from cfo.api import stream
from cfo.api.app import create_app
from cfo.api.deps import StreamConfig, get_agent, get_repo, get_sim_control, get_stream_config
from cfo.api.settings import get_api_settings
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
    Invoice,
    PolicyLevel,
    Transaction,
)

SCHEMA = f"t046_test_{os.getpid()}"
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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


@pytest.fixture
def write_token(monkeypatch):
    monkeypatch.setenv("API_WRITE_TOKEN", TOKEN)
    get_api_settings.cache_clear()
    yield TOKEN
    monkeypatch.delenv("API_WRITE_TOKEN")
    get_api_settings.cache_clear()


def add_approval(repo: Repository, n: int, *, approver: str = "vincenzo") -> str:
    ts = T0 + timedelta(minutes=n)
    dec = Decision(id=f"dec_{n}", ts=ts, action_type="request_credit_line", summary=f"Credit line {n}",
                   level=PolicyLevel.APPROVAL, reason="Credit lines always need CEO approval.",
                   status=DecisionStatus.PENDING, approval_request_id=f"apr_{n}", payload={})
    repo.add_decision(dec)
    repo.add_approval_request(ApprovalRequest(id=f"apr_{n}", decision_id=dec.id, approver_id=approver,
                                              question="Apply?", created_at=ts))
    return f"apr_{n}"


def add_event(repo: Repository, n: int) -> None:
    repo.add_event(Event(id=f"evt_{n}", ts=datetime(2025, 12, 30, 9, n, tzinfo=UTC), kind=EventKind.BANK_TRANSACTION,
                         payload={"n": n}))


class FakeAgent:
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
            dec = Decision(**repo._all("SELECT * FROM decisions WHERE id = %s", (req.decision_id,))[0])
            dec.actor = by
            dec.status = DecisionStatus.EXECUTED if approved else DecisionStatus.REJECTED
            repo.add_decision(dec)
            repo.resolve_approval(approval_id, ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED)
        return HandleResult(status="done", approval_id=approval_id)


class FakeSim:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _status(self):
        from cfo.sim.engine import SimStatus

        return SimStatus(sim_time=T0, paused=True, speed=60.0, emitted=0, total=0, next_event_ts=None, finished=False)

    def start(self, speed=None):
        self.calls.append("start")
        return self._status()

    def pause(self):
        self.calls.append("pause")
        return self._status()

    def step(self):
        self.calls.append("step")
        return None

    def status(self):
        return self._status()


def make_client(db_url: str, **overrides) -> TestClient:
    app = create_app()

    def repo_override():
        with Repository.connect(db_url) as r:
            yield r

    app.dependency_overrides[get_repo] = repo_override
    def provide(value):
        return lambda: value  # a closure: a lambda default would be copied by FastAPI

    for dep, value in overrides.items():
        app.dependency_overrides[{"agent": get_agent, "sim": get_sim_control}[dep]] = provide(value)
    return TestClient(app)


# --- (22) write token --------------------------------------------------------------------------------------


def test_without_a_token_configured_writes_stay_open(db_url, repo):
    add_approval(repo, 1)
    agent, sim = FakeAgent(db_url), FakeSim()
    client = make_client(db_url, agent=agent, sim=sim)
    assert client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"}).status_code == 200
    assert client.post("/v1/sim:pause").status_code == 200


def test_write_endpoints_need_the_bearer_token(db_url, repo, write_token):
    add_approval(repo, 1)
    agent, sim = FakeAgent(db_url), FakeSim()
    client = make_client(db_url, agent=agent, sim=sim)
    writes = [("/v1/approvals/apr_1:approve", {"by": "vincenzo"}), ("/v1/approvals/apr_1:reject", {"by": "vincenzo"}),
              ("/v1/sim:start", None), ("/v1/sim:pause", None), ("/v1/sim:step", None)]
    for path, body in writes:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}, {"Authorization": TOKEN}):
            r = client.post(path, json=body, headers=headers)
            assert r.status_code == 401, (path, headers)
            assert r.json()["error"]["code"] == "unauthorized"
            assert r.headers["www-authenticate"] == "Bearer"
    assert agent.calls == [] and sim.calls == []
    assert client.get("/v1/approvals/apr_1").json()["status"] == "pending"


def test_token_is_checked_before_the_body(db_url, write_token):
    client = make_client(db_url, agent=FakeAgent(db_url), sim=FakeSim())
    assert client.post("/v1/approvals/apr_1:approve", json={}).status_code == 401
    assert client.post("/v1/sim:start", json={"speed": "fast"}).status_code == 401
    assert client.post("/v1/sim:start", json={"speed": "fast"}, headers=AUTH).status_code == 422


def test_valid_token_lets_writes_through(db_url, repo, write_token):
    add_approval(repo, 1)
    sim = FakeSim()
    client = make_client(db_url, agent=FakeAgent(db_url), sim=sim)
    assert client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"}, headers=AUTH).status_code == 200
    for step in ("start", "pause", "step"):
        assert client.post(f"/v1/sim:{step}", headers=AUTH).status_code == 200
    assert sim.calls == ["start", "pause", "step"]


def test_reads_stay_open_with_a_token(db_url, repo, write_token):
    add_approval(repo, 1)
    client = make_client(db_url, agent=FakeAgent(db_url), sim=FakeSim())
    for path in ("/healthz", "/v1/approvals", "/v1/approvals/apr_1", "/v1/decisions", "/v1/rules", "/v1/sim"):
        assert client.get(path).status_code == 200, path


def test_token_maps_to_the_ceo_not_to_the_body(db_url, repo, write_token):
    add_approval(repo, 1)
    agent = FakeAgent(db_url)
    client = make_client(db_url, agent=agent)
    r = client.post("/v1/approvals/apr_1:approve", json={"by": "someone-else"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["decision"]["actor"] == "vincenzo"
    assert agent.calls == [("apr_1", True, "vincenzo")]


def test_token_holder_cannot_answer_for_another_approver(db_url, repo, write_token):
    add_approval(repo, 1, approver="grazyna")
    client = make_client(db_url, agent=FakeAgent(db_url))
    r = client.post("/v1/approvals/apr_1:approve", json={"by": "grazyna"}, headers=AUTH)
    assert r.status_code == 403
    assert client.get("/v1/approvals/apr_1").json()["status"] == "pending"


def test_without_a_token_the_body_still_names_the_approver(db_url, repo):
    add_approval(repo, 1, approver="grazyna")
    agent = FakeAgent(db_url)
    client = make_client(db_url, agent=agent)
    assert client.post("/v1/approvals/apr_1:approve", json={"by": "grazyna"}).status_code == 200
    assert agent.calls == [("apr_1", True, "grazyna")]


def test_openapi_declares_the_bearer_scheme_on_writes():
    spec = create_app().openapi()
    assert spec["components"]["securitySchemes"]["WriteToken"]["scheme"] == "bearer"
    for path in ("/v1/approvals/{approval_id}:approve", "/v1/approvals/{approval_id}:reject", "/v1/sim:start",
                 "/v1/sim:pause", "/v1/sim:step"):
        assert {"WriteToken": []} in spec["paths"][path]["post"]["security"], path
    assert "security" not in spec["paths"]["/v1/sim"]["get"]


# --- (23) SSE ----------------------------------------------------------------------------------------------


def cfg(db_url: str, **kw) -> StreamConfig:
    return StreamConfig(repo_factory=lambda: Repository.connect(db_url), wait=lambda s: None, **kw)


def test_stream_is_an_async_generator(db_url):
    assert inspect.isasyncgen(stream.stream(cfg(db_url, max_polls=1), None))


def test_stream_awaits_an_async_wait(db_url, repo):
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            add_event(repo, 1)

    app = create_app()
    app.dependency_overrides[get_stream_config] = lambda: StreamConfig(
        repo_factory=lambda: Repository.connect(db_url), wait=wait, max_polls=2)
    with TestClient(app).stream("GET", "/v1/events") as response:
        text = "".join(response.iter_text())
    assert waits == [1.0] and "evt_1" in text


def first_chunk_and_rest(db_url: str, last_event_id: str | None = None, polls: int = 1) -> str:
    app = create_app()
    app.dependency_overrides[get_stream_config] = lambda: cfg(db_url, max_polls=polls)
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    with TestClient(app).stream("GET", "/v1/events", headers=headers) as response:
        return "".join(response.iter_text())


def test_first_frame_carries_the_resume_id_even_on_an_empty_database(db_url, repo):
    text = first_chunk_and_rest(db_url)
    first = text.split("\n\n")[0]
    ids = [line for line in first.split("\n") if line.startswith("id:")]
    assert len(ids) == 1 and ids[0] != "id:" and ids[0] != "id: "
    add_event(repo, 1)  # committed while the client was away, before any data frame arrived
    resumed = first_chunk_and_rest(db_url, ids[0][3:].strip())
    assert "evt_1" in resumed


def test_first_frame_id_resumes_at_the_connect_position(db_url, repo):
    add_event(repo, 1)
    first = first_chunk_and_rest(db_url).split("\n\n")[0]
    token = next(line for line in first.split("\n") if line.startswith("id:"))[3:].strip()
    assert stream.decode_cursor(token) is not None
    add_event(repo, 2)
    resumed = first_chunk_and_rest(db_url, token)
    assert '"id": "evt_2"' in resumed and '"id": "evt_1"' not in resumed


def test_empty_cursor_roundtrip():
    assert stream.decode_cursor(stream.encode_cursor({})) == {}


# --- (P3) HTTP edge cases ---------------------------------------------------------------------------------


def test_500_carries_cors_headers(db_url):
    app = create_app()

    def broken():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    app.dependency_overrides[get_repo] = broken
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/v1/rules", headers={"Origin": "https://dashboard.example"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert r.headers["access-control-allow-origin"] == "*"
    assert "x-stub" in r.headers.get("access-control-expose-headers", "").lower()
    plain = client.get("/v1/rules")  # no Origin: no CORS headers needed
    assert plain.status_code == 500 and "access-control-allow-origin" not in plain.headers


def test_500_cors_follows_the_configured_origins(db_url, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://ok.example")
    get_api_settings.cache_clear()
    try:
        app = create_app()

        def broken():
            raise RuntimeError("boom")
            yield  # pragma: no cover

        app.dependency_overrides[get_repo] = broken
        client = TestClient(app, raise_server_exceptions=False)
        ok = client.get("/v1/rules", headers={"Origin": "https://ok.example"})
        assert ok.headers["access-control-allow-origin"] == "https://ok.example"
        other = client.get("/v1/rules", headers={"Origin": "https://evil.example"})
        assert other.status_code == 500 and "access-control-allow-origin" not in other.headers
    finally:
        monkeypatch.delenv("CORS_ORIGINS")
        get_api_settings.cache_clear()


def test_405_keeps_the_allow_header(db_url):
    r = make_client(db_url).get("/v1/sim:pause")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    assert "POST" in r.headers["allow"]


def add_january_booking(repo: Repository) -> None:
    repo.add_transaction(Transaction(id="t1", booking_date=date(2025, 1, 10), type="CARD", reference="x",
                                     counterparty="Supplier", amount=Decimal("-50.00"), category="food"))


def add_unlinked_invoice(repo: Repository, issued: date, total: str = "100.00") -> None:
    repo.add_invoice(Invoice(number="FLV-1", supplier="Flavio", issue_date=issued, total=Decimal(total),
                             category="food", team_id="kitchen"))


def test_half_open_and_inverted_date_ranges_are_422(db_url, repo):
    # a statement with bookings only in January 2025
    add_january_booking(repo)
    client = make_client(db_url)
    assert client.get("/v1/costs/by-team").status_code == 200
    for params in ({"from": "2026-06-01"}, {"to": "2024-12-31"}, {"from": "2025-03-01", "to": "2025-01-01"}):
        r = client.get("/v1/costs/by-team", params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "validation_error"


def test_far_future_range_is_422_not_500(db_url, repo):
    add_january_booking(repo)
    client = make_client(db_url)
    for params in ({"to": "9999-12-31"}, {"from": "0001-01-01", "to": "2025-01-31"}):
        r = client.get("/v1/costs/by-team", params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "validation_error"


def test_by_team_includes_unlinked_invoices_without_any_bank_transactions(db_url, repo):
    add_unlinked_invoice(repo, date(2025, 12, 5), "100.00")
    body = make_client(db_url).get("/v1/costs/by-team").json()
    assert body["total"]["amount"] == "100.00"
    assert [(i["team_id"], i["total"]["amount"]) for i in body["items"] if i["total"]["amount"] != "0.00"] == [
        ("kitchen", "100.00")]
    assert body["from"] == "2025-12-01" and body["to"] == "2025-12-05"


def test_cors_preflight_allows_the_authorization_header(db_url):
    r = make_client(db_url).options("/v1/sim:pause", headers={
        "Origin": "https://dashboard.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


# --- final review round --------------------------------------------------------------------------------------


def test_missing_token_is_401_even_when_agent_and_database_are_unavailable(write_token):
    from cfo.api.deps import set_agent

    set_agent(None)
    app = create_app()

    def no_database():
        raise psycopg.OperationalError("down")
        yield  # pragma: no cover

    app.dependency_overrides[get_repo] = no_database
    client = TestClient(app, raise_server_exceptions=False)
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        r = client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"}, headers=headers)
        assert r.status_code == 401, headers
    # with the right token the dependencies are reached (503: no agent / database)
    assert client.post("/v1/approvals/apr_1:approve", json={"by": "vincenzo"}, headers=AUTH).status_code in (503,)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_ceo_id_falls_back_to_the_default_ceo(db_url, repo, write_token, monkeypatch, blank):
    monkeypatch.setenv("CEO_ID", blank)
    get_api_settings.cache_clear()
    try:
        add_approval(repo, 1, approver="grazyna")
        agent = FakeAgent(db_url)
        client = make_client(db_url, agent=agent)
        r = client.post("/v1/approvals/apr_1:approve", json={"by": "grazyna"}, headers=AUTH)
        assert r.status_code == 403  # answered as the CEO (vincenzo), never as the caller-supplied `by`
        assert agent.calls == [("apr_1", True, "vincenzo")]
    finally:
        monkeypatch.delenv("CEO_ID")
        get_api_settings.cache_clear()


def test_token_with_a_trailing_newline_still_protects(db_url, monkeypatch):
    monkeypatch.setenv("API_WRITE_TOKEN", TOKEN + "\n")  # `oc create secret --from-file` keeps the newline
    get_api_settings.cache_clear()
    try:
        client = make_client(db_url, sim=FakeSim())
        assert client.post("/v1/sim:pause").status_code == 401
        assert client.post("/v1/sim:pause", headers=AUTH).status_code == 200
    finally:
        monkeypatch.delenv("API_WRITE_TOKEN")
        get_api_settings.cache_clear()


def test_range_ending_in_year_9999_is_not_a_500(db_url, repo):
    add_january_booking(repo)
    r = make_client(db_url).get("/v1/costs/by-team", params={"from": "9999-12-01", "to": "9999-12-31"})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["total"]["amount"] == "0.00"
