---
id: T041
title: "Review fixes: agent graph correctness and small-model robustness"
status: review
owner: coder
branch: task/T041-fix-agent-robustness
depends_on: ["T011", "T027"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_agent_fixes.py backend/tests/test_agent.py backend/tests/test_policy.py backend/tests/test_cost_metrics.py backend/tests/test_liquidity_gaps.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests"
    timeout: 1500
protected: []
---
## Objective
Fix the agent findings of the Codex review; each fix gets a failing test first in the new file
`backend/tests/test_agent_fixes.py` (FakeLLM / fake notifier). Items from `$HOUSE_DIR/reports/review-fixes.md`:
1. (12) `cfo/agent/graph.py`: reconciliation compares the invoice number EXTRACTED from the document with the
   expected one; a document that only mentions the expected number is not attached.
2. (13) After an approved action, check the attach/execute result; on refusal record a failed decision (not
   `EXECUTED`), keep the approval unresolved-or-failed visibly, and tell the CEO.
3. (14) A CEO rule that puts anomaly alerts at `APPROVAL` is honoured: interrupt for approval instead of sending.
4. (15) Credit-line approval headline uses the proposal's required start date (e.g. "from 1 March"), not the
   month of the deepest gap.
5. (16) `cfo/agent/investigate.py`: the live alert path passes the invoices from the repository to the detector,
   so ad-spend alerts list contributing invoice lines.
6. (17) Odd LLM shapes never abort a turn: scalar/non-list `lines` in `cfo/agent/llm_tasks.py`, tool arguments
   that are valid non-object JSON (`[]`, `null`) in `cfo/llm.py` → treated as "no extraction" / fallback.
7. (7) If the endpoint rejects a request with `tools` (HTTP 400, vLLM without auto tool choice), `cfo/llm.py` falls
   back to the JSON-in-text path instead of failing (`cfo/agent/policy.py:207` parse_rule and every tool call).
8. (24) A Slack/notifier failure while sending an approval request does not strand it: mark it undelivered and
   retry on the next event (or re-send on the next tick); covered by a test with a failing fake notifier.
9. (P3) Escalation history is filtered by transaction in the query (not last-500-global); a reminder skips a
   recipient now on vacation. Policy: supplier history grouped by normalised name; 1% message matches `>=`.

## Context
- Starts after T011 (cfo.run, in-API agent) and T027 (tracing in graph.py) are merged: rebase on main first and
  keep their behaviour. Source reviews: `$HOUSE_DIR/reports/review-T003.md`, `review-T005.md`, `review-T007.md`,
  `review-T016.md`, `review-T001.md`. Confirm each finding at HEAD; report the ones that are wrong.
- From T044: `cost_metrics` now flags a cost rising from 0, and then `cost_change_pct` is `Decimal("Infinity")`;
  `cfo/agent/investigate.py` turns it into `float('inf')` in the facts. Map it to None / "new cost" in the alert
  text and facts (test it).

## Out of scope
Slack transport (T043), API (T046), data loader (T040).
