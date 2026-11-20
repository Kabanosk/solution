# Review T016

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 9d28c54569d9be547df974dcaad287b847f07f4b
- **Head:** 49368d4bfc4d2238d6544fc67f07b79a611b5d2a
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes needed. The deployed Slack flow cannot reach the liquidity review, and the approval headline can give a deadline months too late. AC-1 was not run under the requested review constraints.
- **Date:** 2026-11-20 15:30

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/forecast/liquidity.py:467 — HEAD has no production caller that runs the liquidity review and submits its proposal to the agent. In the OpenShift deployment, a CEO’s Slack oven question cannot trigger the what-if forecast or generate a credit-line approval. The integration tests submit the event directly.

MAJOR backend/cfo/agent/graph.py:375 — The Slack approval headline uses the month of the deepest gap instead of the credit line’s required start date. In the documented €30,000 oven scenario, it says to request the line before June even though the proposal requires it from March 1 to cover earlier gaps.

MINOR backend/cfo/forecast/liquidity.py:248 — `forecast_with` requires fixed-cost bookings even when `review_liquidity` receives an explicit buffer. A populated Postgres ledger with no fixed-cost bookings raises `ValueError` before the override can be applied.

VERDICT: Changes needed. The deployed Slack flow cannot reach the liquidity review, and the approval headline can give a deadline months too late. AC-1 was not run under the requested review constraints.
