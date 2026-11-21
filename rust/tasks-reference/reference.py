"""Characterize unchanged TaskRecorder against a private, temporary local schema."""
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/"backend"))
from scout.tasks import TaskRecorder


def view(value):
    record=hasattr(value,"model_dump")
    if record:value=value.model_dump(mode="json")
    if isinstance(value,list):return [view(item) for item in value]
    if record:
        return {key:(item is not None if key=="closed_at" else item) for key,item in value.items() if key not in {"created_at","updated_at","ts"}}
    return value


def main():
    try:
        data=json.load(sys.stdin)
        if not isinstance(data,dict) or not isinstance(data.get("operations"),list):raise ValueError()
    except (ValueError,TypeError):
        print('{"error":"invalid_input"}');return
    url=os.environ["SCOUT_RUST_TEST_DATABASE_URL"]
    if "@127.0.0.1:" not in url and "@localhost:" not in url:raise ValueError("requires local test DB")
    schema="tenant_"+uuid4().hex
    with psycopg.connect(url,autocommit=True,row_factory=dict_row) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {};SET search_path TO {}").format(sql.Identifier(schema),sql.Identifier(schema)))
        try:
            for path in ["backend/cfo/db/schema.sql","backend/scout/schema.sql"]:admin.execute((root/path).read_text())
            recorder=TaskRecorder(lambda:psycopg.connect(url,options=f"-csearch_path={schema}",row_factory=dict_row))
            results=[]
            for operation in data["operations"]:
                try:
                    kind=operation["op"]
                    if kind=="open":result=recorder.open(operation["key"],operation.get("loop","invoicing"),operation.get("title","Find invoice"),**operation.get("fields",{}))
                    elif kind=="get":result=recorder.get(operation["id"])
                    elif kind=="by_key":result=recorder.by_key(operation["key"])
                    elif kind=="update":result=recorder.update(operation["id"],**operation.get("fields",{}))
                    elif kind=="find_open":result=recorder.find_open(owner_id=operation.get("owner"),statuses=tuple(operation.get("statuses",["waiting","escalated"])),slack_ref=operation.get("slack_ref"))
                    elif kind=="evidence":result=recorder.add_evidence(operation["id"],operation["item"])
                    elif kind=="step":result=recorder.step(operation["id"],operation.get("kind","lookup"),operation.get("text","Read source"),**operation.get("options",{}))
                    elif kind=="steps":result=recorder.steps(operation["id"])
                    else:raise ValueError("unknown operation")
                    results.append(view(result))
                except KeyError:results.append({"error":"not_found"})
                except TypeError as error:results.append({"error":str(error)})
            tasks=[view(recorder.get(row["id"])) for row in admin.execute("SELECT id FROM agent_tasks ORDER BY id").fetchall()]
            print(json.dumps({"results":results,"tasks":tasks},sort_keys=True,ensure_ascii=False,separators=(",",":")))
        finally:admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__=="__main__":main()
