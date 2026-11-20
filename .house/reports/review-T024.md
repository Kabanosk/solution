# Review T024

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 8502ff46766ba97c2a7b98bcaa0969bb98b6912b
- **Head:** cb60ccbcb26ab1a1538f6ac06f10f97ea2a217cf
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Request changes. The unauthenticated Route is a real deployment issue. OpenShift startup and AC-1 were not verified under the git-and-file-reading constraint.
- **Date:** 2026-11-20 15:31

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR deploy/openshift/mlflow/mlflow.yaml:63 — The OpenShift Route exposes the tracking UI and write APIs without authentication. Anyone who can reach the Route can download logged model artifacts, create runs, or delete runs. Edge TLS encrypts traffic but does not restrict access.

VERDICT: Request changes. The unauthenticated Route is a real deployment issue. OpenShift startup and AC-1 were not verified under the git-and-file-reading constraint.
