# Review T005

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 1c2eddf90dd917918f3a3afceb166becabcae56d
- **Head:** 8287ae114bd30780f0cf39622a3e2efafbd0f7bd
- **Status:** ok
- **Blockers:** 0
- **Majors:** 6
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes needed. At HEAD, the OpenShift worker exits because its entrypoint invokes the missing `cfo.run` module, so the deployed worker cannot process events or handle Slack Socket Mode approvals. AC-1 was unverified under the Git-and-file-reading restriction.
- **Date:** 2026-11-20 15:28

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/agent/graph.py:223 — Reconciliation ignores the extracted invoice number and stores the transaction’s expected number. A document that mentions the expected number anywhere can be attached as that invoice if the extracted total matches, even when the model identifies it as a different invoice.

MAJOR backend/cfo/agent/llm_tasks.py:133 — A malformed vLLM response with a truthy scalar `lines` value raises `TypeError` outside the exception handler. This aborts reconciliation before the agent can try another document or ask the employee.

MAJOR backend/cfo/agent/graph.py:273 — Escalation filters for the transaction only after fetching the latest 500 decisions globally. Once newer decisions displace its history, a reminder starts over at the first recipient and can reopen an exhausted escalation.

MAJOR backend/cfo/agent/graph.py:399 — A Slack delivery failure leaves a pending approval with no message or retry. A transient Slack API failure can strand the approval and leave the CEO without buttons to resume it.

MAJOR backend/cfo/agent/graph.py:423 — If Postgres-backed invoice attachment refuses an approved request, the agent still records the decision as `EXECUTED`, resolves the approval, and sends the usual success message, even though the invoice was never attached.

MAJOR backend/cfo/agent/graph.py:448 — A CEO rule requiring `APPROVAL` for an anomaly alert is recorded as the policy level, but the agent sends the alert immediately without requesting approval or interrupting.

MINOR backend/cfo/agent/graph.py:323 — The reminder branch sends to the previous recipient even when HR now marks them as on vacation, delaying escalation to an available manager by another reminder interval.

VERDICT: Changes needed. At HEAD, the OpenShift worker exits because its entrypoint invokes the missing `cfo.run` module, so the deployed worker cannot process events or handle Slack Socket Mode approvals. AC-1 was unverified under the Git-and-file-reading restriction.
