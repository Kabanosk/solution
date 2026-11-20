# Review T019

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** f144c32c064b61ed537653f2565f25fbc628d9fe
- **Head:** 61b266fbe7733fc43340c1b9ebcd32c6f387c3a3
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 3
- **Nits:** 0
- **Verdict:** Changes needed. Static review confirmed an OpenShift data exposure and a request-triggered 500. AC-1 and live integration were unverified under the requested git-and-file-reading restriction.
- **Date:** 2026-11-20 15:30

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/api/app.py:50 — The OpenShift Route exposes these endpoints without authentication. Once Postgres is seeded, anyone who can reach the Route can read financial KPIs, decision payloads, and CEO policy rules.
MAJOR backend/cfo/api/services.py:104 — The forecast interval uses a holdout RMSE computed after shutdown and payment due-day features were inferred from the full transaction history. Holdout observations therefore influence the features used for training and evaluation, making the deployed interval unreliable.
MINOR backend/cfo/api/services.py:241 — `/v1/costs/by-team` reports zero costs when Postgres contains unlinked invoices but no bank transactions, even though those invoices are a documented cost source.
MINOR backend/cfo/api/services.py:239 — A single supplied date can invert the range after defaults are applied. For example, a `from` date later than the last booking returns HTTP 200 with an inverted range instead of the documented 422.
MINOR backend/cfo/api/services.py:225 — A valid `to=9999-12-31` advances the month iterator into year 10000 and returns HTTP 500 on OpenShift.
VERDICT: Changes needed. Static review confirmed an OpenShift data exposure and a request-triggered 500. AC-1 and live integration were unverified under the requested git-and-file-reading restriction.
