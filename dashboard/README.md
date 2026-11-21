# Scout dashboard

Next.js, React, TypeScript and shadcn/ui dashboard for the CFO agent. Company records are loaded on the server at request time from the existing Postgres service. No statement, employee directory, salary fixture or balance is bundled into the application image or browser JavaScript.

## Data sources

- Balance: sum of all bank bookings, using Postgres NUMERIC arithmetic.
- Year and Q4 expenses/inflows: bank transactions in the latest booking year; Q4 starts in October. All recorded inflows/outflows count, including partner investments.
- Directory: `employees` joined to `teams`. Each salary is the sum of the latest recorded salary month matched by employee name. Missing matches display **Not recorded**.
- Payroll and social contributions: latest month with salary bookings. These are historical records, not a future schedule.
- Agent tasks (`agent_tasks`, grouped by status), task detail at `/tasks/SC-123` (activity log from `agent_task_steps`, approvals, audit rows), Audit log, Connections and Access policies; see `backend/scout/schema.sql`. The old `#investigations` hash opens Agent tasks.
- Approve / Reject, Connect Google and the dev demo panel (`SCOUT_DEMO=1`) call the API server-side through `/api/approve`, `/api/google` and `/api/demo` with `API_WRITE_TOKEN`; the token never reaches the browser.
- **Connect KSeF** (Connections page) posts to `/api/ksef` -> `POST ${CFO_API_URL}/v1/connections/ksef:connect` (write token server-side, not gated by `SCOUT_DEMO`) and polls the `ksef` connection row (`config.phase`, steps, invoices) while it runs.
- `/scout-icon.svg` and `/scout-icon.png` (512×512) are public, for the Slack bot avatar.
- Liquidity forecast: the documented `GET /v1/forecast/liquidity` on the internal CFO API. If unavailable, the chart shows a message; no invented forecast is substituted.

Database queries use a repeatable-read, read-only transaction, a bounded connection pool and statement timeouts. Money remains integer cents until conversion for display/charting. The dashboard never writes company records. **Refresh records** loads current values from the server.

## Local development

Requires Node.js 22.18+. For a UI preview without database or cluster access, create `dashboard/.env.local` containing `DASHBOARD_PREVIEW=1`, then run from the repository root:

```sh
npm --prefix dashboard ci
npm --prefix dashboard run dev
```

Open [localhost:3000](http://localhost:3000). Next.js Fast Refresh updates the page as you edit files. Preview mode displays a sample-data banner and uses invented records, including Pizzeria Amalfi agent tasks, activity logs, audit rows, connections and LLM usage; Scout chat still calls the real Scout API. It works only in development; production always requires configured authentication and real services.

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

Browser regression checks run against the local development preview in a separate terminal. Install Chromium once:

```bash
npx --prefix dashboard playwright install chromium
```

With `DASHBOARD_PREVIEW=1` enabled on the running dev server:

```bash
npm --prefix dashboard run test:e2e
```

Set `BASE_URL` for a non-default port, or `CHROMIUM_PATH` to reuse an installed browser. The suite covers navigation, chat prompts, draft reset, a controlled streamed reply, conversation persistence, usage ranges, hydration, icon/axis styling, and overflow in both themes at desktop, phone and landscape sizes. It saves screenshots under `artifacts/ui-cleanup/` (gitignored). It does not verify live Scout model/backend connectivity.

Builds require no database, credentials or company data. The home page and `/api/dashboard` render dynamically; responses use `Cache-Control: private, no-store`. Both require authentication, including server-rendered React requests. Missing login configuration fails closed. `/healthz` returns only process health and is available for cluster probes.

`Containerfile` packages Next.js standalone output with local fonts and static assets. It supports OpenShift's arbitrary non-root UID and a read-only root filesystem. See `deploy/openshift/dashboard/README.md` for deployment.

## Scout chat

Open `/#chat` (the old `/#llm` still works) or choose **Scout chat** in the sidebar. Send with Enter, Shift+Enter for a new line, stop or retry a response, **New chat** to clear it. The conversation stays in memory while you switch pages and is cleared by a reload; it is not stored by the dashboard.

Authenticated `POST /api/chat` accepts `{ "model": "modelcar-granite-4-0-h-tiny", "messages": [{ "role": "user", "content": "hi" }] }`, validates it (alternating user/assistant roles, 64 messages, 16,000 characters per message, 64,000 total, 256 KiB body) and proxies it server-side to the Scout API's `POST ${CFO_API_URL}/v1/chat` with `API_WRITE_TOKEN`. Scout answers with its tools (missing invoices, forecast, tasks, costs). The response is private/no-store NDJSON: zero or more `{"type":"step","text":"…"}` tool lines, then `delta`, then `done` (optionally with `task_id`) or `error`. Events are validated before they reach the browser. The page shows the tool lines live above each answer and an **Open SC-123** link when Scout recorded a task. Generation has a 120-second timeout and stopping cancels the upstream request. `GET /api/chat` returns the model list (one Granite entry).

## LLM usage

Open `/#llm-usage` (Workspace › **LLM usage**) for requests, tokens, latency and estimated cost of the self-hosted model over 24 hours, 7 or 30 days, plus a live model snapshot. The page reads `GET /api/llm-usage?days=1|7|30`, which calls the CFO API's `/v1/llm/usage` and `/v1/llm/usage/live` server-side with the optional `CFO_API_TOKEN`.

## Access scope

The TLS dashboard route uses a shared HTTP Basic login supplied by Secret `dashboard-auth`. It is a read-only workspace login, not per-employee IAM. Displayed company data is necessarily transmitted to the authenticated viewer. Do not publish screenshots or exports containing confidential records.

The backend API has a separate existing Route and its own access policy; dashboard authentication does not protect that Route. Database and source-file services remain internal.

See `THIRD_PARTY_NOTICES.md` and the font licenses under `assets/fonts/`.
