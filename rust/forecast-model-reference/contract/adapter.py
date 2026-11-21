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
        raise RuntimeError("Forecast model reference is stale; refresh and record again")
import numpy as np
import pandas as pd
from . import reference_model as m


def _encode(value):
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


def evaluate(payload: str) -> str:
    try:body=json.loads(payload)
    except (ValueError,TypeError):return "invalid JSON"
    if not isinstance(body,dict) or body.get("op") not in ("meta","features","predict","band","contributions","importance","explain","forecast","lowest","levels","partner_schedule","last_balance"):
        return "unknown operation"
    try:
        tx=_transactions(body.get("transactions",[]))
        if body["op"]=="partner_schedule":
            series=m.partner_tranche_schedule(tx,total=body.get("total",12));output={str(day.date()):value for day,value in series.items()}
        elif body["op"]=="last_balance":
            day,amount=m.last_known_balance(tx);output={"date":str(day.date()),"balance":amount}
        else:
            path=ROOT/"rust/forecast-model/fixtures"/body["model"]
            model=m.ForecastModel.load(path)
            if body.get("refresh"):model=model.with_levels_from(tx)
            dates=body.get("dates",[])
            match body["op"]:
                case "meta" | "levels":output=model.meta()
                case "features":output=_frame(model.features_for(dates))
                case "predict":output=model.predict(dates).tolist()
                case "band":output=model.band(body["days"])
                case "contributions":output=_frame(model.contributions(dates))
                case "importance":output=[[key,value] for key,value in model.feature_importances().items()]
                case "explain":output=model.explain(body["start"],body["end"],top=body.get("top",5))
                case "forecast" | "lowest":
                    scheduled=body.get("scheduled")
                    forecast=m.liquidity_forecast(body["days"],body.get("start_balance"),model=model,transactions=tx,scheduled_payments=scheduled)
                    if body["op"]=="forecast":output={"frame":_frame(forecast),"attrs":forecast.attrs}
                    else:
                        day,balance=m.lowest_balance(forecast);output={"date":str(day.date()),"balance":balance}
        return json.dumps(_encode(output),sort_keys=True,ensure_ascii=False,separators=(",",":"))
    except (ValueError,KeyError,TypeError,OverflowError,IndexError,OSError,AttributeError):return "invalid model input"
