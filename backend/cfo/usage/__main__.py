"""`python -m cfo.usage sample [--every SECONDS | --once]`."""

from __future__ import annotations

import argparse
import logging
import sys

import psycopg

from cfo.config import get_settings
from cfo.usage import sampler
from cfo.usage.settings import get_usage_settings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m cfo.usage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="scrape VLLM_METRICS_URL into vllm_metrics_samples")
    p.add_argument("--every", type=float, default=60.0, help="seconds between scrapes (default 60)")
    p.add_argument("--once", action="store_true", help="scrape once and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = get_usage_settings()
    if not settings.vllm_metrics_url:
        print("VLLM_METRICS_URL is not set: nothing to sample.", file=sys.stderr)
        return 2
    from cfo.tenancy import bind_tenant, legacy_connectors_enabled, worker_tenant
    from scout.db import factory
    tenant = worker_tenant(get_settings().database_url)
    if not legacy_connectors_enabled(tenant):
        raise PermissionError("Global vLLM metrics sampler requires explicit legacy organization configuration")
    with bind_tenant(tenant):
        connect = factory(get_settings().database_url)
    fetch = sampler.make_fetch(settings)

    try:
        done = sampler.run(args.every, connect, fetch, max_iterations=1 if args.once else None)
    except KeyboardInterrupt:
        return 0
    print(f"scrapes done: {done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
