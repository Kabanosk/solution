import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"backend"))
for path,digest in json.loads((Path(__file__).resolve().parents[1]/"provenance.json").read_text()).items():
    if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:raise RuntimeError("Audit reference is stale")
from . import original_audit as a

def evaluate(payload: str) -> str:
    try:b=json.loads(payload)
    except (ValueError,TypeError):return "invalid JSON"
    if not isinstance(b,dict):return "unknown operation"
    try:
        match b.get("op"):
            case "check":out=a.check(b["requester"],b["role"],b["target"],b.get("resource",""))
            case "salary_sql":out=a.salary_sql(b.get("alias",""))
            case "salary":out=a.is_salary_row(b["row"])
            case "payee":out=a.payee_of(b["row"],b.get("people",[]))
            case "reports":out=sorted(a.reports_of(b["viewer"],b.get("people",[])))
            case "can_salary":out=a.can_see_salary(b["viewer"],b["role"],b.get("payee"),b.get("reports",[]))
            case "can_task":out=a.can_see_task(b["viewer"],b["role"],b.get("requested_by"),b.get("owner_id"))
            case "filter":
                v=a.filter_salary_rows(b.get("rows",[]),b["viewer"],b["role"],b.get("people",[]),fold=b.get("fold",True));out={"visible":v.visible,"hidden":v.hidden,"summaries":v.summaries}
            case _:return "unknown operation"
        return json.dumps(out,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    except (ValueError,TypeError,KeyError,IndexError):return "invalid audit input"
