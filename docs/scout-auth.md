# Scout organizations and sign-in

## Trust boundary

A URL, organization ID, email domain, Google account or shared API token does **not** grant organization access. Both Next.js and the Python API validate an unexpired opaque session against `scout_auth.sessions`, `memberships` and `organizations` on each request. Removing a membership immediately denies subsequent requests. Sessions are organization-bound, last 12 hours, and are stored as SHA-256 token digests. Passwords use salted scrypt (N=32768, r=8, p=1, 64-byte key).

- `/`: find an organization by exact slug or display name; create a new organization.
- `/<organization-slug>`: organization-specific login, or **Scout chat** by default after authentication. `/<organization-slug>#chat` and the old `#llm` alias open the same chat.
- `/<organization-slug>#overview`: financial overview, with clean empty/unavailable states for a new organization; no legacy records or invented forecasts.
- Generic `/#…` links returned by chat/help are normalized by the frontend Markdown parser/renderer to same-organization fragments and stay in the current tab. `/` itself remains organization find/create, not authenticated chat.
- Exact slug matches take precedence over display names. Ambiguous display names require the URL slug.
- New organizations have their own empty PostgreSQL schema, owner employee and owner membership. There is no seed-data, preview, Basic-auth or shared-token fallback. Password registration requires an existing local account to prove its password. Google registration needs no password, but may reuse an existing email account only if the verified Google subject is already explicitly linked; otherwise sign in and link first. An account can own at most three organizations.
- The browser sends `X-Scout-Organization`; this is only a requested organization, not proof. Both servers verify it against the bearer session. The Python API independently rejects anonymous reads, writes, docs, streams and alternate routes. Only `/healthz` is public.
- Database transactions/connections use only the registered schema in `search_path`, with no `public` fallback. Factories capture their immutable tenant scope before background threads start. Client caches/local-storage keys include organization and user; switching/signing in/out uses full navigation, discarding conversation state. Forecasts are not cached globally by Next.js.
- Mutating browser requests require the exact configured Origin. Cookies are HttpOnly, SameSite=Lax, host-only, path `/`; HTTPS origins use Secure. Never terminate production on HTTP.
- Login limits are account-wide, independent of a requested slug; additional per-address budgets protect signup/login/discovery/OAuth. Production requires a client-IP header that a trusted ingress **overwrites**. Expired sessions, OAuth flows and limiter records are pruned during rate-limit checks. Local password emails identify local accounts; they are not asserted to be externally verified identities and never cause organization enrollment or Google account linking.

## Non-destructive migration

Stop application writers for the operational migration window, take and test a full PostgreSQL backup, and test the migration on a restored copy first. No migration runs automatically on startup or signup against legacy data.

Use the backend runtime with `DATABASE_URL` supplied through a protected environment/secret file, not pasted into shell history:

```bash
uv run --directory backend python -m cfo.db.migrate --check
```

`--check` executes the auth/provisioning migration and packaged additive Scout upgrade in a transaction, then rolls everything back. It is a DDL dry run, not a substitute for testing a restored backup. Then, only with operator approval:

```bash
uv run --directory backend python -m cfo.db.migrate --migrate
```

The packaged migration creates/updates `scout_auth.*` and the canonical provisioning function, including the OAuth transaction `registration` JSONB draft used by Google signup. It registers existing public-schema data without rewriting financial records as:

- Display name: **Pizzeria Amalfi**
- Slug: **pizzeria-amalfi**
- ID: `00000000-0000-4000-8000-000000000001`
- Schema: `public`

For registered, initialized Scout schemas, `--migrate` also applies the packaged, non-destructive `backend/cfo/db/scout_dm_replies.sql` upgrade: delegated-message request/reply tables, indexes and `dm_replies.claimed_at`. It validates the registered schema and uses its tenant-only search path. Schemas with neither `agent_tasks` nor `agent_jobs` are skipped; a partially initialized schema is rejected. It never replays the full Scout schema, drops tables, seeds records or migrates arbitrary unregistered schemas. Fresh organization provisioning already includes this storage. Roll out the matching API/worker and rebuild the dashboard together; adding tables alone does not enable reply delivery.

It enrolls **nobody**. There is no first-visitor claim, matching-domain join or automatic Google/email linkage. On 2026-11-21 the operator explicitly selected verified the operator-selected verified Google account as Amalfi's initial owner. Enrollment remains a separate backed-up migration action; knowing the fictitious employee `vincenzo` is not proof of ownership. Never copy the disposable verification account's local password into production.

An operator must verify the selected existing Scout account, then explicitly enroll it (replace the example address, never provide a password/secret in chat):

```bash
uv run --directory backend python -m cfo.db.migrate --enroll-owner-email intended-owner@example.com --confirm-legacy-owner
```

The command requires exactly one existing account and the existing legacy CEO employee; it refuses to replace a different enrolled owner. It must not be exposed as a web route. It has **not** been run against the application database by this change.

Signup inserts organization/user/membership/employee and provisions its schema in one transaction. Duplicate slug/account races roll back without leaving partial memberships or company schemas. Provisioning recreates canonical DDL, not `LIKE public`: foreign keys, sequence defaults, checkpoint tables and jobs remain tenant-local. It never runs the financial/demo seeders.

### Rollback

This migration does not rename, move, delete or reset legacy financial tables. If deployment validation fails before new-tenant writes, roll back the application images and keep the auth metadata/schema for recovery. If reverting auth enforcement to the previous unauthenticated/shared-auth API, first remove external API access; never restore the old public exposure while assuming the new security boundary still exists.

After tenants have written data, preserve **all** organization schemas and `scout_auth` metadata together. Do not drop a schema or replay seeds as a rollback. Restore the tested whole-database backup only through an approved recovery procedure, accounting for post-backup writes. There is intentionally no destructive automatic `down` migration.

## Runtime settings

Next.js (`dashboard/.env.local` for local development; server Secret for production):

| Variable | Meaning |
|---|---|
| `SCOUT_APP_ORIGIN` | Exact browser origin, without trailing slash/path |
| `DATABASE_URL` or existing `PG*` | Tenant data PostgreSQL connection |
| `SCOUT_AUTH_DATABASE_URL` | Optional separate auth/provisioning connection; defaults to `DATABASE_URL`/`PG*` |
| `CFO_API_URL` | Internal Python API origin |
| `SCOUT_TRUSTED_CLIENT_IP_HEADER` | Production-only required trusted header, e.g. `x-forwarded-for` with ingress replacement enforced |
| `SCOUT_GOOGLE_CLIENT_ID` | Google **sign-in** web client ID, server-only |
| `SCOUT_GOOGLE_CLIENT_SECRET` | Google **sign-in** client secret, server-only |
| `SCOUT_PRIVACY_URL`, `SCOUT_TERMS_URL` | Real published HTTPS legal URLs; empty until supplied by the legal team |

Do not prefix credentials with `NEXT_PUBLIC_`. `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `API_WRITE_TOKEN`, `CFO_API_TOKEN` and `DASHBOARD_PREVIEW` no longer authorize dashboard access. Google login settings are separate from legacy mailbox connector `GOOGLE_*` settings.

The OpenShift dashboard template uses optional Secret `scout-login`, keys `client-id` and `client-secret`. It sets the exact application origin and configures `haproxy.router.openshift.io/set-forwarded-headers: replace` before trusting `x-forwarded-for`. Keep the dashboard Service inaccessible to untrusted callers who could bypass the ingress and supply their own trusted header. Do not set the trusted-header variable for an arbitrary proxy that merely appends attacker input.

### Database privilege limitation

Schema isolation in this change is **application-enforced**: the existing deployment uses a shared, broadly privileged PostgreSQL identity. It is not a PostgreSQL RLS/per-tenant-role security boundary. User input is parameterized; no browser-provided SQL or schema is executed. Before exposing a hardened multi-tenant production service, separate auth/provisioning privileges from data access and use tenant-specific data roles or an equivalent DB-enforced boundary. Do not give model-generated SQL direct access to this connection. The optional auth DSN alone is not a substitute for schema-specific privileges.

## Workers, models, connectors and files

Every worker/MCP/operator entrypoint requires an explicitly selected registered organization; absence never means Amalfi. Run a separate worker for each organization that needs queued background work:

- `SCOUT_ORGANIZATION_ID=<organization UUID>`
- Only if explicitly authorizing the existing legacy connectors: `SCOUT_LEGACY_CONNECTORS_ORGANIZATION=00000000-0000-4000-8000-000000000001`

No new organization inherits environment-level Slack, bank, Google, local mailbox or workstation access. New organizations' connectors are unconfigured; connection mutation, demo, KSeF sync and old mailbox OAuth routes are closed in this change. The compact Connections list retains allowlisted historical KSeF progress/results/invoices and recorded Slack granted/missing scopes. Arbitrary connector config/credentials are not serialized. Its disabled actions and Google Workspace setup information are not working Connect flows; identity-only Google sign-in does not authorize a mailbox. Live vLLM host measurements are not treated as a new tenant's private usage. Forecast models/files/cache identities are tenant-specific; shared MLflow model loading/logging is suppressed in tenant contexts. Database notification channels are tenant-specific, including streaming task updates.

API chat executes in its organization-scoped context. Queued approvals/what-if jobs need that organization's worker; provisioning an organization is not deployment of a worker. Do not claim background completion merely because a job was queued. There are no bunq, Slack/email connection additions or connector onboarding flows here. Invites, Team management and Resend email delivery are explicitly later work, outside this change.

### Dashboard help and delegated Slack replies

The read-only `dashboard_help` tool retrieves one bounded navigation topic on demand. It does not install a new authentication system-prompt blob or grant permissions. The frontend rewrites its generic `/#…` links to same-org fragments, preserving the active conversation when switching views.

Delegated Slack messages require explicitly authorized Slack access and a worker bound to that organization. Scout marks an outgoing delegated request **MESSAGE** and correlates only the intended recipient's reply in the tracked thread. The reply appears as an attributed chat/task update; repeated Slack events reuse its stored reply/step. Active, visible chat acknowledges displayed replies through the member-authenticated `/api/chat/ack`; failed acknowledgments retry, including on tab visibility restoration. Without a receipt after about 12 seconds, queued work sends the reply to the original requester on Slack. New chat does not acknowledge a discarded conversation. This requires the additive tenant-local schema upgrade above, updated API/worker and dashboard rebuild. Old untracked threads are not retroactively correlated. Delivery is retry-based, not strictly exactly-once; a crash between Slack acceptance and the database delivery record can repeat a notification. Stale claims are recoverable and exhausted attempts escalate the original task.

## Google Auth Platform setup

Use a **separate Scout Google Cloud project**, not an existing project's shared consent-screen branding. The selected owner for that new project is the operator-selected verified Google account; this does not enroll that account into Pizzeria Amalfi.

1. In Google Auth Platform → Branding, use app name **Scout**, the authorized support/developer email, and accurate app URLs. Do not claim domain verification or consent publication that has not been completed.
2. Audience: External for accounts outside a single Workspace. Google's [basic-identity exception](https://support.google.com/cloud/answer/15549945?hl=en) means an app requesting only name, email and profile through OpenID Connect does not require test-user allowlisting and is exempt from the testing warning/seven-day expiry. This does **not** mean the console publishing status or optional brand verification is complete. Adding other scopes changes these requirements; never broaden scopes to bypass restrictions.
3. Data Access: **openid, email, profile only**. No Gmail, Drive, Calendar, bank or offline-access scopes.
4. Clients → Create client → **Web application**, named **Scout**.
5. Authorized JavaScript origins (the app uses server-side OAuth; these are the matching browser origins):
   - `http://127.0.0.1:3000`
   - `http://localhost:3000` only if running the app with that exact `SCOUT_APP_ORIGIN`
   - `https://dashboard-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com`
6. Authorized redirect URIs (exact, no wildcard or organization slug):
   - `http://127.0.0.1:3000/api/auth/google/callback`
   - `http://localhost:3000/api/auth/google/callback` only when using that exact local origin
   - `https://dashboard-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/api/auth/google/callback`
   - For the isolated verification server only: `http://127.0.0.1:3017/api/auth/google/callback`
7. Download the client JSON directly into a new private directory. Save the canonical copy outside the git worktree with directory mode 0700 and file mode 0600. Never print the secret or commit it. Place its ID/secret in the protected local environment file and in OpenShift Secret `scout-login` using file inputs, not command-line secret literals. Update only those keys after inspecting any existing Secret; do not overwrite unrelated values.

On 2026-11-21 the separate project **Scout** (`scout-515404`) and web client **Scout** were created under the selected personal Gmail account. Credentials were stored outside the repository with 0700/0600 permissions and installed into the existing cluster namespace as Secret `scout-login`. The console remains External / Testing: Publish app is disabled until accurate public legal URLs are supplied. No substitute legal pages, optional app logo or brand-verification request was published. The basic-identity exception above is used; no test users were added. The Google button uses Google's official G artwork served locally.

The production dashboard host above was confirmed using the existing cluster Route on 2026-11-21. Creating the Secret did not deploy the application or migrate the live database.

### Existing-member login and explicit linking

Google OAuth uses stored, single-use state bound to a separate HttpOnly browser cookie, PKCE S256, nonce, exact callback/origin, signed RS256 Google ID-token verification, audience/issuer/expiry checks and verified email. Google access/refresh tokens are not saved. Organization login only works for an already-linked existing member. A password-authenticated user explicitly chooses **Link Google sign-in** from their workspace menu; the verified Google email must match that local account. The callback rechecks the initiating user/session/organization before linking. Matching email alone never creates a membership or implicitly links an account.

Without configured client credentials, Google stays visibly disabled. With the actual client configured, a real-browser check on an isolated local organization completed explicit Google linking for the operator-selected verified Google account, logout, and a fresh Google-only login. The verified subject was stored and a new organization-bound session was issued. Unit tests and simulated callbacks alone do not establish live Google end-to-end success. A second signed-in Google account outside the project/test list completed the basic identity consent and returned `auth=no-membership`: no Scout account or session was created. Production OAuth must be checked again after rollout.

### Minimal Google organization registration

The new signup path is distinct from existing-member login and linking. The create-organization UI collects only a draft `{name, slug}` for Google registration and sends same-origin JSON to `POST /api/auth/google/start`:

```json
{"intent":"register","name":"My organization","slug":"my-organization"}
```

The server validates the draft and stores it in the OAuth transaction's `registration` JSONB, using the same single-use state/browser binding, PKCE S256, nonce and verified Google callback checks. No organization or membership is provisioned before successful identity verification. After verification, the callback creates only a **new empty organization**, its owner employee/membership and an organization-bound session; no password is required and no financial data or global connector credentials are inherited. Organization creation/provisioning remains transactional, and duplicate slug/account races roll back.

A Google identity with an existing email account may reuse that account **only if its verified subject is already explicitly linked**. Otherwise the user must first sign in to that existing account and explicitly link Google; email matching is not an account-link shortcut. Registration never enrolls the user into an existing organization, never claims Amalfi and never bypasses operator-only legacy-owner enrollment. The three-owned-organization limit also applies.

**Real Google signup was verified locally on 2026-11-21.** A previously unregistered Google identity created a new private organization using only its name/URL: no Scout user or organization existed before consent, and after the verified callback the new owner had a null password, a linked Google subject and an organization-bound session, with no Amalfi membership. Real-database tests separately cover refusal to implicitly link an existing email, linked-account ownership quotas, concurrent duplicate/quota rollback, browser-bound/expired state and malformed drafts. Browser checks cover cross-site start rejection and cancelled registration without provisioning. Repeat OAuth and isolation checks after production rollout. Invites, Team and Resend remain outside this scope.

## Verification

Requires Node 22.18+ and the locked backend environment. Ordinary dashboard tests skip real-DB cases unless `SCOUT_AUTH_TEST_DATABASE_URL` names an isolated test database. Never set that variable to a live database.

```bash
npm --prefix dashboard test
```

```bash
npm --prefix dashboard run typecheck
```

```bash
npm --prefix dashboard run build
```

The browser suite is `dashboard/tests/organization-auth.e2e.cjs`. Start the local app against an isolated migrated database, set `BASE_URL`, `SCOUT_AUTH_TEST_DATABASE_URL` and optional `CHROMIUM_PATH`, then run it. It creates test organizations and private task fixtures, exercises signup/logout, two-tenant IDOR/forged headers, anonymous and CSRF rejection, invalid OAuth callbacks and phone-sized light/dark layouts. It does not contact a real Google account or model. Screenshots go to gitignored `artifacts/organization-auth/`.

The inherited `ui-cleanup.e2e.cjs`, `connections.e2e.cjs` and `dm-reply.e2e.cjs` suites now use authenticated isolated browser fixtures and passed after integration. They require the matching disposable local server/database, `SCOUT_AUTH_TEST_DATABASE_URL`, tenant-only records and controlled model/Slack responses. Coverage includes light/dark desktop/mobile/landscape layouts, allowlisted historical connection details, disabled mutations, organization-preserving help links and delegated-reply acknowledgment/retry/visibility. No live connector writes are performed. These suites do not replace the authentication regression suite or real Google checks.

The production application database has not been migrated, legacy owner enrollment has not been executed there, and this application change has not been deployed. The existing Google project/client and cluster Secret are configuration prerequisites, not evidence of rollout.
