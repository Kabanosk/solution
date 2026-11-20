# Review T040

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (1/40 today)
- **Base:** 471e51a16abbff75de3bed67f0c741334f6b2364
- **Head:** 3111dd37464878b367321f9edfa0bc366e698088
- **Status:** ok
- **Blockers:** 0
- **Majors:** 5
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes. Replay and reset still have data-integrity gaps, and generation can delete existing files before rejecting an unusable source.
- **Date:** 2026-11-20 16:03

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/data/load.py:98 — Replay can replace an already reconciled payment’s `invoice_id` if its bank reference matches a different, unlinked invoice of the same amount. It can also move an invoice attached concurrently because the unlinked check happens before `add_invoice` acquires the invoice lock.
MAJOR backend/cfo/data/load.py:32 — Reset does not take the simulator’s replay lock. A tick can read the old cursor, resume after the reseed commits, and write a December event from that stale position into the freshly seeded database.
MAJOR backend/cfo/mcp_servers/company_db.py:144 — Attachment locks the payment before the invoice number, while `add_invoice` takes those locks in reverse order. A concurrent relink of that invoice to the same payment can deadlock and abort one writer.
MAJOR backend/cfo/data/generate.py:147 — Cleanup runs before checking that the CSV contains the seven historical invoice payments required by `rng.sample`. A CSV with both required December invoices but too few historical payments deletes the previous output before generation fails.
MAJOR backend/cfo/data/generate.py:134 — The presence of `scenario.json` is enough to recursively delete every matching directory name. In a user-owned output directory, that can remove unrelated files under paths such as `bank/` or `invoices/`.
MINOR backend/tests/test_data_integrity.py:248 — The successful reseed test converts every load exception into a skip, so SQL and transaction regressions can pass the acceptance run without exercising the success assertions.
VERDICT: Request changes. Replay and reset still have data-integrity gaps, and generation can delete existing files before rejecting an unusable source.
