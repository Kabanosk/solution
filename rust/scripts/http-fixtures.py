"""Provision ONLY the named migration fixture in an explicitly selected local test database."""
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from uuid import NAMESPACE_DNS, uuid5
import psycopg
from psycopg import sql

root = Path(__file__).resolve().parents[2]
url = os.environ["SCOUT_RUST_TEST_DATABASE_URL"]
parsed = urlparse(url)
if parsed.hostname not in ("127.0.0.1", "localhost") or parsed.path != "/scout_migration":
    raise ValueError("requires the dedicated local scout_migration database")
oid = uuid5(NAMESPACE_DNS, "scout-rust-migration-http-fixture")
uid = uuid5(NAMESPACE_DNS, "scout-rust-migration-http-user")
schema = "tenant_" + oid.hex
slug = "rust-migration-fixture"
token = "scout_rust_migration_local_test_session_0123456789"
with psycopg.connect(url) as conn:
    conn.execute((root / "backend/cfo/db/auth.sql").read_text())
    conn.execute("DELETE FROM scout_auth.organizations WHERE id=%s", (oid,))
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    if "--cleanup" in sys.argv:
        conn.execute("DELETE FROM scout_auth.users WHERE id=%s", (uid,))
    else:
        conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,'Rust migration fixture',%s)", (oid,slug,schema))
        conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,'rust-fixture@example.invalid','Fixture Owner') ON CONFLICT (id) DO NOTHING", (uid,))
        conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner','fixture-owner')", (oid,uid))
        conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) VALUES (%s,%s,%s,now()+interval '2 hours')", (hashlib.sha256(token.encode()).hexdigest(),uid,oid))
        conn.execute(sql.SQL("CREATE SCHEMA {}; SET LOCAL search_path TO {}").format(sql.Identifier(schema),sql.Identifier(schema)))
        for file in ["backend/cfo/db/schema.sql","backend/scout/schema.sql","backend/cfo/db/scout_dm_replies.sql","backend/cfo/db/scout_guardrails.sql","backend/cfo/db/scout_slack_members.sql","backend/cfo/db/scout_strategy.sql"]:
            conn.execute((root/file).read_text())
        conn.execute("INSERT INTO employees(id,name,role) VALUES ('fixture-owner','Fixture Owner','CEO')")
        conn.execute("INSERT INTO agent_tasks(id,dedupe_key,loop,title,status,owner_id,amount,counterparty,created_at,updated_at) VALUES ('SC-100','fixture:invoice','invoicing','Find invoice héllo 😀','waiting','fixture-owner',-123.45,'Fixture supplier','2026-11-01T10:00:00Z','2026-11-01T10:00:00Z'),('SC-101','fixture:strategy','strategy','Daily briefing','done','fixture-owner',NULL,NULL,'2026-11-01T09:00:00Z','2026-11-01T09:00:00Z')")
        conn.execute("INSERT INTO agent_task_steps(task_id,ts,kind,source,text,detail) VALUES ('SC-100','2026-11-01T10:00:00Z','lookup','files','Read fixture folder','{\"count\":0}')")
        conn.execute("INSERT INTO approvals(id,task_id,approver_id,question,created_at) VALUES ('apr-fixture','SC-100','fixture-owner','Approve this simulated action?','2026-11-01T10:00:00Z')")
        conn.execute("INSERT INTO audit_log(ts,task_id,actor,action,target,resource,decision) VALUES ('2026-11-01T10:00:00Z','SC-100','agent','read','fixture','files','ALLOW')")
        conn.execute("INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,invoice_number,documented,invoice_id) VALUES ('booking-1','2026-11-01','transfer','Fixture payment','Fixture supplier',-123.45,'INV-TEST',false,NULL)")
        conn.execute("INSERT INTO invoices(id,number,supplier,issue_date,total,transaction_id) VALUES ('invoice-1','INV-TEST','Fixture supplier','2026-11-01',123.45,'booking-1')")
        conn.execute("INSERT INTO invoice_lines(invoice_id,position,description,quantity,unit_price,amount) VALUES ('invoice-1',1,'Supplies',2.0000,61.7250,123.45)")
print("HTTP fixtures cleaned" if "--cleanup" in sys.argv else "HTTP fixtures ready")
