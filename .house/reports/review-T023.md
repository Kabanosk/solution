# Review T023

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 2d39fbd3d7c912102d665abdca334ea11873ccec
- **Head:** f611a824d9eaea0e01916a7b48c2e5faa0b3923d
- **Status:** ok
- **Blockers:** 1
- **Majors:** 3
- **Minors:** 0
- **Nits:** 0
- **Verdict:** The checked-in manifests cannot run the end-to-end OpenShift deployment. Findings were confirmed by static inspection at HEAD.
- **Date:** 2026-11-20 15:31

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER deploy/openshift/app/worker.yaml:20 — The entrypoint runs `python -m cfo.run`, but that module is absent at HEAD. The OpenShift worker will crash-loop before it can start the simulator or connect to Slack Socket Mode.  
MAJOR deploy/openshift/app/api.yaml:17 — The API starts in a separate process with no registered approval resolver. Approve and reject requests return HTTP 503 even with a healthy Postgres instance.  
MAJOR deploy/openshift/app/api.yaml:55 — The public Route exposes an API with no authentication. Anyone who can reach the Route can read the deployed financial data and start, pause, or step the simulator.  
MAJOR deploy/openshift/app/configmap.yaml:12 — The vLLM hostname and served model are still placeholders at HEAD. Applying the checked-in manifests leaves LLM calls unable to reach the existing endpoint.

VERDICT: The checked-in manifests cannot run the end-to-end OpenShift deployment. Findings were confirmed by static inspection at HEAD.
