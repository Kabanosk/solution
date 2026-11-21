"""Characterize deployed pure forecast kernels without database/provider effects.

The source snapshots retain original pure function bodies. DB readers and Python
training-only fit_band remain separate. Float values are represented by their
IEEE754 bits so recordings never hide differences through decimal formatting.
"""
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"backend"))
for _path,_expected in json.loads((Path(__file__).resolve().parents[1]/"provenance.json").read_text()).items():
    if hashlib.sha256((ROOT/_path).read_bytes()).hexdigest()!=_expected:
        raise RuntimeError("Forecast source bundle is stale; refresh and record again")
import numpy as np
import pandas as pd
from . import reference_features as f, reference_schedule as s, reference_band as b


def _encode(value):
    if isinstance(value,np.generic): value=value.item()
    if isinstance(value,float):
        return {"$nan":True} if math.isnan(value) else {"$f64":struct.pack(">d",value).hex()}
    if isinstance(value,pd.Timestamp): return str(value.date())
    if isinstance(value,dict): return {str(k):_encode(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [_encode(v) for v in value]
    return value


def _frame(value):
    return {"columns":list(value.columns),"index":[str(d.date()) for d in value.index],
            "rows":[list(row) for row in value.itertuples(index=False,name=None)]}


def _series(value):
    return {"name":value.name,"index":[str(d.date()) for d in value.index],"values":value.tolist()}


def _transactions(rows):
    frame=pd.DataFrame(rows,columns=["booking_date","amount","category","team_id","counterparty","invoice_id"])
    frame["booking_date"]=pd.to_datetime(frame["booking_date"])
    frame["amount"]=frame["amount"].astype(float)
    return frame


def evaluate(payload: str) -> str:
    try: body=json.loads(payload)
    except (ValueError,TypeError): return "invalid JSON"
    if not isinstance(body,dict): return "unknown operation"
    try:
        tx=_transactions(body.get("transactions",[]))
        match body.get("op"):
            case "feature_columns": output=f.feature_set_columns(body["name"])
            case "has_schedule": output=f.has_schedule(body["name"])
            case "needs_levels": output=f.needs_levels(body["name"])
            case "holidays": output=sorted(str(day) for day in f.public_holidays(body["year"]))
            case "calendar": output=_frame(f.calendar_features(body["dates"],shutdown_days=[tuple(pair) for pair in body.get("shutdown_days",[])],due_days=body.get("due_days")))
            case "infer_due": output=f.infer_due_days(tx,min_months=body.get("min_months",3))
            case "infer_shutdown": output=sorted(f.infer_shutdown_days(tx,min_run=body.get("min_run",7)))
            case "closed": output=_series(f.closed_days(tx,min_run=body.get("min_run",7)))
            case "recent": output=_frame(f.recent_levels(tx,body["dates"],windows=body.get("windows",[28,90])))
            case "baseline" | "applied_baseline":
                frame=pd.DataFrame(body["rows"])
                frame.index=pd.DatetimeIndex(pd.to_datetime(body["dates"]))
                output=_series((f.applied_baseline if body["op"]=="applied_baseline" else f.level_baseline)(frame))
            case "daily_cash": output=_frame(f.daily_cash_flow(tx,start=body.get("start"),end=body.get("end"),exclude_categories=body.get("exclude_categories",[])))
            case "daily_features": output=_frame(f.daily_features(tx,end=body.get("end"),exclude_categories=body.get("exclude_categories",[]),min_shutdown_run=body.get("min_run",7)))
            case "schedule_infer": output=s.infer(tx,categories=body.get("categories",s.CATEGORIES))
            case "schedule_amounts": output=_frame(s.amounts(body["spec"],body["dates"]))
            case "schedule_total": output=_series(s.total(body["spec"],body["dates"]))
            case "band_offsets": output=b.offsets(body["spec"],body["days"])
            case "legacy_offsets": output=b.legacy_offsets(body["sigma"],body["days"])
            case _: return "unknown operation"
        return json.dumps(_encode(output),sort_keys=True,ensure_ascii=False,separators=(",",":"))
    except (ValueError,TypeError,KeyError,OverflowError,ArithmeticError,AttributeError):
        return "invalid forecast input"
