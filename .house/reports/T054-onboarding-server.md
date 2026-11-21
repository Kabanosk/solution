# T054 onboarding server report

Worktree: `.worktrees/T054-onboarding-server` (no commit). Patch: `.house/reports/T054-onboarding-server.patch` (staged, binary, owned files only; `lib/onboarding-model.ts` and `node_modules` not staged).

## Delivered
- `dashboard/lib/auth-store.ts`: `INSERT INTO scout_auth.workspace_setup(organization_id)` inside `createOrganization`, after `provision_organization`, same transaction (rollback on failure).
- Registration-only `#setup` redirect: `app/api/auth/[action]/route.ts` (register only; login unchanged) and `app/api/auth/google/callback/route.ts` (registration only; link/login unchanged).
- `lib/onboarding-server.ts`: provider enum, per-provider body allowlists, PATCH validation, server-derived redirect URI, provider proof cookie (`scout_onboarding_<provider>`, path-scoped, 10 min), auth-URL host allowlist, response sanitizer, generic error mapping, `startProviderConnect`, `completeProviderCallback`, `makeBackendFetch` (timeout <= 10 s).
- `lib/onboarding-handoff.ts`: `buildHandoff` (allowlist only; no network details, no display data/messages; `scout.setup/v1`; claude q-only URL < 5000 chars else null).
- Routes: `app/api/onboarding/route.ts` (GET/PATCH), `connect/[provider]/route.ts` (POST/DELETE), `callback/[provider]/route.ts` (GET), `handoff/route.ts` (POST).
- Docs: `docs/scout-auth.md`, `dashboard/README.md`.

## Checks (Node 22.23.5 via `npm exec --package=node@22`)
- RED first: new tests failed with module-not-found for `onboarding-server.ts`/`onboarding-handoff.ts` and the two registration tests failing (no workspace_setup insert): 1 pass / 4 fail.
- GREEN: 23/23 in the three new files (one test fixture initially too small for its own claim; fixed the test, not the code).
- Full `npm --prefix dashboard test`: 104 tests, 87 pass, 0 fail, 17 skipped (real-DB cases; `SCOUT_AUTH_TEST_DATABASE_URL` not set, no DB used).
- `tsc --noEmit`: clean (with coordinator's copied `onboarding-model.ts`).
- Not run: `next build`, browser suites (coordinator-owned).

## Integration dependencies for coordinator
1. `proxy.ts` (not mine): `/api/onboarding/callback/*` is a browser GET from Google/Slack with no `X-Scout-Organization` header, so the current proxy returns 401 before the route runs. It needs a narrow GET exemption for that path (the route authenticates itself via cookie slug + signed-in owner).
2. Backend schema: `scout_auth.workspace_setup` (FK organization_id, defaults) must exist before registration works; every register call now fails without it (rollback). Existing DB tests (`auth-store`/`google-registration`) therefore need the T053 schema applied to their database.
3. DB needs: none from me. A real-DB registration test (atomic setup row, existing orgs unaffected) is deferred to coordinator's `scout_onboarding` DB; my registration test uses a scripted pool around the real `AuthStore`, so it proves statement order/transaction, not the schema.
4. Backend failure bodies are never forwarded (generic by status); honest KSeF/bunq prerequisites must be returned by the backend as a 2xx setup state (`needs_configuration`/`failed` + message), which is passed through sanitized.
5. Connect for bunq/ksef/builtin/disconnect uses apiFetch's 15 s timeout; GET/PATCH/OAuth start/callback/handoff use 10 s.

## Unverified (no live credentials, no provider calls)
Real Google/Slack consent and callback, token exchange, bunq sandbox, KSeF test, and backend owner/state binding are unverified. Callback logic is tested only against a mocked backend boundary.
