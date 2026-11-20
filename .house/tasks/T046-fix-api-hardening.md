---
id: T046
title: "Review fixes: API write protection, async SSE, HTTP edge cases"
status: review
owner: coder
branch: task/T046-fix-api-hardening
depends_on: ["T011", "T032"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api_fixes.py backend/tests/test_api_skeleton.py backend/tests/test_api_read.py backend/tests/test_api_approvals.py"
    timeout: 900
protected: []
---
## Objective
Fix the API findings of the Codex review (`$HOUSE_DIR/reports/review-fixes.md` items 22, 23, P3 API), tests first in
`backend/tests/test_api_fixes.py`:
1. (22) Write endpoints (approve/reject, simulator controls) require `Authorization: Bearer <API_WRITE_TOKEN>` when
   `API_WRITE_TOKEN` is set (from Secret `cfo-secrets`; unset = open, for local dev); approvals do not trust a
   caller-supplied `by` as identity when a token is set (the token maps to the CEO). Document in `docs/api.md`.
2. (23) `/v1/events` SSE is an async generator (no threadpool slot held while idle); the first frame carries an
   `id:` so an early reconnect resumes without gaps.
3. (P3) 500 responses carry CORS headers; 405 keeps `Allow`; a half-open or inverted date range returns 422;
   `to=9999-12-31` returns 422, not 500; by-team costs include unlinked invoices if that is the documented source.

## Context
Starts after T011 and T032 are merged (they edit the API; T030 was rejected, D-019). Source: `$HOUSE_DIR/reports/review-T018.md`,
`review-T019.md`, `review-T020.md`. Confirm each at HEAD first. Keep `docs/api.md` + `openapi.json` in sync.

## Out of scope
Agent, Slack, deploy YAML (only add API_WRITE_TOKEN to the Secret docs).
