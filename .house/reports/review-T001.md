# Review T001

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 99a79cb9fcfa1e9da4434bab2232c89ce72541c8
- **Head:** 74d5c44060ec662e6989bd6a58f2792d4908c79f
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 2
- **Nits:** 0
- **Verdict:** Changes requested. The reconciliation defects affect deployed Postgres, and malformed vLLM tool responses can abort a turn. No Slack Socket Mode failure was identified in the scoped diff. Acceptance commands and live deployment were not run.
- **Date:** 2026-11-20 15:27

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/db/repo.py:105 — Replaying a bank transaction upserts `documented=False` and `invoice_id=NULL` over an existing reconciliation. The simulator skips relinking because the invoice already has a transaction ID, so Postgres reports the payment as undocumented.
MAJOR backend/cfo/db/repo.py:133 — Updating an invoice’s `transaction_id` leaves the previous payment marked documented. The current invoice attachment path reuses the invoice ID for an existing number, so attaching that number to a second payment leaves both payments marked documented against one invoice.
MAJOR docker-compose.yml:9 — The default `5433:5432` mapping exposes Postgres on all host interfaces with the published `cfo` credentials. Anyone who can reach that port can read or modify the database.
MINOR backend/cfo/llm.py:108 — A tool response whose arguments decode to valid non-object JSON, such as `[]` or `null`, raises a Pydantic validation error and aborts the chat turn. The fallback covers JSON syntax errors only.
MINOR backend/tests/test_repo.py:24 — The fixture drops a fixed schema on the configured `DATABASE_URL`. Concurrent runs can delete each other’s fixtures, and a run against a shared database can delete an existing schema with that name.
VERDICT: Changes requested. The reconciliation defects affect deployed Postgres, and malformed vLLM tool responses can abort a turn. No Slack Socket Mode failure was identified in the scoped diff. Acceptance commands and live deployment were not run.
