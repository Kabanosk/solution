"""Deterministic original cost-metric source characterization; no database writes."""
import hashlib,itertools,json,sys
from pathlib import Path
import provenance
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'backend'))
from cfo.analysis.cost_metrics import monthly_series,to_api_series
from cfo.domain import CostMetric,Invoice,InvoiceLine
cases=[]
for kind,oldcost,newcost,oldvalue,newvalue,gap in itertools.product(['food_cost','ads_cac','cloud_per_order'],['0','100'],['0','119.999','120','130.005'],['0','100'],['0','109.999','110'],[False,True]):
 rows=[{'id':'previous','kind':kind,'month':'2025-01-01','team_id':None,'cost':oldcost,'metric_value':oldvalue,'detail':{'by_supplier':{'A':'40','B':'20','C':'20','D':'20'}}},{'id':'current','kind':kind,'month':'2025-03-01'if gap else'2025-02-01','team_id':'custom','cost':newcost,'metric_value':newvalue,'detail':{'lines':[{'service':'A','cost':'50'},{'name':'B','amount':'30'},{'description':'C','amount':'30'},{'service':'D','cost':'30'},{'service':'A','cost':'1.5'}]}}]
 out=[s.model_dump(mode='json')for s in to_api_series(monthly_series([CostMetric(**v)for v in rows]))]
 cases.append({'metrics':rows,'invoices':[],'ok':{'stub':False,'items':out}})
rows=[{'id':'previous','kind':'ads_cac','month':'2025-01-01','cost':'50.005','metric_value':'9.99','detail':{'lines':[{'service':'ignored','cost':'oops'}],'invoice_number':'INV'}},{'id':'current','kind':'ads_cac','month':'2025-02-01','cost':'100.005','metric_value':'9.99','detail':{'invoice_number':'INV'}}]
inv={'id':'inv','number':'INV','supplier':'Vendor','issue_date':'2025-02-01','total':'100','lines':[{'description':'One','quantity':'1','unit_price':'25.005','amount':'25.005'},{'description':'One','quantity':'1','unit_price':'25.005','amount':'25.005'},{'description':'Two','quantity':'1','unit_price':'49.99','amount':'49.99'}]}
out=[s.model_dump(mode='json')for s in to_api_series(monthly_series([CostMetric(**v)for v in rows],[Invoice(**inv)]))];cases.append({'metrics':rows,'invoices':[inv],'ok':{'stub':False,'items':out}})
for name in [0,False,True,["A","B"],{"key":"value"},19]:
 rows=[{"id":"previous","kind":"food_cost","month":"2025-01-01","cost":"0","metric_value":"0"},{"id":"current","kind":"food_cost","month":"2025-02-01","cost":"100","metric_value":"0","detail":{"lines":[{"service":name,"description":"Fallback","cost":"100"}]}}]
 out=[s.model_dump(mode="json")for s in to_api_series(monthly_series([CostMetric(**v)for v in rows]))]
 cases.append({"metrics":rows,"invoices":[],"ok":{"stub":False,"items":out}})
(root/'rust/finance-api/cost-metrics-cases.jsonl').write_text(''.join(json.dumps(c,separators=(',',':'))+'\n'for c in cases))
print(len(cases),'pure original cost-metric cases')
