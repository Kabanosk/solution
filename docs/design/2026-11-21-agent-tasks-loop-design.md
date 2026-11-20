# Scout: real-time agent loop, jobs, connectors and dashboard

Status: draft v2 (2026-11-21), replaces v1. Client in the product: **Pizzeria Amalfi**. Product name: **Scout**.

## Why

The pitch (`presentation/` in the team folder) shows **Scout**, an autonomous CFO agent. It notices something
happening in the business, checks what already exists, handles the next step when it can and asks a person only
when it needs one. Every case is a **job** with a readable activity log that finance can trust.

The current backend can't show that. It replays a simulated December 2025 from a file, writes flat `decisions`
rows, never shows that it's working, doesn't react to Slack DMs, reads email only from `.eml` files, and is
hard-wired to its data sources. Pedro's decision (2026-11-21): **throw away the replay and the old agent loop and
rebuild the agent core in real time.** Judges will see the slides and the product side by side.

## Goals

1. **Real time, no replay.** Scout reacts to things as they happen now:
   - a payment appearing in the (mock) bank API;
   - a mail arriving in an authorized mailbox;
   - a Slack message or DM;
   - a schedule tick, such as the daily cost check.
2. **Jobs.** Every case is an agent task with an id like `LAN-154` (prefix configurable, default `SC-`; see
   decisions). A task has a loop, status, owner, resolved-via, evidence, category, amount and linked records.
   Follow-ups (reply, reminder, approval) continue the **same** task.
3. **Activity log as in the deck.** Code writes one human-readable, timestamped line per step at the moment it
   happens, never as an LLM summary afterwards. Each line records the source checked and how many results it found.
   For example:
   - "Scout picked up the card payment from Revolut Business"
   - "Searched the invoicing system: 0 matches"
   - "Checked authorized files and company email: no receipt"
   - "Checked authorized chat history: no context"
   - "Asked the card holder, Grażyna, on Slack"
   - "Grażyna sent invoice FLV-42026.pdf"
   - "Resolved · €1,250 filed as …"
4. **Evidence ladder.** Checks run in this order: system of record (invoicing) → authorized files and company
   email → authorized chat history → a Slack DM to the right person, **only if nothing explained it**. When an
   earlier check finds the answer, the rest are skipped.
5. **Slack: a DM starts or continues a job, with a visible working state.** Scout reacts with 👀 straight away and
   posts "Working on it…". It edits that message live as steps happen and ends it with the result. A reply to an
   open job, including a **PDF** attachment, continues that job.
6. **Audit log.** Every connector read, every DM sent and every access-policy decision is recorded with
   ALLOW / DENY, requester, role, target, resource and policy, as in the deck's "Built-in IAM" slide.
7. **Connectors behind small interfaces**, enabled per company in a `connections` table, so another company plugs
   in its own without touching the loop:
   - Bank: a mock Revolut Business API, fed from the CSV.
   - Invoicing: Postgres, treated as a real system.
   - Mail: Gmail API, one-button Google connect; Google Workspace through domain-wide delegation.
   - Chat: Slack.
   - Files: company laptops.
8. **The Scout dashboard shows the work:**
   - It is renamed from Lantern to **Scout**, with the deck's hat logo.
   - The left sidebar is Linear-style.
   - Pages: Agent tasks (grouped by status), task detail with the full activity log and evidence, Audit log,
     Connections (with **Connect Google**), and Overview (cash, forecast, "Explained by Scout").

## Non-goals

- OCR or photos. Receipts are **PDF**, read as text.
- Real bank or invoicing integrations. These are mocks behind interfaces, treated as working systems.
- Moving money. Strategic actions need approval, and execution is always SIMULATED.
- Multi-company tenancy in the database. There is one company per deployment.
- Keeping the December replay, the simulator, the old LangGraph graph, the old chat router, or their tests and eval.

## Anti-goals (a failure even if it "works")

- Log lines that differ from what actually happened.
- A DM when the answer was already in invoicing, mail or chat.
- Reading private folders, unauthorized mailboxes or labels, or channels the bot wasn't added to.
- Secrets or mail content in the public repo.
- The worker silently stopping. Every failure must be visible as an `error` step on the job and in the logs.

## Constraints

- **Repo:** the GitHub repo `Kabanosk/solution` is public, and a PR targets `main`.
- **Cluster:** OpenShift project `cfo-agent-uwur`; Granite 4.0 H Tiny on vLLM (OpenAI-compatible, tool calling
  works); Postgres 16.
- **Stack:** Python 3.12, uv, FastAPI, psycopg 3, LangGraph (kept for its Postgres checkpointer and interrupt/resume
  of approvals), slack_bolt in Socket Mode, and the Next.js dashboard reading Postgres server-side.
- **Slack:** the scopes are updated, per Pedro: `channels:history`, `groups:history`, `files:read` and
  `reactions:write` are added on top of the existing ones. The bot must be invited to `#finance`.
- **Gmail:** read-only scope `gmail.readonly`. The OAuth client must be of type *Web application*, with redirect
  URI `https://api-cfo-agent-uwur.apps.ocp.mwpzc.sandbox39.opentlc.com/v1/connections/google/callback`. The client
  id and secret go in Secret `google-oauth`.
- **Deadline:** feature freeze 2026-11-22 07:00. The scope is prioritised P0, P1 and P2 (see "Order of work").

## Approaches (decided with Pedro)

| Topic | Picked | Considered | Why |
|---|---|---|---|
| Agent core | New job runtime: signal watchers → Postgres job queue → worker pool running a ladder graph per job; the LLM extracts, classifies and explains; code decides | Patch the old graph; free-form ReAct agent | Pedro: throw the old code away. A small model needs a fixed ladder for reliable logs |
| Time | Real time, by wall clock; the bank mock serves the CSV history shifted to end before today, and new payments appear live | December 2025 replay | Pedro: "real time window", no replay |
| Loading state | 👀 reaction plus one "Working on it…" message edited live | Slack assistant "is thinking…" | Works with the current app and shows the steps live |
| Email | `MailConnector`: Gmail (one-button OAuth), Workspace (domain-wide delegation), plus a local folder for tests | `.eml` only | Real integration, one click for companies |
| Receipts | PDF → text | OCR | Demo uses PDF |
| Company / name | Pizzeria Amalfi / Scout | Acme / Lantern | Pedro |

## Design

### Overview

```
 Revolut mock API ──poll 5s──▶ BankWatcher ─┐
 Gmail / Workspace ─poll 30s─▶ MailWatcher ─┤
 Slack (Socket Mode) ─events─▶ SlackListener┼─▶ agent_jobs (Postgres queue) ─▶ worker pool (N threads)
 Scheduler (cost check daily, liquidity weekly, reminders) ─┘                       │
                                                                                     ▼
                                     Scout job graph (ladder) ──▶ connectors (audited) ──▶ agent_tasks / agent_task_steps
                                                                                     │                 audit_log
                                                                                     └─▶ Slack DMs, live status message
 Dashboard (Next.js) ◀── Postgres (tasks, steps, audit, connections, cash) ── API (FastAPI: tasks, approvals, OAuth, bank mock)
```

### Components and code layout (`backend/scout/`, new package)

The old `backend/cfo/{agent,sim,run.py,chat.py,eval,smoke.py}`, the replay generation, and their tests and manifests
are **deleted**. The parts that model the client are kept and moved where needed:
- `cfo/db` schema for company data: employees, teams, vacations, invoices, transactions;
- `cfo/data` CSV loading and the generation of invoices and workstation files;
- `cfo/forecast`, the cash forecast;
- `cfo/llm`, the OpenAI-compatible client;
- `cfo/usage`, LLM usage;
- `cfo/api`, read endpoints still used.

These are cleaned up where touched.

- `scout/connectors/`: one Protocol per kind and its implementations (below), plus `registry.py`, which builds the
  enabled connectors from `connections` rows and falls back to local defaults when there are none.
- `scout/watchers/`, each producing signals:
  - `bank.py` polls `BankConnector.list_transactions(since=cursor)`;
  - `mail.py` uses Gmail `history.list` from a stored `historyId`;
  - `slack.py` uses Bolt Socket Mode handlers;
  - `schedule.py` runs the daily cost check, the weekly liquidity review and due reminders.

  Cursors live in `watcher_state`. A watcher only inserts jobs.
- `scout/jobs.py`: the queue.
  - `enqueue(kind, payload, dedupe_key)` is idempotent.
  - Workers claim jobs with `FOR UPDATE SKIP LOCKED`.
  - Retries use backoff, up to 3 attempts. A final failure leaves an `error` step on the task.
- `scout/worker.py`: the `python -m scout.worker` entry point. It starts the watchers, the Slack socket and a pool
  of `SCOUT_WORKERS` (default 4) threads, and shuts down gracefully.
- `scout/loops/`: one module per loop, each a small LangGraph graph over a shared `JobContext`:
  - `invoicing.py`: the evidence ladder for a payment;
  - `cost_control.py`: cost vs metric, e.g. the cloud bill against orders;
  - `liquidity.py`: the forecast review leading to a credit line or investment proposal;
  - `chat.py`: a DM to Scout (question, rule, what-if) and replies to open tasks.
- `scout/tasks.py`: `TaskRecorder`, with `open_or_continue(dedupe_key, …)`, `step(...)` and `set_status(...)`. It
  notifies listeners: the Slack live message, and `NOTIFY scout_tasks` for the API stream.
- `scout/policy.py`: autonomy levels AUTO / NOTIFY / APPROVAL, ported from the old rules. Thresholds are relative to
  monthly inflows, a new supplier never gets AUTO, and CEO rules override the defaults.
- `scout/access.py`: access-policy checks, written to `audit_log`.
- `scout/slack_ui.py`: Block Kit messages, the live status message, and Approve / Reject buttons.

### Data model (new tables; old `decisions`, `approval_requests`, `rules`, `events`, `sim_state` and `agent_reminders` are dropped or replaced)

- `agent_tasks`: identity, state and links.
  - Identity: `id` (`SC-<n>` from a sequence), `loop` (`invoicing | cost_control | liquidity | chat | expenses`),
    `title`, `dedupe_key UNIQUE`.
  - State: `status` (`todo | in_progress | waiting | escalated | done | cancelled`), `owner_id`, `requested_by`,
    `resolved_via` (`invoicing | files | email | chat_context | slack_dm | approval | auto | forecast`),
    `outcome`, `level` (AUTO/NOTIFY/APPROVAL), `simulated`.
  - Links: `category`, `amount`, `currency`, `transaction_id`, `counterparty`, `slack_ref`, `approval_id`,
    `evidence` (jsonb list).
  - Times: `created_at`, `updated_at`, `closed_at`.
- `agent_task_steps`: `id`, `task_id`, `ts`, `kind`, `source`, `result_count`, `text`, `detail` (jsonb, never full
  mail bodies), `evidence` (jsonb).
  - Kinds: picked_up, checked_system, checked_files, checked_email, checked_chat, asked_person, reminder, escalated,
    reply_received, extracted, matched, mismatch, acted, approval_requested, approved, rejected, resolved, error,
    note.
- `agent_jobs`: `id`, `kind`, `payload`, `dedupe_key UNIQUE`, `status` (queued | running | done | failed),
  `attempts`, `run_after`, `task_id`, `error`, times.
- `approvals`: `id`, `task_id`, `approver_id`, `question`, `status`, `answered_by`, `answered_at`,
  `slack_ref`. LangGraph checkpointer thread = task id.
- `rules`: CEO policy rules, same meaning as before.
- `audit_log`: `id`, `ts`, `task_id`, `actor`, `requester`, `role`, `action`, `target`, `resource`, `decision`
  (ALLOW | DENY), `policy`, `reason`.
- `connections`: `id`, `kind`, `provider`, `enabled`, `display_name`, `config` (jsonb, non-secret), `status`,
  `last_sync_at`, `last_error`.
- `connection_credentials`: `connection_id`, `ciphertext`. Refresh tokens are Fernet-encrypted with
  `CONNECTIONS_KEY` from a Secret, and no endpoint ever returns them.
- `watcher_state`: `name`, `cursor` (jsonb), `updated_at`.
- `expected_cash_events`: `id`, `on_date`, `amount`, `label`, `task_id`, `status` (expected | arrived | missed).

### Connectors

- **Bank: `RevolutMockBank`.** A small FastAPI app (`scout/mock_bank/`, its own pod `bank-mock`) shaped like the
  Revolut Business API.
  - Read endpoints: `GET /api/1.0/accounts` and `GET /api/1.0/transactions?from&to&count`.
  - Data: it loads the organizer CSV and shifts every date by a whole number of weeks, so weekdays stay right and the
    history ends **before today**. Rows that would land in the future are not served.
  - Demo endpoint: `POST /mock/transactions` adds a payment *now*: counterparty, amount, reference with an invoice
    number, and an optional card holder. This is how live cases are started.
  - `BankConnector.list_transactions(since)` → `BankTxn{id, booked_at, amount, currency, counterparty, reference,
    card_last4, holder_employee_id}`.
- **Invoicing: `PostgresInvoicing`** over the `invoices` / `invoice_lines` tables.
  - Methods: `find(number|supplier|amount|around)` and `file(invoice, transaction_id)`. Filing refuses an amount
    mismatch, an already-documented payment, and an invoice already linked to another payment.
  - The old `attach_invoice` bugs are fixed here.
- **Files: `WorkstationFiles`** over the company folders of the laptops (Downloads, Mail, Documents only; no
  symlinks; every read audited). The generated planted PDFs are kept.
- **Mail:**
  - `GmailMail` uses Gmail API read-only. `config.label` limits reads to one label. It provides
    `search(query, since)`, `get_attachment()` and `changes(history_id)`, the last one for the watcher.
  - `WorkspaceMail` uses a service account with domain-wide delegation for `config.mailboxes`.
  - `FolderMail` reads `.eml` files from a folder. It is only for tests and offline dev, and is not the product
    path.
- **Chat: `SlackChat`.** `search(terms, since)` reads `conversations.history` of allowlisted channels the bot is in,
  over the last `CHAT_LOOKBACK_DAYS` (default 30), and matches in code.
  - A match needs the invoice number, or the supplier together with the exact amount.
  - The LLM then classifies the message as `explains_no_invoice | approval | refund_expected | unrelated`.
  - The message's permalink is the evidence.

### The invoicing loop (main demo)

Trigger: a new outgoing bank transaction.

1. `picked_up`, with source Revolut Business.
2. `checked_system`: search invoicing by invoice number from the reference, or by supplier, amount and date ±7 days.
   - Exact match: file it, done, resolved_via `invoicing`. The next steps are skipped.
3. `checked_files` and `checked_email`: workstation search and mail search, by number, supplier and amount. Candidate
   PDFs are read as text, the LLM extracts the invoice, and code matches the amount.
   - Match: file the invoice, done, resolved_via `files` or `email`, with the PDF as evidence. Scout DMs the folder
     owner that it looked.
4. `checked_chat`: a message in an authorized channel may explain the payment.
   - `refund_expected`: done via `chat_context`. An `expected_cash_events` row is added, and a follow-up task "Watch
     refund … due …" opens.
   - `approval`: done via `chat_context`, and the approval is recorded.
   - `explains_no_invoice`: done via `chat_context`.
5. Nothing found: `asked_person`. The person asked is:
   - the card holder, if known;
   - otherwise the document owner from HR;
   - if that person is on vacation, the next one up the HR chain.

   The task becomes `waiting`. A reminder job is scheduled after `REMINDER_AFTER_HOURS` (default 24; set to minutes
   for the demo). Scout then escalates along the chain, and finally to the CEO with status `escalated`.
6. `reply_received`: a DM reply, or a mail that arrives later, which the mail watcher matches to an open task.
   - A PDF reply is extracted and matched, then filed, and the task is done via `slack_dm` or `email`.
   - A text reply is classified as `explains`, which closes the task with the reply as evidence, or as
     `promises_later`, which keeps it waiting.
   - A mismatch triggers a clarification request.

Policy level: filing a found document is AUTO. Payments outside the policy add a NOTIFY or APPROVAL step to the
task.

### The other loops

- **cost_control** runs daily and on demand. It compares the latest month for three cost/metric pairs: food cost vs
  orders, ads vs new customers, and cloud vs online orders. An anomaly gets a task with steps like "Compared cloud
  cost with online orders: +50% vs +3%" and "Alerted Vincenzo and the IT owner" (NOTIFY), with the explanation in
  plain words.
- **liquidity** runs weekly, on demand and on a what-if. Steps: forecast, then gap below the safety buffer or a
  surplus, then a credit line or investment proposal, then an approval with buttons, then "Approved · SIMULATED".
- **chat** handles a DM to Scout that is not a reply. Steps record what Scout did, and each reply is its own task:
  - CEO data question: a tool loop over read-only connectors.
  - CEO rule: saved to `rules`.
  - What-if: hands over to the liquidity loop.
  - Anyone else's request: checked by the access policy; denied and audited if not allowed, as in the intern slide
    (Vanessa asks for Vincenzo's laptop files).

### Slack interaction

- **On a DM:** add the 👀 reaction, post "Working on it…" in the thread, then edit that message as steps arrive (at
  most one edit per second, showing the last 8 lines). The final edit carries the result and its level label. On an
  error, the final edit says what failed.
- **Asking someone:** the DM names the vendor, amount and date, says which sources were checked, and asks one clear
  question with an "Upload PDF" hint. Approvals use Approve / Reject buttons, and only the approver may click them.
- **Channel messages:** messages in allowlisted channels are not answered. They are only searched when a ladder
  needs chat context.

### API (FastAPI)

- Tasks: `GET /v1/tasks?status&loop&owner&q`, `GET /v1/tasks/{id}` (the task with its steps, audit rows and
  approval), and `GET /v1/tasks/stream` (SSE).
- `POST /v1/approvals/{id}:approve|:reject`.
- `GET /v1/audit`.
- Connections: `GET /v1/connections` and `POST /v1/connections/{id}:enable|:disable`.
- Google OAuth:
  - `GET /v1/connections/google/start` redirects to Google with `state` = an HMAC-signed nonce plus expiry. The
    dashboard calls it server-side after its own login.
  - `GET /v1/connections/google/callback` checks `state`, exchanges the code, stores the encrypted token, and
    redirects to the dashboard's `/connections`.
- Unchanged: the existing read endpoints for KPIs, forecast and costs. Endpoints for the deleted parts are removed.

### Dashboard (Scout)

- **Brand.**
  - Name **Scout**, with the deck's hat mark (`#mark` path from `presentation/index.html`) as a white glyph on a
    `#141416` rounded square.
  - Favicon and app icon from the same mark.
  - The tagline "Your autonomous CFO" appears on the login and empty states.
- **Sidebar, Linear-style:**
  - Header: workspace switcher ("Pizzeria Amalfi" with the Scout mark), plus search and new-task icon buttons. ⌘K
    opens the command palette.
  - Primary items: **Inbox** (my waiting items), **My tasks**, **Agent tasks**.
  - Sections, collapsible with small uppercase headers:
    - **Loops**: Invoicing, Cost control, Liquidity, Chat, each with a live dot and an open-task count.
    - **Finance**: Overview, Transactions, Forecast.
    - **Workspace**: Connections, Audit log, Access policies.
  - Style: compact 28–30 px rows, 13 px text, subtle hover and active background, counts on the right, icons from
    lucide.
  - Footer: user, theme toggle and a collapse button. Keyboard: `G` then `T` goes to tasks, `G` then `I` to Inbox.
- **Agent tasks:**
  - A list grouped by status (In progress, Waiting, Escalated, Todo, Done), as in the mockup's right column.
  - Each row: priority bars, id, status icon, title, a channel chip (Slack / Auto / Chat context / Email / Forecast /
    CFO) and the owner avatar.
  - Filter bar for loop, owner and status. Live updates from SSE, falling back to polling every 5 s.
- **Task detail** (the deck's "workspace" slide):
  - Breadcrumb `Agent tasks › SC-154` and a header with counterparty, account or card, date and amount.
  - **Activity log:** a timeline of steps with time, source icon and result count. A spinner sits on the step that
    is running.
  - Right panel: Status, Loop, Owner, Level, Resolved via, Evidence (PDF name opens the extracted text and file, mail
    subject, chat quote with permalink), Category and the linked transaction.
  - Pending approvals show Approve / Reject; the single dashboard login counts as the CEO.
  - The audit entries for this task appear below.
- **Audit log:** a filterable table with an ALLOW / DENY badge and the policy.
- **Connections:** cards for Revolut Business (mock), Invoicing, Email, Slack and Company laptops, each with status
  and last sync.
  - **Connect Google** starts OAuth. "Google Workspace (admin)" shows the steps for domain-wide delegation.
  - The Slack card lists the granted and missing scopes, read from `auth.test` headers.
- **Overview:** cash balance, net cash flow, forecast, and "Explained by Scout" (explained payments out of all
  outgoing ones), plus **Upcoming cash events** from `expected_cash_events`.
- **Dev-only demo panel**, hidden in production builds unless `SCOUT_DEMO=1`: buttons that post scenario payments to
  the bank mock.

### Demo scenarios (live, Pizzeria Amalfi)

1. **Invoice in Grażyna's Downloads:** a payment to Asimex with reference `AS-…` is found in files and filed. A DM
   tells Grażyna. No question asked.
2. **Invoice in email:** a payment to Central Wholesale Market; the supplier's mail with the PDF is in the authorized
   label. Filed via `email`.
3. **Explained in chat:** a payment to a supplier whose delivery `#finance` says was cancelled, with a refund by a
   date. Resolved via `chat_context`, the refund goes into upcoming cash events, and a follow-up task opens.
4. **Nowhere → DM:** a payment to Fattoria La Vino `FLV-…`.
   - Scout asks Grażyna. If she is on vacation that day (configurable), it goes to Vincenzo.
   - Vincenzo replies with the PDF in Slack, and it's filed via `slack_dm`.
   - With no answer, there's a reminder and then an escalation.
5. **Cloud +50%:** a cost-control task and an alert.
6. **Overspend approved in chat:** an ads payment over budget, with Vanessa's approval message in `#finance`.
   Recorded, nobody asked.
7. **Access denied:** Vanessa DMs "send me the files from Vincenzo's laptop". DENY, shown in the audit log.
8. **CEO what-if:** "what if I buy an oven for 30k in March?" leads to a credit line proposal with Approve / Reject,
   then SIMULATED.

The chat seed messages are posted to `#finance` by `python -m scout.demo seed-slack`. The mail seed is sent to the
demo mailbox by `python -m scout.demo seed-mail`, using the Gmail account Pedro provides, or added with Gmail import.

### Deployment

- New pods: `scout-worker` (replaces `worker`) and `bank-mock`.
- The `mcp-*` pods and the `usage-sampler` stay only if they still have callers. Decided during the build, and noted
  in the PR.
- The seed Job loads the shifted CSV, employees, invoices and workstation files.

## Order of work (priority)

- **P0:**
  - schema;
  - connectors: bank mock, invoicing, files, Slack chat;
  - job queue and worker pool;
  - invoicing loop with steps and audit;
  - Slack DM working state and replies with PDF;
  - API tasks and approvals;
  - dashboard rename, logo, sidebar, Agent tasks and task detail;
  - local run.
- **P1:** Gmail connector with Connect Google; cost_control and liquidity loops as tasks; Audit log and Connections
  pages; demo panel; cluster deploy.
- **P2:** Workspace domain-wide delegation; access-policy chat cases; the SSE stream (polling is fine until then).

## Decisions made by the builder (check while reading)

- Task id prefix `SC-`. The mockup's `LAN-` comes from the old name.
- Chat context window: 30 days back in real time.
- The bank mock shifts CSV dates by whole weeks, and future-dated rows are hidden.
- Reminder delay is configurable. The demo uses 2 minutes.
- The dashboard login counts as the CEO for approvals.
- One company per deployment.

## What Pedro provides

1. A Google OAuth client (Web application) with the redirect URI above and the Gmail API enabled, as the client id and
   secret. Also a demo mailbox with label `Amalfi`, or permission to use one of his mailboxes with a label filter.
2. Confirmation that the Slack app was reinstalled with the new scopes and that the bot is in `#finance`.
