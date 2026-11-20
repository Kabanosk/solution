# Review PR2

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** edee87e6160295a47350c8eb534bb87e072a3286
- **Head:** dff7cc124870575421f63690e9b85edbcbe10570
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes. The local stack exposes organizer data to reachable hosts, and the documented OpenShift setup can leave Postgres unseeded. No vLLM or Slack Socket Mode runtime defect was identified in the scoped diff.
- **Date:** 2026-11-20 15:29

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR docker-compose.yml:19 — `docker compose up files` publishes the unauthenticated file server on all host interfaces. Anyone who can reach port 8080 can download the organizer’s bank statement and any other files under `./data`.
MAJOR docs/cluster-setup.md:45 — The documented apply and rollout commands can succeed with an empty `sample-data` PVC. On a fresh OpenShift deployment, the seed Job then gets HTTP 404 and exits before migrating or loading Postgres. The guide omits the step that uploads the required CSV.
MINOR db/init.sql:6 — Running the import a second time appends every CSV row again, so totals queried through `bank_transactions` double. The current OpenShift seed Job uses a separate loader, so this affects manual imports through this script.

VERDICT: Request changes. The local stack exposes organizer data to reachable hosts, and the documented OpenShift setup can leave Postgres unseeded. No vLLM or Slack Socket Mode runtime defect was identified in the scoped diff.
