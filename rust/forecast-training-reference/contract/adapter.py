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
        raise RuntimeError("Forecast training reference is stale; refresh and record again")
import numpy as np
import pandas as pd
from . import reference_training as m


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
    if not isinstance(body,dict) or body.get("op") not in ("train","table","split","fit_band"):
        return "unknown operation"
    try:
        tx=_transactions(body.get("transactions",[]))
        feature_set=body.get("feature_set")
        excluded=body.get("excluded",["partner_investment"])
        if body["op"]=="fit_band":output=m.fband.fit_band(pd.DataFrame(body.get("folds",[])))
        elif body["op"]=="table":output=_frame(m._training_table(tx,excluded,feature_set or "calendar",keep_closed=body.get("keep_closed",False)))
        elif body["op"]=="split":
            train,test=m.backtest_split(tx,body.get("holdout_days",20),excluded,feature_set or "calendar")
            output={"train":_frame(train),"test":_frame(test)}
        else:
            model=m.train(tx,params=body.get("params"),holdout_days=body.get("holdout_days",60),exclude_categories=excluded,log_to_mlflow=False,feature_set=feature_set,band=body.get("band",False))
            dates=body.get("dates",pd.date_range(model.trained_until+pd.Timedelta(days=1),periods=7).astype(str).tolist())
            output={"meta":model.meta(),"predict":model.predict(dates).tolist(),"curves":model.training_curves,"importance":[[key,value] for key,value in model.feature_importances().items()]}
        return json.dumps(_encode(output),sort_keys=True,ensure_ascii=False,separators=(",",":"))
    except (ValueError,KeyError,TypeError,OverflowError,IndexError,OSError,AttributeError):return "invalid training input"
