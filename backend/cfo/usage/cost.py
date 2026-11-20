"""The cost model, in plain `Decimal` arithmetic (EUR). Every rate is an assumption, see `settings.py`.

* busy-time:  the time our requests kept the model busy (sum of request latency) x hourly rate. Requests that
  overlap (continuous batching) are counted twice, so this is an upper bound of the GPU time really used.
* amortized:  the GPU is paid for around the clock. Wall-clock hours x hourly rate x our share of the tokens the
  server processed (the rest of the traffic, or idle time, is not ours). Share 1.0 when the cluster token count
  is unknown (assumes we are the only user).
* hosted:     the same tokens at a hosted API's list price per 1M tokens.
"""

from __future__ import annotations

from decimal import Decimal

from cfo.usage.settings import HostedPrice

MILLION = Decimal(1_000_000)
SECONDS_PER_HOUR = Decimal(3600)


def hourly_rate(gpu_hourly_eur: Decimal, node_hourly_eur: Decimal) -> Decimal:
    return gpu_hourly_eur + node_hourly_eur


def busy_cost(busy_seconds: float | Decimal, rate_per_hour: Decimal) -> Decimal:
    return Decimal(str(busy_seconds)) / SECONDS_PER_HOUR * rate_per_hour


def token_share(our_tokens: int, cluster_tokens: float | None) -> tuple[Decimal, str]:
    """(share 0..1, source). Share is capped at 1: the sampler may have missed a scrape, ours cannot exceed total."""
    if cluster_tokens is None or cluster_tokens <= 0:
        return Decimal(1), "assumed_all_traffic"
    return (
        min(Decimal(1), Decimal(our_tokens) / Decimal(str(cluster_tokens))),
        "vllm_metrics",
    )


def amortized_cost(
    wall_hours: float | Decimal, rate_per_hour: Decimal, share: Decimal
) -> Decimal:
    return Decimal(str(wall_hours)) * rate_per_hour * share


def hosted_cost_eur(
    prompt_tokens: int, completion_tokens: int, price: HostedPrice, usd_to_eur: Decimal
) -> Decimal:
    usd = (
        Decimal(prompt_tokens) / MILLION * price.input_per_million
        + Decimal(completion_tokens) / MILLION * price.output_per_million
    )
    return usd * (usd_to_eur if price.currency.upper() == "USD" else Decimal(1))
