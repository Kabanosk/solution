# Review T020

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 52cc0688d90ece83b0665e2303d2df1c8741544c
- **Head:** 8deee3ed65532c9cd348ba36b3821ea0d8a4ffdb
- **Status:** ok
- **Blockers:** 1
- **Majors:** 3
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes. The checked-in OpenShift deployment cannot answer REST approvals or serve simulator controls. AC-1 was not run under the Git-and-file-reading-only restriction.
- **Date:** 2026-11-20 15:31

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER backend/cfo/api/deps.py:54 — The OpenShift API starts Uvicorn in a separate pod and never registers an approval resolver. With Postgres reachable, both approval actions return `503 agent_unavailable` even if the vLLM endpoint and Slack Socket Mode are healthy.  
MAJOR backend/cfo/api/routers/approvals.py:47 — The handler trusts the caller-supplied `by` as proof of identity. Once a resolver is wired in, anyone who can reach the OpenShift Route can copy an approval ID and its `approver_id` from the list endpoint and approve or reject as the CEO.  
MAJOR backend/cfo/api/deps.py:75 — All four simulator routes load `data/generated/events/december.jsonl`, which the backend image does not include and the API deployment does not mount. They return HTTP 500 on the checked-in OpenShift deployment.  
MAJOR backend/cfo/api/stream.py:115 — An idle SSE connection holds a shared Starlette worker-thread slot until it has data or a keepalive to yield. Enough open streams can exhaust the pool and delay synchronous API requests, including health probes.  
MINOR backend/cfo/api/stream.py:111 — The initial SSE frame has no resume ID. If the connection drops before the first data frame, reconnecting starts from a fresh database cursor and silently skips rows committed during the interruption.

VERDICT: Request changes. The checked-in OpenShift deployment cannot answer REST approvals or serve simulator controls. AC-1 was not run under the Git-and-file-reading-only restriction.
