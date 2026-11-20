"""LLM usage and cost estimation (T032).

* `recorder`: per-call recording used by `cfo.llm` (context of the agent step, best-effort sink).
* `vllm_metrics`: parser of the vLLM Prometheus text format and counter arithmetic.
* `sampler`: scrapes `VLLM_METRICS_URL` into `vllm_metrics_samples` (`python -m cfo.usage sample --every 60`).
* `cost`, `report`: the cost model and the report behind `GET /v1/llm/usage`.

Importing this package stays cheap: `cfo.llm` imports `recorder` only.
"""

from cfo.usage.recorder import usage_context

__all__ = ["usage_context"]
