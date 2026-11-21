# Onboarding release — 2026-11-21

## Authorized scope

The user explicitly requested publishing onboarding to main, deploying it, and keeping missing integrations addable through Connections. They then required one step at a time, a visible step count, and no left/dashboard navigation before finishing or skipping setup.

## Source and behavior

- Baseline onboarding/Connections published as `2807b7c`.
- Sequential frontend published as `3a23c45`, preserving upstream font fixes and the newer sidebar cleanup at `fe95170`.
- Main later advanced concurrently with independent guardrails changes at `64b4afc` while release evidence was being committed. That work is preserved; this report records the exact onboarding image revisions and does not claim that the independent guardrails change was deployed.
- Setup renders one of six steps with `Step N of 6`, accessible progress, Back/Next, individual skip and skip-all. Failed writes do not advance. Successful authorization remains visible before Next.
- Dashboard sidebar, topbar, mobile navigation, chat and search are not mounted during setup; initial route resolution avoids flashing navigation before pending setup.
- Connections has enabled owner-only Connect/Manage actions even for existing workspaces with neither integration rows nor an onboarding record. Each action selects its own `#setup?step=...` screen. Finishing setup refreshes connection status before normal workspace use.
- Provider security, tenant isolation and agent-neutral handoff are unchanged. Setup stays optional; network answers do not establish a VPN and signup does not deploy a tenant worker.

## Verification

- Backend full suites: 730 passed / 40 documented skips in Europe/Warsaw and independently verified UTC (previous integration evidence).
- Exact published frontend: 141 passed / zero skipped; TypeScript and clean optimized build passed.
- Local real-database onboarding and callbacks passed, including one rendered step and no dashboard navigation.
- Mock-provider sequential UI suite passed: progress, Back/Next, errors/conflicts, skip/resume, OAuth-return step, forms, handoff, keyboard focus, readonly members and responsive themes.
- Existing UI, Connections and delegated-reply browser regressions passed, including all six Connections actions in a workspace without a setup record.
- Public font byte/MIME/security checks, actual Inter font glyph checks, and organization authentication/tenant/CSRF browser suite passed.
- Local disk exhaustion interrupted one unit run and a dev-server run. Only this session's generated caches/build outputs and a finished worker's reproducible virtual environment were removed. Source and protected backups were preserved; clean-server reruns passed.

## Production migration and configuration

Explicit target: saved contest kubeconfig, verified OpenShift API `api.ocp.mwpzc.sandbox39.opentlc.com:6443`, namespace `cfo-agent-uwur`. The default context was an unrelated local Kubernetes cluster and was never mutated.

- Original application replicas/images recorded privately.
- Application writers stopped for backup/migration; original replica counts restored in a `finally` block.
- Whole PostgreSQL backup saved with private directory/file permissions outside Git.
- First restore rehearsal failed because the disposable target inherited SQL_ASCII; no production migration was performed on that attempt. A new UTF8 restored database passed the additive migration check and application. Production then passed `--check` and `--migrate`; existing organization/user/membership/session counts were preserved.
- Restored production copies were removed from the trust-authenticated local test cluster after validation. Protected backup archives were retained.
- Stable connection-encryption key generated directly into production Secret `scout-onboarding`; no value was printed or committed. API/worker reference this server-only Secret. Exact backend `SCOUT_APP_ORIGIN` configured without overwriting unrelated live settings.
- No root kustomization, seed/reset job, legacy-owner enrollment, production transfer, provider consent or employee message was run.

## Image rollout

Backend build `cfo-backend-31` used source `2807b7c`; its backend tree is unchanged by the later frontend-only correction. Built image digest:

`sha256:3298afc95cca949d56a3820b0b122e96ebeca4641cc42dc11954ea0d0cc10a56`

Actual production deployments lacked image-stream trigger annotations and were pinned to old digests. They were explicitly switched to the new digest. Both API and worker were verified ready on that exact digest, not merely by a successful rollout command. Anonymous API onboarding/docs and dashboard data requests return 401; API health returns 200.

Dashboard build `cfo-dashboard-22` completed using the allowlisted binary context packaged from `3a23c45` (no environment files, dependencies or company data). Its exact digest is `sha256:d57aabee90fb94fd007d1ae58897e339fbb6d4780093dff9f5262b199c86488e`. The deployment was explicitly switched to that digest; rollout completed and a ready production replica was verified on that exact image. Dashboard health is 200; public font byte/MIME/security checks and anonymous/Basic-only data denials passed on the production route.

A real production registration returned the expected `/<slug>#setup` redirect, but Chromium navigation was blocked by this host's `ERR_NETWORK_ACCESS_DENIED` restriction before page interaction. This is not counted as a live-browser pass. Its owned temporary workspace/schema/user were removed with explicit ownership checks. Local browser coverage passed. Separate authenticated production HTTPS acceptance passed: signup/setup redirect, six ordered persisted steps, empty private tenant, cross-tenant and CSRF denial, real built-in invoicing connection, honest Google/Slack prerequisites, completion/reload persistence, reopening missing email, secret-free handoff and logout. The second owned test workspace/schema/user was also removed, preserving all pre-existing organizations. No external provider consent or provider writes were exercised.

**Final status: requested onboarding/Connections changes are published on main and deployed.** A live Chromium UI run remains unverified because of the host restriction; it is not substituted by a fabricated browser success.

## Remaining provider limits

Google identity sign-in credentials do not authorize Gmail. Dedicated `GOOGLE_CLIENT_ID/SECRET` and `SLACK_CLIENT_ID/SECRET` are absent from the production onboarding configuration. Their steps must report prerequisites, not connected status. Real Google consent/refresh/label reads, Slack installation/channel reads, bunq sandbox verification and optional KSeF live-test access remain unverified. Google upstream grant revocation can affect other workspaces using the same account/client; the UI and docs warn about this.
