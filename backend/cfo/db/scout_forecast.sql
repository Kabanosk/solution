-- Explicit operator upgrade, applied with the registered tenant's schema-only search_path.
DO $forecast$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('agent_tasks')
          AND conname = 'agent_tasks_loop_check' AND pg_get_constraintdef(oid) LIKE '%''forecast''%'
    ) THEN
        ALTER TABLE agent_tasks DROP CONSTRAINT IF EXISTS agent_tasks_loop_check;
        ALTER TABLE agent_tasks ADD CONSTRAINT agent_tasks_loop_check
            CHECK (loop IN ('invoicing', 'cost_control', 'liquidity', 'chat', 'expenses', 'strategy', 'forecast'));
    END IF;
END
$forecast$;

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
