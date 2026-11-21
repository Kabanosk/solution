-- Scout: agent tasks, step log, job queue, approvals, audit, connections. Idempotent; applied after
-- cfo/db/schema.sql (company data). Money is NUMERIC(14,2), signed (negative = outflow).

-- The December replay and the old agent are gone (design 2026-11-21).
DROP TABLE IF EXISTS events, sim_state, agent_reminders, approval_requests, decisions CASCADE;

CREATE SEQUENCE IF NOT EXISTS agent_task_seq START 100;

CREATE TABLE IF NOT EXISTS agent_tasks (
    id            TEXT PRIMARY KEY DEFAULT ('SC-' || nextval('agent_task_seq')),
    dedupe_key    TEXT NOT NULL UNIQUE,
    loop          TEXT NOT NULL CHECK (loop IN ('invoicing', 'cost_control', 'liquidity', 'chat', 'expenses', 'strategy', 'forecast')),
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'in_progress'
                  CHECK (status IN ('todo', 'in_progress', 'waiting', 'escalated', 'done', 'cancelled')),
    owner_id      TEXT,
    requested_by  TEXT NOT NULL DEFAULT 'agent',
    resolved_via  TEXT CHECK (resolved_via IN ('invoicing', 'files', 'email', 'chat_context', 'slack_dm',
                                               'approval', 'auto', 'forecast')),
    outcome       TEXT,
    level         TEXT CHECK (level IN ('AUTO', 'NOTIFY', 'APPROVAL')),
    simulated     BOOLEAN NOT NULL DEFAULT FALSE,
    category      TEXT,
    amount        NUMERIC(14,2),
    currency      TEXT NOT NULL DEFAULT 'EUR',
    counterparty  TEXT,
    transaction_id TEXT,
    slack_ref     TEXT,
    approval_id   TEXT,
    evidence      JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agent_tasks_status_idx ON agent_tasks (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS agent_tasks_owner_idx ON agent_tasks (owner_id) WHERE status IN ('waiting', 'escalated');

CREATE TABLE IF NOT EXISTS agent_task_steps (
    id            BIGSERIAL PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    ts            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    kind          TEXT NOT NULL,
    source        TEXT,
    result_count  INTEGER,
    text          TEXT NOT NULL,
    detail        JSONB NOT NULL DEFAULT '{}',
    evidence      JSONB
);
CREATE INDEX IF NOT EXISTS agent_task_steps_task_idx ON agent_task_steps (task_id, id);

CREATE TABLE IF NOT EXISTS agent_jobs (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    dedupe_key  TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    run_after   TIMESTAMPTZ NOT NULL DEFAULT now(),
    task_id     TEXT,
    worker      TEXT,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_jobs_ready_idx ON agent_jobs (run_after) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS agent_jobs_scout_thread_idx ON agent_jobs ((payload->>'thread'))
    WHERE kind = 'dm' AND payload->>'mention' = 'true';

CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    approver_id  TEXT NOT NULL,
    question     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    answered_by  TEXT,
    answered_at  TIMESTAMPTZ,
    slack_ref    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    task_id    TEXT,
    actor      TEXT NOT NULL,
    requester  TEXT,
    role       TEXT,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    resource   TEXT NOT NULL,
    decision   TEXT NOT NULL CHECK (decision IN ('ALLOW', 'DENY', 'MASK', 'BLOCK')),
    policy     TEXT,
    reason     TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_task_idx ON audit_log (task_id);
CREATE INDEX IF NOT EXISTS audit_log_ts_idx ON audit_log (ts DESC);

CREATE TABLE IF NOT EXISTS connections (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('bank', 'invoicing', 'mail', 'chat', 'files')),
    provider      TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    display_name  TEXT NOT NULL,
    config        JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'disconnected' CHECK (status IN ('connected', 'error', 'disconnected')),
    last_sync_at  TIMESTAMPTZ,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS connection_credentials (
    connection_id  TEXT PRIMARY KEY REFERENCES connections(id) ON DELETE CASCADE,
    ciphertext     BYTEA NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watcher_state (
    name        TEXT PRIMARY KEY,
    cursor      JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS expected_cash_events (
    id          BIGSERIAL PRIMARY KEY,
    on_date     DATE NOT NULL,
    amount      NUMERIC(14,2) NOT NULL,
    label       TEXT NOT NULL,
    task_id     TEXT REFERENCES agent_tasks(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'expected' CHECK (status IN ('expected', 'arrived', 'missed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The bank mock's own ledger (what "Revolut Business" knows). The agent ingests from its API into `transactions`.
CREATE TABLE IF NOT EXISTS bank_mock_transactions (
    id                  TEXT PRIMARY KEY,
    booked_at           TIMESTAMPTZ NOT NULL,
    type                TEXT NOT NULL,
    reference           TEXT NOT NULL,
    counterparty        TEXT NOT NULL,
    counterparty_account TEXT,
    amount              NUMERIC(14,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'EUR',
    card_last4          TEXT,
    holder_employee_id  TEXT,
    source              TEXT NOT NULL DEFAULT 'csv'
);
CREATE INDEX IF NOT EXISTS bank_mock_transactions_booked_idx ON bank_mock_transactions (booked_at);

-- The real Slack person who plays an HR role (written by the worker from SLACK_USER_MAP; the dashboard shows it).
ALTER TABLE employees ADD COLUMN IF NOT EXISTS slack_name TEXT;

CREATE TABLE IF NOT EXISTS slack_members (
    slack_user_id  TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'registered')),
    employee_id    TEXT REFERENCES employees(id) ON DELETE SET NULL,
    invited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_at  TIMESTAMPTZ
);
ALTER TABLE slack_members ADD COLUMN IF NOT EXISTS invite_token_hash TEXT;
ALTER TABLE slack_members ADD COLUMN IF NOT EXISTS slack_name TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS slack_members_invite_idx ON slack_members (invite_token_hash);


-- Guardrails (T052). The prompts of blocked human messages, in full, for the compliance report. Never exposed by
-- an API and never copied to audit_log. `reported_at` is set when the strikes went into a compliance alert.
CREATE TABLE IF NOT EXISTS guardrail_strikes (
    id           BIGSERIAL PRIMARY KEY,
    person       TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    prompt       TEXT NOT NULL,
    rails        TEXT NOT NULL DEFAULT '',
    reported_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS guardrail_strikes_person_idx ON guardrail_strikes (person, ts DESC);

-- The original of a flagged document (T052), kept so an approved invoice has a retrievable attachment. Served only
-- with the write token (`GET /v1/documents/{id}`).
CREATE TABLE IF NOT EXISTS flagged_documents (
    id          BIGSERIAL PRIMARY KEY,
    task_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    content     BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outside items (mail, file, chat message, KSeF number) the owner was already told about.
CREATE TABLE IF NOT EXISTS guardrail_alerts (
    item_key     TEXT PRIMARY KEY,
    rails        TEXT NOT NULL DEFAULT '',
    notified_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- BLOCK = a guardrail verdict (T052). MASK = salary rows folded into one payroll total for the office manager (T050); older databases allowed only ALLOW/DENY.
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_decision_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_decision_check CHECK (decision IN ('ALLOW', 'DENY', 'MASK', 'BLOCK'));
-- A work message Scout sent for someone (a dashboard chat or a Slack DM) and is waiting on. The Slack thread of the
-- sent message is the key: a reply there from the recipient is the answer to it, never a new question for Scout.
CREATE TABLE IF NOT EXISTS dm_requests (
    id            BIGSERIAL PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    requester_id  TEXT NOT NULL,
    recipient_id  TEXT NOT NULL,
    thread_ref    TEXT NOT NULL UNIQUE,
    origin        TEXT NOT NULL DEFAULT 'dashboard' CHECK (origin IN ('dashboard', 'slack')),
    text          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dm_requests_task_idx ON dm_requests (task_id);

-- One row per Slack reply event (event_key = the DM job's key, so a retried or duplicated event is stored once).
-- pending: not yet acknowledged; acked: the open dashboard chat rendered it; slacking: leased by a worker;
-- slacked: delivered to the requester's Slack DM. The lease recovers crashed workers; delivery is not exactly-once.
CREATE TABLE IF NOT EXISTS dm_replies (
    id           BIGSERIAL PRIMARY KEY,
    request_id   BIGINT NOT NULL REFERENCES dm_requests(id) ON DELETE CASCADE,
    event_key    TEXT NOT NULL UNIQUE,
    sender_id    TEXT NOT NULL,
    text         TEXT NOT NULL,
    step_id      BIGINT,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'acked', 'slacking', 'slacked')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS dm_replies_step_idx ON dm_replies (step_id);
ALTER TABLE dm_replies ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;  -- lease of the Slack fallback sender

-- Forecast loop: the CEO's annual revenue expectation and the rules the team must keep (one row per organization).
CREATE TABLE IF NOT EXISTS forecast_plan (
    id                   BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    annual_revenue_target NUMERIC(14,2) NOT NULL CHECK (annual_revenue_target > 0),
    no_hiring            BOOLEAN NOT NULL DEFAULT FALSE,
    max_new_monthly_cost NUMERIC(14,2) CHECK (max_new_monthly_cost IS NULL OR max_new_monthly_cost >= 0),
    guidance             TEXT NOT NULL DEFAULT '',
    enabled              BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by           TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
