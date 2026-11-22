-- Explicit additive upgrade for existing Scout schemas (guardrails, T052). Never replay the full application schema here.
-- The operator migration verifies the registered organization and binds a schema-only search_path first.
-- Keep in step with the guardrails block of backend/scout/schema.sql (fresh organizations get it from there).

-- The prompts of blocked human messages, in full, for the compliance report. Never exposed by an API.
CREATE TABLE IF NOT EXISTS guardrail_strikes (
    id           BIGSERIAL PRIMARY KEY,
    person       TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    prompt       TEXT NOT NULL,
    rails        TEXT NOT NULL DEFAULT '',
    reported_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS guardrail_strikes_person_idx ON guardrail_strikes (person, ts DESC);

-- The lockout after the third strike (T087): one row per lock. Active = unlocked_at IS NULL AND locked_until > now().
-- `unlocked_by` is the person who lifted it (the owner on the dashboard) or "guard-reset" (the demo operator).
CREATE TABLE IF NOT EXISTS guardrail_locks (
    id            BIGSERIAL PRIMARY KEY,
    person        TEXT NOT NULL,
    locked_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    locked_until  TIMESTAMPTZ NOT NULL,
    rails         TEXT NOT NULL DEFAULT '',
    unlocked_at   TIMESTAMPTZ,
    unlocked_by   TEXT
);
CREATE INDEX IF NOT EXISTS guardrail_locks_person_idx ON guardrail_locks (person, locked_until DESC);

-- The original of a flagged document, kept so an approved invoice has a retrievable attachment.
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

-- BLOCK = a guardrail verdict, MASK = salary rows folded into a payroll total; older databases allowed ALLOW/DENY only.
-- Replaces only the decision check of the registered schema's own audit_log (search_path is bound to it), and only
-- when the current one lacks MASK or BLOCK: DROP/ADD CONSTRAINT takes an exclusive lock and scans the whole table,
-- which a repeated --check/--migrate must not do.
DO $guardrails$
BEGIN
    IF to_regclass('audit_log') IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('audit_log') AND conname = 'audit_log_decision_check' AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%''MASK''%' AND pg_get_constraintdef(oid) LIKE '%''BLOCK''%'
    ) THEN
        ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_decision_check;
        ALTER TABLE audit_log ADD CONSTRAINT audit_log_decision_check
            CHECK (decision IN ('ALLOW', 'DENY', 'MASK', 'BLOCK'));
    END IF;
END
$guardrails$;
