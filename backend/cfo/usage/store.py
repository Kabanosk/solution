"""SQL for `llm_usage` and `vllm_metrics_samples`. Plain functions over a psycopg connection, so tests can point
them at an isolated schema and the API can reuse the request's repository connection.

Rows are returned as dicts (the connection must use `dict_row`, like `Repository.connect`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from cfo.usage.recorder import UsageRecord
from cfo.usage.vllm_metrics import VllmSnapshot

# Bucket keys for `group_by`. day/hour buckets are UTC.
GROUP_SQL = {
    "day": "date_trunc('day', ts AT TIME ZONE 'UTC')",
    "hour": "date_trunc('hour', ts AT TIME ZONE 'UTC')",
    "event_type": "COALESCE(event_type, 'unknown')",
    "purpose": "COALESCE(purpose, 'unknown')",
}


def insert_usage(conn: Any, r: UsageRecord) -> None:
    conn.execute(
        "INSERT INTO llm_usage (ts, model, prompt_tokens, completion_tokens, latency_ms, estimated, success, "
        "event_id, event_type, purpose, decision_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (r.ts, r.model, r.prompt_tokens, r.completion_tokens, r.latency_ms, r.estimated, r.success,
         r.event_id, r.event_type, r.purpose, r.decision_id),
    )


def link_decision(conn: Any, event_id: str, decision_id: str) -> None:
    conn.execute("UPDATE llm_usage SET decision_id = %s WHERE event_id = %s AND decision_id IS NULL",
                 (decision_id, event_id))


def insert_sample(conn: Any, ts: datetime, s: VllmSnapshot) -> None:
    conn.execute(
        "INSERT INTO vllm_metrics_samples (ts, model, prompt_tokens_total, generation_tokens_total, "
        "requests_success_total, requests_by_reason, prompt_tokens_cached_total, inference_time_sum, "
        "inference_time_count, e2e_latency_sum, e2e_latency_count, e2e_latency_buckets, requests_running, "
        "requests_waiting, kv_cache_usage, kv_cache_metric) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ts, s.model, s.prompt_tokens_total, s.generation_tokens_total, s.requests_success_total,
         Jsonb(s.requests_by_reason), s.prompt_tokens_cached_total, s.inference_time_sum, s.inference_time_count,
         s.e2e_latency_sum, s.e2e_latency_count,
         Jsonb({("+Inf" if le == float("inf") else repr(le)): c for le, c in s.e2e_latency_buckets.items()}),
         s.requests_running, s.requests_waiting, s.kv_cache_usage, s.kv_cache_metric),
    )


def usage_groups(conn: Any, group_by: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    key = GROUP_SQL[group_by]
    return conn.execute(
        f"""SELECT {key} AS key,
                   count(*) AS requests,
                   count(*) FILTER (WHERE NOT success) AS failed_requests,
                   count(*) FILTER (WHERE estimated) AS estimated_requests,
                   COALESCE(sum(prompt_tokens), 0)::bigint AS prompt_tokens,
                   COALESCE(sum(completion_tokens), 0)::bigint AS completion_tokens,
                   COALESCE(sum(latency_ms), 0) / 1000.0 AS busy_seconds,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
            FROM llm_usage WHERE ts >= %s AND ts < %s GROUP BY 1 ORDER BY 1""",
        (start, end),
    ).fetchall()


def usage_total(conn: Any, start: datetime, end: datetime) -> dict[str, Any]:
    return conn.execute(
        """SELECT count(*) AS requests,
                  count(*) FILTER (WHERE NOT success) AS failed_requests,
                  count(*) FILTER (WHERE estimated) AS estimated_requests,
                  COALESCE(sum(prompt_tokens), 0)::bigint AS prompt_tokens,
                  COALESCE(sum(completion_tokens), 0)::bigint AS completion_tokens,
                  COALESCE(sum(latency_ms), 0) / 1000.0 AS busy_seconds,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                  count(DISTINCT event_id) AS events,
                  min(ts) AS first_ts
           FROM llm_usage WHERE ts >= %s AND ts < %s""",
        (start, end),
    ).fetchone() or {}


def usage_by_action(conn: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """LLM usage attributed to decisions (`llm_usage.decision_id`), grouped by the decision's action type."""
    return conn.execute(
        """SELECT d.action_type AS action_type,
                  count(*) AS requests,
                  count(DISTINCT u.decision_id) AS decisions_with_usage,
                  COALESCE(sum(u.prompt_tokens + u.completion_tokens), 0)::bigint AS tokens,
                  COALESCE(sum(u.prompt_tokens), 0)::bigint AS prompt_tokens,
                  COALESCE(sum(u.completion_tokens), 0)::bigint AS completion_tokens,
                  COALESCE(sum(u.latency_ms), 0) / 1000.0 AS busy_seconds
           FROM llm_usage u JOIN decisions d ON d.id = u.decision_id
           WHERE u.ts >= %s AND u.ts < %s GROUP BY d.action_type ORDER BY d.action_type""",
        (start, end),
    ).fetchall()


def decisions_per_action(conn: Any, start: datetime, end: datetime) -> dict[str, int]:
    rows = conn.execute(
        "SELECT action_type, count(*) AS n FROM decisions WHERE ts >= %s AND ts < %s GROUP BY action_type",
        (start, end),
    ).fetchall()
    return {r["action_type"]: r["n"] for r in rows}


def first_data_ts(conn: Any) -> datetime | None:
    row = conn.execute(
        "SELECT least((SELECT min(ts) FROM llm_usage), (SELECT min(ts) FROM vllm_metrics_samples)) AS ts"
    ).fetchone()
    return row["ts"] if row else None


def samples_between(conn: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Samples in [start, end] plus, per model, the last one before `start` (the baseline for the first delta)."""
    return conn.execute(
        """(SELECT * FROM vllm_metrics_samples WHERE ts >= %(s)s AND ts <= %(e)s)
           UNION ALL
           (SELECT DISTINCT ON (model) * FROM vllm_metrics_samples WHERE ts < %(s)s ORDER BY model, ts DESC)
           ORDER BY ts, id""",
        {"s": start, "e": end},
    ).fetchall()


def latest_samples(conn: Any, per_model: int = 2) -> list[dict[str, Any]]:
    """The newest `per_model` samples of the model that was scraped last, newest first."""
    return conn.execute(
        """SELECT * FROM vllm_metrics_samples
           WHERE model = (SELECT model FROM vllm_metrics_samples ORDER BY ts DESC, id DESC LIMIT 1)
           ORDER BY ts DESC, id DESC LIMIT %s""",
        (per_model,),
    ).fetchall()
