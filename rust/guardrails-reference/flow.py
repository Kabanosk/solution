"""Scoped guardrail effects against a disposable schema in a dedicated local DB."""
import dataclasses
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid5,NAMESPACE_DNS
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
logging.disable(logging.CRITICAL)
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from scout import guardrails as g
from cfo.tenancy import Tenant,bind_tenant

def main(data):
    url=os.environ['SCOUT_RUST_TEST_DATABASE_URL']
    parsed=urlparse(url)
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.path!='/scout_migration':raise ValueError('requires isolated local database')
    # rust-anything runs independent cases concurrently; serialize this owned fixture.
    fixture_lock=psycopg.connect(url,autocommit=True)
    fixture_lock.execute("SELECT pg_advisory_lock(hashtext('scout-guardrails-migration-fixture'))")
    oid=uuid5(NAMESPACE_DNS,'scout-guardrails-migration-fixture')
    schema='tenant_'+oid.hex
    data['database_url']=url
    data['tenant']={'id':str(oid),'slug':'guardrails-fixture','schema_name':schema,'employee_id':'owner','role':'owner','legacy':False}
    with psycopg.connect(url) as connection:
        connection.execute((ROOT/'backend/cfo/db/auth.sql').read_text())
        connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE;CREATE SCHEMA {};SET LOCAL search_path TO {}').format(*[sql.Identifier(schema)]*3))
        connection.execute((ROOT/'backend/cfo/db/schema.sql').read_text())
        connection.execute((ROOT/'backend/scout/schema.sql').read_text())
        connection.execute("ALTER TABLE guardrail_strikes ALTER COLUMN ts SET DEFAULT date_trunc('day',now())")
        connection.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES(%s,'guardrails-fixture','Guardrails fixture',%s) ON CONFLICT(id) DO NOTHING",(oid,schema))
        connection.execute("DELETE FROM scout_auth.guardrails_settings WHERE organization_id=%s",(oid,))
        if 'switch' in data:connection.execute('INSERT INTO scout_auth.guardrails_settings(organization_id,enabled) VALUES(%s,%s)',(oid,data['switch']))
    def connect():return psycopg.connect(url,options='-csearch_path='+schema,row_factory=dict_row)
    if '--native' in sys.argv:
        binary=Path(sys.argv[sys.argv.index('--native')+1]).resolve()
        process=subprocess.run([str(binary)],input=json.dumps(data),text=True,capture_output=True,check=True)
        result=json.loads(process.stdout)
    else:
        events=[];responses=list(data.get('responses',[]));requests=[]
        class Client:
            enabled=data.get('enabled',True)
            def check(self,config,messages):
                requests.append({'config':config,'messages':messages})
                response=responses.pop(0) if responses else {'status':'success'}
                return g.Verdict(**response)
        class Audit:
            def record(self,actor,action,target,resource,decision,**options):events.append({'kind':'audit','actor':actor,'action':action,'target':target,'resource':resource,'decision':decision,**{key:options.get(key) for key in ['task_id','requester','role','policy','reason']}})
        class People:
            def employee(self,person):return {'id':person} if person in ['owner','person','compliance'] else None
            def by_slack_id(self,person):return {'id':'compliance'} if person=='UCOMPLIANCE' else None
            def name(self,person):return 'Person Name' if person=='person' else person
        class Notifier:
            def dm(self,person,text,level,**options):
                events.append({'kind':'dm','employee_id':person,'text':text,'level':level,'task_id':options.get('task_id')})
                return data.get('delivery','ref')
            def upload(self,person,files,comment):
                events.append({'kind':'upload','employee_id':person,'files':[[name,content.decode()] for name,content in files],'comment':comment})
                return data.get('upload','file-ref')
        settings=SimpleNamespace(ceo_id='owner',compliance_contact=data.get('contact',''),dashboard_url='https://scout.example.invalid')
        ctx=SimpleNamespace(conn_factory=connect,guard=Client(),audit=Audit(),hr=People(),notifier=Notifier(),settings=settings)
        values=[]
        g.clear_switch_cache()
        with bind_tenant(Tenant(**data['tenant'])):
            for call in data.get('calls',[]):
                op=call['op'];options={key:call[key] for key in ['task_id','requester','role','source'] if key in call}
                if op=='check':value=g.check(ctx,call.get('config','user-input'),call.get('texts',[]),record_block=call.get('record_block',True),**{key:value for key,value in options.items() if key!='role'})
                elif op=='human':value=g.screen_human(ctx,call.get('person'),call.get('text',''),**{key:value for key,value in options.items() if key in ['role','task_id']})
                elif op=='tool':value=g.screen_tool(ctx,call.get('task_id'),call['tool'],call['text'],requester=call.get('requester'),result=call.get('result'))
                elif op=='document':value=g.screen_document(ctx,call.get('task_id'),call['text'],call.get('source',''))
                elif op=='output':value=g.screen_output(ctx,call.get('task_id'),call['text'],**{key:value for key,value in options.items() if key!='task_id'})
                elif op=='history':value=g.screen_history(ctx,call['turns'],task_id=call.get('task_id'),requester=call.get('requester'))
                elif op=='alert':value=g.alert_outside(ctx,call['key'],call['label'],call['rails'],call.get('task_id'))
                elif op=='set_switch':
                    with connect() as connection:value=g.set_switch(connection,str(oid),call['enabled'],'owner')
                elif op=='active':value=g.active(ctx)
                else:raise ValueError('invalid op')
                if isinstance(value,g.Verdict):value=dataclasses.asdict(value)
                values.append(value)
        result={'values':values,'events':events,'requests':requests}
    with connect() as connection:
        result['strikes']=connection.execute('SELECT person,prompt,rails,reported_at IS NOT NULL AS reported FROM guardrail_strikes ORDER BY id').fetchall()
        result['alerts']=connection.execute('SELECT item_key,rails FROM guardrail_alerts ORDER BY item_key').fetchall()
        result['audit']=connection.execute('SELECT task_id,actor,requester,role,action,target,resource,decision,policy,reason FROM audit_log ORDER BY id').fetchall()
    fixture_lock.close()
    return result

if __name__=='__main__':
    try:
        data=json.load(sys.stdin)
        if not isinstance(data,dict) or 'calls' not in data:raise ValueError()
    except (ValueError,TypeError):print('{"error":"invalid_input"}');sys.exit()
    print(json.dumps(main(data),sort_keys=True,ensure_ascii=False,separators=(',',':')))
