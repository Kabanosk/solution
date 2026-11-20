# Review T044

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (5/40 today)
- **Base:** 471e51a16abbff75de3bed67f0c741334f6b2364
- **Head:** b2c9e3c4fb65eef4d8861042258c8daa63c37495
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. Static review only. AC-1 was not run under the Git-and-file-reading restriction.
- **Date:** 2026-11-20 16:15

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/mcp_servers/workstations.py:81 — An employee-directory symlink to another workstation under the same root passes the parent check. File operations then read the other workstation’s company folders while the audit row names the requested alias.
MAJOR backend/cfo/sim/engine.py:190 — A valid explicit speed cannot recover a running simulator with a persisted `NaN` speed. `_elapse` uses the old speed before `start` installs the replacement, so `timedelta` raises `ValueError`.
MINOR backend/cfo/mcp_servers/workstations.py:99 — On supported Python 3.13+, non-strict `resolve()` can leave a symlink loop unresolved. The containment check can then pass, causing a successful-access audit and a filesystem error or `not_found` instead of the denial asserted by the new tests.
VERDICT: Changes requested. Static review only. AC-1 was not run under the Git-and-file-reading restriction.
