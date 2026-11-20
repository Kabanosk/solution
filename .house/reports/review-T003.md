# Review T003

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 767593370b60e7266341bd6f06a6ddb0ade634fc
- **Head:** f85306c5b9ae865574e4e84a1206752a2bfc0918
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Two material issues remain at HEAD. Static review only. AC-1 and the live deployment were not run.
- **Date:** 2026-11-20 15:26

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/agent/policy.py:207 — `parse_rule` sends tools without an explicit tool choice. A vLLM endpoint without automatic tool calling enabled rejects the request before the JSON fallback can run. FakeLLM does not exercise this failure, and the OpenShift endpoint’s configuration is unverified.

MAJOR backend/cfo/agent/policy.py:165 — Supplier history is grouped by raw name, then normalized in `classify`. Case and whitespace variants produce separate medians, and the last variant overwrites the others. An outlier under a name variant can make a payment above 5% of inflows look routine and bypass approval.

MINOR backend/cfo/agent/policy.py:141 — A new supplier payment at exactly 1% requires approval, but the reason shown to the CEO says approval applies only above 1%.

VERDICT: Two material issues remain at HEAD. Static review only. AC-1 and the live deployment were not run.
