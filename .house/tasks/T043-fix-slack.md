---
id: T043
title: "Review fixes: Slack adapter call shape, private answers, level labels"
status: review
owner: coder
branch: task/T043-fix-slack
depends_on: ["T011"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_slack.py backend/tests/test_slack_fixes.py"
    timeout: 600
protected: []
---
## Objective
Fix the Slack findings of the Codex review (`$HOUSE_DIR/reports/review-fixes.md` items 4, 21 and P3 Slack), tests
first in `backend/tests/test_slack_fixes.py`:
1. (4) `cfo/channels/slack.py` implements the agent's `Notifier` protocol exactly as the agent calls it (employee
   id string), resolving the Employee via the repository; a test calls it the way `cfo/agent/graph.py` and
   `cfo/run.py` do. If T011 already fixed this, keep the test only.
2. (21) Answers to `app_mention` in a channel go to the CEO privately (DM or ephemeral), never into the channel;
   the channel gets at most "answered you in DM".
3. (P3) Error/refusal messages for approvals carry a level label like every other bot message; the HR CSV
   `slack_id` column is either read into the employee mapping or removed from `docs/slack-setup.md`.

## Context
Starts after T011 is merged. Source: `$HOUSE_DIR/reports/review-T008.md`. Confirm each finding at HEAD.

## Out of scope
Agent graph logic (T041), API (T046).
