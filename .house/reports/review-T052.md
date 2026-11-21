# Review T052

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (2/40 today)
- **Base:** 35679a2626eeb507d2314626aea3004023c5ea0a
- **Head:** 1e44fd969e486d217d3effc57fbddd40c146dacc
- **Status:** ok
- **Blockers:** 1
- **Majors:** 7
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes. Dashboard guardrails are disabled in production wiring, and withheld content can be exposed through the audit API.
- **Date:** 2026-11-21 01:22

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER backend/scout/chat_api.py:82 — The production dashboard context leaves `guard` unset and uses the raw `get_llm()`. All three guardrail checks are disabled on `/v1/chat`, and dashboard LLM calls lack tracing. The tests inject a preconfigured context and miss this wiring failure.
MAJOR backend/scout/chat_api.py:164 — The endpoint screens only the final user turn, then passes client-supplied history into the model and its compaction call. A previously blocked prompt can reach the model on the next request as an earlier turn.
MAJOR backend/scout/guardrails.py:153 — Blocked tool results and bot replies are copied into `audit_log.resource`. The read endpoint at `/v1/audit` returns that field without authentication, exposing excerpts of content the guardrail withheld.
MAJOR backend/scout/guardrails.py:171 — Strike recording, counting, sending the DM, and resetting the counter are separate operations. Concurrent blocks for the same person can send duplicate alerts or reset away a strike that was absent from the alert.
MAJOR backend/scout/guardrails.py:210 — `SlackUI.dm()` returns `None` when the recipient has no Slack channel. Escalation still records the prompts as reported and resets the counter. A configured Slack ID outside the employee directory also falls back to the CEO, sending the prompts to a different recipient.
MAJOR backend/scout/guardrails.py:204 — The alert reconstructs prompts from `audit_log.resource`, which `Audit.record()` truncates to 500 characters. Compliance can miss the part of a prompt that triggered the block.
MAJOR backend/scout/loops/invoicing.py:104 — Approving a flagged PDF files an invoice record synthesized from the payment amount, supplier, and date. The filing call receives no original document, so approval can create a paid invoice record with unverified values while leaving the reviewed PDF unattached.
MAJOR backend/scout/guardrails.py:224 — External alert deduplication uses a process-local hash of the whole tool response and includes the task ID. The same item can alert repeatedly across tasks, changed search results, or restarts, while a failed notification is suppressed on retry within the same process.
MINOR .house/checks/T052.json:24 — AC-3 requires evidence in `reports/T052.md`, but the reviewed revision contains no T052 report or recorded cluster result.
VERDICT: Request changes. Dashboard guardrails are disabled in production wiring, and withheld content can be exposed through the audit API.
