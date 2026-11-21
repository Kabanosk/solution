"""Exact model-inference characterization with portable development artifacts."""
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"backend"))
for _path,_hash in json.loads((Path(__file__).resolve().parents[1]/"provenance.json").read_text()).items():
    if hashlib.sha256((ROOT/_path).read_bytes()).hexdigest()!=_hash:
        raise RuntimeError("Liquidity reference is stale; refresh and record again")
import numpy as np
import pandas as pd
from . import reference_liquidity as m
from decimal import Decimal
from scout.policy import PolicyContext


def _encode(value):
    if isinstance(value,Decimal):return str(value)
    if hasattr(value,"model_dump"):return _encode(value.model_dump(mode="python"))
    if hasattr(value,"isoformat") and not isinstance(value,pd.Timestamp):return value.isoformat()
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,float):return {"$nan":True} if math.isnan(value) else {"$f64":struct.pack(">d",value).hex()}
    if isinstance(value,pd.Timestamp):return str(value.date())
    if isinstance(value,dict):return {str(k):_encode(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_encode(v) for v in value]
    return value


def _frame(frame):
    return {"columns":list(frame.columns),"index":[str(day.date()) for day in frame.index],"rows":[list(row) for row in frame.itertuples(index=False,name=None)]}


def _transactions(rows):
    frame=pd.DataFrame(rows,columns=["booking_date","amount","category","team_id","counterparty","invoice_id"])
    frame["booking_date"]=pd.to_datetime(frame["booking_date"])
    frame["amount"]=frame["amount"].astype(float)
    return frame


def _forecast(body):
    rows=body.get("forecast",[])
    f=pd.DataFrame(rows)
    if "date" in f:f.index=pd.to_datetime(f.pop("date"))
    else:f.index=pd.DatetimeIndex([])
    f.attrs.update(body.get("attrs",{}))
    return f


def _proposal(p):return {"model":p.model_dump(mode="python"),"payload":p.to_payload(),"needs_approval":p.needs_approval}


def evaluate(payload: str) -> str:
    try:body=json.loads(payload)
    except (ValueError,TypeError):return "invalid JSON"
    if not isinstance(body,dict) or body.get("op") not in ("helpers","fixed","gaps","surplus","credit","investment","forecast","review"):
        return "unknown operation"
    try:
        tx=_transactions(body.get("transactions",[]))
        op=body["op"]
        if op=="helpers":
            amount=Decimal(str(body["amount"]));step=Decimal(str(body.get("step",500)))
            output={"dec":m._dec(amount),"eur":m._eur(amount),"up":m._round_up(amount,step),"down":m._round_down(amount,step)}
        elif op=="fixed":output=m.fixed_costs_monthly(tx)
        elif op=="gaps":output=m.detect_gaps(_forecast(body),body.get("buffer"),transactions=tx if body.get("use_transactions") else None)
        elif op=="surplus":output=m.find_surplus(_forecast(body),body.get("buffer"),transactions=tx if body.get("use_transactions") else None,min_amount=Decimal(str(body.get("min_amount",1000))),step=Decimal(str(body.get("step",500))))
        else:
            model=m.fm.ForecastModel.load(ROOT/"rust/forecast-model/fixtures"/body["model"]) if body.get("model") else None
            outflows=m._normalize_outflows(body.get("outflows",[]))
            evidence=m.Evidence(model=model,transactions=tx if body.get("transactions") else None,outflows=outflows)
            context=PolicyContext(**body.get("policy_context",{}))
            if op=="credit":output=_proposal(m.propose_credit_line([m.LiquidityGap(**g)for g in body["gaps"]],evidence,policy_context=context,reserve=Decimal(str(body.get("reserve","0.20")))))
            elif op=="investment":output=_proposal(m.propose_investment(m.Surplus(**body["surplus"]),evidence,policy_context=context,instrument=body.get("instrument","money market fund")))
            elif op=="forecast":
                frame=m.forecast_with(outflows,days=body.get("days",90),start_balance=body.get("start_balance"),model=model,transactions=tx,scheduled_payments=body.get("scheduled"),buffer=body.get("buffer"))
                output={"frame":_frame(frame),"attrs":frame.attrs}
            else:
                review=m.review_liquidity(outflows,days=body.get("days",90),start_balance=body.get("start_balance"),model=model,transactions=tx,buffer=body.get("buffer"),policy_context=context)
                output={"forecast":{"frame":_frame(review.forecast),"attrs":review.forecast.attrs},"buffer":review.buffer,"gaps":review.gaps,"proposals":[_proposal(p)for p in review.proposals]}
        return json.dumps(_encode(output),sort_keys=True,ensure_ascii=False,separators=(",",":"))
    except (ValueError,KeyError,TypeError,OverflowError,IndexError,OSError,AttributeError):return "invalid liquidity input"
