"""Compare saved original cases with native results and report concise first differences."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"python"))
import scout_liquidity


def firstdiff(a,b,path=""):
    if type(a)!=type(b):return [path,a,b]
    if isinstance(a,dict):
        if set(a)!=set(b):return [path,list(a),list(b)]
        for key in a:
            difference=firstdiff(a[key],b[key],path+"/"+key)
            if difference:return difference
    elif isinstance(a,list):
        if len(a)!=len(b):return [path,len(a),len(b)]
        for index,(x,y) in enumerate(zip(a,b)):
            difference=firstdiff(x,y,path+"/"+str(index))
            if difference:return difference
    elif a!=b:return [path,a,b]
    return None


mismatches=[]
for index,line in enumerate((ROOT/"cases.jsonl").read_text().splitlines()):
    case=json.loads(line)
    expected=case["ok"]
    actual=scout_liquidity.evaluate(*case["args"])
    if expected!=actual:
        try:difference=firstdiff(json.loads(expected),json.loads(actual))
        except ValueError:difference=[expected[:150],actual[:150]]
        body=json.loads(case["args"][0])
        mismatches.append({"case":index,"op":body.get("op"),"model":body.get("model"),"difference":difference})
(ROOT/"mismatch-summary.json").write_text(json.dumps(mismatches,indent=2)+"\n")
print(len(mismatches),"mismatches")
print(json.dumps(mismatches,indent=2))
