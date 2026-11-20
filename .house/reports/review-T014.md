# Review T014

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** b7e557f58c0ce7bfcc3f10937601a29b5feecfbd
- **Head:** c920feb3a19718cd66eec20f1d99611a2906f326
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. The backtest leaks holdout data and the feature loader crashes against an empty Postgres table. No vLLM or Slack Socket Mode dependency is involved. AC-1 was not run per the Git-and-file-reading restriction.
- **Date:** 2026-11-20 15:28

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/forecast/features.py:230 — Shutdown and due-day features are inferred from the full transaction history before the current backtest splits off its holdout. Held-out bookings can therefore change both training and evaluation features, making the reported holdout error unreliable. Infer these features from the training window for each split.

MINOR backend/cfo/forecast/features.py:233 — An empty Postgres `transactions` table leaves the date bounds as `NaT`, so `load_daily_features()` raises `ValueError` when building the date range. This can occur on OpenShift before the database is seeded.

VERDICT: Changes requested. The backtest leaks holdout data and the feature loader crashes against an empty Postgres table. No vLLM or Slack Socket Mode dependency is involved. AC-1 was not run per the Git-and-file-reading restriction.
