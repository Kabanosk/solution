"""Parser for the vLLM `/metrics` endpoint (Prometheus text exposition format) and counter arithmetic.

Metric names (vLLM v1 engine, docs.vllm.ai/en/latest/design/metrics/ and the v0.9 metrics page):
`vllm:prompt_tokens_total`, `vllm:generation_tokens_total`, `vllm:request_success_total` (label `finished_reason`),
`vllm:e2e_request_latency_seconds` (histogram), gauges `vllm:num_requests_running`, `vllm:num_requests_waiting`
and the KV-cache gauge, a fraction 0..1, which is `vllm:kv_cache_usage_perc` in newer releases and
`vllm:gpu_cache_usage_perc` in older ones (both are read; the new name wins). Every metric carries `model_name`;
series that differ only in other labels (finished_reason, engine) are summed (the split by finished_reason is
kept). `*_created` samples and the many other `vllm:` series (per-phase histograms, flops, prefix-cache counters)
are ignored, except `prompt_tokens_cached_total` and `request_inference_time_seconds` (GPU time per request).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+-?\d+)?\s*$")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

KV_CACHE_NAMES = ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")  # newest first


@dataclass(frozen=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float


def parse_prometheus_text(text: str) -> list[Sample]:
    """All sample lines of a text exposition; comments, blank and malformed lines are skipped."""
    out: list[Sample] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name, label_text, value_text = m.groups()
        try:
            value = float(value_text)
        except ValueError:
            continue
        labels = {k: v.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
                  for k, v in _LABEL.findall(label_text or "")}
        out.append(Sample(name, labels, value))
    return out


@dataclass(frozen=True)
class VllmSnapshot:
    """One scrape, one model. A missing metric is `None` (older server, feature off)."""

    model: str
    prompt_tokens_total: float | None = None
    generation_tokens_total: float | None = None
    requests_success_total: float | None = None  # all finished_reason values summed
    requests_by_reason: dict[str, float] = field(default_factory=dict)  # stop / length / abort / error / ...
    prompt_tokens_cached_total: float | None = None  # prompt tokens served from the prefix cache (part of prompt_tokens)
    inference_time_sum: float | None = None  # `vllm:request_inference_time_seconds`: time on the GPU, no queueing
    inference_time_count: float | None = None
    e2e_latency_sum: float | None = None
    e2e_latency_count: float | None = None
    e2e_latency_buckets: dict[float, float] = field(default_factory=dict)  # upper bound (seconds) -> cumulative count
    requests_running: float | None = None
    requests_waiting: float | None = None
    kv_cache_usage: float | None = None  # fraction 0..1
    kv_cache_metric: str | None = None  # which metric name it came from


_COUNTERS = {
    "prompt_tokens_total": ("vllm:prompt_tokens_total", "vllm:prompt_tokens"),
    "generation_tokens_total": ("vllm:generation_tokens_total", "vllm:generation_tokens"),
    "requests_success_total": ("vllm:request_success_total", "vllm:request_success"),
    "prompt_tokens_cached_total": ("vllm:prompt_tokens_cached_total", "vllm:prompt_tokens_cached"),
}
_GAUGES = {
    "requests_running": "vllm:num_requests_running",
    "requests_waiting": "vllm:num_requests_waiting",
}
_E2E = "vllm:e2e_request_latency_seconds"
_INFERENCE = "vllm:request_inference_time_seconds"


def _le(value: str) -> float:
    return math.inf if value in ("+Inf", "Inf", "inf") else float(value)


def snapshots_from_text(text: str) -> dict[str, VllmSnapshot]:
    """vLLM metrics text -> one snapshot per `model_name`. Empty dict when there is no vLLM metric in the text."""
    acc: dict[str, dict] = {}
    reasons: dict[str, dict[str, float]] = {}

    def slot(model: str) -> dict:
        return acc.setdefault(model, {"buckets": {}})

    def add(model: str, key: str, value: float) -> None:
        s = slot(model)
        s[key] = s.get(key, 0.0) + value

    kv_found: dict[str, dict[str, float]] = {}
    for s in parse_prometheus_text(text):
        if not s.name.startswith("vllm:"):
            continue
        model = s.labels.get("model_name") or "unknown"
        if math.isnan(s.value):
            continue
        for key, names in _COUNTERS.items():
            if s.name in names:
                add(model, key, s.value)
                if key == "requests_success_total":
                    by = reasons.setdefault(model, {})
                    reason = s.labels.get("finished_reason", "unknown")
                    by[reason] = by.get(reason, 0.0) + s.value
        for key, name in _GAUGES.items():
            if s.name == name:
                add(model, key, s.value)
        if s.name in KV_CACHE_NAMES:
            by_name = kv_found.setdefault(model, {})
            by_name[s.name] = by_name.get(s.name, 0.0) + s.value
        elif s.name == f"{_INFERENCE}_sum":
            add(model, "inference_time_sum", s.value)
        elif s.name == f"{_INFERENCE}_count":
            add(model, "inference_time_count", s.value)
        elif s.name == f"{_E2E}_sum":
            add(model, "e2e_latency_sum", s.value)
        elif s.name == f"{_E2E}_count":
            add(model, "e2e_latency_count", s.value)
        elif s.name == f"{_E2E}_bucket" and "le" in s.labels:
            buckets = slot(model)["buckets"]
            le = _le(s.labels["le"])
            buckets[le] = buckets.get(le, 0.0) + s.value
    out: dict[str, VllmSnapshot] = {}
    for model, d in acc.items():
        kv_name = next((n for n in KV_CACHE_NAMES if n in kv_found.get(model, {})), None)
        out[model] = VllmSnapshot(
            model=model,
            prompt_tokens_total=d.get("prompt_tokens_total"),
            generation_tokens_total=d.get("generation_tokens_total"),
            requests_success_total=d.get("requests_success_total"),
            requests_by_reason=reasons.get(model, {}),
            prompt_tokens_cached_total=d.get("prompt_tokens_cached_total"),
            inference_time_sum=d.get("inference_time_sum"),
            inference_time_count=d.get("inference_time_count"),
            e2e_latency_sum=d.get("e2e_latency_sum"),
            e2e_latency_count=d.get("e2e_latency_count"),
            e2e_latency_buckets=dict(sorted(d["buckets"].items())),
            requests_running=d.get("requests_running"),
            requests_waiting=d.get("requests_waiting"),
            kv_cache_usage=kv_found[model][kv_name] if kv_name else None,
            kv_cache_metric=kv_name,
        )
    return out


# --- counter arithmetic ------------------------------------------------------------------------------------


def counter_increase(prev: float | None, cur: float | None) -> float:
    """Increase of a cumulative counter between two scrapes. A counter that went down means the server restarted
    (it started again from zero), so the new value itself is the increase since the restart."""
    if cur is None:
        return 0.0
    if prev is None:
        return 0.0
    return cur - prev if cur >= prev else cur


def counter_increase_series(values: list[float | None]) -> float:
    """Total increase over consecutive scrapes, restarts handled between every pair."""
    total, prev = 0.0, None
    for v in values:
        if v is None:
            continue
        total += counter_increase(prev, v)
        prev = v
    return total


def bucket_increase(prev: dict[float, float], cur: dict[float, float]) -> dict[float, float]:
    """Per-bucket increase of a histogram between two scrapes (restart: the new cumulative counts)."""
    reset = any(cur.get(le, 0.0) < prev.get(le, 0.0) for le in prev)
    return {le: (c if reset else c - prev.get(le, 0.0)) for le, c in cur.items()}


def histogram_quantile(q: float, buckets: dict[float, float]) -> float | None:
    """Prometheus-style quantile (linear interpolation inside the bucket) from cumulative bucket counts."""
    if not buckets:
        return None
    items = sorted(buckets.items())
    total = items[-1][1]
    if total <= 0:
        return None
    rank = q * total
    lower_bound, lower_count = 0.0, 0.0
    for upper, count in items:
        if count >= rank:
            if math.isinf(upper):
                return lower_bound  # the best we can say: at least the last finite bound
            span = count - lower_count
            if span <= 0:
                return upper
            return lower_bound + (upper - lower_bound) * (rank - lower_count) / span
        lower_bound, lower_count = upper, count
    return lower_bound
