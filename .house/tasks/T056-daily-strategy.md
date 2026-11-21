---
id: T056
title: Daily CEO strategy briefing and growth scenarios
status: review
owner: codex
branch: null
depends_on: []
acceptance:
  - id: AC-1
    run: "python -m pytest backend/tests/test_strategy.py backend/tests/scout/test_strategy_loop.py"
  - id: AC-2
    run: "npm --prefix dashboard run typecheck"
  - id: AC-3
    run: "npm --prefix dashboard test"
---
## Objective
Daily persistent Strategy task combining sales trends, operating cash surplus, owner income and expansion readiness with relevant public news and explicit missing targets. Show the brief in existing task views; support manual daily review.

## Contract
Job strategy_review, task loop strategy, one review per Berlin calendar day. Outcome contains a readable note; strategy_briefing evidence contains structured growth, news and scorecard data. Public news is untrusted data. No messages or financial actions are sent/executed.

## Context
Feedback session supplied by the user on 2026-11-21. Canonical house executable/state paths are unavailable on this machine; local repository .house used for task tracking. Backend delegated to strategy_backend, integration and dashboard handled by coordinator.
