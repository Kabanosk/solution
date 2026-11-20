-- Company database (the "pretend ERP"). Idempotent: safe to apply repeatedly.
-- Money is NUMERIC(14,2) EUR, signed (negative = outflow).

CREATE TABLE IF NOT EXISTS teams (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    owner_id       TEXT,
    monthly_budget NUMERIC(14,2)
);

CREATE TABLE IF NOT EXISTS employees (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL,
    team_id        TEXT REFERENCES teams(id),
    manager_id     TEXT REFERENCES employees(id),
    slack_user_id  TEXT
);

CREATE TABLE IF NOT EXISTS vacations (
    id           SERIAL PRIMARY KEY,
    employee_id  TEXT NOT NULL REFERENCES employees(id),
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    number          TEXT NOT NULL UNIQUE,
    supplier        TEXT NOT NULL,
    issue_date      DATE NOT NULL,
    due_date        DATE,
    total           NUMERIC(14,2) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'EUR',
    transaction_id  TEXT,
    source          TEXT NOT NULL DEFAULT 'db',
    category        TEXT,
    team_id         TEXT
);
-- Added by T002 (databases created before it): backward-compatible.
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS team_id TEXT;

CREATE TABLE IF NOT EXISTS invoice_lines (
    id          SERIAL PRIMARY KEY,
    invoice_id  TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    description TEXT NOT NULL,
    quantity    NUMERIC(14,4) NOT NULL DEFAULT 1,
    unit_price  NUMERIC(14,4) NOT NULL,
    amount      NUMERIC(14,2) NOT NULL,
    category    TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   TEXT PRIMARY KEY,
    booking_date         DATE NOT NULL,
    value_date           DATE,
    type                 TEXT NOT NULL,
    reference            TEXT NOT NULL,
    counterparty         TEXT NOT NULL,
    counterparty_account TEXT,
    amount               NUMERIC(14,2) NOT NULL,
    currency             TEXT NOT NULL DEFAULT 'EUR',
    invoice_number       TEXT,
    category             TEXT,
    team_id              TEXT REFERENCES teams(id),
    documented           BOOLEAN NOT NULL DEFAULT FALSE,
    invoice_id           TEXT REFERENCES invoices(id)
);
CREATE INDEX IF NOT EXISTS transactions_booking_date_idx ON transactions (booking_date);
CREATE INDEX IF NOT EXISTS transactions_invoice_number_idx ON transactions (invoice_number);

CREATE TABLE IF NOT EXISTS cost_metrics (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    month         DATE NOT NULL,
    team_id       TEXT REFERENCES teams(id),
    cost          NUMERIC(14,2) NOT NULL,
    metric_value  NUMERIC(14,2) NOT NULL,
    detail        JSONB NOT NULL DEFAULT '{}',
    UNIQUE (kind, month)
);

CREATE TABLE IF NOT EXISTS events (
    id       TEXT PRIMARY KEY,
    ts       TIMESTAMPTZ NOT NULL,
    kind     TEXT NOT NULL,
    payload  JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_ts_idx ON events (ts);

CREATE TABLE IF NOT EXISTS decisions (
    id                   TEXT PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    action_type          TEXT NOT NULL,
    summary              TEXT NOT NULL,
    level                TEXT NOT NULL CHECK (level IN ('AUTO', 'NOTIFY', 'APPROVAL')),
    reason               TEXT NOT NULL,
    status               TEXT NOT NULL,
    actor                TEXT NOT NULL,
    payload              JSONB NOT NULL DEFAULT '{}',
    executed_simulated   BOOLEAN NOT NULL DEFAULT FALSE,
    approval_request_id  TEXT
);
CREATE INDEX IF NOT EXISTS decisions_ts_idx ON decisions (ts);

CREATE TABLE IF NOT EXISTS approval_requests (
    id           TEXT PRIMARY KEY,
    decision_id  TEXT NOT NULL REFERENCES decisions(id),
    approver_id  TEXT NOT NULL,
    question     TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    resolved_at  TIMESTAMPTZ,
    channel_ref  TEXT
);

CREATE TABLE IF NOT EXISTS rules (
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    level        TEXT NOT NULL CHECK (level IN ('AUTO', 'NOTIFY', 'APPROVAL')),
    category     TEXT,
    supplier     TEXT,
    action_type  TEXT,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- Every read of a company workstation by the agent (D-004); the MCP server writes it.
CREATE TABLE IF NOT EXISTS access_log (
    id        SERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    employee  TEXT NOT NULL,
    action    TEXT NOT NULL,
    path      TEXT NOT NULL,
    reason    TEXT
);
