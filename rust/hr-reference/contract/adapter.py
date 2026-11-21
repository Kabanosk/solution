import hashlib,json
from pathlib import Path
from datetime import date
for path,digest in json.loads((Path(__file__).resolve().parents[1]/"provenance.json").read_text()).items():
    if hashlib.sha256((Path(__file__).resolve().parents[3]/path).read_bytes()).hexdigest()!=digest:raise RuntimeError("HR source is stale")
from .original_hr import HR
class _Cursor:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0]if self.rows else None
    def fetchall(self):return self.rows
class _Conn:
    def __init__(self,b):self.b=b
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=()):
        people=self.b.get("people",[])
        if "FROM vacations" in sql:
            return _Cursor([{"n":1}for v in self.b.get("vacations",[])if v["employee_id"]==args[0]and v["start"]<=str(args[1])<=v["end"]])
        if "WHERE id =" in sql:return _Cursor([p for p in people if p["id"]==args[0]])
        if "WHERE slack_user_id =" in sql:return _Cursor([p for p in people if p.get("slack_user_id")==args[0]])
        return _Cursor(sorted(people,key=lambda p:p["id"]))
def evaluate(payload: str) -> str:
    try:b=json.loads(payload)
    except (ValueError,TypeError):return "invalid JSON"
    if not isinstance(b,dict):return "unknown operation"
    hr=HR(lambda:_Conn(b),b.get("user_map"),b.get("slack_names"))
    try:
        op=b.get("op");person=b.get("person","")
        match op:
            case "employee":out=hr.employee(person)
            case "by_slack":out=hr.by_slack_id(person)
            case "all":out=hr.all()
            case "name":out=hr.name(b.get("person"))
            case "full_name":out=hr.full_name(person)
            case "role":out=hr.role(person)
            case "vacation":out=hr.on_vacation(person,date.fromisoformat(b["day"]))
            case "manager":out=hr.manager(person)
            case "chain":out=hr.chain(person)
            case "available":out=hr.first_available(person,date.fromisoformat(b["day"]))
            case _:return "unknown operation"
        return json.dumps(out,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    except (ValueError,TypeError,KeyError,IndexError):return "invalid HR input"
