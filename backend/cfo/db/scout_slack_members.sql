-- Slack is the source of truth for who works here (scout/slack_directory.py). One row per Slack workspace member
-- Scout has asked for their name ('invited') or created an account for ('registered').
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
