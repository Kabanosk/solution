# Dashboard: runtime Postgres data and separate OpenShift pod

Implemented at the user's request on 2026-11-20.

## Result

The Next.js dashboard reads bank aggregates, employees/teams, payroll and recorded agent decisions from the existing Postgres service in the selected project. It obtains liquidity predictions from the existing internal CFO API. The former bundled synthetic CSV, fixed balance, employee/salary fixture, generated forecast and canned investigation have been removed from the dashboard.

Read queries use a read-only repeatable-read transaction, numeric/integer-cent aggregation, timeouts and a bounded connection pool. Missing salaries remain unknown, not zero. Database failures show a generic error; forecast failures do not hide the bank totals. The page and JSON endpoint run at request time and responses disable caching.

A separate `dashboard` Deployment, Service, TLS Route, ImageStream and binary BuildConfig are defined in `deploy/openshift/dashboard`. The manifests use the current OpenShift project and short ImageStream references; MLflow obtains its namespace through the Downward API. The allowlisted source archive excludes records, credentials and dependencies. The standalone runtime supports the OpenShift-assigned UID, a read-only filesystem and health probes. Database credentials reference the existing Secret; dashboard access uses a separate generated Secret. No secret values are included in this report or repository.

## Verification

- `npm --prefix dashboard test`: 3 tests pass (exact cents, malformed money and closed-by-default authentication).
- `npm --prefix dashboard run typecheck`: passes.
- Local production build and cluster container build pass without database credentials or records during the build.
- Server-side dry-run of the manifests succeeds; deployment is applied in the selected namespace.
- Integration requests match transaction count, income, expenses, balance and employee count against SQL executed in the existing Postgres pod; no rows or aggregate values were saved in the repository.
- The authenticated response contains the backend's 90 daily forecast points and preserves an unknown salary.
- External HTTPS checks pass for authenticated JSON and HTML, health, no-store headers, anonymous/wrong-login denial, React server data requests and middleware-bypass-header attempts.
- Local production outage checks pass: missing model returns null forecast with bank data intact; database outage returns a generic 503; missing login configuration returns 503 while the health endpoint stays available.
- Runtime check confirms an arbitrary non-root UID, credentials configured at runtime, and no `/app/data` directory.
- The final Git index was scanned for the targeted private path, namespace and actual runtime secrets. No matches remain in current files.
- Root `.gitignore` rules protect dashboard build output, environment files and confidential records; there is no dashboard-specific `.gitignore`.
- Portable manifests render without a fixed namespace; server dry-runs and short ImageStream resolution succeed without creating workloads.
- No browser automation was available; interaction/layout changes were checked by TypeScript and rendered HTTP responses.

## Review and operation

Route: ${DASHBOARD_URL}

Deployment instructions and a reusable HTTP smoke check are in `deploy/openshift/dashboard/README.md`. The login file is outside the repository at `${DASHBOARD_CREDENTIALS_FILE}`, mode 0600. The source, deployment changes, update notes and this report are included in a single sanitized branch commit based on `origin/main`. Runtime routes, project names and credential-file locations are supplied locally.

The existing backend API Route has its own demo access policy and remains unauthenticated. Dashboard authentication does not secure that separate endpoint.

The absolute house-system paths supplied in AGENTS.md are absent on this machine. Repository-local `.house` guidance was read instead; the house launcher/export/merge tools were unavailable. No running backend services, company rows or seed jobs were modified during the source cleanup. Shared deployment manifests were made portable.
