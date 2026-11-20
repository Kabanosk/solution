---
name: house-submit
description: Submission for optimize contests - choose exploit/explore/probe, apply the endgame rule, submit, log local vs leaderboard score.
disable-model-invocation: true
---
Follow `/home/kabanosk/house/contests/collabothon-2026/.house/protocols/submissions.md` and `/home/kabanosk/house/contests/collabothon-2026/.house/playbooks/optimize.md`.
Candidate: `$ARGUMENTS` (commit / file) or the best untested candidate from the latest reports.
Endgame or `auto_submit: false` → ask the human first. After submitting, log the row in `submissions.csv` and
report the updated local↔leaderboard agreement.
