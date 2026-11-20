"""Response models of `/v1/llm/usage` and `/v1/llm/usage/live` (T032). Same conventions as `schemas.py`, with one
exception: costs of single LLM calls are fractions of a cent, so `CostAmount` carries six decimal places."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

DecimalStr = Annotated[str, Field(pattern=r"^-?\d+(\.\d+)?$", examples=["0.37"])]


class CostAmount(BaseModel):
    amount: Annotated[str, Field(pattern=r"^-?\d+\.\d{6}$", examples=["0.001234"],
                                 description="String decimal with six places (a call costs fractions of a cent).")]
    currency: str = Field(default="EUR", examples=["EUR"])


class UsageBucket(BaseModel):
    key: str = Field(description="Bucket label: a UTC day (`2026-11-20`), a UTC hour (`2026-11-20T14:00:00Z`), "
                                 "an event type, a purpose, or `total`.", examples=["2026-11-20"])
    bucket_start: datetime | None = Field(default=None, description="Start of a day/hour bucket, UTC.")
    requests: int
    failed_requests: int = Field(description="Calls that raised an error (counted with the tokens they used: none).")
    estimated_requests: int = Field(description="Calls whose token counts are estimated (the server sent no `usage`).")
    prompt_tokens: int
    completion_tokens: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    busy_seconds: float = Field(description="Sum of request latency.")
    cost_busy_time: CostAmount = Field(description="Busy seconds x hourly rate. Upper bound: overlapping requests count twice.")
    cost_amortized: CostAmount = Field(
        description="Wall-clock hours x hourly rate x our share of the tokens the server processed.")


class ActionCost(BaseModel):
    action_type: str = Field(examples=["attach_invoice"])
    actions: int = Field(description="Decisions of this type in the range (at least those with attributed LLM calls).")
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_busy_time: CostAmount
    cost_amortized: CostAmount
    cost_per_action_busy_time: CostAmount
    cost_per_action_amortized: CostAmount


class PerEvent(BaseModel):
    events: int = Field(description="Distinct agent events that made at least one LLM call.")
    cost_per_event_busy_time: CostAmount | None
    cost_per_event_amortized: CostAmount | None


class ClusterUsage(BaseModel):
    samples: int = Field(description="Sample pairs behind the numbers.")
    counter_resets: int = Field(description="Pod restarts seen between samples (counters went down).")
    prompt_tokens: int
    generation_tokens: int
    requests: int
    requests_by_finish_reason: dict[str, int] = Field(description="Server-side requests by `finished_reason` "
                                                                  "(stop, length, abort, error, ...).")
    cached_prompt_tokens: int = Field(description="Prompt tokens served from the prefix cache (not recomputed).")
    computed_prompt_tokens: int = Field(description="Prompt tokens the GPU computed: prompt tokens minus cached.")
    busy_seconds: float = Field(description="GPU time of all requests in the range, for the cost below.")
    busy_seconds_source: Literal["request_inference_time_seconds", "e2e_request_latency_seconds"] = Field(
        description="Inference time (no queueing) when the server reports it, the e2e latency otherwise.")
    cost_busy_time_all_traffic: CostAmount = Field(description="`busy_seconds` x hourly rate, all users of the server.")
    cost_busy_time_our_share: CostAmount = Field(description="The same times `our_token_share`.")
    avg_e2e_latency_seconds: DecimalStr | None
    p50_e2e_latency_seconds: DecimalStr | None = Field(description="From the histogram buckets (interpolated).")
    p95_e2e_latency_seconds: DecimalStr | None
    our_token_share: DecimalStr = Field(description="Our tokens divided by all tokens the server processed.")


class HostedComparison(BaseModel):
    model: str
    input_per_million: DecimalStr
    output_per_million: DecimalStr
    price_currency: str
    source: str
    as_of: str = Field(description="Date the list price was read. Prices change.")
    cost: CostAmount = Field(description="Our tokens at this price, in EUR.")
    vs_busy_time: DecimalStr | None = Field(description="Hosted cost divided by our busy-time cost.")


class UsageMethods(BaseModel):
    busy_time: str
    amortized: str
    hosted: str


class UsageAssumptions(BaseModel):
    gpu_hourly_cost_eur: DecimalStr
    node_hourly_cost_eur: DecimalStr
    hourly_rate_eur: DecimalStr
    gpu_price_source: str
    window_hours: float = Field(description="Wall-clock hours the amortized method covers.")
    token_share: DecimalStr
    token_share_source: Literal["vllm_metrics", "assumed_all_traffic"]
    usd_to_eur: DecimalStr
    methods: UsageMethods
    notes: list[str]


class LlmUsageReport(BaseModel):
    stub: bool = False
    currency: str = "EUR"
    from_: datetime = Field(alias="from")
    to: datetime
    window_from: datetime = Field(description="Start of the priced wall-clock window (not before the first data).")
    window_to: datetime
    group_by: Literal["day", "hour", "event_type", "purpose"]
    total: UsageBucket
    items: list[UsageBucket]
    per_action: list[ActionCost] = Field(description="Cost per decision, by action type.")
    per_event: PerEvent
    cluster: ClusterUsage | None = Field(description="From the sampled vLLM counters; null when none were sampled.")
    hosted_comparison: list[HostedComparison]
    assumptions: UsageAssumptions

    model_config = {"populate_by_name": True}


class LlmUsageLive(BaseModel):
    stub: bool = False
    model: str
    sampled_at: datetime
    age_seconds: float
    stale: bool = Field(description="The sample is older than VLLM_METRICS_STALE_SECONDS: the sampler is not running.")
    requests_running: int | None
    requests_waiting: int | None
    kv_cache_usage: DecimalStr | None = Field(description="Fraction 0..1 of the KV cache in use; null if the "
                                                          "server does not report it.")
    kv_cache_metric: str | None = Field(description="`vllm:kv_cache_usage_perc` or the older `vllm:gpu_cache_usage_perc`.")
    prompt_tokens_per_second: DecimalStr | None
    generation_tokens_per_second: DecimalStr | None
    interval_seconds: float | None = Field(description="Time between the two samples the rates come from.")
    counter_reset: bool = Field(description="The server restarted in that interval; the rates use the new counters.")
