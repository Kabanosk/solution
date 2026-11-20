# Review T025

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (15/40 today)
- **Base:** 2c672d88d8052637e0184a816b9845504d9d04fb
- **Head:** b9fadf4c6466058dfa7264645848f5fa0051250d
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Changes requested. AC-1 is satisfied by inspection. The team-cluster deployment and judge evidence were not verified.
- **Date:** 2026-11-20 22:42

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR docs/deploy.md:90 — The apply creates an unauthenticated public MLflow Route before the delete removes it. On updates, an already-running server can be exposed during that window, and an interrupted apply or failed delete can leave the Route active. Exclude the Route from the default kustomization and use the documented port-forward for UI access.
MAJOR docs/deploy.md:89 — AC-2 specifies `oc apply -k deploy/openshift`, but that directory has no `kustomization.yaml` at the reviewed commit. The guide uses `deploy/`, where the entry point exists. Align the acceptance command and the documented entry point.
VERDICT: Changes requested. AC-1 is satisfied by inspection. The team-cluster deployment and judge evidence were not verified.
