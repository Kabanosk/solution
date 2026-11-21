//! Development-only characterization executable. All effects are captured locally.
use async_trait::async_trait;
use chrono::NaiveDate;
use scout_domain::*;
use scout_guardrails::{client::CheckClient,facade::clear_switch_cache,*};
use scout_platform::{Database,Tenant};
use serde_json::{Value,json};
use std::{collections::VecDeque,io::{self,Read},sync::{Arc,Mutex}};
#[derive(Clone)]
struct Fixture{events:Arc<Mutex<Vec<Value>>>,requests:Arc<Mutex<Vec<Value>>>,responses:Arc<Mutex<VecDeque<Verdict>>>,enabled:bool,delivery:Option<String>,upload:Option<String>}
#[async_trait]
impl CheckClient for Fixture{
fn enabled(&self)->bool{self.enabled}
async fn check(&self,config:&str,messages:&[Value])->Verdict{self.requests.lock().unwrap().push(json!({"config":config,"messages":messages}));self.responses.lock().unwrap().pop_front().unwrap_or_else(||verdict("success"))}
}
#[async_trait]
impl AuditSink for Fixture{async fn record(&self,value:&AuditRecord)->Result<()>{self.events.lock().unwrap().push(json!({"kind":"audit","actor":value.actor,"action":value.action,"target":value.target,"resource":value.resource,"decision":value.decision,"task_id":value.task_id,"requester":value.requester,"role":value.role,"policy":value.policy,"reason":value.reason}));Ok(())}}
#[async_trait]
impl Notifier for Fixture{
async fn dm(&self,employee_id:&str,text:&str,level:&str,options:&DmOptions)->Result<Option<String>>{self.events.lock().unwrap().push(json!({"kind":"dm","employee_id":employee_id,"text":text,"level":level,"task_id":options.task_id}));Ok(self.delivery.clone())}
async fn upload(&self,employee_id:&str,files:&[(String,Vec<u8>)],comment:&str)->Result<Option<String>>{self.events.lock().unwrap().push(json!({"kind":"upload","employee_id":employee_id,"files":files.iter().map(|(name,content)|json!([name,String::from_utf8_lossy(content)])).collect::<Vec<_>>(),"comment":comment}));Ok(self.upload.clone())}
async fn update(&self,_:&str,_:&str,_:&str)->Result<()>{Ok(())}
async fn track(&self,_:&str,_:&str)->Result<()>{Ok(())}
}
#[async_trait]
impl People for Fixture{
async fn employee(&self,id:&str)->Result<Option<Value>>{Ok(["owner","person","compliance"].contains(&id).then(||json!({"id":id})))}
async fn by_slack_id(&self,id:&str)->Result<Option<Value>>{Ok((id=="UCOMPLIANCE").then(||json!({"id":"compliance"})))}
async fn name(&self,id:Option<&str>)->Result<String>{Ok(if id==Some("person"){"Person Name"}else{id.unwrap_or("")}.into())}
async fn all(&self)->Result<Vec<Value>>{Ok(vec![])}
async fn full_name(&self,id:&str)->Result<String>{self.name(Some(id)).await}
async fn role(&self,_:&str)->Result<String>{Ok("staff".into())}
async fn on_vacation(&self,_:&str,_:NaiveDate)->Result<bool>{Ok(false)}
async fn manager(&self,_:&str)->Result<Option<String>>{Ok(None)}
async fn chain(&self,_:&str)->Result<Vec<String>>{Ok(vec![])}
async fn first_available(&self,id:&str,_:NaiveDate)->Result<(String,Vec<String>)>{Ok((id.into(),vec![]))}
}
async fn run(data:Value)->anyhow::Result<Value>{
let tenant=&data["tenant"];
let tenant=Tenant::new(tenant["id"].as_str().unwrap_or(""),tenant["slug"].as_str().unwrap_or(""),tenant["schema_name"].as_str().unwrap_or(""),tenant["employee_id"].as_str().map(str::to_owned),tenant["role"].as_str().unwrap_or(""),false)?;
let fixture=Arc::new(Fixture{events:Default::default(),requests:Default::default(),responses:Arc::new(Mutex::new(data["responses"].as_array().into_iter().flatten().map(|value|{let mut defaults=json!({"status":"success","activated_rails":[],"latency":0.0,"detail":""});defaults.as_object_mut().unwrap().extend(value.as_object().unwrap().clone());serde_json::from_value(defaults)}).collect::<std::result::Result<VecDeque<_>,_>>()?)),enabled:data["enabled"].as_bool().unwrap_or(true),delivery:if data.get("delivery").is_none(){Some("ref".into())}else{data["delivery"].as_str().map(str::to_owned)},upload:if data.get("upload").is_none(){Some("file-ref".into())}else{data["upload"].as_str().map(str::to_owned)}});
let guard=Guardrails{database:Database::new(data["database_url"].as_str().unwrap_or("")),tenant,client:fixture.clone(),audit:fixture.clone(),notifier:fixture.clone(),people:fixture.clone(),settings:GuardSettings{ceo_id:"owner".into(),compliance_contact:data["contact"].as_str().unwrap_or("").into(),dashboard_url:"https://scout.example.invalid".into()}};
clear_switch_cache(None);
let mut values=Vec::new();for call in data["calls"].as_array().into_iter().flatten(){let options=GuardOptions{task_id:call["task_id"].as_str().map(str::to_owned),requester:call["requester"].as_str().map(str::to_owned),source:call["source"].as_str().unwrap_or("").into(),role:call["role"].as_str().map(str::to_owned)};let text=call["text"].as_str().unwrap_or("");let value=match call["op"].as_str().unwrap_or(""){
"check"=>{let texts=match &call["texts"]{Value::String(value)=>vec![value.clone()],Value::Array(values)=>values.iter().map(|value|value.as_str().unwrap_or("").into()).collect(),_=>vec![]};json!(guard.check(call["config"].as_str().unwrap_or(USER_INPUT),&texts,&options,call["record_block"].as_bool().unwrap_or(true)).await)},
"human"=>json!(guard.screen_human(call["person"].as_str(),text,&options).await),
"tool"=>json!(guard.screen_tool(call["tool"].as_str().unwrap_or(""),text,call.get("result"),&options).await),
"document"=>json!(guard.screen_document(text,&options).await),
"output"=>json!(guard.screen_output(text,&options).await),
"history"=>json!(guard.screen_history(call["turns"].as_array().map(Vec::as_slice).unwrap_or(&[]),&options).await),
"alert"=>{guard.alert_outside(call["key"].as_str().unwrap_or(""),call["label"].as_str().unwrap_or(""),call["rails"].as_str().unwrap_or(""),options.task_id.as_deref()).await;Value::Null},
"active"=>json!(guard.active().await),
"set_switch"=>json!(guard.set_switch(call["enabled"].as_bool().unwrap_or(true),"owner","dashboard",Some("owner")).await?),
_=>anyhow::bail!("invalid operation")};values.push(value);}
Ok(json!({"values":values,"events":*fixture.events.lock().unwrap(),"requests":*fixture.requests.lock().unwrap()}))}
#[tokio::main(flavor="current_thread")]
async fn main(){let mut text=String::new();io::stdin().read_to_string(&mut text).unwrap();let data=serde_json::from_str::<Value>(&text).unwrap();println!("{}",run(data).await.expect("local fixture"));}
