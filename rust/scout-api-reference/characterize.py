"""Read-only direct-handler comparison against the dedicated HTTP fixture.

The fixture is provisioned separately by rust/scripts/http-fixtures.py. No
production credentials or schema are used. This script only issues SELECTs.
"""
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.encoders import jsonable_encoder
from cfo.api.auth import verify_session
from cfo.api.errors import ApiError
from cfo.config import get_settings
from cfo.tenancy import bind_tenant, connect
from scout import api
from scout.config import ScoutSettings


def corpus():
    yield {"op": "tasks"}
    for options in [{"status":"waiting"},{"status":"waiting,done"},{"status":"unknown"},{"status":""},{"loop":"strategy"},{"owner":"fixture-owner"},{"owner":"missing"},{"q":"héllo"},{"q":"😀"},{"q":"%"},{"q":"_"},{"q":"missing"},{"limit":1}]:
        yield {"op":"tasks",**options}
    for body in [{"op":"tasks"},{"op":"tasks","loop":"strategy"},{"op":"task","id":"SC-101"},{"op":"task","id":"SC-100"}]:
        yield {**body,"role":"staff"}
    for identifier in ["SC-100","SC-101","missing","héllo😀"]:
        yield {"op":"task","id":identifier}
    for status in ["pending","approved","unknown",""]:
        yield {"op":"approvals","status":status}
    for options in [{},{"decision":"allow"},{"decision":"deny"},{"task_id":"SC-100"},{"task_id":"missing"},{"limit":1}]:
        yield {"op":"audit",**options}
    for identifier in ["booking-1","missing"]:
        yield {"op":"invoice","id":identifier}
    yield {"op":"connections"}
    yield {"op":"row"}


def original(body, owner, url):
    tenant = dataclasses.replace(owner, role="staff",employee_id="fixture-staff") if body.get("role")=="staff" else owner
    with bind_tenant(tenant), connect(url) as conn:
        try:
            match body["op"]:
                case "tasks": output = api.list_tasks(conn, status=body.get("status"), loop=body.get("loop"), owner=body.get("owner"), q=body.get("q"), limit=body.get("limit",50))
                case "task": output = api.get_task(body["id"],conn)
                case "approvals": output = api.list_approvals(conn,body.get("status","pending"))
                case "audit": output = api.audit(conn,task_id=body.get("task_id"),decision=body.get("decision"),limit=body.get("limit",100))
                case "invoice": output = api.transaction_invoice(body["id"],conn)
                case "connections": output = api.connections(conn,ScoutSettings())
                case "row": output = api._jsonable(conn.execute("SELECT 1.2300::numeric(14,4) AS amount,'top-secret'::bytea AS ciphertext,NULL::text AS empty,'2026-11-01'::date AS day,'2026-11-01T10:00:00.123456Z'::timestamptz AS ts").fetchone())
            return {"ok":jsonable_encoder(output)}
        except ApiError as error:
            return {"error":{"status":error.status_code,"code":error.code,"message":error.message}}


if __name__=="__main__":
    url=os.environ["SCOUT_TEST_DATABASE_URL"]
    parsed=urlparse(url)
    if parsed.hostname not in ("localhost","127.0.0.1") or parsed.path!="/scout_migration":
        raise ValueError("only the dedicated local scout_migration database is allowed")
    get_settings.cache_clear()
    os.environ["DATABASE_URL"]=url
    owner=verify_session("scout_rust_migration_local_test_session_0123456789","rust-migration-fixture")
    if owner is None: raise RuntimeError("provision HTTP fixtures before comparison")
    cases=[{"input":body,"output":original(body,owner,url)} for body in corpus()]
    out=ROOT/"rust/scout-api/characterization.jsonl"
    out.write_text("".join(json.dumps(c,ensure_ascii=False)+"\n" for c in cases))
    binary=Path(os.environ["SCOUT_API_CHARACTERIZATION_BINARY"])
    result=subprocess.run([str(binary)],input="".join(json.dumps(c["input"])+"\n" for c in cases),capture_output=True,text=True,check=True)
    actual=[json.loads(line) for line in result.stdout.splitlines()]
    mismatches=[{"input":c["input"],"expected":c["output"],"actual":a} for c,a in zip(cases,actual) if c["output"]!=a]
    if len(actual)!=len(cases): raise RuntimeError("native output count differs")
    (ROOT/"rust/scout-api/characterization-mismatches.json").write_text(json.dumps(mismatches,indent=2,ensure_ascii=False)+"\n")
    print(f"{len(cases)-len(mismatches)}/{len(cases)} original direct-handler cases match")
    if mismatches:
        print(json.dumps(mismatches[:2],ensure_ascii=False,indent=2));raise SystemExit(1)
