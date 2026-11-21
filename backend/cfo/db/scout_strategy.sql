-- Explicit operator upgrade, applied with the registered tenant's schema-only search_path.
DO $strategy$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('agent_tasks')
          AND conname = 'agent_tasks_loop_check' AND pg_get_constraintdef(oid) LIKE '%''strategy''%'
    ) THEN
        ALTER TABLE agent_tasks DROP CONSTRAINT IF EXISTS agent_tasks_loop_check;
        ALTER TABLE agent_tasks ADD CONSTRAINT agent_tasks_loop_check
            CHECK (loop IN ('invoicing', 'cost_control', 'liquidity', 'chat', 'expenses', 'strategy'));
    END IF;
END
$strategy$;
