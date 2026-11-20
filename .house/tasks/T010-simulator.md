---
id: T010
title: December event simulator with speed control
status: todo
owner: null
branch: null
depends_on: ["T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_simulator.py"
    timeout: 900
protected: []
---
## Objective
`cfo/sim/`: replays `data/generated/events/` (December 2025) into Postgres as Events at `SIM_SPEED` (sim-seconds per real second), with control: start, pause, jump to date, step one event (CLI + functions the API can call).

## Out of scope
Agent handling of events.
