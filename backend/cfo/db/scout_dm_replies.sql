-- Explicit additive upgrade for existing Scout schemas. Never replay the full application schema here.
-- The operator migration verifies the registered organization and binds a schema-only search_path first.
CREATE INDEX IF NOT EXISTS agent_jobs_scout_thread_idx ON agent_jobs ((payload->>'thread'))
    WHERE kind = 'dm' AND payload->>'mention' = 'true';

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
ALTER TABLE dm_replies ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
