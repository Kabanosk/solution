-- Opt-in only, installed by python -m cfo.db.migrate --migrate. Never run at application startup.
CREATE SCHEMA IF NOT EXISTS scout_auth;
REVOKE ALL ON SCHEMA scout_auth FROM PUBLIC;
CREATE TABLE IF NOT EXISTS scout_auth.organizations (
    id uuid PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    schema_name text NOT NULL UNIQUE,
    legacy boolean NOT NULL DEFAULT false,
    CONSTRAINT organization_schema_isolation CHECK (
      (id = '00000000-0000-4000-8000-000000000001'::uuid AND slug = 'pizzeria-amalfi'
       AND name = 'Pizzeria Amalfi' AND schema_name = 'public' AND legacy)
      OR
      (id <> '00000000-0000-4000-8000-000000000001'::uuid AND slug <> 'pizzeria-amalfi'
       AND NOT legacy AND schema_name = 'tenant_' || replace(id::text, '-', ''))
    ),
    CONSTRAINT organization_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,79}$')
);
CREATE TABLE IF NOT EXISTS scout_auth.users (
    id uuid PRIMARY KEY,
    email text NOT NULL UNIQUE,
    name text NOT NULL,
    password_hash text,
    google_subject text UNIQUE
);
CREATE TABLE IF NOT EXISTS scout_auth.memberships (
    organization_id uuid NOT NULL REFERENCES scout_auth.organizations(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES scout_auth.users(id) ON DELETE CASCADE,
    role text NOT NULL,
    employee_id text NOT NULL,
    PRIMARY KEY (organization_id, user_id)
);
CREATE TABLE IF NOT EXISTS scout_auth.sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES scout_auth.users(id) ON DELETE CASCADE,
    organization_id uuid NOT NULL REFERENCES scout_auth.organizations(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON scout_auth.sessions(expires_at);
CREATE TABLE IF NOT EXISTS scout_auth.oauth_transactions (
    state_hash text PRIMARY KEY,
    browser_hash text NOT NULL,
    organization_id uuid REFERENCES scout_auth.organizations(id) ON DELETE CASCADE,
    verifier text NOT NULL,
    nonce text NOT NULL,
    expires_at timestamptz NOT NULL,
    link_user_id uuid REFERENCES scout_auth.users(id) ON DELETE CASCADE
);
-- A registration draft has no organization/member until verified Google consent completes.
ALTER TABLE scout_auth.oauth_transactions ADD COLUMN IF NOT EXISTS registration jsonb;
CREATE TABLE IF NOT EXISTS scout_auth.rate_limits (
    key text PRIMARY KEY,
    attempts integer NOT NULL,
    reset_at timestamptz NOT NULL
);
-- Workspace setup (onboarding). One optional row per organization: decisions and last results only. Actual connector
-- status is always derived from the tenant-local `connections` rows. Signup inserts just (organization_id); existing
-- organizations get no row and are never silently marked pending.
CREATE TABLE IF NOT EXISTS scout_auth.workspace_setup (
    organization_id uuid PRIMARY KEY REFERENCES scout_auth.organizations(id) ON DELETE CASCADE,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'dismissed', 'completed')),
    steps jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(steps) = 'object'),
    network jsonb CHECK (network IS NULL OR jsonb_typeof(network) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- Provider OAuth (mailbox, Slack) in flight. Only hashes are stored; a row is single-use and short-lived and is bound
-- to the initiating organization, owner session, browser proof, provider and exact redirect.
CREATE TABLE IF NOT EXISTS scout_auth.setup_oauth_states (
    state_hash text PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES scout_auth.organizations(id) ON DELETE CASCADE,
    session_hash text NOT NULL,
    browser_hash text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('google', 'slack')),
    redirect_uri text NOT NULL,
    label text,
    verifier_ciphertext bytea,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS setup_oauth_states_org_idx ON scout_auth.setup_oauth_states(organization_id, provider);
-- Register unmodified existing data, but deliberately enroll nobody. Operator enrollment is required.
INSERT INTO scout_auth.organizations(id,slug,name,schema_name,legacy)
VALUES ('00000000-0000-4000-8000-000000000001','pizzeria-amalfi','Pizzeria Amalfi','public',true)
ON CONFLICT (id) DO NOTHING;
