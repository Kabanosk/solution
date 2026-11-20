# Review T044

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (4/40 today)
- **Base:** 471e51a16abbff75de3bed67f0c741334f6b2364
- **Head:** 38621d1d94d43e71303b85ed9b54ce9ff32100c3
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes before merging.
- **Date:** 2026-11-20 16:04

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/analysis/cost_metrics.py:211 — A zero-to-positive cost anomaly produces `Decimal("Infinity")`. The investigation agent converts it to a float and includes it in the alert payload, which PostgreSQL rejects as invalid JSONB. The new anomaly is detected but its alert cannot be saved.
MINOR backend/cfo/sim/engine.py:203 — `start()` validates only an explicit speed. With `SIM_SPEED=nan` and no speed override, a fresh simulator loads the invalid default and can persist the running state before `status()` fails.
VERDICT: Request changes before merging.
