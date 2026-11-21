"""The only way onboarding talks to providers: fixed allowlisted base URLs, short timeouts, no redirects.

There is deliberately no function that accepts a caller-supplied URL. Tests replace `TRANSPORT` with a scripted
`httpx.MockTransport`; nothing else in the package builds an HTTP client."""

from __future__ import annotations

import httpx

from scout.connectors.ksef import TEST_URL as KSEF_TEST_URL

BUNQ_SANDBOX = "https://public-api.sandbox.bunq.com/v1"  # sandbox only; the production host is not reachable from here
BASES: dict[str, str] = {
    "bunq": BUNQ_SANDBOX,
    "google_auth": "https://accounts.google.com",
    "google_token": "https://oauth2.googleapis.com",
    "gmail": "https://gmail.googleapis.com",
    "slack": "https://slack.com",
    "ksef": KSEF_TEST_URL,  # KSeF test environment only
}
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
REVOKE_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
TRANSPORT: httpx.BaseTransport | None = None  # tests only


def client(service: str, *, timeout: httpx.Timeout = TIMEOUT) -> httpx.Client:
    base = BASES[service]  # KeyError for anything that is not allowlisted
    return httpx.Client(base_url=base, timeout=timeout, transport=TRANSPORT, follow_redirects=False,
                        headers={"User-Agent": "scout-onboarding/1"})
