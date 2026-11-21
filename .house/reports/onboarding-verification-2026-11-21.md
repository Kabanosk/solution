# Workspace onboarding — integration verification

Date: 2026-11-21

## Scope and release status

Application: `Kabanosk/solution`, based on freshly pulled `origin/main` at `6f6f7e4`. Application changes are in the nested `solution/` checkout, not the surrounding Projects repository.

**Implementation and local verification complete; live-provider acceptance and production rollout remain unverified.** No commits, pushes, PRs, production migrations or deployments were performed. House CLI/freeze/check/merge tooling is unavailable on this host; the task files remain in review, not falsely marked merged.

## Implemented behavior

- Workspace creation commits a private empty tenant, owner/session and pending setup atomically. Only registration enters `/<slug>#setup`; existing organizations and ordinary login are not forced into setup.
- Optional, durable, resumable six-step checklist: bunq sandbox bank, built-in invoicing, optional KSeF test environment, Gmail/Google Workspace, Slack and company-network questionnaire. Per-step skip, skip-all, retry, disconnect and Connections resume entry are supported.
- Owner-only writes, current membership/session checks, exact-origin CSRF, bounded JSON, response allowlists, optimistic revision conflicts and no-store responses. Old global mutation/demo routes stay blocked.
- Provider grants are tenant-owned and encrypted with organization/connection-derived keys. OAuth state is short-lived, single-use and bound to the initiating session, browser, organization, provider and exact redirect.
- Gmail authorization is separate from Google identity sign-in. PKCE/offline refresh, scope/profile/label verification, runtime refresh failures and label-enforced message/attachment reads are implemented. Google grants mailbox-wide readonly access; Scout enforces the selected label. The UI explains this distinction.
- Slack OAuth verifies workspace identity but remains incomplete until selected channel reads are verified. The owner manually invites the bot and supplies C/G channel IDs. Empty selection disables reads. Stable IDs continue working after channel rename. Verification does not join channels or send messages.
- bunq is fixed to its sandbox, with pinned RSA response verification, signed read/session requests and encrypted session renewal persistence guarded against stale/disconnected credentials. No transfer or production banking surface was added.
- KSeF is test-only and bounded by elapsed time. Network answers do not install a VPN, change routing, mount folders or establish verified connectivity. Signup does not deploy a worker.
- “Continue with your agent” provides authenticated HTTPS setup link, instructions, Markdown and `scout.setup/v1` JSON for Codex, Claude Code or other agents. It grants no access and contains no secrets, provider diagnostics, employee data or network answers. An unconfirmed-revocation boolean retains a fixed actionable warning; arbitrary display metadata remains excluded. The optional Claude launcher supplements, not replaces, the generic handoff.

## Final verification

All databases were explicitly disposable PostgreSQL 18 instances on `127.0.0.1:55437`. The real auth/onboarding browser database was `scout_onboarding`, with the actual additive migration, not the earlier minimal mocked-UI fixture schema.

| Check | Result |
|---|---|
| Full backend, Europe/Warsaw | **730 passed, 40 skipped**, exit 0; 55.59s |
| Full backend, verified database-default UTC | **730 passed, 40 skipped**, exit 0; 59.67s |
| Dashboard unit/database suite, Node 22 | **135 passed, zero skipped**, exit 0 |
| Dashboard TypeScript | Passed, exit 0 |
| Final optimized Next.js build | Passed, exit 0; includes all onboarding routes and Slack channel route |
| Actual local callback routing browser test | Passed |
| Actual local full-stack onboarding browser test | Passed |
| Mock-provider onboarding UI browser test | Passed; setup responses explicitly mocked |
| Existing organization authentication browser suite | Passed |
| Existing UI/navigation browser suite | Passed, both themes |
| Existing Connections browser suite | Passed, both themes |
| Existing delegated-reply browser suite | Passed |
| `git diff --check` | Passed |

Backend skips in each full run: 17 missing generated-data cases, 16 missing bank-statement CSV cases, 2 missing organizer CSV cases, 4 unavailable MLflow cases, 1 opt-in live KSeF case. These were not bypassed or represented as tested.

### Timezone evidence

A preliminary `PGOPTIONS='-c timezone=UTC'` full run also passed 730/40, but explicit per-connection search-path options override that environment setting in some fixtures. It is not sufficient UTC evidence by itself. A third full run temporarily set only the disposable `scout_backend` database default to UTC and confirmed UTC even with explicit search-path options. A `finally` block restored the original inherited database setting; fresh connections again reported Europe/Warsaw. No production, global or role timezone settings were changed.

### Browser acceptance details

The real full-stack test exercises registration entry, durable setup, stale revision rejection, owner demotion, tenant isolation, same-origin checks, handoff privacy, built-in invoicing connect/disconnect/reconnect, dismissal/reopen and responsive light/dark layouts (320/375/1440 px). It additionally checks the real Slack PUT route for cross-origin rejection, invalid channel validation, allowlisted-body reconstruction, owner-only authorization and honest uninstalled state. No real providers or model services are contacted.

The UI-only suite uses real signup/session but controlled setup API responses. It checks Google start with default INBOX label, provider failures/prerequisites, Slack channel form validation/save/disable/disconnect, cancellation, network form, revision conflicts, per-step skip/resume, keyboard focus, read-only members, agent copy/download/fallback and responsive themes. These are UI proofs, not provider OAuth proofs.

Screenshots were opened and visually inspected:
- `artifacts/organization-auth/onboarding/setup-light-1440.png` — real API/database; visibly missing external credentials and uninstalled Slack, built-in invoicing genuinely available.
- `artifacts/organization-auth/onboarding/setup-dark-375.png` — real API/database, mobile dark.
- `artifacts/onboarding-ui/setup-dark-375.png` — explicitly mocked setup responses, mobile dark.
- `artifacts/onboarding-ui/handoff-dialog.png` — explicitly mocked handoff interaction.

### Failures encountered and resolved

- UTC API serialization, Slack ID-vs-name resolution, Gmail label/runtime error handling, bunq signature/session persistence, callback proxy authentication, JSON disconnect guard, strict display allowlists, and dialog focus defects have regression coverage.
- An exact Slack display assertion was updated to assert the intentional safe `channel_ids` prefill field; full backend reruns passed afterwards.
- The new handoff regression first failed because revocation warnings were lost; only the allowlisted boolean/static warning is now retained. The focused regression and final full dashboard suite pass.
- The final mailbox guidance regression first failed on misleading label-only OAuth wording; the catalog now distinguishes Google's grant from Scout enforcement and warns about shared revocation. The final full dashboard suite passes.
- An added browser assertion initially expected unknown Slack body fields to be rejected. Inspection confirmed the specified boundary reconstructs an allowlisted body and drops them (already asserted by unit tests). The browser assertion now verifies that intended behavior rather than changing production semantics.
- Repeated browser runs reached the real registration rate limit (429) in the disposable local database. Inspected and reset only its test rate counters, then reran both affected suites successfully. Production guards and limits were not weakened.
- Temporary integration patches were preserved in ignored `.house/tmp/`; generated screenshots/build output remain ignored.

## Live acceptance and release gates — not done

1. Configure the dedicated mailbox `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, Slack OAuth client and stable `CONNECTIONS_KEY` through trusted server configuration. Identity-only `SCOUT_GOOGLE_*` configuration does not establish Gmail access. Never paste secrets into chat or an agent handoff.
2. With explicit authorized test-account consent, verify real Google consent, refresh and selected-label message/attachment reads; verify denied/cancelled/revoked/expired grants. Gmail restricted-scope/test-user/Workspace-admin/publication requirements differ from identity-only consent; External Testing refresh tokens may expire after seven days.
3. Verify real Slack installation and owner-selected channel access; verify real bunq sandbox installation/signatures/session renewal; verify optional real KSeF test access. Current provider adapter tests use controlled HTTP fixtures, not live services.
4. Google revocation is client/account-grant-wide, not workspace-specific. Disconnect or failed-flow cleanup may invalidate the same account's grant in another Scout workspace using the same client. The UI/docs warn about this and runtime errors are detected, but shared-grant reference counting is not implemented. Test this explicitly before multi-workspace live rollout; never promise upstream revocation isolation.
5. Review and apply the additive control-plane auth migration through `python -m cfo.db.migrate --check` / `--migrate` before deploying the dashboard. Deploy matching backend/dashboard and explicitly provision any needed organization worker. No production command was run here.
6. Repeat production OAuth, origin/cookie, tenant-isolation and smoke checks after a separately authorized rollout.

Owned local API/dashboard processes and the disposable PostgreSQL cluster were stopped after acceptance. Test data and evidence were retained; no application work was committed or published.
