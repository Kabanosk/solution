"""The report behind `GET /v1/llm/usage` and `GET /v1/llm/usage/live`. Pure over a database connection and an
explicit clock; returns plain dicts shaped like `cfo.api.schemas_usage`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from cfo.usage import cost, store
from cfo.usage.settings import UsageSettings
from cfo.usage.vllm_metrics import bucket_increase, counter_increase, histogram_quantile

STEP = {"day": timedelta(days=1), "hour": timedelta(hours=1)}


def money(value: Decimal) -> dict[str, str]:
    """EUR with six places: one LLM call costs fractions of a cent, two places would show 0.00."""
    return {"amount": format(value.quantize(Decimal("0.000001")), "f"), "currency": "EUR"}


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 3)


# --- cluster deltas from the sampled counters --------------------------------------------------------------


@dataclass
class ClusterDelta:
    prompt_tokens: float = 0.0
    generation_tokens: float = 0.0
    requests: float = 0.0
    latency_sum: float = 0.0
    latency_count: float = 0.0
    inference_sum: float = 0.0
    inference_count: float = 0.0
    cached_prompt_tokens: float = 0.0
    by_reason: dict[str, float] = field(default_factory=dict)
    buckets: dict[float, float] = field(default_factory=dict)
    samples: int = 0
    counter_resets: int = 0

    @property
    def tokens(self) -> float:
        return self.prompt_tokens + self.generation_tokens


def cluster_delta(rows: list[dict[str, Any]], start: datetime, end: datetime) -> ClusterDelta:
    """Usage of the whole server between consecutive samples whose later sample lies in (start, end].

    `rows` come from `store.samples_between` (incl. the baseline before `start`). Restarts (a counter that went
    down) are handled per pair, so the sum survives pod restarts."""
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)
    out = ClusterDelta()
    for series in by_model.values():
        series.sort(key=lambda r: (r["ts"], r["id"]))
        for prev, cur in zip(series, series[1:]):
            if not (start < cur["ts"] <= end):
                continue
            out.samples += 1
            if any(cur[k] is not None and prev[k] is not None and cur[k] < prev[k]
                   for k in ("prompt_tokens_total", "generation_tokens_total")):
                out.counter_resets += 1
            out.prompt_tokens += counter_increase(prev["prompt_tokens_total"], cur["prompt_tokens_total"])
            out.generation_tokens += counter_increase(prev["generation_tokens_total"], cur["generation_tokens_total"])
            out.requests += counter_increase(prev["requests_success_total"], cur["requests_success_total"])
            out.latency_sum += counter_increase(prev["e2e_latency_sum"], cur["e2e_latency_sum"])
            out.latency_count += counter_increase(prev["e2e_latency_count"], cur["e2e_latency_count"])
            out.inference_sum += counter_increase(prev["inference_time_sum"], cur["inference_time_sum"])
            out.inference_count += counter_increase(prev["inference_time_count"], cur["inference_time_count"])
            out.cached_prompt_tokens += counter_increase(prev["prompt_tokens_cached_total"],
                                                         cur["prompt_tokens_cached_total"])
            pr, cr = prev["requests_by_reason"] or {}, cur["requests_by_reason"] or {}
            for reason, value in cr.items():
                out.by_reason[reason] = out.by_reason.get(reason, 0.0) + counter_increase(pr.get(reason), value)
            pb = {float(k): v for k, v in (prev["e2e_latency_buckets"] or {}).items()}
            cb = {float(k): v for k, v in (cur["e2e_latency_buckets"] or {}).items()}
            for le, inc in bucket_increase(pb, cb).items():
                out.buckets[le] = out.buckets.get(le, 0.0) + inc
    return out


# --- usage report ------------------------------------------------------------------------------------------


def _bucket(row: dict[str, Any], *, key: str, bucket_start: datetime | None, rate: Decimal, share: Decimal,
            hours: float | None, amortized: Decimal | None = None) -> dict[str, Any]:
    busy = cost.busy_cost(row["busy_seconds"], rate)
    if amortized is None:
        amortized = cost.amortized_cost(hours or 0.0, rate, share)
    return {
        "key": key,
        "bucket_start": bucket_start,
        "requests": row["requests"],
        "failed_requests": row["failed_requests"],
        "estimated_requests": row["estimated_requests"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "latency_p50_ms": _round(_num(row["p50_ms"])),
        "latency_p95_ms": _round(_num(row["p95_ms"])),
        "busy_seconds": float(row["busy_seconds"]),
        "cost_busy_time": money(busy),
        "cost_amortized": money(amortized),
    }


def _hours(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 3600.0)


def build_usage_report(conn: Any, start: datetime, end: datetime, group_by: str, now: datetime,
                       settings: UsageSettings) -> dict[str, Any]:
    """`start`/`end` are timezone-aware UTC. The wall-clock window of the amortized method is [start, min(end, now)]
    but not before the first data row: time before anything was recorded is not ours to price."""
    rate = cost.hourly_rate(settings.gpu_hourly_cost_eur, settings.node_hourly_cost_eur)
    first = store.first_data_ts(conn)
    win_start = max(start, first) if first else start
    win_end = max(win_start, min(end, now))
    window_hours = _hours(win_start, win_end)

    total_row = store.usage_total(conn, start, end)
    cluster = cluster_delta(store.samples_between(conn, start, end), start, end)
    our_tokens = int(total_row.get("prompt_tokens", 0)) + int(total_row.get("completion_tokens", 0))
    share, share_source = cost.token_share(our_tokens, cluster.tokens if cluster.samples else None)

    groups = store.usage_groups(conn, group_by, start, end)
    items: list[dict[str, Any]] = []
    total_amortized = cost.amortized_cost(window_hours, rate, share)
    for g in groups:
        if group_by in STEP:
            b_start = g["key"].replace(tzinfo=timezone.utc)
            h = _hours(max(b_start, win_start), min(b_start + STEP[group_by], win_end))
            key = b_start.date().isoformat() if group_by == "day" else b_start.strftime("%Y-%m-%dT%H:00:00Z")
            items.append(_bucket(g, key=key, bucket_start=b_start, rate=rate, share=share, hours=h))
        else:
            g_tokens = int(g["prompt_tokens"]) + int(g["completion_tokens"])
            part = total_amortized * (Decimal(g_tokens) / Decimal(our_tokens)) if our_tokens else Decimal(0)
            items.append(_bucket(g, key=str(g["key"]), bucket_start=None, rate=rate, share=share, hours=None,
                                 amortized=part))

    total = _bucket({**total_row, "p50_ms": total_row.get("p50_ms"), "p95_ms": total_row.get("p95_ms")},
                    key="total", bucket_start=None, rate=rate, share=share, hours=None, amortized=total_amortized)

    # cost per agent action
    actions_total = store.decisions_per_action(conn, start, end)
    per_action = []
    for a in store.usage_by_action(conn, start, end):
        n = actions_total.get(a["action_type"]) or a["decisions_with_usage"]
        busy = cost.busy_cost(a["busy_seconds"], rate)
        amort = total_amortized * (Decimal(int(a["tokens"])) / Decimal(our_tokens)) if our_tokens else Decimal(0)
        per_action.append({
            "action_type": a["action_type"],
            "actions": n,
            "requests": a["requests"],
            "prompt_tokens": a["prompt_tokens"],
            "completion_tokens": a["completion_tokens"],
            "cost_busy_time": money(busy),
            "cost_amortized": money(amort),
            "cost_per_action_busy_time": money(busy / n),
            "cost_per_action_amortized": money(amort / n),
        })
    events = int(total_row.get("events", 0))
    busy_total = cost.busy_cost(total_row.get("busy_seconds", 0), rate)
    per_event = {
        "events": events,
        "cost_per_event_busy_time": money(busy_total / events) if events else None,
        "cost_per_event_amortized": money(total_amortized / events) if events else None,
    }

    p = int(total_row.get("prompt_tokens", 0))
    c = int(total_row.get("completion_tokens", 0))
    hosted = []
    for price in settings.hosted_prices:
        eur = cost.hosted_cost_eur(p, c, price, settings.usd_to_eur)
        hosted.append({
            "model": price.model,
            "input_per_million": str(price.input_per_million),
            "output_per_million": str(price.output_per_million),
            "price_currency": price.currency,
            "source": price.source,
            "as_of": price.as_of,
            "cost": money(eur),
            "vs_busy_time": None if busy_total == 0 else str((eur / busy_total).quantize(Decimal("0.01"))),
        })

    cluster_block = None
    if cluster.samples:
        # GPU time of ALL requests: the server's own inference time (no queueing), the e2e latency as fallback
        busy_s, busy_src = ((cluster.inference_sum, "request_inference_time_seconds") if cluster.inference_count
                            else (cluster.latency_sum, "e2e_request_latency_seconds"))
        p95 = histogram_quantile(0.95, cluster.buckets)
        p50 = histogram_quantile(0.5, cluster.buckets)
        cluster_block = {
            "samples": cluster.samples,
            "counter_resets": cluster.counter_resets,
            "prompt_tokens": int(cluster.prompt_tokens),
            "generation_tokens": int(cluster.generation_tokens),
            "requests": int(cluster.requests),
            "requests_by_finish_reason": {k: int(v) for k, v in sorted(cluster.by_reason.items())},
            "cached_prompt_tokens": int(cluster.cached_prompt_tokens),
            "computed_prompt_tokens": int(cluster.prompt_tokens - cluster.cached_prompt_tokens),
            "busy_seconds": round(busy_s, 3),
            "busy_seconds_source": busy_src,
            "cost_busy_time_all_traffic": money(cost.busy_cost(busy_s, rate)),
            "cost_busy_time_our_share": money(cost.busy_cost(busy_s, rate) * share),
            "avg_e2e_latency_seconds": (str(Decimal(cluster.latency_sum / cluster.latency_count).quantize(Decimal("0.001")))
                                        if cluster.latency_count else None),
            "p50_e2e_latency_seconds": None if p50 is None else str(Decimal(p50).quantize(Decimal("0.001"))),
            "p95_e2e_latency_seconds": None if p95 is None else str(Decimal(p95).quantize(Decimal("0.001"))),
            "our_token_share": str(share.quantize(Decimal("0.0001"))),
        }

    assumptions = {
        "gpu_hourly_cost_eur": str(settings.gpu_hourly_cost_eur),
        "node_hourly_cost_eur": str(settings.node_hourly_cost_eur),
        "hourly_rate_eur": str(rate),
        "gpu_price_source": settings.gpu_price_source,
        "window_hours": round(window_hours, 4),
        "token_share": str(share.quantize(Decimal("0.0001"))),
        "token_share_source": share_source,
        "usd_to_eur": str(settings.usd_to_eur),
        "methods": {
            "busy_time": "Sum of request latency x hourly rate. Overlapping requests are counted twice, so this "
                         "is an upper bound of the GPU time our requests used.",
            "amortized": "Wall-clock hours of the window x hourly rate x our share of the tokens the server "
                         "processed (llm_usage tokens / vLLM token counters). Share is 1.0 when there are no "
                         "vLLM samples. Buckets with no request are not listed, so the total can exceed the sum "
                         "of the listed buckets (idle time).",
            "hosted": "The same prompt and completion tokens at a hosted API's list price per 1M tokens, "
                      "converted to EUR at usd_to_eur. List prices change; they are assumptions with a date.",
        },
        "notes": ([f"{total_row.get('estimated_requests', 0)} request(s) have estimated token counts "
                   "(the server sent no usage field, about 4 characters per token)."]
                  if total_row.get("estimated_requests") else []),
    }
    return {
        "stub": False,
        "currency": "EUR",
        "from": start,
        "to": end,
        "window_from": win_start,
        "window_to": win_end,
        "group_by": group_by,
        "total": total,
        "items": items,
        "per_action": per_action,
        "per_event": per_event,
        "cluster": cluster_block,
        "hosted_comparison": hosted,
        "assumptions": assumptions,
    }


# --- live snapshot -----------------------------------------------------------------------------------------


def build_live(conn: Any, now: datetime, settings: UsageSettings) -> dict[str, Any] | None:
    """Newest vLLM sample and rates over the interval to the one before it. None when nothing was sampled."""
    rows = store.latest_samples(conn, 2)
    if not rows:
        return None
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    interval = (cur["ts"] - prev["ts"]).total_seconds() if prev else None
    prompt_rate = gen_rate = None
    reset = False
    if prev and interval and interval > 0:
        reset = any(cur[k] is not None and prev[k] is not None and cur[k] < prev[k]
                    for k in ("prompt_tokens_total", "generation_tokens_total"))
        if cur["prompt_tokens_total"] is not None:
            prompt_rate = counter_increase(prev["prompt_tokens_total"], cur["prompt_tokens_total"]) / interval
        if cur["generation_tokens_total"] is not None:
            gen_rate = counter_increase(prev["generation_tokens_total"], cur["generation_tokens_total"]) / interval
    age = max(0.0, (now - cur["ts"]).total_seconds())

    def dec(v: float | None, places: str) -> str | None:
        return None if v is None or math.isnan(v) else str(Decimal(v).quantize(Decimal(places)))

    return {
        "stub": False,
        "model": cur["model"],
        "sampled_at": cur["ts"].astimezone(timezone.utc),
        "age_seconds": round(age, 1),
        "stale": age > settings.vllm_metrics_stale_seconds,
        "requests_running": None if cur["requests_running"] is None else int(cur["requests_running"]),
        "requests_waiting": None if cur["requests_waiting"] is None else int(cur["requests_waiting"]),
        "kv_cache_usage": dec(cur["kv_cache_usage"], "0.0001"),
        "kv_cache_metric": cur["kv_cache_metric"],
        "prompt_tokens_per_second": dec(prompt_rate, "0.01"),
        "generation_tokens_per_second": dec(gen_rate, "0.01"),
        "interval_seconds": None if interval is None else round(interval, 1),
        "counter_reset": reset,
    }
