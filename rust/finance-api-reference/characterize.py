"""Original financial read handlers; isolated local schema, explicit model fixture, no credentials."""
import datetime as dt
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
import provenance

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
import psycopg
from psycopg.rows import dict_row
from psycopg import sql
from cfo.api import services
from cfo.api.settings import get_api_settings
from cfo.db.repo import Repository
from cfo.tenancy import Tenant,bind_tenant
from cfo.config import get_settings

URL='postgresql://scout_migration:scout_migration@127.0.0.1:55433/scout_migration'
OID=str(uuid.uuid5(uuid.NAMESPACE_DNS,'scout-rust-finance-fixture'))
SCHEMA='tenant_'+uuid.UUID(OID).hex
BASE=Path('/tmp/scout-finance-api-fixture/model.json')
MODEL=BASE.parent/'tenants'/OID/BASE.name
os.environ['FORECAST_MODEL_PATH']=str(BASE)
os.environ.pop('MLFLOW_TRACKING_URI',None)
os.environ.pop('SAFETY_BUFFER_EUR',None)
get_settings.cache_clear();get_api_settings.cache_clear()

def setup():
    MODEL.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'rust/forecast-model/fixtures/components.json',MODEL)
    shutil.copyfile(ROOT/'rust/forecast-model/fixtures/components.json.meta.json',str(MODEL)+'.meta.json')
    os.utime(MODEL,ns=(1793520000000000000,1793520000000000000))
    with psycopg.connect(URL,autocommit=True)as c:
        c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(SCHEMA)))
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(SCHEMA)))
    with psycopg.connect(URL,options='-csearch_path='+SCHEMA)as c:
        c.execute((ROOT/'backend/cfo/db/schema.sql').read_text());c.execute((ROOT/'backend/scout/schema.sql').read_text())
        c.execute("INSERT INTO teams(id,name,owner_id,monthly_budget) VALUES('kitchen','Kitchen','owner',12000),('marketing','Marketing','marketer',2000),('it','IT',NULL,1500),('empty','Empty',NULL,NULL)")
        rows=[]
        for i in range(350):
            day=dt.date(2025,1,1)+dt.timedelta(days=i)
            if not 150<=i<167:
                rows.extend([(f'rev-{i}',day,'transfer','Revenue','Customer',round(220+i/10,2),'revenue','kitchen',None,False),(f'food-{i}',day,'transfer','Food','Supplier',-round(20+i/100,2),'food','kitchen','MISSING' if i%29==0 else None,False)])
            if day.day==1:rows.append((f'rent-{i}',day,'transfer','Rent','Landlord',-1000,'rent',None,None,False))
            if day.day==28:rows.append((f'payroll-{i}',day,'transfer','Payroll','Staff',-2000,'payroll','kitchen',None,False))
        with c.cursor()as cur:cur.executemany('INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,category,team_id,invoice_number,documented) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',rows)
        c.execute("INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,category,team_id) VALUES('refund','2025-02-01','transfer','Refund','Supplier',35.01,'food','kitchen'),('unknown','2025-03-01','transfer','Other','Supplier',-11.11,NULL,NULL)")
        c.execute("INSERT INTO invoices(id,number,supplier,issue_date,total,category,team_id) VALUES('inv1','ADS-FEB','Ads','2025-02-01',1300,'ads','marketing'),('inv2','UNLINKED','Cloud','2025-11-09',99.99,'cloud','it')")
        c.execute("INSERT INTO invoice_lines(invoice_id,position,description,unit_price,amount) VALUES('inv1',0,'Search',800,800),('inv1',1,'Display',500,500)")
        metrics=[]
        for kind,team in [('food_cost','kitchen'),('ads_cac','marketing'),('cloud_per_order','it')]:
            for month,cost,value,detail in [(1,1000,100,{'by_supplier':{'A':600,'B':400}}),(2,1300,100,{'lines':[{'service':'A','cost':'800'},{'description':'B','amount':'500'}]}),(3,0,0,{}),(4,500,0,{'invoice_number':'ADS-FEB'}),(5,600,5,{})]:
                metrics.append((kind+str(month),kind,dt.date(2025,month,1),team,cost,value,json.dumps(detail)))
        with c.cursor()as cur:cur.executemany('INSERT INTO cost_metrics(id,kind,month,team_id,cost,metric_value,detail) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',metrics)
        c.execute("INSERT INTO agent_tasks(id,dedupe_key,loop,title,status) VALUES('SC-FA1','finance1','invoicing','Wait','waiting'),('SC-FA2','finance2','strategy','Escalated','escalated')")
        c.execute("INSERT INTO approvals(id,task_id,approver_id,question) VALUES('finance-approval','SC-FA1','owner','Approve?')")

def capture():
    result=[]
    tenant=Tenant(OID,'finance-fixture',SCHEMA,'owner','owner',False)
    calls=[('kpis',{}),('cost_metrics',{}),('cost_metrics',{'kind':'food_cost'}),('cost_metrics',{'kind':'ads_cac'}),('cost_metrics',{'kind':'cloud_per_order'}),('forecast',{'horizon_days':7,'granularity':'week'}),('forecast',{'horizon_days':8,'granularity':'week'}),('forecast',{'horizon_days':90,'granularity':'week'}),('forecast',{'horizon_days':365,'granularity':'week'}),('forecast',{'horizon_days':7,'granularity':'day'}),('costs',{}),('costs',{'start':'2025-02-15','end':'2025-06-01'}),('costs',{'start':'2025-11-01','end':'2025-11-30'}),('costs',{'start':'2020-01-01','end':'2030-01-01'}),('costs',{'start':'2026-01-01'})]
    with bind_tenant(tenant),psycopg.connect(URL,options='-csearch_path='+SCHEMA,row_factory=dict_row)as conn:
        conn.execute('SET TRANSACTION READ ONLY');repo=Repository(conn)
        for buffer in [None,'1234.567']:
            if buffer is None:os.environ.pop('SAFETY_BUFFER_EUR',None)
            else:os.environ['SAFETY_BUFFER_EUR']=buffer
            get_api_settings.cache_clear()
            for op,args in calls:
                case={'op':op,'args':args,'buffer':buffer}
                try:
                    if op=='kpis':out=services.kpis(repo)
                    elif op=='cost_metrics':out={'stub':False,'items':[s.model_dump(mode='json')for s in services.cost_metric_series(repo,args.get('kind'))]}
                    elif op=='forecast':out=services.liquidity_forecast(repo,**args)
                    else:out=services.costs_by_team(repo,dt.date.fromisoformat(args['start'])if args.get('start')else None,dt.date.fromisoformat(args['end'])if args.get('end')else None)
                    case['ok']=out.model_dump(mode='json',by_alias=True)if hasattr(out,'model_dump')else out
                except services.ApiError as exc:case['error']={'status':exc.status_code,'code':exc.code,'message':exc.message}
                result.append(case)
    (ROOT/'rust/finance-api/characterization.jsonl').write_text(''.join(json.dumps(c,separators=(',',':'))+'\n'for c in result))
    (ROOT/'rust/finance-api/fixture.json').write_text(json.dumps({'id':OID,'schema':SCHEMA,'model_path':str(BASE),'url':URL},indent=2)+'\n')
    print(len(result),'original financial handler cases')

if __name__=='__main__':
    if '--setup' in sys.argv:setup()
    capture()
