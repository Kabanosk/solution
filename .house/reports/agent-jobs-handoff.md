# Agent jobs handoff

Saved at the user's request before disconnecting the network. Integration branch: `feature/agent-jobs-logs`, rebased onto main `07e3251` without conflicts. Work is saved as a checkpoint, not deployed.

Implemented:
- Investigations renamed to Agent jobs, with compact Linear-inspired rows, filters, search, direct links and details. No stages.
- Factual job log recording in `decisions.payload.job_log`, using the existing JSONB column. No database migration.
- LangGraph node wrapper carries history through checkpoints and approval resume, records generic errors after a decision exists, and treats interrupts as control flow.
- Authenticated dashboard log route `/api/jobs/[id]/log`, detail-page log viewer, refresh/retry/empty states and development fixtures.
- Updated `dashboard/README.md` and `docs/api.md`.

Validation completed:
- Dashboard tests and TypeScript checks passed during implementation.
- Five backend job-log tests passed, including an actual LangGraph interrupt/resume test without Postgres.
- The compact jobs UI passed browser checks at 320–1440 px, both themes, navigation, filtering and reduced motion before adding the log viewer.
- The new preview log endpoint returned HTTP 200 after a transient development hot-reload error.

Resume with:
- Browser verification of the new log viewer, including empty/error/retry states.
- Real PostgreSQL verification of payload updates and existing agent/approval regression tests. The isolated test environment is `/tmp/lantern-backend-test-env`; repository runtime dependencies are not installed globally. The first broader test collection failed because the temporary environment had MCP 2.x; installation of the locked MCP 1.30.0 and Postgres checkpointer was subsequently requested. Check its final state before running those tests.
- Review the bounded log snapshot behavior on graph retries; a later snapshot can replace an error recorded outside a failed node's checkpoint.
- Delegate the LLM page described in `.house/tasks/T047-llm-page.md`. No subagent was launched before the stop request.

No push was requested for this checkpoint. The existing shared dashboard development server was left running at localhost:3000.
