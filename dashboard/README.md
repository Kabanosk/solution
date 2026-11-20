# Lantern dashboard

Next.js, React, TypeScript and shadcn/ui dashboard for the CFO agent. Company records are loaded on the server at request time from the existing Postgres service. No statement, employee directory, salary fixture or balance is bundled into the application image or browser JavaScript.

## Data sources

- Balance: sum of all bank bookings, using Postgres NUMERIC arithmetic.
- Year and Q4 expenses/inflows: bank transactions in the latest booking year; Q4 starts in October. All recorded inflows/outflows count, including partner investments.
- Directory: `employees` joined to `teams`. Each salary is the sum of the latest recorded salary month matched by employee name. Missing matches display **Not recorded**.
- Payroll and social contributions: latest month with salary bookings. These are historical records, not a future schedule.
- Agent jobs: latest 50 `decisions`, enriched with `approval_requests`, attributed `llm_usage` and linked `events`. Pending approvals and undocumented invoice-reference payments are counted from their tables.
- Liquidity forecast: the documented `GET /v1/forecast/liquidity` on the internal CFO API. If unavailable, the chart shows a message; no invented forecast is substituted.

Database queries use a repeatable-read, read-only transaction, a bounded connection pool and statement timeouts. Money remains integer cents until conversion for display/charting. The dashboard never writes company records. **Refresh records** loads current values from the server.

## Agent jobs

Open `/#agent-jobs` for the jobs list. Search by title, action, actor, job ID or linked event ID; combine search with a status filter. Each job links to `/#agent-jobs/<id>` with its reasoning, approval request, recorded activity and model usage. Browser Back returns to the list. The previous `/#investigations` link remains supported. There are no pipeline stages.

The current database stores action outcomes rather than job lifecycles. This first view uses one decision per job, preserving the decision ID. `executed` maps to Completed; a pending approval maps to Waiting for approval; rejected and escalated outcomes remain distinct. Unknown outcomes are Recorded. A failed model call does not override a successful action. Model time is the sum of attributed call latency, not elapsed job duration. Missing telemetry stays missing. `llm_usage` attribution is best-effort and links an event's calls to its final decision, so it does not describe every action or every execution step.

For real queued/running states, retries and job duration, the agent should write a dedicated **`agent_jobs`** lifecycle table: `id`, `trigger_event_id`, `status`, `created_at`, `started_at`, `finished_at`, `last_heartbeat_at`, `attempt`, `error_summary`. Add `job_id` attribution to decisions and model usage so several decisions can belong to one job and retries remain distinguishable. These remain proposed additions; active jobs are not inferred from simulator events. Running and queued examples currently exist only in the explicitly labelled development preview.

### Job log

Each job detail page includes a timestamped **Job log** with **Refresh log**, loading, empty and retry states. Entries are loaded on demand from the dashboard's authenticated `GET /api/jobs/<decision-id>/log`, separate from the jobs list. This returns `{ "entries": [...] }` with `Cache-Control: private, no-store`; unknown decisions return 404, invalid IDs 400, unauthorized requests 401 (or the existing proxy's unconfigured-auth 503), and unavailable storage 503. This is a dashboard route, not a new CFO backend endpoint.

The agent writes factual action messages into the existing Postgres JSONB field **`decisions.payload.job_log`**. No schema migration, extra column or new table is required. The LangGraph checkpoint accumulates the actions before a decision exists; once a node creates a decision, its available history is saved. Later nodes atomically merge that history with existing entries. Stable entry IDs prevent duplicate checkpoint entries and preserve failures recorded before a retry. Approval interrupts are not errors, and approval responses extend the checkpoint's history. A node error after a decision exists saves a generic error entry and rethrows the original exception. Other payload keys are preserved. Logs contain up to 500 entries, with messages capped at 2,000 characters, in execution order.

Each new entry has `id` (opaque stable identifier), `ts` (UTC timestamp), `level` (`info`, `warning`, `error`), `source` (action/node name) and `message`. The reader also accepts legacy entries without IDs. Logs describe observed actions, not internal model reasoning, prompts or full document bodies. Persistence is best-effort: a log-write failure never repeats or blocks a business action. Decisions produced by the same graph invocation share the history available at their last update. Failures before any decision exists cannot appear as a decision-backed job. Historical decisions without `job_log` show an empty log; no history is invented or backfilled. New logs require rolling out the updated agent backend. The dashboard remains read-only.

## Local development

Requires Node.js 22.18+. For a UI preview without database or cluster access, create `dashboard/.env.local` containing `DASHBOARD_PREVIEW=1`, then run from the repository root:

```sh
npm --prefix dashboard ci
npm --prefix dashboard run dev
```

Open [localhost:3000](http://localhost:3000). Next.js Fast Refresh updates the page as you edit files. Preview mode displays a sample-data banner and uses invented records, including chart, payroll and agent job examples. It works only in development; production always requires configured authentication and real services.

Light is the default theme. Use the theme button in the top bar to switch between light and dark; your choice is saved in this browser and restored before the page paints.

The cash forecast supports drag-to-pan on both axes. Hover over the plot and hold Ctrl while scrolling up/down to zoom in/out around the pointer; no click is needed. Click or tab into the chart, then use Ctrl + / Ctrl − to zoom and arrow keys to pan. Double-click, Ctrl 0 or **Reset** fits the full forecast. Zoom and pan buttons provide the same controls without dragging; browser shortcuts outside the forecast keep working normally. On narrow screens, the surrounding scrollbars let you reach the whole chart.

For real records, copy `.env.example` to `.env.local`, keep `DASHBOARD_PREVIEW=0`, and fill the server-only Postgres connection fields, `CFO_API_URL` and dashboard login. Never use `NEXT_PUBLIC_` for these values or commit credentials. Enter the configured login in the browser. For access to the shared cluster services, run each port-forward in its own terminal:

```sh
oc port-forward svc/postgres 5432:5432
oc port-forward svc/api 8000:8000
```

See `docs/cluster-setup.md` for the existing Postgres Secret. Passwords are separate connection parameters, so URL-special characters are handled correctly.

## Verification and production

```sh
npm --prefix dashboard test
npm --prefix dashboard run typecheck
npm --prefix dashboard run build
npm --prefix dashboard start
```

Builds require no database, credentials or company data. The home page and `/api/dashboard` render dynamically; responses use `Cache-Control: private, no-store`. Both require authentication, including server-rendered React requests. Missing login configuration fails closed. `/healthz` returns only process health and is available for cluster probes.

`Containerfile` packages Next.js standalone output with local fonts and static assets. It supports OpenShift's arbitrary non-root UID and a read-only root filesystem. See `deploy/openshift/dashboard/README.md` for deployment.

## Access scope

The TLS dashboard route uses a shared HTTP Basic login supplied by Secret `dashboard-auth`. It is a read-only workspace login, not per-employee IAM. Displayed company data is necessarily transmitted to the authenticated viewer. Do not publish screenshots or exports containing confidential records.

The backend API has a separate existing Route and its own access policy; dashboard authentication does not protect that Route. Database and source-file services remain internal.

See `THIRD_PARTY_NOTICES.md` and the font licenses under `assets/fonts/`.

## LLM usage page

Open `/#llm` or choose **LLM** in the workspace navigation. The page shows recorded requests and tokens, failures, estimated token counts, latency percentiles, busy-time and allocated cost estimates, cost per agent action, hosted-price comparisons and the latest model-server snapshot. Choose 24 hours, 7 days or 30 days and use **Refresh** to load a new snapshot. Times are UTC; costs keep the API's six decimal places. Busy-time includes overlapping requests; allocated cost includes idle time and the backend's token-share assumption. These are estimates, not billed charges.

The authenticated dashboard route `GET /api/llm-usage?days=1|7|30` fetches the existing CFO API endpoints `/v1/llm/usage` and `/v1/llm/usage/live` on the server, using `CFO_API_URL`. Optional `CFO_API_TOKEN` supplies a server-only bearer token when the API or its gateway requires one. Never use `NEXT_PUBLIC_` for this token. Responses use `Cache-Control: private, no-store`. Invalid ranges return 400; missing authentication returns 401 (or the existing proxy's unconfigured-auth 503); both upstream sources being unavailable returns 503. One source failing does not hide the other source's data.

The model snapshot displays its sample timestamp and the backend's stale indicator; it is refreshed on demand, not presented as a continuously streaming measurement. Missing gauges display **Not reported**. A successful zero-request report has an explicit empty state, while unavailable or malformed API responses display unavailable/retry states. No production metrics are fabricated. `DASHBOARD_PREVIEW=1` supplies explicitly labelled fictional usage only under `next dev`, using the same development-only guard as the rest of the dashboard.
