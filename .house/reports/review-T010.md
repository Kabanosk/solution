# Review T010

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** abf541895d834dba8f2ff9dc549569103e1727de
- **Head:** a381502e4a04fd49aca6d7aeb1ff26661ea10647
- **Status:** ok
- **Blockers:** 1
- **Majors:** 1
- **Minors:** 1
- **Nits:** 0
- **Verdict:** The simulator API fails as packaged for OpenShift, before vLLM or Slack Socket Mode are involved. AC-1 was not run under the git-and-file-reading-only review constraint.
- **Date:** 2026-11-20 15:28

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER backend/cfo/sim/control.py:25 — Every simulator API call reads `/app/data/generated/events/december.jsonl`. The OpenShift image does not include that file and the API deployment does not mount it, so status, start, pause, and step fail with `FileNotFoundError` even when Postgres is healthy.
MAJOR backend/cfo/sim/engine.py:221 — A single step writes every event sharing the next timestamp. While playback is running, the same call also writes any overdue events before stepping. Both the CLI and API can therefore advance multiple events on one step.
MINOR backend/cfo/sim/engine.py:190 — `start --speed nan` passes the positivity check and commits NaN to Postgres. The status calculation then raises `ValueError`, and an active worker fails on its next tick.

VERDICT: The simulator API fails as packaged for OpenShift, before vLLM or Slack Socket Mode are involved. AC-1 was not run under the git-and-file-reading-only review constraint.
