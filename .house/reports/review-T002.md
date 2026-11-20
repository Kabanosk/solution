# Review T002

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 944b48309deadfad991d3dd7f9cbd1007d64df20
- **Head:** 5ee814de72bb6699b5d1d9a9ff2522c484dfcead
- **Status:** ok
- **Blockers:** 0
- **Majors:** 4
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Fix the Postgres reset and replay bugs and the December data leak before the demo. These runtime findings follow from static review. Acceptance commands and live OpenShift, vLLM, and Slack Socket Mode checks were not run.
- **Date:** 2026-11-20 15:27

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/data/load.py:27 — `--reset` clears the replayed data but leaves the Postgres `sim_state` cursor intact. Rerunning the OpenShift seed job after a completed demo leaves December empty while the simulator still reports that replay is finished.
MAJOR backend/cfo/data/load.py:76 — Replaying an already-applied supplier payment overwrites `documented` and `invoice_id` with their defaults. The existing invoice still points to the payment, so the guard on line 79 skips relinking and Postgres now reports the payment as undocumented.
MAJOR backend/cfo/data/generate.py:150 — The loader seeds invoices for December payments before replay, including invoices issued in December. For example, AS-74281 is paid December 27 and gets an issue date between December 18 and 24, yet its amount is already visible in the November seed.
MAJOR backend/cfo/data/generate.py:124 — A second run recursively deletes the entire `--out` directory whenever `scenario.json` exists, including unrelated files and any source CSV stored there. Deletion happens before source validation.
MINOR backend/cfo/data/metrics.py:93 — December online orders grow by about 3% even though the requested cloud-spike scenario calls for flat orders.
VERDICT: Fix the Postgres reset and replay bugs and the December data leak before the demo. These runtime findings follow from static review. Acceptance commands and live OpenShift, vLLM, and Slack Socket Mode checks were not run.
