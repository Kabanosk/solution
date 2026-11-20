# Review T018

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 866f32ed45f18a14f1a47abe447271f94e747a1e
- **Head:** 63acfed7fc860818da69d4d5d2e3ac541c8a6ed6
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 3
- **Nits:** 0
- **Verdict:** Changes requested. The findings affect the running OpenShift API, including Postgres-backed reads. No scoped startup failure was identified. vLLM, Slack Socket Mode, and both acceptance commands were unverified under the Git-and-file-read-only constraint.
- **Date:** 2026-11-20 15:28

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/api/routers/decisions.py:17 — The unauthenticated decisions endpoint now returns Postgres-backed decision history, including free-form payloads, to anyone who can reach the OpenShift Route. The deployed wildcard CORS setting also lets arbitrary websites read those responses from a browser that can reach the Route.  
MINOR backend/cfo/api/app.py:41 — CORS runs inside Starlette’s server-error middleware, so the generic 500 response lacks CORS headers. A cross-origin dashboard sees a browser network error and cannot read the documented `internal_error` envelope.  
MINOR backend/cfo/api/routers/costs.py:25 — Range validation runs only when both dates are supplied. A `from` date after the latest booking with `to` omitted returns HTTP 200 with an inverted effective range and zero totals instead of the documented 422.  
MINOR backend/cfo/api/errors.py:97 — The HTTP exception handler drops Starlette’s headers, so a 405 response omits the required `Allow` header.  
VERDICT: Changes requested. The findings affect the running OpenShift API, including Postgres-backed reads. No scoped startup failure was identified. vLLM, Slack Socket Mode, and both acceptance commands were unverified under the Git-and-file-read-only constraint.
