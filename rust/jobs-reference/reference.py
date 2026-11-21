"""Isolated state/effect characterization of the unmodified Scout job queue."""
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from scout.jobs import JobQueue


def view(job):
    return {k: v for k, v in job.model_dump(mode="json").items() if k != "run_after"}


def main():
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
            raise ValueError()
    except (ValueError, TypeError):
        print('{"error":"invalid_input"}')
        return
    url = os.environ["SCOUT_RUST_TEST_DATABASE_URL"]
    if not any(x in url for x in ("@127.0.0.1:", "@localhost:")):
        raise ValueError("characterization requires local PostgreSQL")
    schema = "tenant_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            admin.execute("CREATE TABLE agent_jobs (id BIGSERIAL PRIMARY KEY,kind TEXT NOT NULL,payload JSONB NOT NULL DEFAULT '{}',dedupe_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,run_after TIMESTAMPTZ NOT NULL DEFAULT now(),task_id TEXT,worker TEXT,error TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            factory = lambda: psycopg.connect(url, options=f"-csearch_path={schema}", row_factory=dict_row)
            queue, results = JobQueue(factory), []
            for op in data["operations"]:
                kind = op["op"]
                if kind == "enqueue":
                    result = queue.enqueue(op["kind"], op.get("payload", {}), op["key"])
                elif kind == "claim":
                    job = queue.claim(op.get("worker", "worker"))
                    result = view(job) if job else None
                elif kind == "renew":
                    result = queue.renew(op["id"], op["attempt"])
                elif kind == "done":
                    result = queue.done(op["id"], op.get("task_id"), attempt=op.get("attempt"))
                elif kind == "fail":
                    result = queue.fail(op["id"], op.get("error", "failed"), attempt=op.get("attempt"))
                elif kind == "get":
                    result = view(queue.get(op["id"]))
                elif kind == "expire":
                    admin.execute("UPDATE agent_jobs SET updated_at=now()-interval '1 day',run_after=now()-interval '1 day' WHERE id=%s", (op["id"],))
                    result = None
                elif kind == "reap":
                    result = [view(job) for job in sorted(queue.reap_expired(), key=lambda job:job.id)]
                else:
                    raise ValueError("unknown operation")
                results.append(result)
            rows = admin.execute("SELECT id,kind,payload,dedupe_key,status,attempts,task_id,worker,error FROM agent_jobs ORDER BY id").fetchall()
            columns = ["id","kind","payload","dedupe_key","status","attempts","task_id","worker","error"]
            print(json.dumps({"results":results,"rows":[dict(zip(columns,row)) for row in rows]}, ensure_ascii=False, separators=(",",":"), sort_keys=True))
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()
