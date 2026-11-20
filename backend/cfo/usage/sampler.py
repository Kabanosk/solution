"""Scrapes the vLLM metrics endpoint into `vllm_metrics_samples`.

    python -m cfo.usage sample --every 60        # loop, until interrupted
    python -m cfo.usage sample --once            # one scrape, then exit

From a worker process: `start_background(every=60)` returns a stopped-on-demand daemon thread (a no-op returning
`None` when `VLLM_METRICS_URL` is unset). Scrape the metrics of ONE server pod: counters of several replicas
behind a service would look like restarts. The URL, TLS check and token come from `UsageSettings`
(`VLLM_METRICS_URL`, `VLLM_METRICS_VERIFY`, `VLLM_METRICS_BEARER_TOKEN`).
"""

from __future__ import annotations

import logging
import ssl
import threading
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from cfo.domain import utcnow
from cfo.usage import store
from cfo.usage.settings import UsageSettings, get_usage_settings
from cfo.usage.vllm_metrics import VllmSnapshot, snapshots_from_text

log = logging.getLogger("cfo.usage.sampler")

Fetch = Callable[[], str]


def _ssl_context(verify: str) -> ssl.SSLContext | None:
    v = (verify or "true").strip()
    if v.lower() in ("true", "1", "yes", ""):
        return None  # urllib default: verify against the system CAs
    if v.lower() in ("false", "0", "no"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if not Path(v).is_file():
        raise FileNotFoundError(f"VLLM_METRICS_VERIFY: CA bundle {v!r} not found")
    return ssl.create_default_context(cafile=v)


def make_fetch(settings: UsageSettings | None = None) -> Fetch:
    """HTTP(S) GET of `VLLM_METRICS_URL` returning the body text."""
    s = settings or get_usage_settings()
    if not s.vllm_metrics_url:
        raise ValueError("VLLM_METRICS_URL is not set")
    context = _ssl_context(s.vllm_metrics_verify)
    headers = {"Accept": "text/plain"}
    if s.vllm_metrics_bearer_token:
        headers["Authorization"] = f"Bearer {s.vllm_metrics_bearer_token}"

    def fetch() -> str:
        req = urllib.request.Request(s.vllm_metrics_url, headers=headers)  # type: ignore[arg-type]
        with urllib.request.urlopen(
            req, timeout=s.vllm_metrics_timeout, context=context
        ) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")

    return fetch


def sample_once(
    conn: Any, fetch: Fetch, now: Callable[[], datetime] = utcnow
) -> list[VllmSnapshot]:
    """One scrape, stored. Returns the snapshots written (empty when the text had no vLLM metric)."""
    snapshots = list(snapshots_from_text(fetch()).values())
    ts = now()
    for snap in snapshots:
        store.insert_sample(conn, ts, snap)
    conn.commit()
    return snapshots


def run(
    every: float,
    connect: Callable[[], Any],
    fetch: Fetch,
    *,
    stop: threading.Event | None = None,
    max_iterations: int | None = None,
    now: Callable[[], datetime] = utcnow,
) -> int:
    """Scrape every `every` seconds until `stop` is set (or `max_iterations` scrapes). Returns the scrapes done.

    Waiting is `stop.wait(every)`, so a stop request ends the pause at once and tests never sleep for real
    (they pass an Event whose `wait` is replaced, or a small `max_iterations` with `every=0`).
    A failed scrape or database write is logged and retried at the next tick."""
    stop = stop or threading.Event()
    done = 0
    while not stop.is_set() and (max_iterations is None or done < max_iterations):
        try:
            conn = connect()
            try:
                sample_once(conn, fetch, now)
            finally:
                conn.close()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - the server may be restarting; keep sampling
            log.warning("vLLM metrics scrape failed: %s", exc)
        done += 1
        if max_iterations is not None and done >= max_iterations:
            break
        stop.wait(every)
    return done


def start_background(
    every: float = 60.0, settings: UsageSettings | None = None
) -> threading.Event | None:
    """Daemon thread running the sampler for a worker process. Returns the stop event, or None when disabled."""
    s = settings or get_usage_settings()
    if not s.vllm_metrics_url:
        log.info("VLLM_METRICS_URL not set: vLLM metrics sampler disabled")
        return None
    import psycopg
    from cfo.config import get_settings

    stop = threading.Event()
    fetch = make_fetch(s)
    threading.Thread(
        target=run,
        args=(
            every,
            lambda: psycopg.connect(get_settings().database_url, connect_timeout=5),
            fetch,
        ),
        kwargs={"stop": stop},
        daemon=True,
        name="vllm-metrics-sampler",
    ).start()
    return stop
