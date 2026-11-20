"""Settings of the usage and cost estimate (same `.env` as cfo.config, kept separate to avoid colliding on config.py).

Every price in here is an ASSUMPTION with a source and a date; override with the environment variables below.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from cfo.config import REPO_ROOT

# One NVIDIA L4 (the GPU of the RHOAI cluster, INFRA.md) on demand. Reference: AWS EC2 g6.xlarge (1x L4, 4 vCPU,
# 16 GiB) is USD 0.8048 per hour in us-east-1 (https://instances.vantage.sh/aws/ec2/g6.xlarge, read 2026-11-20,
# an aggregator, not AWS's own page). Converted at an ASSUMED 0.87 EUR per USD and rounded: 0.70 EUR per hour.
DEFAULT_GPU_HOURLY_COST_EUR = Decimal("0.70")
GPU_PRICE_SOURCE = (
    "AWS EC2 g6.xlarge (1x NVIDIA L4) on-demand USD 0.8048/h in us-east-1, https://instances.vantage.sh/aws/ec2/g6.xlarge, "
    "read 2026-11-20; converted at an assumed 0.87 EUR/USD and rounded to 0.70 EUR/h. Assumption, not a quote."
)


class HostedPrice(BaseModel):
    """List price of a hosted model per 1M tokens, EUR-agnostic: `currency` says what the numbers are."""

    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    currency: str = "USD"
    source: str = ""
    as_of: str = ""


# OpenAI list prices (standard tier), read 2026-11-20 via search results quoting the model pages:
# https://developers.openai.com/api/docs/models/gpt-5-mini and https://developers.openai.com/api/docs/models/gpt-5-nano
DEFAULT_HOSTED_PRICES = [
    HostedPrice(model="gpt-5-mini", input_per_million=Decimal("0.25"), output_per_million=Decimal("2.00"),
                source="https://developers.openai.com/api/docs/models/gpt-5-mini", as_of="2026-11-20"),
    HostedPrice(model="gpt-5-nano", input_per_million=Decimal("0.05"), output_per_million=Decimal("0.40"),
                source="https://developers.openai.com/api/docs/models/gpt-5-nano", as_of="2026-11-20"),
]


class UsageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # URL of the vLLM metrics, e.g. http://<predictor-service>:8080/metrics. Unset = the sampler is disabled.
    vllm_metrics_url: str | None = None
    vllm_metrics_timeout: float = 5.0  # seconds per scrape
    # TLS check for an https URL: "true" (default), "false" (self-signed certificate, no check) or the path of a CA bundle.
    vllm_metrics_verify: str = "true"
    vllm_metrics_bearer_token: str | None = None  # only if the endpoint sits behind an authenticating proxy
    # The /live snapshot is flagged stale when older than this many seconds.
    vllm_metrics_stale_seconds: float = 300.0

    gpu_hourly_cost_eur: Decimal = DEFAULT_GPU_HOURLY_COST_EUR
    # Extra EUR per hour on top of the GPU (the share of the node: CPU, RAM, platform licence). Default none.
    node_hourly_cost_eur: Decimal = Decimal("0")
    gpu_price_source: str = GPU_PRICE_SOURCE
    # JSON list of {"model", "input_per_million", "output_per_million", "currency", "source", "as_of"}.
    hosted_prices_json: str | None = None
    # USD -> EUR used to compare with the hosted list prices (which are in USD). Assumption.
    usd_to_eur: Decimal = Field(default=Decimal("0.87"))

    # Switch off the per-call recording in cfo.llm entirely ("false").
    llm_usage_record: bool = True

    @property
    def hosted_prices(self) -> list[HostedPrice]:
        if not self.hosted_prices_json:
            return list(DEFAULT_HOSTED_PRICES)
        data: Any = json.loads(self.hosted_prices_json)
        return [HostedPrice(**row) for row in data]


@lru_cache
def get_usage_settings() -> UsageSettings:
    return UsageSettings()
