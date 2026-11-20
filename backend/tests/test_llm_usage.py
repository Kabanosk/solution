"""T032: LLM usage recording, vLLM metrics parsing, cost model, and the `/v1/llm/usage*` endpoints.

DB-free: parser, counter arithmetic, cost math, recording through `FakeLLM`/`OpenAICompatLLM`, sampler loop.
With Postgres: report and API against an isolated schema (`t032_test`, dropped afterwards; the shared public
schema is never touched). No test sleeps: clocks and the sampler's wait are injected.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from cfo import llm as cfo_llm
from cfo.api.app import create_app
from cfo.api.deps import get_repo
from cfo.api.routers.llm_usage import get_clock, get_usage_config
from cfo.config import get_settings
from cfo.llm import FakeLLM, LLMResponse, Message, OpenAICompatLLM, TokenUsage
from cfo.usage import cost, recorder, report, sampler, store
from cfo.usage.recorder import DbSink, UsageRecord, usage_context
from cfo.usage.settings import UsageSettings
from cfo.usage.vllm_metrics import (
    bucket_increase,
    counter_increase,
    counter_increase_series,
    histogram_quantile,
    parse_prometheus_text,
    snapshots_from_text,
)
from fastapi.testclient import TestClient

UTC = timezone.utc


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


# --- fixture /metrics text -----------------------------------------------------------------------------------

MODEL = "modelcar-granite-4-0-h-tiny"

METRICS_NEW = f'''# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{{engine="0",model_name="{MODEL}"}} 2.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{{engine="0",model_name="{MODEL}"}} 1.0
# HELP vllm:kv_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{{engine="0",model_name="{MODEL}"}} 0.37
# HELP vllm:prompt_tokens_total Number of prefill tokens processed.
# TYPE vllm:prompt_tokens_total counter
vllm:prompt_tokens_total{{engine="0",model_name="{MODEL}"}} 14000.0
# TYPE vllm:prompt_tokens_created gauge
vllm:prompt_tokens_created{{engine="0",model_name="{MODEL}"}} 1.7635e+09
# HELP vllm:generation_tokens_total Number of generation tokens processed.
# TYPE vllm:generation_tokens_total counter
vllm:generation_tokens_total{{engine="0",model_name="{MODEL}"}} 2600.0
# HELP vllm:request_success_total Count of successfully processed requests.
# TYPE vllm:request_success_total counter
vllm:request_success_total{{engine="0",finished_reason="stop",model_name="{MODEL}"}} 18.0
vllm:request_success_total{{engine="0",finished_reason="length",model_name="{MODEL}"}} 2.0
# HELP vllm:e2e_request_latency_seconds Histogram of e2e request latency in seconds.
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_bucket{{engine="0",le="0.3",model_name="{MODEL}"}} 0.0
vllm:e2e_request_latency_seconds_bucket{{engine="0",le="1.0",model_name="{MODEL}"}} 5.0
vllm:e2e_request_latency_seconds_bucket{{engine="0",le="5.0",model_name="{MODEL}"}} 18.0
vllm:e2e_request_latency_seconds_bucket{{engine="0",le="+Inf",model_name="{MODEL}"}} 20.0
vllm:e2e_request_latency_seconds_sum{{engine="0",model_name="{MODEL}"}} 52.5
vllm:e2e_request_latency_seconds_count{{engine="0",model_name="{MODEL}"}} 20.0
python_gc_objects_collected_total{{generation="0"}} 1234.0
process_cpu_seconds_total 12.5
'''

# vLLM 0.8 / 0.9: the KV gauge is called gpu_cache_usage_perc
METRICS_OLD = METRICS_NEW.replace("kv_cache_usage_perc", "gpu_cache_usage_perc").replace("0.37", "0.52")
# a release that exposes both during the transition: the new name wins
METRICS_BOTH = METRICS_NEW + f'vllm:gpu_cache_usage_perc{{engine="0",model_name="{MODEL}"}} 0.99\n'


GRANITE_FIXTURE = Path(__file__).parent / "fixtures" / "vllm_metrics_granite.txt"


class TestRealGraniteMetrics:
    """`/metrics` captured from the running Granite 4.0 H Tiny pod (vLLM v1 engine, 643 lines)."""

    def test_real_output(self):
        snaps = snapshots_from_text(GRANITE_FIXTURE.read_text(encoding="utf-8"))
        assert list(snaps) == [MODEL]
        s = snaps[MODEL]
        assert (s.prompt_tokens_total, s.generation_tokens_total) == (530.0, 271.0)  # `_created` series are not counted
        assert s.requests_success_total == 14.0
        assert s.requests_by_reason["stop"] == 14.0 and {"length", "abort", "error"} <= set(s.requests_by_reason)
        assert s.kv_cache_metric == "vllm:kv_cache_usage_perc" and s.kv_cache_usage == 0.0
        assert (s.requests_running, s.requests_waiting) == (0.0, 0.0)
        assert s.prompt_tokens_cached_total == 0.0
        assert s.inference_time_count == 14.0 and s.inference_time_sum == pytest.approx(5.196064621988626)
        assert s.e2e_latency_count == 14.0 and s.e2e_latency_sum == pytest.approx(5.261658668518066)
        assert s.e2e_latency_buckets[float("inf")] == 14.0 and len(s.e2e_latency_buckets) > 5
        assert histogram_quantile(0.95, s.e2e_latency_buckets) is not None


class TestParser:
    def test_new_kv_name_and_all_fields(self):
        snap = snapshots_from_text(METRICS_NEW)[MODEL]
        assert snap.prompt_tokens_total == 14000.0
        assert snap.generation_tokens_total == 2600.0
        assert snap.requests_success_total == 20.0  # finished_reason series summed
        assert snap.requests_running == 2.0 and snap.requests_waiting == 1.0
        assert snap.kv_cache_usage == pytest.approx(0.37)
        assert snap.kv_cache_metric == "vllm:kv_cache_usage_perc"
        assert snap.e2e_latency_sum == 52.5 and snap.e2e_latency_count == 20.0
        assert snap.e2e_latency_buckets == {0.3: 0.0, 1.0: 5.0, 5.0: 18.0, float("inf"): 20.0}

    def test_old_kv_name(self):
        snap = snapshots_from_text(METRICS_OLD)[MODEL]
        assert snap.kv_cache_usage == pytest.approx(0.52)
        assert snap.kv_cache_metric == "vllm:gpu_cache_usage_perc"

    def test_both_names_new_wins(self):
        snap = snapshots_from_text(METRICS_BOTH)[MODEL]
        assert snap.kv_cache_usage == pytest.approx(0.37)
        assert snap.kv_cache_metric == "vllm:kv_cache_usage_perc"

    def test_one_snapshot_per_model_and_series_summed(self):
        text = METRICS_NEW + (
            'vllm:prompt_tokens_total{engine="1",model_name="' + MODEL + '"} 1000.0\n'
            'vllm:prompt_tokens_total{engine="0",model_name="other \\"quoted\\" model"} 7.0\n'
        )
        snaps = snapshots_from_text(text)
        assert set(snaps) == {MODEL, 'other "quoted" model'}
        assert snaps[MODEL].prompt_tokens_total == 15000.0
        assert snaps['other "quoted" model'].prompt_tokens_total == 7.0
        assert snaps['other "quoted" model'].kv_cache_usage is None  # missing metric stays None

    def test_garbage_and_foreign_metrics_are_ignored(self):
        assert snapshots_from_text("") == {}
        assert snapshots_from_text("not metrics at all\n# only comments\n") == {}
        assert snapshots_from_text("process_cpu_seconds_total 3\n") == {}
        samples = parse_prometheus_text('a_metric{x="1"} NaN\nb_metric 4 1763000000000\nbroken line {\n')
        assert [(s.name, s.value) for s in samples if s.name == "b_metric"] == [("b_metric", 4.0)]


class TestCounters:
    def test_increase_and_reset(self):
        assert counter_increase(10, 25) == 15
        assert counter_increase(25, 4) == 4  # restart: the new value is the increase since it
        assert counter_increase(None, 5) == 0 and counter_increase(5, None) == 0

    def test_series_with_a_reset_between_samples(self):
        # 100 -> 160 (+60), restart, 20 (+20), 50 (+30)
        assert counter_increase_series([100, 160, 20, 50]) == 110
        assert counter_increase_series([None, 5, 8]) == 3

    def test_bucket_increase_and_reset(self):
        prev = {1.0: 5.0, 5.0: 18.0, float("inf"): 20.0}
        cur = {1.0: 7.0, 5.0: 25.0, float("inf"): 30.0}
        assert bucket_increase(prev, cur) == {1.0: 2.0, 5.0: 7.0, float("inf"): 10.0}
        restarted = {1.0: 1.0, 5.0: 2.0, float("inf"): 3.0}
        assert bucket_increase(prev, restarted) == restarted

    def test_histogram_quantile(self):
        buckets = {1.0: 5.0, 5.0: 18.0, float("inf"): 20.0}
        assert histogram_quantile(0.5, buckets) == pytest.approx(1.0 + 4.0 * (10 - 5) / 13)
        assert histogram_quantile(0.95, buckets) == pytest.approx(5.0)  # rank 19 falls in +Inf: last finite bound
        assert histogram_quantile(0.5, {}) is None


class TestCostMath:
    def test_busy_time(self):
        # 1800 s of busy GPU at 0.70 EUR/h = 0.35 EUR
        assert cost.busy_cost(1800, Decimal("0.70")) == Decimal("0.35")
        assert cost.hourly_rate(Decimal("0.70"), Decimal("0.30")) == Decimal("1.00")

    def test_amortized_with_share(self):
        share, source = cost.token_share(250, 1000.0)
        assert (share, source) == (Decimal("0.25"), "vllm_metrics")
        # 10 wall-clock hours at 0.70 EUR/h, 25 % of the tokens are ours
        assert cost.amortized_cost(10, Decimal("0.70"), share) == Decimal("1.75")

    def test_share_without_cluster_data_and_cap(self):
        assert cost.token_share(100, None) == (Decimal(1), "assumed_all_traffic")
        assert cost.token_share(100, 0.0) == (Decimal(1), "assumed_all_traffic")
        assert cost.token_share(300, 100.0)[0] == Decimal(1)  # a missed scrape cannot make us bigger than the total

    def test_hosted(self):
        price = UsageSettings().hosted_prices[0]  # gpt-5-mini 0.25 / 2.00 USD per 1M
        usd = cost.hosted_cost_eur(1_000_000, 500_000, price, Decimal(1))
        assert usd == Decimal("0.25") + Decimal("1.00")
        assert cost.hosted_cost_eur(1_000_000, 0, price, Decimal("0.5")) == Decimal("0.125")

    def test_defaults_are_documented_assumptions(self):
        s = UsageSettings()
        assert s.gpu_hourly_cost_eur == Decimal("0.70")
        assert "g6.xlarge" in s.gpu_price_source and "2026-11-20" in s.gpu_price_source
        assert all(p.source.startswith("https://") and p.as_of == "2026-11-20" for p in s.hosted_prices)


# --- recording through cfo.llm ----------------------------------------------------------------------------------


class MemorySink:
    def __init__(self):
        self.records: list[UsageRecord] = []
        self.links: list[tuple[str, str]] = []

    def write(self, record: UsageRecord) -> None:
        self.records.append(record)

    def link(self, event_id: str, decision_id: str) -> None:
        self.links.append((event_id, decision_id))


@pytest.fixture()
def sink():
    s = MemorySink()
    recorder.set_sink(s)
    yield s
    recorder.set_sink(None)


@pytest.fixture()
def fake_clock(monkeypatch):
    """Frozen wall clock and a perf counter that advances 0.25 s per reading."""
    ticks = iter(x * 0.25 for x in range(1000))
    monkeypatch.setattr(recorder, "now", lambda: at(20, 10))
    monkeypatch.setattr(recorder, "perf_counter", lambda: next(ticks))


class TestRecording:
    def test_fake_llm_records_with_estimated_tokens_and_context(self, sink, fake_clock):
        llm = FakeLLM(["Hello there, this is the answer."])
        with usage_context(event_id="evt_1", event_type="bank_transaction"):
            with usage_context(purpose="explain"):
                llm.chat([Message(role="system", content="x" * 40), Message(role="user", content="y" * 20)])
        (r,) = sink.records
        assert r.model == "fake"
        assert r.estimated is True and r.success is True
        assert r.prompt_tokens == 16  # 61 characters (two messages and a separator) / 4, rounded up
        assert r.completion_tokens == 8  # 32 characters / 4, rounded up
        assert r.latency_ms == pytest.approx(250.0)
        assert (r.event_id, r.event_type, r.purpose, r.decision_id) == ("evt_1", "bank_transaction", "explain", None)
        assert r.ts == at(20, 10)

    def test_server_usage_is_used_and_not_flagged(self, sink, fake_clock):
        llm = FakeLLM([LLMResponse(content="ok", usage=TokenUsage(prompt_tokens=123, completion_tokens=45))])
        llm.chat([Message(role="user", content="hi")])
        (r,) = sink.records
        assert (r.prompt_tokens, r.completion_tokens, r.estimated) == (123, 45, False)

    def test_context_does_not_leak_out_of_the_block(self, sink):
        llm = FakeLLM(handler=lambda m, t: "fine")
        with usage_context(purpose="a"):
            llm.chat([Message(role="user", content="1")])
        llm.chat([Message(role="user", content="2")])
        assert [r.purpose for r in sink.records] == ["a", None]

    def test_unknown_context_key_is_rejected(self):
        with pytest.raises(TypeError):
            with usage_context(colour="red"):
                pass

    def test_context_follows_langgraph_threads(self, sink):
        from langgraph.graph import END, START, StateGraph

        llm = FakeLLM(handler=lambda m, t: "node answer")

        def node(state: dict) -> dict:
            llm.chat([Message(role="user", content="q")])
            return {"done": True}

        g = StateGraph(dict)
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)
        with usage_context(event_id="evt_graph", event_type="metric"):
            g.compile().invoke({})
        assert [(r.event_id, r.event_type) for r in sink.records] == [("evt_graph", "metric")]

    def test_a_failing_sink_never_breaks_the_call(self):
        class Broken:
            def write(self, record):
                raise RuntimeError("disk on fire")

        recorder.set_sink(Broken())
        try:
            out = FakeLLM(["still works"]).chat([Message(role="user", content="hi")])
        finally:
            recorder.set_sink(None)
        assert out.content == "still works"

    def test_database_failure_does_not_break_the_call_and_trips_the_breaker(self):
        attempts = []
        now = [0.0]

        def connect():
            attempts.append(1)
            raise psycopg.OperationalError("connection refused")

        recorder.set_sink(DbSink(connect=connect, clock=lambda: now[0], cooldown=30.0))
        try:
            llm = FakeLLM(handler=lambda m, t: "ok")
            for _ in range(3):
                assert llm.chat([Message(role="user", content="hi")]).content == "ok"
            assert len(attempts) == 1  # the database is skipped during the cooldown
            now[0] = 31.0  # injected clock, no sleeping
            llm.chat([Message(role="user", content="hi")])
            assert len(attempts) == 2
        finally:
            recorder.set_sink(None)

    def test_openai_compat_records_usage_latency_and_failure(self, sink, fake_clock):
        class Completions:
            def __init__(self):
                self.fail = False

            def create(self, **kwargs):
                if self.fail:
                    raise ConnectionError("down")
                msg = SimpleNamespace(content="answer", tool_calls=None)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                                       usage=SimpleNamespace(prompt_tokens=321, completion_tokens=17))

        completions = Completions()
        llm = OpenAICompatLLM("http://localhost:1/v1", "granite")
        llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        out = llm.chat([Message(role="user", content="hi")])
        assert out.usage == TokenUsage(prompt_tokens=321, completion_tokens=17)
        completions.fail = True
        with pytest.raises(ConnectionError):
            llm.chat([Message(role="user", content="hi")])
        ok, failed = sink.records
        assert (ok.model, ok.prompt_tokens, ok.completion_tokens, ok.estimated, ok.success) == ("granite", 321, 17, False, True)
        assert ok.latency_ms == pytest.approx(250.0)
        assert failed.success is False and failed.completion_tokens == 0

    def test_link_decision_uses_the_last_decision(self, sink):
        recorder.link_decision("evt_9", ["dec_a", "dec_b"])
        recorder.link_decision("evt_9", [])
        assert sink.links == [("evt_9", "dec_b")]

    def test_self_signed_https_client_builds_without_the_network(self):
        assert cfo_llm.tls_verify_setting("false") is False
        OpenAICompatLLM("https://svc.example.svc:8000/v1", "m", verify_tls="false")


# --- sampler (no database) ---------------------------------------------------------------------------------------


class FakeConn:
    def __init__(self):
        self.rows: list[tuple] = []
        self.commits = 0
        self.closed = False

    def execute(self, sql, params=()):
        self.rows.append((sql, params))

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class TestSampler:
    def test_sample_once_stores_one_row_per_model(self):
        conn = FakeConn()
        snaps = sampler.sample_once(conn, lambda: METRICS_NEW, now=lambda: at(20, 12))
        assert [s.model for s in snaps] == [MODEL]
        assert len(conn.rows) == 1 and conn.rows[0][1][0] == at(20, 12) and conn.commits == 1

    def test_text_without_vllm_metrics_stores_nothing(self):
        conn = FakeConn()
        assert sampler.sample_once(conn, lambda: "up 1\n", now=lambda: at(20, 12)) == []
        assert conn.rows == []

    def test_run_survives_failures_and_waits_through_the_stop_event(self):
        waits: list[float] = []

        class RecordingStop(threading.Event):
            def wait(self, timeout=None):
                waits.append(timeout)  # nothing sleeps
                return False

        texts = iter([RuntimeError("pod restarting"), METRICS_NEW, METRICS_OLD])

        def fetch():
            t = next(texts)
            if isinstance(t, Exception):
                raise t
            return t

        conns: list[FakeConn] = []

        def connect():
            conns.append(FakeConn())
            return conns[-1]

        done = sampler.run(60.0, connect, fetch, stop=RecordingStop(), max_iterations=3, now=lambda: at(20, 12))
        assert done == 3 and waits == [60.0, 60.0]
        assert [len(c.rows) for c in conns] == [0, 1, 1] and all(c.closed for c in conns)

    def test_disabled_without_url(self):
        assert sampler.start_background(settings=UsageSettings(vllm_metrics_url=None)) is None

    def test_tls_verify_options(self, tmp_path):
        assert sampler._ssl_context("true") is None
        assert sampler._ssl_context("false").verify_mode.name == "CERT_NONE"
        with pytest.raises(FileNotFoundError):
            sampler._ssl_context(str(tmp_path / "nope.pem"))


# --- database: report and API -----------------------------------------------------------------------------------

SCHEMA = f"t032_test_{os.getpid()}"


@pytest.fixture(scope="module")
def repo():
    from cfo.db.migrate import SCHEMA_PATH
    from cfo.db.repo import Repository
    from psycopg.rows import dict_row

    url = get_settings().database_url
    try:
        conn = psycopg.connect(url, connect_timeout=2, row_factory=dict_row)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {SCHEMA}")
    conn.execute(f"SET search_path TO {SCHEMA}")
    conn.commit()
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    r = Repository(conn)
    try:
        yield r
    finally:
        conn.rollback()
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()
        conn.close()


def _snapshot(prompt, gen, ok, lat_sum, lat_count, buckets, *, running=0.0, waiting=0.0, kv=0.1):
    from cfo.usage.vllm_metrics import VllmSnapshot

    return VllmSnapshot(model=MODEL, prompt_tokens_total=prompt, generation_tokens_total=gen,
                        requests_success_total=ok, requests_by_reason={"stop": ok},
                        prompt_tokens_cached_total=prompt * 0.1,
                        inference_time_sum=lat_sum * 0.9, inference_time_count=lat_count,
                        e2e_latency_sum=lat_sum, e2e_latency_count=lat_count,
                        e2e_latency_buckets=buckets, requests_running=running, requests_waiting=waiting,
                        kv_cache_usage=kv, kv_cache_metric="vllm:kv_cache_usage_perc")


INF = float("inf")


@pytest.fixture(scope="module")
def seeded(repo):
    """Four calls over two days, two decisions, four samples with a pod restart between sample 2 and 3."""
    conn = repo.conn

    def usage(ts, event, etype, purpose, p, c, ms, decision, estimated=False):
        store.insert_usage(conn, UsageRecord(ts=ts, model=MODEL, prompt_tokens=p, completion_tokens=c, latency_ms=ms,
                                             estimated=estimated, event_id=event, event_type=etype, purpose=purpose,
                                             decision_id=decision))

    # the old agent's `decisions` table (Scout drops it); the report attributes cost per action only when it exists
    conn.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, ts TIMESTAMPTZ NOT NULL, action_type TEXT NOT NULL)")
    conn.execute("INSERT INTO decisions VALUES ('d1', %s, 'attach_invoice'), ('d2', %s, 'alert_cost_anomaly')",
                 (at(20, 10, 6), at(20, 11, 1)))
    usage(at(20, 10), "e1", "bank_transaction", "extract_invoice", 1000, 200, 4000, "d1")
    usage(at(20, 10, 5), "e1", "bank_transaction", "explain", 500, 100, 2000, "d1")
    usage(at(20, 11), "e2", "metric", "explain", 800, 150, 3000, "d2", estimated=True)
    usage(at(21, 9), "e3", "bank_transaction", "extract_invoice", 600, 50, 1000, None)
    for ts, snap in [
        (at(20, 9), _snapshot(10000, 2000, 20, 40.0, 20, {1.0: 5, 5.0: 18, INF: 20})),
        (at(20, 12), _snapshot(14000, 2600, 28, 60.0, 28, {1.0: 8, 5.0: 26, INF: 28})),
        (at(21, 8), _snapshot(500, 100, 2, 3.0, 2, {1.0: 1, 5.0: 2, INF: 2})),  # restart
        (at(21, 10), _snapshot(2000, 500, 6, 13.0, 6, {1.0: 2, 5.0: 6, INF: 6}, running=2.0, waiting=1.0, kv=0.37)),
    ]:
        store.insert_sample(conn, ts, snap)
    conn.commit()
    return repo


CFG = UsageSettings(vllm_metrics_url=None, gpu_hourly_cost_eur=Decimal("0.70"), node_hourly_cost_eur=Decimal("0"),
                    hosted_prices_json=None)
RANGE_FROM, RANGE_TO = at(20, 0), at(22, 0)


def amount(m: dict) -> Decimal:
    return Decimal(m["amount"])


class TestReport:
    def test_cluster_deltas_survive_the_restart(self, seeded):
        rows = store.samples_between(seeded.conn, RANGE_FROM, RANGE_TO)
        d = report.cluster_delta(rows, RANGE_FROM, RANGE_TO)
        assert d.samples == 3 and d.counter_resets == 1
        assert d.prompt_tokens == 4000 + 500 + 1500  # +4000, restart (new value 500), +1500
        assert d.generation_tokens == 600 + 100 + 400
        assert d.requests == 8 + 2 + 4

    def test_baseline_sample_before_the_range_is_used(self, seeded):
        start, end = at(20, 10), at(20, 13)
        d = report.cluster_delta(store.samples_between(seeded.conn, start, end), start, end)
        assert (d.prompt_tokens, d.generation_tokens, d.samples) == (4000, 600, 1)

    def test_totals_and_both_cost_methods(self, seeded):
        r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "day", at(22, 0), CFG)
        t = r["total"]
        assert (t["requests"], t["prompt_tokens"], t["completion_tokens"], t["estimated_requests"]) == (4, 2900, 500, 1)
        assert t["busy_seconds"] == pytest.approx(10.0)
        assert t["latency_p50_ms"] == pytest.approx(2500.0)
        assert t["latency_p95_ms"] == pytest.approx(3850.0)
        busy = Decimal(10) / 3600 * Decimal("0.70")
        assert abs(amount(t["cost_busy_time"]) - busy) < Decimal("0.000001")
        # window: first data 2026-11-20 09:00 until 2026-11-22 00:00 = 39 h; our share = 3400 / (7100 cluster tokens)
        a = r["assumptions"]
        assert a["window_hours"] == pytest.approx(39.0)
        assert a["token_share_source"] == "vllm_metrics"
        assert Decimal(a["token_share"]) == Decimal("0.4789")  # 3400 / 7100, shown with four places
        expected = Decimal("39") * Decimal("0.70") * Decimal(3400) / Decimal(7100)
        assert abs(amount(t["cost_amortized"]) - expected) < Decimal("0.0001")
        assert a["gpu_hourly_cost_eur"] == "0.70" and "g6.xlarge" in a["gpu_price_source"]
        assert r["cluster"]["counter_resets"] == 1 and r["cluster"]["prompt_tokens"] == 6000
        c = r["cluster"]
        assert c["requests_by_finish_reason"] == {"stop": 14}
        assert c["cached_prompt_tokens"] + c["computed_prompt_tokens"] == 6000
        # cluster GPU time: inference time (0.9 x e2e) deltas 18 + 2.7 (restart) + 9 = 29.7 s, not the e2e 33 s
        assert c["busy_seconds_source"] == "request_inference_time_seconds" and c["busy_seconds"] == pytest.approx(29.7)
        assert abs(amount(c["cost_busy_time_all_traffic"]) - Decimal("29.7") / 3600 * Decimal("0.70")) < Decimal("0.000001")
        assert amount(c["cost_busy_time_our_share"]) < amount(c["cost_busy_time_all_traffic"])
        assert r["currency"] == "EUR" and r["stub"] is False

    def test_day_buckets_cover_their_own_hours(self, seeded):
        r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "day", at(22, 0), CFG)
        assert [b["key"] for b in r["items"]] == ["2026-11-20", "2026-11-21"]
        d20, d21 = r["items"]
        assert (d20["requests"], d21["requests"]) == (3, 1)
        share = Decimal(3400) / Decimal(7100)
        assert abs(amount(d20["cost_amortized"]) - Decimal("15") * Decimal("0.70") * share) < Decimal("0.000001")  # 09:00-24:00
        assert abs(amount(d21["cost_amortized"]) - Decimal("24") * Decimal("0.70") * share) < Decimal("0.000001")

    def test_hour_buckets(self, seeded):
        r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "hour", at(22, 0), CFG)
        assert [b["key"] for b in r["items"]] == ["2026-11-20T10:00:00Z", "2026-11-20T11:00:00Z", "2026-11-21T09:00:00Z"]
        assert r["items"][0]["requests"] == 2

    def test_group_by_dimension_sums_to_the_total(self, seeded):
        for dim, keys in (("event_type", {"bank_transaction", "metric"}), ("purpose", {"extract_invoice", "explain"})):
            r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, dim, at(22, 0), CFG)
            assert {b["key"] for b in r["items"]} == keys
            assert abs(sum(amount(b["cost_amortized"]) for b in r["items"]) - amount(r["total"]["cost_amortized"])) < Decimal("0.00001")
            assert sum(b["requests"] for b in r["items"]) == 4

    def test_cost_per_action(self, seeded):
        r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "day", at(22, 0), CFG)
        by = {a["action_type"]: a for a in r["per_action"]}
        assert set(by) == {"attach_invoice", "alert_cost_anomaly"}
        att = by["attach_invoice"]
        assert (att["actions"], att["requests"]) == (1, 2)
        assert abs(amount(att["cost_per_action_busy_time"]) - Decimal(6) / 3600 * Decimal("0.70")) < Decimal("0.000001")
        assert r["per_event"]["events"] == 3
        assert abs(amount(r["per_event"]["cost_per_event_busy_time"]) - amount(r["total"]["cost_busy_time"]) / 3) < Decimal("0.000001")

    def test_cost_per_action_is_empty_without_the_decisions_table(self, seeded):
        seeded.conn.execute("ALTER TABLE decisions RENAME TO decisions_hidden")
        try:
            r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "day", at(22, 0), CFG)
            assert r["per_action"] == [] and r["total"]["requests"] == 4
        finally:
            seeded.conn.rollback()

    def test_hosted_comparison(self, seeded):
        r = report.build_usage_report(seeded.conn, RANGE_FROM, RANGE_TO, "day", at(22, 0), CFG)
        mini = next(h for h in r["hosted_comparison"] if h["model"] == "gpt-5-mini")
        # 2900 x 0.25/1M + 500 x 2.00/1M = 0.001725 USD x 0.87
        assert amount(mini["cost"]) == Decimal("0.001501")
        assert mini["as_of"] == "2026-11-20" and mini["source"].startswith("https://")

    def test_without_samples_the_share_is_assumed_to_be_one(self, seeded):
        r = report.build_usage_report(seeded.conn, at(5, 0), at(6, 0), "day", at(22, 0), CFG)
        assert r["total"]["requests"] == 0 and r["items"] == [] and r["cluster"] is None
        r = report.build_usage_report(seeded.conn, at(21, 8, 30), at(21, 23), "day", at(22, 0), CFG)
        assert r["assumptions"]["token_share_source"] == "vllm_metrics"  # the 08:00 sample is the baseline

    def test_live_snapshot_rates_and_stale_flag(self, seeded):
        live = report.build_live(seeded.conn, at(21, 10, 1), CFG)
        assert live["model"] == MODEL and live["sampled_at"] == at(21, 10)
        assert (live["requests_running"], live["requests_waiting"]) == (2, 1)
        assert live["kv_cache_usage"] == "0.3700"
        assert live["interval_seconds"] == 7200.0 and live["counter_reset"] is False
        assert live["prompt_tokens_per_second"] == "0.21"  # +1500 in 7200 s
        assert live["generation_tokens_per_second"] == "0.06"
        assert live["stale"] is False
        assert report.build_live(seeded.conn, at(21, 11), CFG)["stale"] is True


@pytest.fixture()
def client(seeded):
    app = create_app()

    def override():
        yield seeded

    app.dependency_overrides[get_repo] = override
    app.dependency_overrides[get_clock] = lambda: (lambda: at(22, 0))
    app.dependency_overrides[get_usage_config] = lambda: CFG
    return TestClient(app)


class TestApi:
    def test_usage_defaults_to_the_last_seven_days(self, client):
        r = client.get("/v1/llm/usage")
        assert r.status_code == 200
        body = r.json()
        assert body["group_by"] == "day" and body["total"]["requests"] == 4
        assert body["from"] == "2026-11-15T00:00:00Z" and body["to"] == "2026-11-22T00:00:00Z"
        assert body["total"]["cost_busy_time"]["currency"] == "EUR"
        assert set(body["assumptions"]) >= {"gpu_hourly_cost_eur", "gpu_price_source", "token_share", "methods"}

    def test_usage_with_range_and_grouping(self, client):
        r = client.get("/v1/llm/usage", params={"from": "2026-11-21T00:00:00Z", "to": "2026-11-22T00:00:00Z",
                                                "group_by": "purpose"})
        body = r.json()
        assert r.status_code == 200
        assert body["total"]["requests"] == 1 and [i["key"] for i in body["items"]] == ["extract_invoice"]

    def test_invalid_parameters_use_the_error_model(self, client):
        bad = client.get("/v1/llm/usage", params={"group_by": "week"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
        backwards = client.get("/v1/llm/usage", params={"from": "2026-11-22T00:00:00Z", "to": "2026-11-21T00:00:00Z"})
        assert backwards.status_code == 422 and backwards.json()["error"]["code"] == "validation_error"

    def test_live(self, client):
        r = client.get("/v1/llm/usage/live")
        assert r.status_code == 200
        body = r.json()
        assert body["model"] == MODEL and body["requests_waiting"] == 1 and body["stale"] is True  # clock is 12 h later
        assert body["sampled_at"] == "2026-11-21T10:00:00Z"

    def test_live_is_503_without_samples(self, repo):
        from cfo.db.repo import Repository

        conn = repo.conn
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}_empty")
        conn.execute(f"SET search_path TO {SCHEMA}_empty")
        conn.commit()
        from cfo.db.migrate import SCHEMA_PATH

        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        try:
            app = create_app()

            def override():
                yield Repository(conn)

            app.dependency_overrides[get_repo] = override
            app.dependency_overrides[get_clock] = lambda: (lambda: at(22, 0))
            r = TestClient(app).get("/v1/llm/usage/live")
            assert r.status_code == 503 and r.json()["error"]["code"] == "metrics_unavailable"
            # the usage endpoint still answers (empty) without any sampler data
            empty = TestClient(app).get("/v1/llm/usage")
            assert empty.status_code == 200 and empty.json()["total"]["requests"] == 0
            assert empty.json()["assumptions"]["token_share_source"] == "assumed_all_traffic"
        finally:
            conn.rollback()
            conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}_empty CASCADE")
            conn.execute(f"SET search_path TO {SCHEMA}")
            conn.commit()

    def test_openapi_lists_both_paths(self):
        paths = create_app().openapi()["paths"]
        assert "/v1/llm/usage" in paths and "/v1/llm/usage/live" in paths
