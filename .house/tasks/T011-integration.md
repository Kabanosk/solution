---
id: T011
title: Wire everything: golden path end-to-end
status: merged
owner: coder
branch: task/T011-integration
depends_on: ["T005", "T007", "T008", "T010", "T016", "T019", "T020"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_golden_path.py"
    timeout: 900
  - id: AC-2
    manual: "Golden path from $HOUSE_DIR/demo-flow.md runs on the cluster with the real model and Slack; judge records evidence"
protected:
  - backend/tests/test_golden_path.py
---
## Objective
One process/entrypoint (`python -m cfo.run`) runs simulator → agent → notifier, with forecast and cost-metric checks scheduled; `tests/test_golden_path.py` (written first, protected) drives demo-flow steps 1–7 with FakeLLM and fake Slack and asserts decisions and levels.

## Out of scope
New features.
