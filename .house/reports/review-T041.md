# Review T041

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (9/40 today)
- **Base:** 56be134cb7d3b8eee2e6287c6f62fbd8c512e348
- **Head:** b79b8995edd72f2832b4ca6023b4eb3e85273078
- **Status:** ok
- **Blockers:** 0
- **Majors:** 4
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. Static review only. The acceptance suites were not run, per the tool restriction.
- **Date:** 2026-11-20 16:24

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/agent/graph.py:487 — A refused invoice attachment still resolves the approval as `APPROVED`. The linked decision records the failure, but the approval itself has no failed state and subsequent attempts return `already_resolved`.
MAJOR backend/cfo/agent/graph.py:480 — Approved anomaly alerts remain `EXECUTED` even when Slack delivery fails for every recipient. The send errors are swallowed, and the recorded decision suppresses future attempts for that month.
MAJOR backend/cfo/llm.py:172 — One successful native tool call disables fallback for all later calls. If the endpoint subsequently returns a tool-related HTTP 400, the client raises instead of retrying through JSON-in-text.
MAJOR backend/cfo/llm.py:39 — Fallback JSON with a list or object in `name` raises `TypeError` during set membership, so a malformed model response can abort the call.
MINOR backend/cfo/agent/llm_tasks.py:153 — A model response with a numeric `total` such as `1e309` decodes to infinity, passes `parse_amount`, and raises a Pydantic validation error outside the guarded extraction call.
VERDICT: Changes requested. Static review only. The acceptance suites were not run, per the tool restriction.
