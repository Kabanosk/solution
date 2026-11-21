# T053 onboarding backend: report

Patch: `.house/reports/T053-onboarding-backend.patch` (git diff --cached --binary, 30 files, applies cleanly to HEAD 6f6f7e4). Nothing committed or pushed. Worktree: `.worktrees/T053-onboarding-backend`. No dashboard files, no `cfo/usage/report.py`, no `tests/test_llm_usage.py`, no shared TS model.

## Implemented (all provider paths have real code; none is a placeholder)
- Schema (`cfo/db/auth.sql`, additive/idempotent): `scout_auth.workspace_setup` (organization_id FK cascade, revision 0, status pending, steps jsonb {}, network jsonb null; matches Next `insert(organization_id)`), `scout_auth.setup_oauth_states` (hashes only).
- API `/v1/onboarding` (`scout/onboarding/`): GET (no write, missing row = not_started/rev 0), PATCH (skip/resume/complete/step/network, optimistic 409, owner only), POST connect/{bunq|builtin|ksef|google|slack}, POST callback/{google|slack}, DELETE connect/{provider}. Org always from session Tenant; legacy org mutations 403; old `/v1/connections/*` and `/v1/demo/*` still closed.
- Connections state derived from tenant `connections` rows; `workspace_setup` stores only decisions, last failure result and non-secret network answers. PATCH cannot set connected. Network `required=true` stays `needs_configuration`; `required=false` shows `skipped` ("not needed").
- Per-org credential sealing: `derive_key(master, org, connection)`; ciphertext copied across orgs/connections cannot be opened.
- bunq sandbox (`scout/connectors/bunq.py`): installation, device-server, session-server, accounts, paginated payments (`count`/`older_id`), exact Decimal money, 401 re-login, read-only (no write surface), fixed sandbox host, sandbox_ keys only. Definitions taken from official `bunq/doc` swagger.json.
- KSeF: TEST only (fixed URL, settings URL ignored), `KsefClient.login` + one read query (`verify_read`), no sessions/issuance/suppliers.
- Google mailbox: gmail.readonly, offline consent, PKCE, hashed single-use state bound to org/owner session/browser proof/provider/redirect, token exchange, refresh with the saved token, profile + label validation (default INBOX), sealed refresh token, revoke on failure/disconnect.
- Slack: OAuth v2 read-only scopes, `auth.test` identity check, sealed bot token, revoke, no message sent.
- Registry: non-legacy tenants only get explicitly authorized (`managed_by=onboarding`, connected, enabled, provider-matched, decryptable) connectors; no folder/global/workstation fallback (stray `mail_folder` ignored). `GmailMail(require_label, strict credentials)` fails instead of widening to the whole mailbox and raises on lost scope. `tenant_settings` keeps only app-level OAuth registration + master key.
- Application prerequisites and provider failures return 2xx setup (`needs_configuration` / `failed`, fixed safe messages); real validation/auth/state errors are 4xx. Abandoned OAuth "connecting" recovers by itself when the 10 min state expires.
- `.env.example`: SCOUT_APP_ORIGIN, CONNECTIONS_KEY, GOOGLE_CLIENT_ID/SECRET, SLACK_CLIENT_ID/SECRET.

## Interface notes for the coordinator
- connect google/slack returns `{url}` on success, or the full setup object (no `url`) when a prerequisite is missing. Proxy must branch on `url`.
- Callback: all post-validation outcomes (success, cancel, provider failure) are 200 + setup; Next must choose `-connected`/`-failed` from the step state. Invalid/replayed/expired/mis-bound state is 400 `invalid_state`.
- Apply `cfo/db/auth.sql` (`python -m cfo.db.migrate --migrate`) before signup inserts.
- Slack: connected but chat reading stays off until channels are authorized (`config.channels` empty -> NoChat). No channel selection endpoint exists yet.

## Not done / limits (implementation vs credentials kept separate)
- Missing implementation: Slack channel selection UI/endpoint; persisting a refreshed bunq session token (in-memory only); bunq server response-signature verification (reads are over TLS to the fixed host); one-Slack-workspace-per-org uniqueness across tenants; worker-level handling when `MailLabelMissing`/`RefreshError` surfaces (propagates, not auto-marked `error`).
- Missing credentials, all unverified live: Google OAuth client + consent, Slack app + install, bunq sandbox host reachability, KSeF TE reachability. Only documentation GETs were made (bunq docs/swagger). Every provider is tested only at the HTTP boundary with scripted httpx.MockTransport; an unscripted request fails the test.

## Verification (disposable DB `scout_onboarding_worker` on :55437, TZ=UTC, main-clone venv with PYTHONPATH)
- RED evidence, each before implementation: schema (UndefinedTable), setup API (18 failed, 404), bunq (15 failed, ModuleNotFoundError), KSeF (5 failed, 501), Google (failed on 501), Slack (11 failed), registry (7 failed). Then GREEN per cycle.
- Final: `pytest tests/onboarding` = 122 passed, 0 skipped.
- Existing affected: tenant isolation, scout schema upgrade, scout api/connectors/ksef, deploy manifests, api skeleton = 109 passed, 1 skipped (pre-existing opt-in).
- Not run: full backend suite and Europe/Warsaw run (host load average >100; coordinator integration). `test_llm_usage` untouched.
- Commands: `. /tmp/t053env.sh; cd backend; $PY -m pytest tests/onboarding -q` (env: PYTHONPATH=<worktree>/backend, DATABASE_URL/TENANT_TEST_DATABASE_URL/SCOUT_TEST_DATABASE_URL=postgresql://pedro@127.0.0.1:55437/scout_onboarding_worker).
