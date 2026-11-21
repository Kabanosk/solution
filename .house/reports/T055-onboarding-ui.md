# T055 onboarding UI — INTERIM report (browser acceptance pending)

Status: implementation + unit tests done; browser e2e NOT passed (inconclusive, see below). No commits.

## Patch
`.house/reports/T055-onboarding-ui.patch` (git diff --cached --binary, 7 files, shared `onboarding-model.ts` excluded). Apply from the repo root.
Files (all under dashboard/): components/onboarding.tsx (new), lib/onboarding-client.ts (new), components/dashboard.tsx, components/connections.tsx, lib/nav.ts, tests/onboarding-client.test.ts (new), tests/onboarding-ui.e2e.cjs (new). authenticated-browser.cjs untouched.

## Behaviour
- `#setup` is a native route (nav `View` "setup", not a sidebar item; nav test ordering unchanged). Ordinary owner load with no hash: GET /api/onboarding; pending/in_progress -> `#setup` (replace). not_started/dismissed/completed stay chat; any explicit hash is preserved and then no lookup is made; non-owners never auto-open; lookup failure leaves chat.
- Six steps in contract order with CTAs: Connect sandbox bank / Use built-in invoicing / Set up test environment / Continue with Google / Connect workspace / Answer a few questions. Status text distinguishes Connected, Sandbox connected, Needs setup (Administrator setup required for network), Connecting, Failed, Skipped, Not started; server messages and non-null display facts are shown. Failures are shown, never applied locally; 409 reloads the latest state and tells the user the change was not applied.
- Per-step Skip/Set up (PATCH step), Disconnect (DELETE), Check status for connecting. KSeF NIP (10 digits) validated client-side. Optional bunq sandbox API key field (password, empty = sandbox). Company network: conditional questions, company-only consent, secret/key-like input and oversize rejected, no file/secret fields.
- Google/Slack: POST /api/onboarding/connect/{google|slack} with body `{}` (client never sends redirect/proof; server owns them, per coordinator). Response `{url}` (https or loopback http only) -> window.location.assign; a 200 Setup (missing credentials) is rendered as the prerequisite. `?setup=<provider>-connected|failed` callback result is shown once and removed.
- Continue to Scout: PATCH `complete` if every step connected/sandbox/skipped, otherwise `skip` (dismiss); navigates only after the save succeeds. "Skip for now" = PATCH skip. Non-owners: read-only banner, no write controls, Continue just navigates.
- "Continue with your agent": POST /api/onboarding/handoff -> dialog with Copy setup link, Copy instructions (prompt), Download Markdown, Download JSON, optional "open in Claude Code" link only for claude-cli:// URLs; clipboard failure shows a manual-copy textarea; load error with retry.
- Connections: "Resume setup" strip (`#scout-onboarding-reopen`), still exactly six `[data-connection-row]`.
- IDs per coordinator: #scout-onboarding, -skip, -agent, -continue, -step-{id}, -skip-{id}, #scout-onboarding-reopen, "Copy instructions" label. Also -action-{id}, -status-{id}, -disconnect-{id}, -resume-{id}, -retry.

## Verification actually run
- TDD: tests/onboarding-client.test.ts written first; seen failing (module missing; later OAuth-body and prerequisite-response changes seen red) then green: 21/21.
- `npm exec --yes --package=node@22 -- npm test` (full dashboard suite): tests 102, pass 85, fail 0, skipped 17 (the skipped ones are the real-DB cases without SCOUT_AUTH_TEST_DATABASE_URL, as before).
- `npx tsc --noEmit`: clean.
- Not run: `next build`.

## Browser e2e: INCONCLUSIVE
`tests/onboarding-ui.e2e.cjs` (real signup + session, all /api/onboarding responses are in-browser MOCKS; no provider contacted) was run against my own dev server (:3055, new DB scout_t055_ui, migrated; UTF8 template0 needed). Host load average was >100.
- Run 1: page.goto 30s timeouts (cold compile, then load). Run 2 (domcontentloaded->commit, 60s/120s timeouts): a page.reload timed out at 120s. Run 3: reached the `#setup` reload scenario (section 2, after the first scenario sections and the not_started/dismissed/completed/#tasks/#chat checks passed) and failed at `locator('#scout-onboarding').waitFor` (60s) on the post-reload page.
- Diagnostics: the same sequence (goto #setup, reload, wait for #scout-onboarding) in an isolated script passed in 1.5s each with status 200 on page and /api/onboarding; only console error was a 503 from /api/llm-usage (no Python API running, unrelated). So the failure is not reproduced and is most likely shared-host load, but this is not proven; the full suite has never completed. Scenarios from 3 on (error/retry, connect states, skip/resume, Google, network, 409, leave, reopen, handoff, keyboard, read-only member, responsive/dark screenshots, raw-colour and unique-id checks) have NOT executed and are unverified. Assertions were not weakened. No screenshots were inspected.
- Coordinator should run `node tests/onboarding-ui.e2e.cjs` sequentially (env: BASE_URL, SCOUT_AUTH_TEST_DATABASE_URL, optional CHROMIUM_PATH; chrome at ~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome). It needs no Python API for assertions but /api/llm-usage etc. return 503 there. Note the existing app also asserts the "Your workspace is ready" heading in the empty overview, so locators in other suites should scope by #scout-onboarding.

## Limitations / notes for integration
- T054 route must accept POST bodies `{}` for slack and `{label?}` for google, return `{url}` or 200 Setup, and PATCH/POST/DELETE with Setup JSON responses (client validates strictly: version 1, six known steps).
- Newly signed-up orgs only open setup if T054's signup redirect lands on `#setup` or GET returns pending; existing signup e2e that expects the chat/overview after signup needs the coordinator's update.
- Environment side effects: created database scout_t055_ui on :55437 (kept until integration), untracked backend/.venv (gitignored) and untracked artifacts/ in the worktree (not staged); dev server :3055 stopped.
