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

## LLM chat

Open `/#llm` or choose **LLM** for an interactive chat in the compact Linear-inspired interface. Select the model from the dropdown (currently only `modelcar-granite-4-0-h-tiny`), send with Enter or the arrow button, use Shift+Enter for a new line, and stop or retry a response as needed. Replies stream from the actual model. **New chat** clears the conversation; **Copy response** copies an answer. The conversation stays in memory while switching workspace pages and is cleared by a browser reload. It is not stored in Postgres or local storage. The model receives only the conversation you send, not company database records or CFO tools.

`GET /api/chat` returns the allowed model list. Authenticated `POST /api/chat` accepts `{ "model": "modelcar-granite-4-0-h-tiny", "messages": [{ "role": "user", "content": "hi" }] }`. The server calls the OpenAI-compatible `/chat/completions` endpoint at `LLM_CHAT_BASE_URL` with streaming enabled. The default is `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1`. Optional `LLM_CHAT_API_KEY` provides a server-only bearer token; the supplied internal endpoint does not require one. Neither configuration value is exposed to browser code. Preview mode still calls the actual model; no fabricated chat responses are substituted.

Outside OpenShift, port-forward the model pod's port 8080 and set `LLM_CHAT_BASE_URL=http://127.0.0.1:18080/v1` in the ignored `dashboard/.env.local`:

```sh
oc -n my-first-model port-forward pod/<model-pod-name> 18080:8080
```

Responses are private/no-store NDJSON events (`delta`, `done`, `error`), adapted from upstream SSE. Invalid models, malformed/oversized histories and client-supplied system/tool roles are rejected. Conversations are limited to 64 messages, 16,000 characters per message and 64,000 total characters, with a 256 KiB request-body cap. Generation uses a 120-second timeout and a 2,048-token output limit; stopping cancels the upstream request. Validation errors return 400; unavailable models return 502; initial upstream timeouts return 504; mid-stream interruptions are reported in the stream and UI. The existing login/preview authentication rules apply. Chat text and provider exception bodies are not written to application logs.

The earlier `GET /api/llm-usage?days=1|7|30` telemetry route remains available to API consumers, using server-only `CFO_API_URL`/`CFO_API_TOKEN`; the LLM page itself now displays chat.
