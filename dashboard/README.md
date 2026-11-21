# Scout dashboard

Next.js 16, React, TypeScript and shadcn/ui. The calm Linear-like interface uses the existing semantic theme tokens and local Inter/Geist fonts. Company records are loaded server-side at request time; no company statement, employee directory, salary fixture or balance is bundled into the application image or browser JavaScript.

## Organizations and access

Start at `/` to find or create an organization. `/<organization-slug>` is that organization's sign-in page and, after authentication, opens **Scout chat** by default. Choose **Overview** in the sidebar or open `/<organization-slug>#overview` for financial records and forecasts. A new organization shows clean empty/unavailable states, not Amalfi records or invented forecasts. `/<organization-slug>#chat` and the old `#llm` alias also open chat.

The dashboard and Python API both verify opaque organization-bound sessions and current membership. Company queries run in a read-only repeatable-read transaction with a registered, tenant-only PostgreSQL search path. API calls carry the real member's session, not a shared write token. An organization URL/header is a selector, never authorization. There is no anonymous data-access bypass.

**Read [the complete authentication/migration guide](../docs/scout-auth.md) before running this version against existing data.** The opt-in migration registers Pizzeria Amalfi without granting it to any visitor. An operator must explicitly enroll the intended verified owner. Google OAuth setup, exact callback URLs, secret configuration, worker binding, database-role limitations and backup/dry-run/rollback requirements are in that guide. The application database has not been migrated and this version has not been deployed yet.

## Local development

Requires Node 22.18+, the Python backend and an isolated migrated PostgreSQL database. Copy `.env.example` to gitignored `.env.local`; configure the exact `SCOUT_APP_ORIGIN`, database connection and internal `CFO_API_URL`. Never use `NEXT_PUBLIC_` for credentials. Do not point development tests at a live database.

Run these commands from the repository root:

```bash
npm --prefix dashboard ci
```

```bash
npm --prefix dashboard run dev
```

The default browser origin is [http://127.0.0.1:3000](http://127.0.0.1:3000). Use that same origin in `SCOUT_APP_ORIGIN`; changing between localhost and 127.0.0.1 changes cookie/Origin/OAuth behavior. Create a disposable organization through `/` rather than using an anonymous preview.

Google sign-in is disabled until `SCOUT_GOOGLE_CLIENT_ID` and `SCOUT_GOOGLE_CLIENT_SECRET` are configured. These are identity-only credentials, separate from any mailbox connector. Existing password accounts must explicitly **Link Google sign-in** while authenticated before using Google login; email matching never implicitly links an account or grants membership.

The minimal Google registration flow starts from a new organization draft (name and URL slug), verifies Google, and creates only a **new empty organization** with its owner, without requiring a password. It does not join an existing organization. An existing email account is reusable only when the verified Google subject is already explicitly linked; otherwise sign in and link first. Real-browser Google signup with a previously unregistered identity has been verified locally, separately from the completed linking/login checks; production checks must repeat after rollout. Invites, Team management and Resend email delivery are later work, not part of this change.

Light is the default theme. The theme control supports dark mode and saves that cosmetic preference in the browser. Organization-specific task-seen state and company-data caches are scoped to the rendered organization and user.

## Data sources and Overview

- Balance is the sum of signed bank bookings, using PostgreSQL NUMERIC/integer-cent arithmetic. Year/Q4 inflows and outflows use the latest booking year, with Q4 starting in October; all recorded flows, including partner investments, count.
- Directory data joins employees to teams in the member's company schema. Salaries use the latest recorded salary month matched by employee name; missing matches show **Not recorded**. Payroll/social contributions are historical records, not a future payment schedule.
- Tasks, steps, approvals, audit rows, invoice documents and connection status use the same tenant boundary. Open a task through `/<organization-slug>#task/SC-123`; `#investigations` remains an alias for Tasks.
- Forecasts come from the authenticated Python API. Missing data/services produce an honest unavailable/empty state, never an invented forecast. **Refresh records** loads current server values.
- The forecast supports drag-to-pan, Ctrl-scroll zoom around the pointer, and keyboard zoom/pan after focusing the chart. Double-click, Ctrl 0 or **Reset** fits the full forecast; buttons provide the same controls without dragging.
- Approvals require the membership's mapped employee to be the named approver. Queued work requires a worker explicitly configured for that organization; signup does not deploy one.

## Scout chat and dashboard help

Send with Enter, use Shift+Enter for a new line, stop or retry a response, and choose **New chat** to clear it. The conversation stays in page memory across view switches and is discarded on reload/sign-out or organization change. This does not delete tenant-local backend tasks/history/checkpoints.

Authenticated `POST /api/chat` accepts the selected Granite model, alternating user/assistant messages (optionally preceded by Scout's compacted summary) and an optional conversation ID. Validation limits are 64 messages, 16,000 characters per message, 120,000 total and a 512 KiB body. The server proxies to `${CFO_API_URL}/v1/chat` with the member's session and organization context, not a shared token. Private/no-store NDJSON carries validated tool steps, answer deltas and completion/error events, with task, compaction or file information when available. Tool steps appear above answers, and **Open SC-123** opens recorded work. Generation times out after 120 seconds; stop cancels the browser's upstream request. `GET /api/chat` returns the authenticated model list.

Ask **How do I use this dashboard?**, also available as a suggested prompt, for navigation help. Scout's read-only `dashboard_help` tool retrieves one short topic at a time: getting started, chat, overview, tasks, transactions, connections, audit, policies or LLM usage. No full guide or new authentication instructions are inserted into every system prompt. Regression limits keep the tool schema under 650 characters and each topic response at most 1,600 characters. This requires the updated backend or a controlled test fixture, not a fabricated chat response.

Help answers may contain generic `/#chat`, `/#overview` or other `/#…` links. The frontend Markdown parser/renderer normalizes those to same-organization fragments (`#chat`, `#overview`, etc.) and opens them in the current tab, preserving the conversation. Visiting `/` itself still opens organization find/create; it is not the authenticated chat route.

## Connections

Open `/<organization-slug>#connections` for a compact integrations list using shared theme tokens and Lucide SVG icons. Rows show status and last sync. Chevrons expand historical KSeF progress/results and matched invoices, recorded Slack granted/missing scopes, or Google Workspace setup information. Display metadata is explicitly allowlisted; arbitrary connector config/credentials are not serialized to the browser.

These rows remain **read-only historical details**. Their old global mutation routes remain closed. Owners use each row's **Connect / Manage** action or **Resume setup** to configure explicitly tenant-owned connections through the authenticated checklist. These actions remain available to existing workspaces with no onboarding record, and after setup is skipped or completed. Members see owner-only guidance rather than enabled setup actions. Google **sign-in** is separate and does not authorize Gmail. New organizations inherit no environment-level bank, Slack, mail, Google or workstation connectors/credentials. Legacy connector use requires explicit operator authorization for the registered legacy organization. Do not interpret a historical connected status or setup instructions as permission to connect another tenant.

## Workspace setup API

New workspaces start with a pending server-side setup record (registration only; login and existing organizations are unchanged) and registration redirects to `/<organization-slug>#setup`. `GET`/`PATCH /api/onboarding`, `POST`/`DELETE /api/onboarding/connect/{bunq|builtin|ksef|google|slack}`, `GET /api/onboarding/callback/{google|slack}` and `POST /api/onboarding/handoff` proxy to the Python `/v1/onboarding` API with the member's session. Writes need the current organization's owner, same-origin JSON and bounded bodies; responses are private/no-store and generic on failure. Mailbox/Slack authorization uses its own short-lived provider cookie and a redirect derived from `SCOUT_APP_ORIGIN`, separate from Google sign-in. The handoff is agent-neutral (Codex, Claude Code, others), secret-free and omits company-network details; see `../docs/scout-auth.md` for the contract and limits. `PUT /api/onboarding/slack/channels` lets the owner select up to 20 channel IDs after OAuth; the bot must be invited manually and Scout verifies reads without sending messages. Gmail uses `gmail.readonly` plus offline refresh and enforces the chosen label in Scout (Google itself does not grant label-only access). Google grant revocation can affect other workspaces using the same client/account. Apply the additive backend auth migration before rolling out the dashboard and configure the separate mailbox/Slack clients and encryption key server-side. Live Google/Slack/bunq/KSeF behavior is unverified without real test credentials and explicit consent.

## Replies to delegated Slack messages

For an organization with explicitly authorized Slack and its worker, Scout labels a message sent on your behalf **MESSAGE**, tracks the returned Slack thread and leaves the conversation task waiting. A reply from the intended recipient in that exact thread becomes an attributed chat update without passing through the generic smalltalk/invoice classifier. Repeated Slack events reuse the stored reply/step; multiple requests retain their own recipients and question previews.

An active, visible chat acknowledges displayed replies through the private, member-authenticated `/api/chat/ack` proxy. Failed acknowledgments retry while visible, including when a hidden tab becomes visible again. If no receipt arrives within about 12 seconds, a queued worker job sends the reply to the original requester on Slack instead. **New chat** does not acknowledge the discarded conversation. Stale worker claims are recoverable; exhausted delivery attempts escalate the original task rather than silently disappearing.

This requires tenant-local `dm_requests` and `dm_replies` tables (including `claimed_at`), the updated API/worker and a dashboard rebuild. The operator migration's `--check`/`--migrate` includes the packaged additive upgrade for registered, initialized Scout schemas; do not replay the full schema against existing data. See the migration guide first. Old untracked threads are not retroactively correlated. Slack/network delivery is retry-based, not strictly exactly-once: a crash after Slack accepts a message but before recording delivery can repeat a notification. Tests use isolated schemas and fake Slack/model services, not employee messages.

## LLM usage

Open `/<organization-slug>#llm-usage` (Workspace → **LLM usage**) for requests, tokens, latency and estimated cost over 24 hours, 7 or 30 days. `GET /api/llm-usage?days=1|7|30` calls the authenticated Python usage endpoints with the real member's session. Reports use the same organization boundary; shared host telemetry/live model measurements are not exposed as a new tenant's private usage. Missing telemetry is unavailable, not fabricated. Estimates are telemetry, not billing invoices.

## Verification and production

```bash
npm --prefix dashboard test
```

```bash
npm --prefix dashboard run typecheck
```

```bash
npm --prefix dashboard run build
```

Real database tests additionally require `SCOUT_AUTH_TEST_DATABASE_URL` pointing at an isolated migrated test database. Without it those cases are explicitly skipped.

The real-browser authentication suite is `tests/organization-auth.e2e.cjs`. It requires a running local server, the same isolated database, `BASE_URL` and optionally `CHROMIUM_PATH`. Its acceptance coverage includes signup, private empty workspaces, logout/login, two-tenant object access, forged organization headers, anonymous/CSRF rejection, invalid OAuth callbacks and responsive light/dark login. Screenshots are gitignored under `artifacts/organization-auth/`. Simulated callback tests do not prove live Google success.

The inherited `tests/ui-cleanup.e2e.cjs`, `tests/connections.e2e.cjs` and `tests/dm-reply.e2e.cjs` now use authenticated, isolated browser fixtures. All three passed locally after integration, including light/dark responsive layouts, historical KSeF/Slack details, disabled connection mutations, same-organization help links and delegated-reply acknowledgment/retry/visibility. Require the matching disposable local server/database and `SCOUT_AUTH_TEST_DATABASE_URL`; model/Slack responses remain controlled. No real connector writes are performed. Screenshot/artifact directories are gitignored.

Real-browser Google linking of the selected owner, logout and fresh Google-only login have already succeeded with actual credentials; a second nonmember Google account was denied without an account/session being created. A previously unregistered Google identity also completed new-organization signup without a password; database checks confirmed only its new owner membership, with no Amalfi access. Repeat production OAuth checks after rollout; neither local success nor the existing cluster Secret establishes deployment or migration.

Builds require no database or credentials. `/healthz` exposes only process health. Business responses are private/no-store and require membership, including server-rendered React requests. `Containerfile` packages standalone Next output for OpenShift's arbitrary non-root UID and read-only filesystem; deployment settings are in `deploy/openshift/dashboard/`.

See `THIRD_PARTY_NOTICES.md` and `assets/fonts/` for licenses. Never publish screenshots or exports containing confidential records.
