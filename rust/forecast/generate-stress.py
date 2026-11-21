"""Deterministic forecast characterization inputs; original results are never rewritten."""
import datetime as dt
import json
import random
from pathlib import Path

RNG=random.Random(20261121)
ROOT=Path(__file__).resolve().parent
cases=[json.loads(line) for line in (ROOT/"inputs.jsonl").read_text().splitlines()]

def add(op,**fields):
    cases.append({"fn":"evaluate","args":[json.dumps({"op":op,**fields},separators=(",",":"))]})

for index in range(100):
    start=dt.date(2024+index%3,1,1)
    rows=[]
    for number in range(RNG.randrange(10,100)):
        day=start+dt.timedelta(days=RNG.randrange(0,100))
        rows.append({"booking_date":str(day),"amount":RNG.randrange(-1000000,1000000)/100,"category":RNG.choice(["revenue","food","rent","tax","payroll","utilities","partner_investment",None])})
    if index%2==0:rows.sort(key=lambda r:r["booking_date"])
    dates=[str(start+dt.timedelta(days=i)) for i in [0,7,28,60,100,130]]
    for op in ["daily_cash","infer_shutdown","infer_due","schedule_infer"]:add(op,transactions=rows)
    add("recent",transactions=rows,dates=dates,windows=[7,28,90])
    if index<10:add("daily_features",transactions=rows,end=str(start+dt.timedelta(days=110)))

for amount in [0,0.0,-0.0,-100,-100.0,0.1,-0.1]:
    spec={"categories":{"rent":{"period":1,"profiles":{"0":[[31,amount]]},"latest":[[31,amount]]}}}
    for dates in [["2026-02-01"],["2026-02-28"],["2026-02-01","2026-02-28"],[]]:
        for op in ["schedule_amounts","schedule_total"]:add(op,spec=spec,dates=dates)

for index in range(100):
    add("band_offsets",spec={"p":RNG.randrange(5000,10001)/10000,"lower":RNG.random()*100,"upper":RNG.random()*100,"horizon_measured":RNG.randrange(1,100)},days=RNG.randrange(1,10000))

(ROOT/"all-inputs.jsonl").write_text("".join(json.dumps(case)+"\n" for case in cases))
print(len(cases),"structured forecast cases")
