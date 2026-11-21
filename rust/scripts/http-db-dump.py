"""Dump only the named local HTTP fixture, including durable effects and sequence state."""
import os
from urllib.parse import urlparse
from uuid import NAMESPACE_DNS, uuid5
import psycopg
from psycopg import sql

url = os.environ["SCOUT_RUST_TEST_DATABASE_URL"]
parsed = urlparse(url)
if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/scout_migration":
    raise ValueError("requires dedicated local test database")
schema = "tenant_" + uuid5(NAMESPACE_DNS, "scout-rust-migration-http-fixture").hex
with psycopg.connect(url) as conn:
    for table in ["agent_tasks","agent_task_steps","agent_jobs","approvals","audit_log","connections","invoices","invoice_lines","transactions"]:
        result = conn.execute(sql.SQL("SELECT coalesce(jsonb_agg(to_jsonb(row) ORDER BY row.id),'[]'::jsonb)::text FROM {}.{} row").format(sql.Identifier(schema),sql.Identifier(table))).fetchone()[0]
        print(table + ":" + result)
    for sequence in ["agent_task_seq","agent_task_steps_id_seq","agent_jobs_id_seq","audit_log_id_seq","invoice_lines_id_seq"]:
        value,called = conn.execute(sql.SQL("SELECT last_value,is_called FROM {}.{}").format(sql.Identifier(schema),sql.Identifier(sequence))).fetchone()
        print(f"{sequence}:{value}:{called}")
