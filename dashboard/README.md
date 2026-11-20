# Lantern dashboard

Next.js, React, TypeScript and shadcn/ui dashboard for the CFO agent. Company records are loaded on the server at request time from the existing Postgres service. No statement, employee directory, salary fixture or balance is bundled into the application image or browser JavaScript.

## Data sources

- Balance: sum of all bank bookings, using Postgres NUMERIC arithmetic.
- Year and Q4 expenses/inflows: bank transactions in the latest booking year; Q4 starts in October. All recorded inflows/outflows count, including partner investments.
- Directory: `employees` joined to `teams`. Each salary is the sum of the latest recorded salary month matched by employee name. Missing matches display **Not recorded**.
- Payroll and social contributions: latest month with salary bookings. These are historical records, not a future schedule.
- Investigations: latest 50 `decisions`; pending approvals and undocumented invoice-reference payments are counted from their tables.
- Liquidity forecast: the documented `GET /v1/forecast/liquidity` on the internal CFO API. If unavailable, the chart shows a message; no invented forecast is substituted.

Database queries use a repeatable-read, read-only transaction, a bounded connection pool and statement timeouts. Money remains integer cents until conversion for display/charting. The dashboard never writes company records. **Refresh records** loads current values from the server.

## Local development

Requires Node.js 22.18+ and a seeded database. Copy `.env.example` to `.env.local` and fill the server-only Postgres connection fields, `CFO_API_URL` and dashboard login. Never use `NEXT_PUBLIC_` for these values or commit credentials.

```sh
npm --prefix dashboard ci
npm --prefix dashboard run dev
```

Open `http://127.0.0.1:3000` and enter the configured dashboard login. For access to the shared cluster services, run each port-forward in its own terminal:

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
