---
id: T040
title: "Review fixes: data integrity of replay, reset and invoice attachment"
status: todo
owner: null
branch: null
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_data_integrity.py backend/tests/test_repo.py backend/tests/test_data.py backend/tests/test_mcp.py backend/tests/test_simulator.py"
    timeout: 900
protected: []
---
## Objective
Fix the data-integrity findings of the Codex review; each fix gets a failing test first in the new file
`backend/tests/test_data_integrity.py`. Items (details, file:line and source reviews in
`$HOUSE_DIR/reports/review-fixes.md`):
1. (item 9) Re-adding/replaying a bank transaction must not reset `documented` / `invoice_id` of an already
   reconciled transaction (`cfo/db/repo.py` add_transaction upsert, `cfo/data/load.py:76`).
2. (item 10) `cfo load --reset` also resets the simulator cursor (`sim_state`), so a reseed replays December again.
3. (item 11) Attaching an invoice number that is already linked to a different transaction is refused (clear
   error, no overwrite of that invoice or its lines); the documented check + update in
   `cfo/mcp_servers/company_db.py` runs under a row lock (`SELECT ... FOR UPDATE`) in one transaction. Same rule
   in `repo.py` when an invoice's `transaction_id` changes: the previous transaction must not stay documented.
4. (item 25) `load --reset` does truncate + load in ONE transaction (a failed load leaves the old data).
5. (P3) `cfo generate --out DIR` must not rmtree a directory it did not create: delete only its own generated
   subpaths, and validate the source CSV before deleting anything.

## Context
- Read each finding in `$HOUSE_DIR/reports/review-T001.md`, `review-T002.md`, `review-T004.md`, `review-T022.md` and
  confirm it at HEAD first; if a finding is wrong, say so in the report instead of "fixing" it.
- Tests use the shared local Postgres (docker compose, per-process schemas); keep the existing tests green.

## Out of scope
Agent graph, API, Slack, deploy YAML (other fix tasks T041–T046).
