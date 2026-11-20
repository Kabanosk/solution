# Scout real-time agent loop: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the December replay and old agent with **Scout**, a real-time agent:
- watchers over a mock Revolut bank, Gmail and Slack feed a Postgres job queue;
- a worker pool runs ladder loops that write tasks, step logs and an audit log;
- Slack shows a live "Working on it…" message, and replies with a PDF continue the task;
- the dashboard becomes **Scout**, with a Linear-style sidebar and task pages.

**Architecture:**
- **Backend:** a new Python package `backend/scout/` holds the connectors, jobs, loops, Slack UI, worker and API
  routers. The kept client-model code (`cfo.data`, `cfo.forecast`, `cfo.analysis`, `cfo.llm`, `cfo.db` company
  tables) is imported from `cfo`. The old agent, sim, run, chat and eval code is deleted.
- **Contract:** the database schema (`backend/scout/schema.sql`) is the contract between backend and dashboard.
- **Dashboard:** it reads Postgres directly, as it does today. Writes (approvals, OAuth, demo) go through the API.

**Tech Stack:**
- Backend: Python 3.12, uv, psycopg 3, FastAPI, LangGraph (Postgres checkpointer for approvals), slack_bolt Socket
  Mode, httpx, pypdf, google-api-python-client + google-auth, cryptography (Fernet).
- Dashboard: Next.js (App Router), pg, shadcn/radix, lucide, recharts.

**Spec:** `docs/design/2026-11-21-agent-tasks-loop-design.md` (v2, approved 2026-11-21).

## Global Constraints

- **Product:** Pizzeria Amalfi, name **Scout**, task ids `SC-<n>` from sequence `agent_task_seq` starting at 100.
- **Time:** real time only, no replay.
  - The bank mock shifts CSV dates by whole weeks so the last row lands before today. With the 2025 CSV and today
    2026-11-20, that is +46 weeks.
  - Rows dated after now are hidden.
- **Logs:** step lines are written by code at the step, in deck wording, never summarised by the LLM.
- **Ladder order:** invoicing → files + email → chat → Slack DM, the DM only if nothing explained it.
- **Chat match:** a chat message needs the invoice number, or the supplier together with the exact amount;
  lookback `CHAT_LOOKBACK_DAYS=30`.
- **Receipts:** PDF only, no OCR. Execution of strategic actions is always SIMULATED.
- **Secrets:** none in git. Gmail scope `https://www.googleapis.com/auth/gmail.readonly`. Refresh tokens
  Fernet-encrypted with `CONNECTIONS_KEY`.
- **Slack:**
  - 👀 reaction plus a "Working on it…" thread reply, edited at most once per second, showing the last 8 step lines.
  - The final edit carries a level label `[AUTO]`, `[NOTIFY]`, `[APPROVAL]` or `[SIMULATED]`.
- **Worker:** `SCOUT_WORKERS` default 4. Job retries: 3 attempts, backoff 5 s / 30 s / 120 s. `REMINDER_AFTER_HOURS`
  default 24; the demo `.env` uses `0.05` (3 min).
- **Repo rules:** one company per deployment. The dashboard login counts as the CEO (`vincenzo`). The repo is public.
- **Tests:** `uv run --project backend pytest -q backend/tests`, against `DATABASE_URL`. Each test gets its own
  schema (`scout_test_<pid>_<n>`); tests never touch the cluster.

## Review Focus

1. **Two watchers or workers racing on the same bank transaction or reply.** Exactly one task and no duplicate DM.
   Covered by `test_jobs.py::test_enqueue_is_idempotent` and
   `test_invoicing_loop.py::test_same_txn_twice_one_task`.
2. **An LLM that returns garbage or times out mid-loop.** The task gets an `error` or fallback step and still ends in
   a state (`waiting` or `done`), never stuck `in_progress`. Covered by
   `test_invoicing_loop.py::test_llm_garbage_still_finishes`.
3. **A DM from an unknown Slack user, a bot, or an edited message.** Ignored, no task. Covered by
   `test_slack_ui.py::test_ignores_bots_and_strangers`.
4. **A PDF reply whose amount doesn't match, or a non-PDF attachment.** Scout asks for clarification and the task
   stays `waiting`. Covered by `test_chat_loop.py::test_reply_pdf_mismatch_keeps_waiting` and
   `::test_reply_non_pdf_attachment`.
5. **Gmail not connected or the token revoked.** The mail check logs "Company email: not connected" as a step and
   the ladder continues. Covered by `test_connectors_mail.py::test_mail_unavailable_is_a_step_not_a_crash`.

---

## File map

```
backend/scout/__init__.py
backend/scout/config.py            ScoutSettings (env) — worker count, reminder hours, lookback, OAuth, keys, mock bank URL
backend/scout/db.py                connect(), apply_schema(), tx helper, NOTIFY helper
backend/scout/schema.sql           new tables (spec "Data model") + DROP of replay tables
backend/scout/models.py            pydantic: Task, Step, Job, Approval, AuditEntry, BankTxn, MailHit, ChatHit, InvoiceDoc
backend/scout/tasks.py             TaskRecorder
backend/scout/jobs.py              JobQueue
backend/scout/audit.py             Audit (write rows) + access.check
backend/scout/policy.py            port of cfo.agent.policy (classify, context, rules)
backend/scout/llm_tasks.py         extract_invoice, classify_chat, classify_reply, explain, route_dm (ported + new)
backend/scout/documents.py         pdf_text (ported)
backend/scout/connectors/{__init__,bank,invoicing,files,mail,chat,registry,crypto}.py
backend/scout/mock_bank/{__init__,app,store}.py      Revolut-shaped mock API + CSV shift
backend/scout/loops/{__init__,context,invoicing,cost_control,liquidity,chat}.py
backend/scout/slack_ui.py          SlackUI: send_dm, live status, buttons, file download, listeners
backend/scout/watchers.py          BankWatcher, MailWatcher, Scheduler
backend/scout/worker.py            python -m scout.worker
backend/scout/seed.py              python -m scout.seed (company data, shifted history, workstation files, connections rows)
backend/scout/demo.py              python -m scout.demo play <scenario> | seed-slack | seed-mail
backend/scout/api/{__init__,tasks,approvals,audit,connections,google_oauth,demo}.py   routers mounted in cfo.api.app
backend/tests/scout/...            tests per module (conftest gives schema-isolated DB + FakeLLM + FakeSlack)
dashboard/components/brand.tsx, app-sidebar.tsx, tasks/*.tsx, audit-log.tsx, connections.tsx
dashboard/lib/scout.ts             server-side queries for tasks/steps/audit/connections
dashboard/app/(app)/...            routes: /, /tasks, /tasks/[id], /inbox, /audit, /connections, /transactions, /forecast
```

Deleted:
- Backend modules:
  - `backend/cfo/agent/`, `backend/cfo/sim/`, `backend/cfo/eval/`;
  - `backend/cfo/run.py`, `backend/cfo/chat.py`, `backend/cfo/smoke.py`, `backend/cfo/channels/`;
  - `backend/cfo/api/routers/{sim,decisions,approvals,rules,events}.py`, `backend/cfo/api/stubs.py`,
    `backend/cfo/api/stream.py`;
  - the December part of `cfo/data/generate.py` and `cfo/data/load.py` (`events/`, `apply_event`).
- Tests of all of the above.
- `deploy/openshift/app/worker.yaml`, which is replaced by `scout-worker.yaml`.

---

### Task 1: Schema, DB helpers, test fixtures

**Files:**
- Create: `backend/scout/{__init__,config,db,models}.py`, `backend/scout/schema.sql`, `backend/tests/scout/conftest.py`, `backend/tests/scout/test_schema.py`
- Modify: `backend/pyproject.toml` (packages `["cfo","scout"]`; deps `httpx`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `cryptography`)

**Interfaces — Produces:**
- `scout.db.connect(url: str | None = None, schema: str | None = None) -> psycopg.Connection` (dict_row, autocommit off)
- `scout.db.apply_schema(conn) -> None`: applies `cfo/db/schema.sql`, then `scout/schema.sql`. Idempotent.
- `scout.config.get_scout_settings() -> ScoutSettings`. Fields:
  - `database_url`, `scout_workers=4`, `reminder_after_hours=24.0`, `chat_lookback_days=30`;
  - `chat_channels: list[str]=["finance"]`;
  - `bank_api_url="http://localhost:8010"`, `bank_poll_seconds=5`, `mail_poll_seconds=30`;
  - `google_client_id`, `google_client_secret`, `google_redirect_uri`;
  - `connections_key`, `oauth_state_secret`, `dashboard_url="http://127.0.0.1:3000"`, `ceo_id="vincenzo"`,
    `document_owner_id="grazyna"`.
- Pydantic models in `scout.models`, fields exactly as the spec's data model:
  - `Task`, `Step`, `Job`, `Approval`, `AuditEntry`;
  - `BankTxn{id, booked_at, amount: Decimal, currency, counterparty, reference, card_last4, holder_employee_id}`;
  - `MailHit{id, mailbox, sender, subject, date, snippet, attachments: list[str]}`;
  - `ChatHit{channel, ts, user, text, permalink}`;
  - `InvoiceDoc{number, supplier, issue_date, total, lines, source, evidence}`.
- Tables in `scout/schema.sql`: `agent_tasks`, `agent_task_steps`, `agent_jobs`, `approvals`, `rules`, `audit_log`,
  `connections`, `connection_credentials`, `watcher_state`, `expected_cash_events`, sequence `agent_task_seq`.
  Plus `DROP TABLE IF EXISTS events, sim_state, agent_reminders, approval_requests, decisions CASCADE`.
- Fixtures in `backend/tests/scout/conftest.py`:
  - `db`: a connection on a fresh schema with the schema applied;
  - `fake_llm`: `cfo.llm.FakeLLM`;
  - `fake_slack`: an in-memory recorder of posts, updates and reactions.

- [ ] **Step 1: Write `test_schema.py::test_apply_twice`** (apply_schema twice, no error) and `::test_task_id_sequence` (`SELECT 'SC-'||nextval('agent_task_seq')` = `SC-100`).
- [ ] **Step 2: Run** `uv run --project backend pytest -q backend/tests/scout/test_schema.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement the schema, db, config and models** as listed. Write `rules` with the same columns as the old table.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): schema, settings and test fixtures`.

### Task 2: Task recorder, audit, job queue

**Files:**
- Create: `backend/scout/{tasks,audit,jobs}.py`
- Test: `backend/tests/scout/test_tasks.py`, `test_jobs.py`, `test_audit.py`

**Interfaces — Consumes:** Task 1. **Produces:**
- `TaskRecorder(conn_factory)` with:
  - `.open(dedupe_key, loop, title, **fields) -> Task`, which returns the existing task when the key exists (any
    status except `cancelled`);
  - `.step(task_id, kind, text, *, source=None, result_count=None, detail=None, evidence=None) -> Step`;
  - `.set_status(task_id, status, *, outcome=None, resolved_via=None, level=None, owner_id=None) -> Task`, which
    sets `closed_at` on `done` and `cancelled`;
  - `.get(task_id) -> Task`, `.find_open(owner_id=None, transaction_id=None) -> list[Task]`;
  - `.add_listener(fn: Callable[[Task, Step], None])`.
  - Each `step` also runs `NOTIFY scout_tasks, '<task_id>'`.
- `Audit(conn_factory)` with `.record(actor, action, target, resource, decision="ALLOW", *, task_id=None,
  requester=None, role=None, policy=None, reason=None) -> None`.
- `access.check(requester: str, role: str, target: str, resource: str) -> tuple[bool, str]`, returning
  `(allowed, policy)`. Rules:
  - `ceo` may access anything;
  - `office_manager` may access `files:*` and `mail:*` of company folders;
  - other roles may access only their own `laptop:<self>`;
  - denials return the policy `"<owner>-devices: owner-or-exec-only"`.
- `JobQueue(conn_factory)` with:
  - `.enqueue(kind, payload, dedupe_key, run_after=None) -> str | None`, which returns None when the key already
    exists;
  - `.claim(worker_id) -> Job | None`, using `FOR UPDATE SKIP LOCKED` and `run_after <= now()`;
  - `.done(job_id, task_id=None)`;
  - `.fail(job_id, error)`, which reschedules with backoff `[5, 30, 120]` s and marks the job `failed` after 3
    attempts.

- [ ] **Step 1: Write tests.**
  - `test_tasks.py::test_open_is_idempotent`: the same dedupe key gives the same id `SC-100`.
  - `::test_step_order_and_listener`: two steps come back in order, and the listener is called twice.
  - `::test_done_sets_closed_at`.
  - `test_jobs.py::test_enqueue_is_idempotent`: the second enqueue returns None.
  - `::test_claim_skip_locked`: two connections claim different jobs.
  - `::test_fail_backoff_then_failed`: attempts 1→3, the status ends `failed`, and `run_after` grows.
  - `test_audit.py::test_intern_denied_ceo_laptop`:
    `check("vanessa","waitstaff","laptop:vincenzo","files/board")` → `(False, "vincenzo-devices: owner-or-exec-only")`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): tasks, audit and job queue`.

### Task 3: Mock Revolut bank and the shifted seed

**Files:**
- Create: `backend/scout/mock_bank/{__init__,app,store}.py`, `backend/scout/seed.py`
- Modify: `backend/cfo/data/generate.py` and `backend/cfo/data/load.py`:
  - remove December events, `events/` output and `apply_event`;
  - keep history, invoices, workstation files, HR and metrics, applied with the date shift.
- Test: `backend/tests/scout/test_mock_bank.py`, `test_seed.py`

**Interfaces — Produces:**
- `store.week_shift(last_csv_date: date, today: date) -> int`: the smallest whole number of weeks that puts
  `last_csv_date + weeks` strictly before `today`. For 2025-12-31 and 2026-11-20 it returns 46.
- `store.BankStore(conn_factory)`:
  - `.load_csv(path, weeks)` writes the table `bank_mock_transactions`, with ids `tx_<sha1[:12]>` and rows dated
    after now hidden;
  - `.list(from_ts, to_ts, count=1000) -> list[BankTxn]`;
  - `.post(counterparty, amount, reference, holder_employee_id=None, card_last4=None) -> BankTxn`, booked now.
- `mock_bank.app.app`, a FastAPI app with:
  - `GET /api/1.0/accounts` → `[{"id":"acc_amalfi","name":"Pizzeria Amalfi EUR","balance":...,"currency":"EUR"}]`;
  - `GET /api/1.0/transactions?from&to&count` → Revolut shape
    `{id, type:"card_payment"|"transfer", state:"completed", created_at, completed_at, reference, legs:[{amount, currency, description:counterparty}], card:{card_number:"****4417", first_name, last_name}?}`;
  - `POST /mock/transactions` with the same body as `.post`.
- `python -m scout.seed --data data/generated [--reset]`. It:
  - applies the schema and runs `generate` and `load` with the shift;
  - loads the bank mock;
  - sets `watcher_state('bank') = {"since": now}`;
  - inserts default `connections` rows: `revolut_mock`, `postgres` invoicing, `workstation` files, `slack` chat, and
    `gmail` as disconnected.
- `python -m scout.mock_bank` runs uvicorn on `:8010`.

- [ ] **Step 1: Write tests.**
  - `test_mock_bank.py::test_week_shift_46` asserts `week_shift(date(2025,12,31), date(2026,11,20)) == 46`.
  - `::test_future_rows_hidden`.
  - `::test_post_then_list` uses a FastAPI TestClient: POST, then GET with `from` = 1 minute ago contains the new id
    with `legs[0].amount == -1250.0`.
  - `test_seed.py::test_seed_history_ends_before_today` asserts `max(booking_date) < today`, ≥ 1000 transactions, 5
    employees, and connections rows present.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The shift is applied in `cfo.data.source.load_bank_csv` through a new parameter `shift_weeks: int = 0`.
  - Grażyna's vacation in `people.py` becomes relative: `today - 2 days` to `today + 10 days`, so the DM escalation
    demo works.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): mock Revolut bank and real-time seed`.

### Task 4: Connectors (bank client, invoicing, files, mail, chat, registry, crypto)

**Files:**
- Create: `backend/scout/connectors/{__init__,bank,invoicing,files,mail,chat,registry,crypto}.py`
- Test: `backend/tests/scout/test_connectors_{bank,invoicing,files,mail,chat}.py`

**Interfaces — Consumes:** Tasks 1–3. **Produces:** Protocols, with every call writing one audit row:
- **Bank:** `BankConnector.list_transactions(since: datetime) -> list[BankTxn]`. `RevolutMockBank(base_url,
  http=httpx.Client)` maps the Revolut shape to `BankTxn`.
- **Invoicing:** `InvoicingConnector`.
  - `.find(number=None, supplier=None, amount=None, around: date|None=None, days=7) -> list[InvoiceDoc]`
  - `.file(doc: InvoiceDoc, transaction_id) -> tuple[bool, str]`. Errors: `amount_mismatch`, `already_documented`,
    `linked_elsewhere`.
  - Implemented by `PostgresInvoicing(conn_factory, audit)`.
- **Files:** `FilesConnector` with `.search(owner, terms: list[str]) -> list[dict(path, name)]` and
  `.read(owner, path) -> bytes`. `WorkstationFiles(root, audit)` reuses `cfo.mcp_servers.workstations.WorkstationTools`.
- **Mail:** `MailConnector` with:
  - `.available() -> bool`;
  - `.search(terms: list[str], since: datetime) -> list[MailHit]`;
  - `.attachment(hit_id, name) -> bytes`;
  - `.changes(cursor: dict) -> tuple[list[MailHit], dict]`.

  Implementations:
  - `GmailMail(creds, label)` uses `users.messages.list(q=" OR ".join(terms) + f" after:{epoch}", labelIds=[label])`
    and `history.list` for changes.
  - `WorkspaceMail(service_account_json, mailboxes, label)` wraps `GmailMail` per mailbox through delegated
    credentials.
  - `FolderMail(root)` reads `.eml` files, for tests only.
- **Chat:** `ChatConnector` with `.search(terms: list[str], since: datetime, require_amount: Decimal|None) ->
  list[ChatHit]`. `SlackChat(client: WebClient, channels: list[str])` resolves channel names to ids with
  `conversations.list` and paginates `conversations.history(oldest=since)`. A message matches when it contains the
  invoice number, or the supplier plus the exact amount: formatted `1,250.00`, `1250,00` and `1250` are all
  accepted.
- **Crypto:** `crypto.seal(text, key) -> bytes` and `crypto.open_(data, key) -> str`, both Fernet.
- **Registry:** `registry.build(conn_factory, settings, slack_client=None) -> Connectors` dataclass with
  `bank, invoicing, files, mail, chat`. Gmail is built from `connections`/`connection_credentials` when they are
  connected, otherwise `UnavailableMail`, whose `available()` returns False.

- [ ] **Step 1: Write tests.**
  - `test_connectors_bank.py::test_maps_revolut_shape` (httpx MockTransport).
  - `test_connectors_invoicing.py::test_find_by_number`, `::test_file_refuses_mismatch`,
    `::test_file_refuses_linked_elsewhere`.
  - `test_connectors_files.py::test_private_folder_denied_and_audited`.
  - `test_connectors_mail.py::test_folder_mail_search_finds_pdf_attachment`,
    `::test_mail_unavailable_is_a_step_not_a_crash` (`UnavailableMail().available() is False`, `search` returns []),
    `::test_gmail_query` (fake service: q contains `AS-77001` and `after:`).
  - `test_connectors_chat.py::test_match_number`, `::test_supplier_without_amount_no_match`,
    `::test_supplier_with_amount_1250_formats`.
  - `test_crypto.py::test_roundtrip`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): connectors behind protocols`.

### Task 5: LLM tasks and policy port

**Files:**
- Create: `backend/scout/{llm_tasks,documents,policy}.py`
- Test: `backend/tests/scout/test_llm_tasks.py`, `test_policy.py`

**Interfaces — Produces:**
- Ported unchanged in behaviour from `cfo.agent.*`:
  - `extract_invoice(llm, text) -> ExtractedInvoice | None`;
  - `explain(llm, instruction, facts, fallback) -> str`;
  - `parse_amount`;
  - `pdf_text(bytes) -> str`.
- `classify_chat(llm, text, facts) -> Literal["explains_no_invoice","approval","refund_expected","unrelated"]`,
  plus an optional `refund: {amount, on_date}`. It is a tool call `classify_message`, and a garbage answer becomes
  `unrelated`.
- `classify_reply(llm, text, facts) -> Literal["explains","promises_later","unrelated"]`, with the same fallback.
- `route_dm(llm, text) -> Literal["question","rule","what_if","request_files","smalltalk"]` plus args. One call,
  fallback `smalltalk`.
- `policy.classify` and `policy.context_from_transactions` are ported from `cfo.agent.policy`, keeping the rules
  and thresholds. `parse_rule` is ported as well.

- [ ] **Step 1: Write tests** with `FakeLLM`:
  - `::test_classify_chat_refund` returns `refund_expected` with refund 1250.00;
  - `::test_classify_garbage_is_unrelated`;
  - `::test_route_dm_what_if`;
  - the old policy tests are moved into `test_policy.py`, with imports switched to `scout.policy`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): llm tasks and policy`.

### Task 6: Invoicing loop (evidence ladder)

**Files:**
- Create: `backend/scout/loops/{__init__,context,invoicing}.py`
- Test: `backend/tests/scout/test_invoicing_loop.py`

**Interfaces — Consumes:** Tasks 2, 4, 5. **Produces:**
- `JobContext`: a dataclass with `recorder, audit, connectors, llm, notifier, jobs, settings, hr` (HR reads
  employees, vacations and manager chain from Postgres).
- `Notifier`: a Protocol, implemented in Task 8.
  - `.dm(employee_id, text, level, *, task_id, buttons=(), thread=None) -> str|None`
  - `.update(ref, text, level)`
- `invoicing.run(ctx, job: Job) -> Task`, for job kinds `bank_txn` (payload `{txn: BankTxn}`), `reminder` (`{task_id}`)
  and `reply` (`{task_id, text, pdf: bytes|None, from_id}`).

Step texts, verbatim with values filled in:
1. `picked_up`: `"Scout picked up the {card payment|transfer} from Revolut Business"` (source `revolut_mock`).
2. `checked_system`: `"Searched the invoicing system: {n} match{es}"` (source `invoicing_pg`).
3. `checked_files`: `"Checked {Name}'s company folders: {found|no receipt}"`.
4. `checked_email`: `"Checked company email: {found|no receipt}"`. When mail is unavailable:
   `"Company email: not connected"`.
5. `checked_chat`: `"Checked authorized chat history: {no context|found context in #{channel}}"`.
6. `asked_person`: `"Asked {role phrase}, {Name}, on Slack"`, where the role phrase is "the card holder" or "the
   document owner", or, with vacation, `"{Name} is on vacation, asked {Manager}"`.
7. `reminder` and `escalated`: `"Reminded {Name}"` and `"Escalated to {Name}"`.
8. `reply_received`: `"{Name} sent {file.pdf|a message}"`.
9. `resolved`: `"Resolved · €{amount} filed as {number}"`, or
   `"Resolved · {reason} (from chat context)"`.

Outcomes as in the spec:
- `chat_context` refund: an `expected_cash_events` row, plus `jobs.enqueue("followup", ...)` that opens a `todo`
  task `"Watch {supplier} refund, due {date}"`.
- Asking: the task becomes `waiting`, and a `reminder` job is enqueued with `run_after = now + reminder_after_hours`.
- An exhausted chain: `escalated`, then the CEO.

- [ ] **Step 1: Write tests** with fake connectors and FakeLLM.
  - `test_found_in_invoicing_skips_rest`: steps are exactly `[picked_up, checked_system, resolved]`, with
    resolved_via `invoicing`.
  - `test_found_in_files`: resolved_via `files`, and the owner gets an FYI DM.
  - `test_found_in_email`.
  - `test_chat_refund_adds_expected_cash_and_followup`.
  - `test_nothing_found_asks_owner_then_waiting`: one DM, and the reminder job is enqueued.
  - `test_vacation_goes_to_manager`.
  - `test_reminder_then_escalation_then_ceo`.
  - `test_same_txn_twice_one_task`.
  - `test_llm_garbage_still_finishes`: the status ends `waiting` and no step has kind `None`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** as a sequence of plain functions. The ladder is linear; LangGraph is used only where an
  interrupt is needed, in Task 7.
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): invoicing loop with evidence ladder`.

### Task 7: Cost control, liquidity (approval interrupt), chat loop

**Files:**
- Create: `backend/scout/loops/{cost_control,liquidity,chat}.py`
- Test: `backend/tests/scout/test_cost_control.py`, `test_liquidity_loop.py`, `test_chat_loop.py`

**Interfaces — Produces:**
- `cost_control.run(ctx, job)`, for job `cost_check` with payload `{}`.
  - It uses `cfo.analysis.cost_metrics` on the latest months, with one task per anomaly (dedupe
    `metric:<kind>:<month>`).
  - Steps: `"Compared {cost} with {metric}: {+x%} vs {+y%}"`, then `"Alerted {names}"` (NOTIFY).
- `liquidity.run(ctx, job)`, for jobs `liquidity_review` and `what_if` with payload `{amount, month, what}`.
  - It uses `cfo.forecast.liquidity.review_liquidity` and keeps LangGraph `interrupt()` for approvals, with thread id
    = task id and a Postgres checkpointer.
  - `liquidity.resume(ctx, approval_id, approved: bool, by: str) -> Task` raises `PermissionError` when the clicker is
    not the approver, and returns the task unchanged when it is already answered.
- `chat.run(ctx, job)`, for job `dm` with payload `{from_id, text, files:[{name, mimetype, bytes}], slack_ref}`. It
  first decides whether this is a reply to an open task:
  - the sender owns a `waiting` or `escalated` task;
  - or the message is in that task's thread.

  A reply is handed to `invoicing.run(kind="reply")`. Otherwise `route_dm` picks the intent:
  - `question`: a read-only tool loop, at most 4 rounds, over invoicing, bank and tasks. Only the CEO and the office
    manager may ask; anyone else is denied and audited.
  - `rule`: the CEO only, saved to `rules`.
  - `what_if`: enqueued as a `what_if` job.
  - `request_files`: goes through `access.check`, and a denial is written as a step plus an audit DENY.
  - `smalltalk`: the help text.

- [ ] **Step 1: Write tests.**
  - `test_cost_control.py::test_cloud_50pct_one_task_notify`.
  - `test_liquidity_loop.py::test_what_if_oven_creates_approval_then_approve_simulated`: the steps end with
    `approved`, `resolved`, and the outcome contains "SIMULATED".
  - `::test_only_approver_can_click`.
  - `test_chat_loop.py::test_reply_pdf_resolves_waiting_task`.
  - `::test_reply_pdf_mismatch_keeps_waiting`.
  - `::test_reply_non_pdf_attachment`: the step reads "Received a file that is not a PDF" and the task stays
    `waiting`.
  - `::test_vanessa_request_ceo_laptop_denied`: there is an audit DENY and the reply text contains "can't".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): cost, liquidity and chat loops`.

### Task 8: Slack UI (live status, DMs, buttons, files)

**Files:**
- Create: `backend/scout/slack_ui.py`
- Test: `backend/tests/scout/test_slack_ui.py`

**Interfaces — Consumes:** Tasks 2, 7. **Produces:**
- `SlackUI(client: WebClient, employees, user_map, jobs: JobQueue, recorder: TaskRecorder)`, which implements
  `Notifier`.
- `.register(app: slack_bolt.App)`.
- `on_message(event)`:
  - it ignores bots, `subtype` edits, unknown users and non-IM channels;
  - it calls `reactions.add(name="eyes")`;
  - it posts a thread reply `"[WORKING] Working on it…"` and keeps its ts;
  - it downloads `files[]` with `url_private_download` and the bot token;
  - it enqueues `dm` with dedupe `dm:<channel>:<ts>` and `slack_ref`.
- `LiveStatus`, a recorder listener. For steps of a task with `slack_ref` it calls `chat.update` with the last 8 step
  lines, throttled to one edit per second per message. The final status edit is
  `"[{LEVEL}] {outcome}"`, followed by the step lines.
- Buttons:
  - `scout_approve` and `scout_reject` call `liquidity.resume`.
  - On success the buttons are replaced with "Approved by {Name}".
  - On `PermissionError` an ephemeral note is posted.

- [ ] **Step 1: Write tests** with a fake WebClient and a fake app.
  - `::test_dm_adds_eyes_posts_working_and_enqueues`.
  - `::test_ignores_bots_and_strangers`.
  - `::test_live_status_edits_last_8_lines_throttled`.
  - `::test_pdf_file_downloaded_into_job`.
  - `::test_approve_click_by_non_approver_ephemeral`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): slack ui with live status`.

### Task 9: Watchers, scheduler, worker

**Files:**
- Create: `backend/scout/{watchers,worker}.py`
- Test: `backend/tests/scout/test_watchers.py`, `test_worker.py`

**Interfaces — Produces:**
- `BankWatcher(connectors, jobs, state).poll() -> int`. It enqueues `bank_txn` for every outgoing transaction
  newer than the cursor (dedupe `txn:<id>`), ingests the transaction into the `transactions` ledger, and moves the
  cursor.
- `MailWatcher.poll() -> int`. New mail whose subject, body or attachments name the invoice number or supplier of an
  open `waiting` task enqueues `reply` with `pdf` set and `from_id = "email"`.
- `Scheduler.tick(now)` enqueues:
  - `cost_check` daily at 07:00 local (dedupe `cost:<date>`);
  - `liquidity_review` Mondays at 07:00 (dedupe `liq:<isoweek>`);
  - reminders come from `run_after` and need no extra work.
- `Worker(ctx, n_threads).run(stop: threading.Event)`. Each thread loops: claim → dispatch by `job.kind` → done or
  fail. Watcher threads poll every `bank_poll_seconds` and `mail_poll_seconds`, and Slack runs on
  `SocketModeHandler.connect()`.
- `python -m scout.worker` wires everything from env and handles SIGTERM.

- [ ] **Step 1: Write tests.**
  - `test_watchers.py::test_bank_watcher_only_new_outgoing_once`: two polls give one job.
  - `::test_scheduler_daily_dedupe`.
  - `test_worker.py::test_dispatch_and_retry`: a failing handler is retried, then the job is `failed`, and the task
    gets an `error` step `"Something went wrong: …"`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(scout): watchers, scheduler and worker pool`.

### Task 10: API (tasks, approvals, audit, connections, Google OAuth, demo) and removals

**Files:**
- Create: `backend/scout/api/{__init__,tasks,approvals,audit,connections,google_oauth,demo}.py`
- Modify: `backend/cfo/api/app.py` (mount the scout routers, drop the deleted ones), `backend/cfo/api/routers/kpis.py`
  (undocumented and pending counts from the new tables), `deploy/cfo-entrypoint.sh` (`worker` → `python -m
  scout.worker`, new `bank-mock`, `seed`)
- Delete: everything in the plan's "Deleted" list, along with its tests
- Test: `backend/tests/scout/test_api.py`

**Interfaces — Produces:**
- **Tasks:**
  - `GET /v1/tasks?status&loop&owner&q&limit` → `{items:[Task], counts:{status:n}}`
  - `GET /v1/tasks/{id}` → `{task, steps, audit, approval}`
  - `GET /v1/tasks/stream`: SSE over `LISTEN scout_tasks`, event `task` carrying the id, keep-alive every 15 s
- **Approvals:** `POST /v1/approvals/{id}:approve|:reject`. Bearer `API_WRITE_TOKEN` is required when it is set.
  The approver is the CEO.
- **Audit:** `GET /v1/audit?task_id&decision&limit`.
- **Connections:**
  - `GET /v1/connections`. The Slack card shows the scopes granted and missing, from `auth.test`
    `x-oauth-scopes`.
  - `POST /v1/connections/{id}:enable|:disable`.
- **Google OAuth:**
  - `GET /v1/connections/google/start` (bearer) → `{url}`, with `state` = HMAC-SHA256(`oauth_state_secret`,
    nonce|exp) and a 10-minute expiry.
  - `GET /v1/connections/google/callback?code&state` checks `state`, exchanges the code, stores
    `connection_credentials`, sets `connections('gmail').status='connected'` and its `config.email`, and returns a
    302 to `{dashboard_url}/connections?google=connected`.
- **Demo:**
  - `POST /v1/demo/play/{scenario}` (bearer, only when `SCOUT_DEMO=1`) posts that scenario's payment to the bank
    mock.
  - Scenarios: `files`, `email`, `chat_refund`, `nowhere`, `overspend_chat`.
- **Entry points:** `deploy/cfo-entrypoint.sh` dispatches `worker`, `bank-mock`, `seed` and `api`.

- [ ] **Step 1: Write tests.**
  - `::test_tasks_list_counts`.
  - `::test_task_detail_has_steps_in_order`.
  - `::test_oauth_state_tamper_rejected` (400).
  - `::test_oauth_callback_stores_encrypted` (the token exchange is monkeypatched; the ciphertext is not the
    plaintext).
  - `::test_demo_disabled_404`.
  - `::test_approve_requires_token`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement and delete.**
- [ ] **Step 4: Run** `uv run --project backend pytest -q backend/tests`. Expected: everything passes, and no test
  imports a deleted module.
- [ ] **Step 5: Commit** `feat(scout): api, oauth and removal of replay code`.

### Task 11: Dashboard: Scout brand and Linear-style sidebar

**Files:**
- Create: `dashboard/components/brand.tsx` (`ScoutMark`, `ScoutLockup`), `dashboard/components/app-sidebar.tsx`,
  `dashboard/app/icon.svg` (replaced with the Scout mark)
- Modify:
  - `dashboard/app/layout.tsx`: title "Scout · Your autonomous CFO";
  - `dashboard/components/dashboard.tsx`: split into the shell and pages;
  - `package.json`: name `scout-dashboard`, pinned versions instead of `latest`;
  - `README.md`.
- Test: `dashboard/tests/nav.test.ts`

**Requirements:**
- **Mark:** the deck's `#mark` path, verbatim:
  - `viewBox 0 0 24 24`;
  - `transform="translate(1.5 2.7) scale(.26)"`;
  - `d="M16 43l3-19c1-6 5-9 9-9 5-1 9 3 13 2 5-1 6-6 12-6 4 0 6 3 7 6l8 18 7-2c3-1 5 2 3 5-7 11-23 19-43 21-11 1-23 0-30-5-3-2-3-5 0-7l11-4Zm9 2c11 5 28 1 39-7-5 9-28 15-39 7Z"`;
  - drawn white on a `#141416` rounded square (radius 22% of the size).
- **Sidebar:** as in the spec (workspace switcher, search ⌘K, new-task button, Inbox / My tasks / Agent tasks, the
  collapsible sections Loops, Finance and Workspace, counts on the right, footer with user and theme).
  - Rows are 28 px high with 13 px text. Active row `bg-sidebar-accent`; hover a subtle background.
  - Sections collapse with persistence in localStorage, and keyboard `g t` / `g i` navigates.
- **Nav model:** `lib/nav.ts` exports `NAV: {section, items:[{id, label, href, icon, countKey?}]}[]`. The test checks
  that the order and hrefs match the spec.

- [ ] **Step 1: Write** `nav.test.ts::test_sections_and_order`.
- [ ] **Step 2: Run** `npm test` in `dashboard/`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test && npm run typecheck && npm run build`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(dashboard): Scout brand and Linear-style sidebar`.

### Task 12: Dashboard: Agent tasks, task detail, audit, connections, overview, demo panel

**Files:**
- Create:
  - `dashboard/lib/scout.ts`: server queries `listTasks`, `getTask`, `listAudit`, `listConnections`,
    `explainedShare`, `upcomingCashEvents`;
  - `dashboard/components/tasks/{task-list,task-row,task-detail,activity-log,status-icon}.tsx`;
  - `dashboard/components/{audit-log,connections,demo-panel}.tsx`;
  - routes `dashboard/app/(app)/{tasks,tasks/[id],inbox,audit,connections,transactions,forecast}/page.tsx`;
  - `dashboard/app/api/{approve,google,demo}/route.ts`, which proxy to the API with `API_WRITE_TOKEN` server-side.
- Modify: `dashboard/lib/finance.ts`, removing the reads of the dropped `decisions` and `approval_requests` tables.
- Test: `dashboard/tests/scout.test.ts`

**Requirements:** as in the spec's "Dashboard (Scout)" section.
- Status icons follow Linear: in progress = half circle (amber), waiting = clock, escalated = red `!`, todo = dashed
  circle, done = green check, cancelled = grey x.
- Channel chip from `resolved_via` or the current channel: `slack_dm` → Slack, `auto|invoicing|files` → Auto,
  `chat_context` → Chat context, `email` → Email, `forecast` → Forecast, CEO approval → CFO.
- The activity log shows `HH:MM` local time, with the full timestamp on hover. The running step (the latest, while
  the task is `in_progress`) shows a spinner.
- The page polls every 5 s with `router.refresh()` while any task is `in_progress` or `waiting`.

- [ ] **Step 1: Write tests.**
  - `scout.test.ts::test_channel_chip_mapping`.
  - `::test_status_grouping_order` (In progress, Waiting, Escalated, Todo, Done).
  - `::test_explained_share_formats_99_4`: 310 of 312 → "99.4%".
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test && npm run typecheck && npm run build`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(dashboard): agent tasks, activity log, audit, connections`.

### Task 13: Demo seeds, local end-to-end run, deploy manifests, docs

**Files:**
- Create:
  - `backend/scout/demo.py`;
  - `deploy/openshift/app/{scout-worker,bank-mock}.yaml`;
  - `docs/scout.md`: how it works, how to run it locally, the demo script, and how to connect Google and Slack.
- Modify:
  - `deploy/openshift/app/kustomization.yaml`;
  - `deploy/openshift/app/configmap.yaml`: `BANK_API_URL=http://bank-mock:8010`, `CHAT_CHANNELS=finance`;
  - `deploy/openshift/db/seed-job.yaml`: `cfo seed`;
  - `deploy/slack-app-manifest.yaml`: the new scopes;
  - `.env.example`;
  - `INFRA.md`: remove the simulator.

**Requirements:**
- `python -m scout.demo seed-slack` posts the chat-refund message and Vanessa's ads approval to `#finance`, joining
  the channel first with `conversations.join`.
- `seed-mail` creates the supplier mail with a PDF in the connected Gmail under label `Amalfi`, using
  `users.messages.insert`, which needs the `gmail.insert` scope. When that scope is missing, it prints the `.eml`
  path to import manually instead.
- `python -m scout.demo play <scenario>`.
- Local run, with these processes and ports:
  - the bank mock on `:8010`;
  - the API on `:8000`;
  - the worker;
  - the dashboard on `:3000`.

  In that run, play `files`, `chat_refund` and `nowhere`, DM Scout, and take screenshots of the tasks list and the
  task detail.

- [ ] **Step 1: Run** `python -m scout.seed --reset`, start the four processes, and run `python -m scout.demo play nowhere`. Expected: within 10 s a task `SC-1xx` is in `waiting`, with steps picked_up → checked_system → checked_files → checked_email → checked_chat → asked_person, and Grażyna or Vincenzo receives a Slack DM.
- [ ] **Step 2: Reply** to that DM with the FLV PDF. Expected: 👀 and "Working on it…" are edited live, and the task is `done` via `slack_dm`.
- [ ] **Step 3: Check the dashboard.** Expected: `/tasks` shows the task under Done and `/tasks/SC-1xx` shows all steps. Take screenshots.
- [ ] **Step 4: Commit** `feat(scout): demo seeds, manifests and docs`.
