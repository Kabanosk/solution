# Approved workspace onboarding contract (2026-11-21)

Main checkout: /home/pedro/Desktop/Projects/collabothon2026/.claude/worktrees/onboarding-import-flow-3f87be/solution. The machine-specific HOUSE_DIR and house CLI are unavailable; main checkout .house is the shared state. Native subagents, maximum three. No commits/push/deploy/provider live calls or production DB writes. Tests use isolated local DBs. Do not recursively delegate. Work in assigned task worktree; return a staged binary patch including new files, not a commit. Coordinator owns integration.

## Wire contract (fixed; communicate before changing)

`GET /v1/onboarding` (Next `GET /api/onboarding`) returns:
```
{version:1, status:'not_started'|'pending'|'in_progress'|'dismissed'|'completed', revision:number,
 steps:[{id:'bank'|'invoicing'|'ksef'|'email'|'slack'|'network',
 state:'not_started'|'needs_configuration'|'connecting'|'sandbox_connected'|'connected'|'failed'|'skipped',
 provider:string|null,message:string|null,display:Record<string,string|boolean|null>}],
 network:{required:boolean,provider:string,protocol:string,systems:string[],administrator:string,company_only:boolean}|null}
```
Stable step IDs/order as shown. Missing setup row returns status not_started/revision0, all steps not_started, no GET write and no auto-onboarding for existing organizations. Signup creates pending row in its existing transaction. Provider missing config uses needs_configuration, never connected.

`PATCH /v1/onboarding` (Next same): `{revision:number,action:'skip'|'resume'|'complete'|'step'|'network',step?:StepId,state?:'skipped'|'not_started',network?:NetworkAnswers}`. Optimistic conflict=409. Owners only. skip dismisses whole onboarding, complete requires every step configured or explicitly skipped, step updates decisions not fake connector status, network records non-secret intent and needs_configuration if private access required (no VPN deployment).

`POST /v1/onboarding/connect/{provider}` (Next same suffix), provider enum `bunq|builtin|ksef|google|slack`:
- bunq: `{api_key?:string}` (empty creates a sandbox company/person); fixed sandbox host, no transfers.
- builtin: `{}`; existing tenant-local PostgresInvoicing is available.
- ksef: `{nip:string}`; TEST only, reuse KsefClient for read/auth, never call legacy demo/supplier issuance. Unsupported/missing valid auth must return honest prerequisite rather than connected.
- google/slack: `{redirect_uri:string,browser_proof:string,label?:string}` -> `{url:string}`. redirect must equal configured SCOUT_APP_ORIGIN + `/api/onboarding/callback/{provider}` (loopback HTTP dev permitted); state short-lived single-use and bound to session hash/org/browser proof/provider/redirect. Backend provider settings are dedicated mailbox GOOGLE_CLIENT_ID/SECRET and SLACK_CLIENT_ID/SECRET, not SCOUT_GOOGLE identity credentials. Google: gmail.readonly only, offline consent, verified profile/mailbox read, label selection (default `INBOX`) validated; never claim Google sign-in authorizes mail.
`POST /v1/onboarding/callback/{google|slack}` body `{state,code?,error?,redirect_uri,browser_proof}` -> setup state after token exchange/validated read. Must consume errors/cancel state too; no provider tokens returned. Owner session required for callback. Next GET callback route uses own HttpOnly provider-specific short-lived browser proof cookie containing slug + proof, authenticates own session, forwards above, clears cookie, redirects `/{slug}?setup={provider}-connected|{provider}-failed#setup`. The slug cookie is only selector; backend state/session is authority.
`DELETE /v1/onboarding/connect/{provider}` -> setup state, removes local encrypted credential/config; provider revoke where available with short timeout (local deletion must survive provider outage).

Next `POST /api/onboarding/handoff` owner+CSRF guarded returns `{setupUrl,markdown,manifest,prompt,claudeUrl}`. setupUrl=normal authenticated `origin/slug#setup`; manifest version `scout.setup/v1`, allowlisted states only; plain Markdown/prompt compatible with Codex/Claude Code/etc, no secret or network details. claudeUrl optional `claude-cli://open?q=...` (<5000 chars). No API keys/cookies/internal schema/mailbox data in context, no agent bearer token. Tell agent to configure connections with user-owned authentication/consent, NOT modify Scout code. Parallel independent tasks only. Main CTA **Continue with your agent**. Do not add any model API dependency.

## Ownership

T053 backend: all backend/** changes for setup schema in cfo/db/auth.sql, API/router, provider helpers, registry, tests; NOT cfo/usage/report.py or tests/test_llm_usage.py (coordinator UTC fix). May update .env.example for new provider configuration. No dashboard files. Add workspace_setup with organization_id FK, revision default0,status default pending,steps jsonb default{},network jsonb nullable; Next signup insert only organization_id.
T054 dashboard server: lib/auth-store.ts (insert pending row; session type may include setupPending if needed), app/api/auth/[action]/route.ts and google/callback route (registration-only #setup redirect), app/api/onboarding/**; lib/onboarding-server.ts/handoff.ts and tests; docs/scout-auth.md, dashboard/README.md. No UI/nav/components, no backend edits, no onboarding-model.ts edits.
T055 UI: components/onboarding.tsx and dashboard/nav/connections integration; lib/onboarding-client.ts and UI tests/e2e. No auth store/routes/backend/docs changes, no onboarding-model.ts edits. Initial setup entry via signup #setup; on default/no-hash owner load GET setup and if pending/in_progress navigate to setup, but preserve explicit hash. Existing/legacy status not_started must stay chat. Persist dismiss before navigation; reopen from Connections. Scope requests with tenantFetch. Six step checklist, Google real CTA, input labels and network conditional questions, per-step skip, errors/loading/retry, generic handoff dialog copy/download. Use existing shadcn/theme tokens/Lucide, unique IDs, keyboard/mobile/light/dark. Follow ui-ux-pro-max. Native /#setup route (NOT #/setup).

Shared TS types are dashboard/lib/onboarding-model.ts, owned by coordinator, copied into task worktrees for compile only; do not include this file in staged patch.

## Verification

TDD actual fail-before-green and report commands/results. Full suites must report skips and failures. Use source-root uv run --directory backend for Python imports. Isolated main Postgres :55437, username pedro/no password; database `scout_onboarding` reserved coordinator app/frontend, `scout_backend` reserved coordinator backend suite. Ask coordinator for separate DB if worker needs one; never use existing :5434/container or production. Network clients default timeout<=10s and no live calls from workers. Mock external HTTP at boundary but test actual state persistence/authorization. house tooling unavailable; report rather than pretending freeze/merge ran.
