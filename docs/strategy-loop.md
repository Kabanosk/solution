# Daily strategy news review

Open **Loops → Strategy → Daily news settings**. GPT is configured automatically when the dashboard has `API_KEY`; OpenShift reads it from the existing `gpt-key` Secret's `API_KEY` field. The key stays on the server. The provider is also offered automatically in Scout chat's model selector to workspace owners. Granite remains the initial chat model.

Owners can replace this default by registering OpenAI or Mistral in Strategy, or selecting **Save provider for the daily Strategy loop** in Scout chat's provider dialog. The saved workspace provider takes precedence. Chat-only credentials retain their existing tab-memory lifecycle. Removing a chat provider does not delete the saved Strategy provider; replace it in Strategy settings or pause the loop.

Add company context (industry, location, markets and interests), customize the system prompt, enable automatic research and save. Only the supplied context, organization name and prompt go to the provider; private records are never loaded. Native web search scans news from the last 24 hours and requires public citations. Models/account access must support web search. Groq is not supported for news. Model defaults to `gpt-4.1-mini`; override with `SCOUT_GPT_MODEL` if needed.

Each invocation creates a Strategy task with start/completion/error activity. Successful tasks include a report and source evidence; failures are escalated with a safe error. **Run review now** runs using the last saved settings, even while the automatic loop is paused. Refreshing the dashboard shows scheduled results through the existing task list.

## Dashboard pod scheduling

`instrumentation.ts` starts a cron-style scheduler inside the long-running dashboard Node process when `SCOUT_STRATEGY_SCHEDULER=1`. It checks persisted due times on startup and every minute; it does not require a browser, a separate Kubernetes CronJob pod or the backend worker. First enablement is due immediately; each attempt advances the due time by 24 hours, including failures. Manual reviews also reset that clock. Pausing retains settings and keys. Downtime produces one catch-up review on restart, rather than replaying every missed day.

Postgres advisory locks serialize manual/scheduled runs and replicas per organization. Task creation and advancing the schedule are atomic. After a crash, an unfinished task is marked interrupted when the next review starts. A crash after calling the provider can incur usage without a completed result; external calls are not exactly-once. Runs have a two-minute provider timeout. The former worker's fixed-feed strategy schedule is disabled to prevent duplicate daily briefings; its legacy handler/API remain available for older integrations.

## Deployment

1. Apply the normal migration with the backend environment configured: `python -m cfo.db.migrate --migrate`. This adds `scout_auth.strategy_settings` and retains tenant-local task storage.
2. Deploy the updated dashboard and backend worker. The dashboard manifest sets `API_KEY` from `gpt-key/API_KEY` and enables the scheduler.
3. Optionally create `scout-strategy` with an `encryption-key` field containing a stable random secret of at least 32 characters. This sets `SCOUT_STRATEGY_SECRET` for AES-256-GCM encryption of user-supplied provider keys, bound to organization ID. Otherwise `API_KEY` supplies the encryption secret. Keep the encryption secret stable: rotating it without re-encrypting saved credentials requires registering those providers again.
4. In Strategy, save company context and enable the loop. Provider setup requires no user key entry when `gpt-key` is available.

Settings and manual runs require an authenticated workspace owner and same-origin writes. No credentials are returned by settings/chat metadata APIs. Research failure never exposes raw provider errors. Provider charges apply.

## Verification

Dashboard typecheck, unit tests and production build cover the implementation. Strategy tests cover encrypted credential isolation/tampering, the API_KEY default, bounded settings, native web-search prompts and citations. Existing market-research tests cover OpenAI/Mistral transport failures and citation validation. Light/dark browser checks at 375px and 1280px cover settings, provider registration and manual runs with mocked APIs. The standalone backend Strategy tests pass; database integration checks could not run because local PostgreSQL on port 5434 was unavailable. Live provider calls were not exercised.
