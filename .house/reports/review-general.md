# General code review (Codex)

## Summary

The Scout, dashboard, and deployment paths are wired together, but the public API and several finance workflows need fixes before the demo.  
**1 BLOCKER · 7 MAJOR · 1 MINOR**  
**First:** Put the public API behind authentication, including task reads.  
**Second:** Make approval handling recoverable so a recorded decision also closes the task.  
**Third:** Validate invoice currency before KSeF or Scout marks a payment documented.

## A. Potential bugs

### BLOCKER

- `deploy/openshift/app/api.yaml:65`, `backend/scout/api.py:108`, `backend/cfo/api/auth.py:30` — The public API Route bypasses the dashboard login. Task detail returns raw step data without authentication, including full invoice PDFs when Scout stores them in `sent_file` steps (`backend/scout/loops/toolbox.py:377`). The write token leaves those reads open. If the optional Secret is absent or lacks the token, write endpoints also accept unauthenticated requests, and chat runs as CEO (`backend/scout/chat_api.py:121`). **Trigger:** A deployed Route with a task containing a `sent_file` step, or a deployment without `API_WRITE_TOKEN`. **Fix:** Keep the API internal where possible, expose only the OAuth callback if needed, and require authentication for private reads and writes.

### MAJOR

- `backend/scout/loops/liquidity.py:245` — An approval is committed before the audit write and graph resume. If either fails, the worker retries, sees the already-answered approval, and exits successfully while the task is still waiting. **Trigger:** A transient database error during the audit insert immediately after the approval commit. **Fix:** Make the task transition idempotent and let retries finish an answered approval whose task is still open.
- `backend/scout/jobs.py:34`, `backend/scout/worker.py:91` — A worker restart can strand a claimed job in `running` forever. Claims only consider `queued` jobs, and worker startup has no recovery step. A stranded `ksef_connect` job also leaves the connection phase `running`, so another click returns 409 (`backend/scout/ksef_sync.py:128`). **Trigger:** The worker pod is terminated after claiming a KSeF sync and before the handler updates its phase. **Fix:** Recover expired claims with a lease and make the affected handlers safe to replay.
- `backend/scout/ksef_sync.py:195`, `backend/scout/loops/invoicing.py:159`, `backend/scout/connectors/invoicing.py:71` — Reconciliation compares nominal amounts across currencies. The sync can link a PLN invoice to an equal-numbered EUR debit, and the live filing path drops the invoice currency and stores it as EUR. **Trigger:** A trusted supplier’s PLN 1,000 invoice with the same invoice number as an open EUR 1,000 debit. **Fix:** Carry currency through `InvoiceDoc` and the repository write, and require matching currencies or an explicit FX reconciliation.
- `backend/scout/loops/invoicing.py:257`, `backend/cfo/forecast/liquidity.py:244` — Scout tells the user it added an expected refund to the forecast, but the forecast schedule never reads `expected_cash_events`. The dashboard can show the refund in Upcoming cash events while its chart and Scout’s liquidity review omit it. **Trigger:** The `chat_refund` demo records an expected refund that falls within the forecast horizon. **Fix:** Feed pending refunds into the shared forecast schedule and remove them when the corresponding bank credit is reconciled.
- `backend/scout/loops/invoicing.py:345`, `backend/scout/loops/invoicing.py:387` — Any reply suppresses the pending reminder, even when the task stays open. A “send it later” reply tries to schedule another reminder with the same dedupe key because it adds only a `note`, so the enqueue is ignored. **Trigger:** The invoice owner promises to send the PDF later and never sends it. **Fix:** Track a reminder deadline or generation per unresolved request and reschedule it after an incomplete reply.
- `backend/scout/loops/chat.py:373` — Scout saves CEO rules and says they will apply “from now on,” but no live decision path loads those rules. The only production caller of `Repository.list_rules()` is the MCP listing tool, and Scout’s liquidity review receives no stored policy context. **Trigger:** The CEO saves “always ask me before equipment purchases,” then Scout processes a matching payment. **Fix:** Load active rules at the decision points they govern, or disable rule creation until enforcement is wired.
- `backend/scout/loops/liquidity.py:49`, `backend/cfo/forecast/liquidity.py:252` — A what-if for the current month is placed on the first, which can already be outside the forecast. The model starts the day after the latest booking. **Trigger:** On November 21, with a booking on November 20, ask about buying an oven in November. Scout schedules it for November 1 and reports that the forecast failed. **Fix:** Preserve explicit dates and put month-only scenarios on the first forecastable day within the requested month, or ask for a date if the month has passed.

### MINOR

- `backend/cfo/api/services.py:68`, `backend/scout/loops/liquidity.py:44` — The dashboard forecast honors `SAFETY_BUFFER_EUR`, but the worker’s liquidity review derives its own buffer. **Trigger:** Set `SAFETY_BUFFER_EUR=25000` with data whose largest fixed-cost month is €17,730. The dashboard and Scout can give different answers about the same balance. **Fix:** Share the buffer calculation and configuration between both entry points.

## B. Unused or dead code

| Path / symbol | Evidence | Safe to delete? |
|---|---|---|
| `deploy/openshift/app/configmap.yaml:18` and `.env.example:29` — `MCP_COMPANY_DB_URL`, `MCP_WORKSTATIONS_URL`, `MCP_HR_URL` | `git grep` finds the three keys in configuration and docs, with no application reader. Scout builds its connectors directly. The MCP server entrypoint is separate. | Yes for the keys. Check external MCP clients before removing the server deployments. |
| `backend/scout/policy.py:153` — `context_from_transactions` | `git grep` finds callers only in `backend/tests/scout/test_policy.py` and `backend/tests/test_llm_robustness.py`. The remaining production hit is a docstring. | Check whether this helper will be used to wire rule enforcement. Otherwise remove it with its dedicated tests. |
| `backend/cfo/forecast/liquidity.py:140` — `LiquidityProposal.to_payload`, `to_event`, `LIQUIDITY_SIGNAL` | The only executable caller of `to_event` is `backend/tests/test_liquidity_gaps.py`. `to_payload` is called only by `to_event`. Scout consumes the proposal fields directly. | Safe for current in-repo callers after removing the legacy serialization test. Check external Python consumers first. |
| `backend/scout/api.py:87` — `GET /v1/tasks/stream` | No in-repo client references the route. The dashboard refreshes every five seconds in `dashboard/components/dashboard.tsx:174`. | Check external API consumers first. |
| `dashboard/lib/finance-model.ts:19` — `DashboardData.q4` | A tracked-code search finds the declaration and the live and preview producers, with no UI or test reading it. Current-quarter spend uses `quarterSpend`. | Yes. Remove the field and simplify the annual totals query. |
| `backend/cfo/api/services.py:68` and `backend/cfo/forecast/liquidity.py:190` — fixed-cost buffer calculation | Both paths group the same fixed-cost categories by booking month and take the largest monthly total. Their override and empty-data behavior differ. | Consolidate after choosing common override and empty-data behavior. |
| `docs/api.md:751`, `docs/api.md:810`, `docs/openapi.json:881` — December replay API contract | The checked-in docs advertise `/v1/events` and `/v1/sim*`. The live app mounts Scout’s routes and the API skeleton test asserts that the legacy base routes are absent. | Yes. Remove the replay instructions and regenerate the OpenAPI snapshot from the mounted app. |

## C. Notes

- Static review of `main` at `418f825`. The checkout stayed unchanged. I skipped the Postgres-backed suite because no local Postgres listener was visible, and made no cluster calls. The deployment findings describe the checked-in manifests.
- The highest-value regression tests would cover private task reads through the public ingress, worker interruption during approval, and invoice reconciliation with equal amounts in different currencies.

---

# General code review, pass 2 (Codex): duplication and missed issues

## Summary

**14 duplication findings · 0 new BLOCKERs · 8 MAJOR · 3 MINOR · 5 unused symbols · 3 additional demo risks.**  
**First:** Make Gmail fail closed when its configured label is missing, and save the watcher cursor only after reply jobs are durable.  
**Second:** Preserve the direction of what-if scenarios so the dashboard charts incoming cash correctly.  
**Third:** Make invoice jobs resume after a partial failure so a pickup step cannot leave an invoice task stranded.  
Static review of clean `main` at `418f825`. Pass 1 findings are excluded. No files changed, no tests run, and no network or cluster calls made.

## A. Duplication

| Locations | What is duplicated / how they differ | Keep | Effort |
|---|---|---|---|
| `backend/scout/loops/liquidity.py:333`, `backend/cfo/forecast/liquidity.py:244`, `dashboard/lib/overview-model.ts:11` | The worker models a dated, signed cash event. The dashboard reconstructs it from the task title, takes the absolute amount, and always subtracts it. This reverses incoming cash in the chart. | Backend scenario semantics. Expose the effective date and signed amount as structured fields. | M |
| `backend/cfo/api/services.py:122`, `backend/cfo/api/services.py:161`, `backend/cfo/forecast/model.py:384`, `backend/cfo/forecast/liquidity.py:497` | Model selection is split between an API cache with MLflow alias resolution and a worker path that loads or trains from disk. The two deployments have separate local files, so a registry promotion or cold start can give them different models. | Shared forecast model provider with the API’s registry and fallback policy. | M |
| `backend/cfo/api/services.py:197`, `backend/cfo/forecast/liquidity.py:269` | Both detect contiguous stretches below the liquidity buffer and find the minimum. The review also returns recovery date and shortfall. No boundary mismatch found for the same daily series and buffer. | `detect_gaps`, with a thin API response mapper. | S |
| `backend/scout/api.py:109`, `backend/scout/tasks.py:59`, `backend/scout/tasks.py:141`, `dashboard/lib/scout.ts:71` | Three paths read task rows and steps. The API and Next.js also independently assemble audit and approval data. Next.js selects fewer step fields, adds transaction details, and sorts audit rows by timestamp then ID. | One backend task read service with explicit list and detail projections. Preserve the dashboard’s narrow field selection. | L |
| `backend/scout/models.py:11`, `backend/scout/schema.sql:12`, `dashboard/lib/scout-model.ts:2`, `dashboard/components/dashboard.tsx:45` | Status and resolution values are repeated across Python, SQL, and TypeScript. The dashboard widens `loop` to `string`, then maintains a separate four-item list, while Python and SQL also admit `expenses`. Current status and resolution values agree. | Backend task contract for generated client types. Keep SQL constraints and verify their parity in a contract test. | M |
| `backend/cfo/api/services.py:292`, `backend/cfo/db/repo.py:137`, `dashboard/lib/finance.ts:88`, `dashboard/lib/scout.ts:51` | Three queries define undocumented payments. The dashboard paths require a debit, while the API counts any undocumented booking with an invoice reference. Credits can inflate the API count and reduce its outstanding total. | A shared debit-only payment query for the API and dashboard. | M |
| `backend/cfo/data/source.py:24`, `backend/cfo/data/source.py:92`, `backend/scout/loops/context.py:20`, `backend/scout/watchers.py:62` | Seed and live ingestion infer invoice numbers with different regexes. Seed accepts four named prefixes and unlimited digits. Live accepts a wider prefix set, limits digits, and supports a suffix. The same reference can produce different ledger metadata. | One parser with fixtures for historical and live references. | S |
| `backend/scout/chat_api.py:56`, `backend/scout/worker.py:155` | Both build a `JobContext`, Slack client, notifier, HR adapter, and connector registry. Web chat uses its own client timeout and name cache. The worker also registers Socket Mode, links HR records, and adds a checkpointer. | Shared context construction with channel-specific setup at the entry points. | M |
| `backend/scout/api.py:129`, `backend/scout/jobs.py:21`, `backend/scout/slack_ui.py:286` | API approval hand-writes the same queue insert, dedupe key, and notify behavior used by Slack through `JobQueue`. The API ignores whether the insert won and can echo the opposite of the decision already queued. | `JobQueue` enqueue logic with a caller-owned transaction and an explicit duplicate result. | S |
| `dashboard/lib/scout-api.ts:6`, `dashboard/lib/finance.ts:53`, `dashboard/lib/llm-usage.ts:5` | Three server-side backend clients repeat base URL, fetch, timeout, and cache setup. Usage strips a trailing slash, while forecast and Scout interpolate it directly. Scout requires a write token. | One URL and request helper with explicit authentication and per-call deadlines. | S |
| `dashboard/lib/scout-model.ts:168`, `dashboard/components/dashboard.tsx:37`, `dashboard/components/staffing.tsx:33`, `dashboard/components/overview/panel.tsx:4` | Several dashboard components instantiate the same EUR formatter. Scout can supply a currency and the overview intentionally rounds some figures to whole euros. | Shared dashboard formatter with currency and precision options. | S |
| `backend/scout/api.py:33`, `backend/scout/connectors/mail.py:19` | Google’s token endpoint and Gmail read scope appear in both consent/exchange and refresh code. Consent also needs `openid email` for identity. Values agree today. | Shared Google OAuth constants, with identity scopes added by the consent flow. | S |
| `dashboard/components/access-policies.tsx:4`, `backend/scout/audit.py:11`, `backend/scout/slack_ui.py:240` | The dashboard hand-maintains role and access descriptions. It omits finance access to bank and invoicing, and says Scout denies channel answers even though the Slack handler accepts app mentions. | Backend policy and explicit channel behavior as the source for dashboard copy. | M |
| `deploy/openshift/app/api.yaml:35`, `deploy/openshift/app/scout-worker.yaml:39`, `deploy/openshift/app/mcp.yaml:27`, `deploy/openshift/app/bank-mock.yaml:27`, `deploy/openshift/app/usage-sampler.yaml:29`, `deploy/openshift/db/seed-job.yaml:33` | Deployments repeat raw URI interpolation. The seed job builds the same DSN with percent-encoded credentials, so seed can succeed with a password the app pods cannot parse. | One consistently encoded connection configuration consumed by all workloads. | M |

## B. Potential bugs

### BLOCKER

None newly identified.

### MAJOR

- **Gmail label failures widen mailbox access.** `backend/scout/connectors/mail.py:147`, `:177`, `:201`. A missing or renamed `Amalfi` label makes both search and history polling omit the label filter. Scout can inspect unrelated messages while the UI says access is label-scoped. **Fix:** Fail closed when a configured label cannot be resolved and surface the connection error.
- **The mail watcher can permanently skip an invoice reply.** `backend/scout/watchers.py:89`, `:103`, `:108`. The watcher commits the new cursor before it downloads attachments and enqueues reply jobs. A transient download or database failure after that commit drops the affected messages from subsequent polls. **Fix:** Checkpoint only after processing the batch successfully and use the existing job dedupe keys on replay.
- **Gmail history polling can drop later pages.** `backend/scout/connectors/mail.py:201`. When history listing returns a `nextPageToken`, the connector consumes only the first page and saves the returned `historyId`. Invoice replies on later pages can be skipped. **Fix:** Drain the history pages before advancing the checkpoint.
- **The dashboard charts incoming cash as an expense.** `backend/scout/loops/liquidity.py:339`, `:360`, `dashboard/lib/overview-model.ts:11`, `:26`. A future client payment is stored as positive and added by the worker. The dashboard applies `Math.abs` and subtracts it, potentially showing a shortfall for the same scenario Scout says improves liquidity. **Fix:** Plot the signed amount and effective date from the worker’s structured scenario data.
- **An invoice retry can report success after doing no work.** `backend/scout/loops/invoicing.py:351`, `backend/scout/worker.py:96`. If an error escapes after `picked_up` is written, such as a database failure recording a later step, the retry sees an existing step and immediately returns. The worker marks the job done while the task can remain in progress. **Fix:** Resume from durable milestones and short-circuit only after a completed handoff or terminal outcome.
- **Unrelated credits and replayed bookings can settle refunds.** `backend/scout/watchers.py:57`, `:63`. A credit marks the earliest expected event with the same amount as arrived, without checking the counterparty or reference. The update also runs when the ledger insert hits `ON CONFLICT DO NOTHING`, so a retry after a cursor-write failure can settle another equal-value refund. **Fix:** Link settlements to a matching bank transaction and enforce a unique settlement per transaction.
- **An opposite approval can be acknowledged as queued.** `backend/scout/api.py:129`, `:138`, `:143`. With the worker delayed, approve and then reject the same pending request. The second insert conflicts, but the response echoes `approved: false` while only the approval is queued. **Fix:** Return the existing queued decision for identical retries and a conflict for an opposite decision.
- **Concurrent file reads can corrupt audit attribution.** `backend/scout/connectors/files.py:24`, `:46`, `:55`, `backend/scout/worker.py:144`, `:182`. The worker threads share one files connector whose logger stores the current `task_id` in a mutable field. When two jobs overlap, one can overwrite the other’s ID before the audit write. **Fix:** Bind audit context per operation and keep the shared connector stateless.

### MINOR

- **Invoice line items display the wrong currency.** `dashboard/lib/invoice-model.ts:40`, `dashboard/components/transactions.tsx:97`, `:106`. A non-EUR invoice header uses its currency, but unit prices, line amounts, and the footer default to EUR. The mismatch banner also compares nominal amounts across currencies. **Fix:** Pass invoice currency to every line formatter and compare totals only within the same currency.
- **Credits skew the undocumented-payments KPI.** `backend/cfo/db/repo.py:152`, `backend/cfo/api/services.py:304`, `dashboard/lib/finance.ts:91`. A positive booking with an invoice reference increases the API count and reduces the reported outstanding total, while the dashboard excludes it. **Fix:** Use the same debit-only predicate in both surfaces.
- **A bank page boundary can skip transactions.** `backend/scout/connectors/bank.py:45`, `backend/scout/mock_bank/store.py:57`, `backend/scout/watchers.py:71`. If the 1,000th row shares its timestamp with an omitted row, the next bank query uses strict `booked_at > cursor` and never returns the omitted row. **Fix:** Paginate by `(booked_at, id)` or use an inclusive timestamp with a continuation key.

## C. Unused or dead code

| Path / symbol | Evidence | Safe to delete? |
|---|---|---|
| `backend/cfo/api/deps.py:16` — `mark_stub` | `git grep` finds the definition and historical House reports. No mounted router or test calls it. | Yes for in-repo callers. |
| `backend/cfo/usage/recorder.py:52` — `current_context` | `git grep` finds only the definition. The recorder accesses the context internally. | Yes for in-repo callers. |
| `backend/cfo/forecast/model.py:288` — `train_from_db` | `git grep` finds the definition and historical House reports. Production forecast paths use `train` or `get_model`. | Check external Python or notebook consumers first. |
| `dashboard/components/brand.tsx:14` — `ScoutLockup` | `git grep` finds the definition and a design-plan reference. No component or test imports it. | Yes. |
| `dashboard/components/view-skeletons.tsx:87` — `SKELETON_VIEWS` | `git grep` finds only the declaration. The skeleton component is called directly with view names. | Yes. |

## D. Demo risks and missing tests

- **MAJOR — A green rollout can precede usable data.** API readiness calls the database-free `/healthz`, and dashboard readiness also checks a data-free endpoint. The seed job runs independently. Before the documented seed wait completes, both rollouts can look healthy while the dashboard has no usable data. **Fix:** Add a seeded-schema readiness check and gate the demo on the documented seed wait and an authenticated dashboard smoke check. `deploy/openshift/app/api.yaml:45`, `backend/cfo/api/app.py:73`, `deploy/openshift/dashboard/dashboard.yaml:54`, `docs/cluster-setup.md:65`
- **MAJOR — Slow Gmail polling can delay the bank demo.** Bank, mail, scheduling, connector refresh, and notifier flush share one serial watcher thread. A slow Gmail batch performs multiple synchronous requests before the next bank tick. **Fix:** Give mail polling an explicit time and batch budget, and isolate it from the bank watcher. `backend/scout/worker.py:130`, `:194`, `backend/scout/connectors/mail.py:123`, `:201`
- **MAJOR — A web chat can time out while Scout keeps acting.** The dashboard proxy gives the request 120 seconds, while the backend waits up to 150 seconds for each next event and runs the turn in a separate thread without a disconnect signal. A user can see a timeout, retry, and have both turns continue. **Fix:** Propagate one deadline and cancellation into the backend turn and dedupe side-effecting actions across retries. `dashboard/app/api/chat/route.ts:22`, `backend/scout/chat_api.py:147`, `:171`, `:176`
- **Missing ingestion regression tests.** The existing tests cover basic bank dedupe, a successful mail reply, and Gmail query and label construction. Add a missing-label case, multi-page history, a transient attachment failure before checkpointing, and a bank batch split across identical timestamps. `backend/tests/scout/test_worker.py:25`, `:43`, `backend/tests/scout/test_connectors.py:131`
- **Missing cross-surface forecast test.** The worker test verifies an inflow stays positive, while the dashboard test covers only an outflow. Add a positive scenario fixture that checks the delta and effective date on the chart against the worker result. `backend/tests/scout/test_other_loops.py:93`, `dashboard/tests/overview.test.ts:25`
- **Missing failure and concurrency tests.** Inject a failure after invoice pickup, submit opposite approval decisions before the worker claims either, and interleave two file searches on one connector. Assert forward progress, an accurate approval response, and correct audit task IDs. `backend/scout/loops/invoicing.py:357`, `backend/scout/api.py:138`, `backend/scout/connectors/files.py:46`
