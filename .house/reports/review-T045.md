# Review T045

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (1/40 today)
- **Base:** 471e51a16abbff75de3bed67f0c741334f6b2364
- **Head:** b948d500ff60d6fe559567dd3f143cf86872504d
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. Ensure the API serves corrected RMSE for existing deployments. AC-1 was not run under the review’s tool restrictions.
- **Date:** 2026-11-20 16:02

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/api/services.py:165 — The API still accepts existing local and MLflow models with pre-fix holdout RMSE. Deploying this change alone leaves their forecast intervals based on the leaked score until those artifacts are replaced.
MINOR backend/tests/test_forecast_leakage.py:66 — The test claims to verify that a training shutdown carries into the next year’s holdout, but its holdout contains no days in that shutdown window. It would pass even if holdout shutdown flags were always zero.
VERDICT: Changes requested. Ensure the API serves corrected RMSE for existing deployments. AC-1 was not run under the review’s tool restrictions.
