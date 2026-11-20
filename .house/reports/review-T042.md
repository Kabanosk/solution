# Review T042

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (5/40 today)
- **Base:** 8ad6df8ff15f6379d82b4976f776f98870cb2391
- **Head:** b49ea8463600aad0605e2e6429485102cdb135c6
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Changes requested. The documented CSV upload fails on a fresh deployment. Acceptance commands were not run under the review’s tool restrictions.
- **Date:** 2026-11-20 16:14

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR docs/cluster-setup.md:49 — On a fresh namespace, the upload step assumes `deploy/files` and the `sample-data` PVC already exist. The first apply appears in step 7 and also creates the seed Job. Apply the file-server resources before uploading the CSV.

MAJOR docs/cluster-setup.md:50 — `oc cp` expects a pod name in `[namespace/]pod:path`. It interprets `deploy/files:/data/...` as pod `files` in namespace `deploy`. Resolve the file-server pod name first or use the documented `oc exec -i` upload as the primary command.

VERDICT: Changes requested. The documented CSV upload fails on a fresh deployment. Acceptance commands were not run under the review’s tool restrictions.
