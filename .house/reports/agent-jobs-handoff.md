# Agent jobs and LLM integration

Completed on `feature/agent-jobs-logs`, based on main `07e3251`. The original saved checkpoint is `02e78d1`; job-log retry fixes are `3bac36e`, and the subagent's LLM page is integrated as `2790979`. Nothing was pushed or deployed.

## Delivered

- Compact Linear-inspired Agent jobs list with filters, search, direct links, details and no stages.
- Timestamped job log on the detail page, fetched on demand through authenticated `/api/jobs/[id]/log`, with refresh, loading, empty, error and retry states.
- Existing `decisions.payload.job_log` JSONB storage, requiring no migration or new column. Stable entry IDs and atomic merges preserve failed attempts across retries without duplicating checkpoint entries. Other payload keys remain intact. Logs retain at most 500 entries and 2,000 characters per message.
- LangGraph action summaries before and after approval interrupts/resumes. Generic errors exclude raw exception details; logging failures do not replay business actions. Failures before a decision exists are outside this decision-backed view.
- LLM page in sidebar, mobile navigation and search: now an interactive Granite chat per the user's corrected scope, with a one-model dropdown, streamed replies, conversation context, stop/retry/new chat and copy controls. See T048; the earlier telemetry API remains available.
- Updated `dashboard/README.md`, `docs/api.md`, task T047 and its report.

## Validation

- Job logs, repository and agent: **31 passed**, against an isolated PostgreSQL 17 Docker instance on localhost:55432.
- Decision/approval API and tracing regression selection: **25 passed, 31 skipped**. Skips require generated company data or the optional MLflow extra; these were not supplied for this change.
- Dashboard test suite and TypeScript checks passed after integration. `git diff --check` passed.
- Log viewer browser checks passed: on-demand API, no-store headers, invalid/unknown IDs, refresh, unavailable/retry, empty logs, bounds, escaped text, scrollable output, unique IDs, both themes, 320–1440 px and no page errors. Script: `/tmp/lantern-job-log-browser-check.cjs`.
- LLM checks passed in the subagent's isolated worktree; see `reports/T047.md`.
- Integrated browser check passed on localhost:3000, including desktop/mobile navigation between job logs and LLM and LLM range changes. Script: `/tmp/lantern-integrated-check.cjs`.

## Runtime and limitations

The shared development-preview server remains at localhost:3000. All displayed fixtures are invented and labelled. Production log collection requires rolling out the updated backend; historical decisions are not backfilled. Real queued/running job lifecycles still require the separately proposed job registry. Live cluster telemetry was not tested. The temporary PostgreSQL test container is stopped after verification. The Python test environment is isolated under `/tmp/lantern-backend-test-env`.

T048 adds actual model chat to this local server: company records remain labelled fixtures, but chat calls Granite on OpenShift through a local model-pod port-forward on 18080. Real streaming and two-turn context were verified in the browser. The default deployment chat endpoint remains the in-cluster address supplied by the user.
