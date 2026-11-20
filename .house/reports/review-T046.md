# Review T046

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (10/40 today)
- **Base:** 7c5aec04cfc12361f923aa913b6f9040e5648ec3
- **Head:** 1d58c6b35d1200eddc214d59a7f974625a3653e7
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 2
- **Nits:** 0
- **Verdict:** Changes requested. AC-1 was not run under the git-and-file-reading-only constraint.
- **Date:** 2026-11-20 16:33

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR deploy/openshift/app/README.md:52 — The API deployment imports `slack` and never reads `cfo-secrets`. Setting `API_WRITE_TOKEN` in the required Secret therefore leaves the write endpoints open.
MAJOR backend/cfo/api/routers/approvals.py:68 — An empty `CEO_ID` makes both approval handlers fall back to caller-supplied `by` even when the bearer token is configured, allowing a token holder to impersonate another approver.
MAJOR backend/cfo/api/services.py:324 — A short range ending in December 9999 passes the 120-month limit, then `_month_starts` tries to construct January 10000. For example, `from=9999-12-01&to=9999-12-31` still returns 500.
MINOR backend/cfo/api/services.py:343 — A request with only one date bound still succeeds when the default makes the range valid. With January 2025 bookings, `?from=2025-01-01` returns 200 despite the requirement that half-open ranges return 422.
MINOR backend/cfo/api/routers/approvals.py:65 — Approve and reject resolve the database and agent dependencies before authenticating, so a missing or invalid token can trigger a database connection and receive 503 instead of 401.
VERDICT: Changes requested. AC-1 was not run under the git-and-file-reading-only constraint.
