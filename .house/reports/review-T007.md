# Review T007

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** c9bb990c4544611b530acf059ccb684288d9e8b5
- **Head:** bdeb17c7e17fceef9cf47821f3d1870d994c5bcd
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. The generated +50% cloud case meets the detector thresholds by inspection. No deployment crash was identified, but the live Slack path has the attribution defect above. AC-1 and the deployed stack were untested under the Git-and-file-reading-only constraint.
- **Date:** 2026-11-20 15:29

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/agent/investigate.py:37 — The live Slack alert path calls the detector without invoices. Advertising metrics contain only an invoice number, so an ad-spend anomaly produces an alert with no contributing invoice lines even when those lines are in Postgres.
MAJOR backend/cfo/analysis/cost_metrics.py:204 — A cost increase from €100 to €150 with zero orders in both months is silently skipped because percentage growth from zero is undefined. This suppresses alerts when spending rises while activity remains at zero.
MINOR backend/cfo/analysis/cost_metrics.py:240 — The detector compares consecutive stored rows even when a calendar month is missing, so a January-to-March increase can be flagged and described as month-over-month growth.

VERDICT: Changes requested. The generated +50% cloud case meets the detector thresholds by inspection. No deployment crash was identified, but the live Slack path has the attribution defect above. AC-1 and the deployed stack were untested under the Git-and-file-reading-only constraint.
