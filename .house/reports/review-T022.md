# Review T022

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 26141762898910253d1f3ec721153cffc01f5fec
- **Head:** 6927effd993e75bdf3703b510dd284e01167dfe9
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 1
- **Nits:** 0
- **Verdict:** HEAD has OpenShift and Postgres failure cases on first deployment and reseed. The seed Job does not call vLLM or Slack Socket Mode. This was a static review, so AC-1 and live deployment were not run.
- **Date:** 2026-11-20 15:30

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR deploy/openshift/db/seed-job.yaml:39 — `cfo load --reset` preserves the Postgres `sim_state` replay cursor while deleting previously replayed events. After a reseed, the simulator skips those events, so a completed December run leaves the demo with no December events to replay.

MAJOR deploy/openshift/db/seed-job.yaml:39 — The loader commits `TRUNCATE` before inserting seed data. If loading fails or OpenShift terminates the Job after that commit, Postgres can remain empty or partially seeded if retries are exhausted.

MINOR deploy/openshift/db/seed-job.yaml:13 — The 15-minute deadline includes time spent waiting for the backend image. On a fresh OpenShift deployment, a slow first build can permanently fail the Job before seeding starts. Publishing the image and reapplying the same Job do not restart it.

VERDICT: HEAD has OpenShift and Postgres failure cases on first deployment and reseed. The seed Job does not call vLLM or Slack Socket Mode. This was a static review, so AC-1 and live deployment were not run.
