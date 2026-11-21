//! Tenant-scoped worker orchestration over the existing durable job/task protocol.
#![forbid(unsafe_code)]
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use scout_audit::Audit;
use scout_config::Settings;
use scout_domain::{
    Clock, Connectors, Guard, InstallationFence, Notifier, People, Result, RuntimeError,
};
use scout_jobs::{Job, JobQueue, MAX_ATTEMPTS, lease_seconds};
use scout_liquidity_native::{Outflow, Review};
use scout_llm::client::Llm;
use scout_platform::{Database, Tenant};
use scout_tasks::{StepOptions, TaskRecorder};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::PathBuf, sync::Arc, time::Duration};
use tokio::sync::{RwLock, watch};
fn state_error(e: impl std::fmt::Display) -> RuntimeError {
    RuntimeError::new("RuntimeError", e.to_string())
}
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64() != Some(0.),
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}
#[async_trait]
pub trait Forecaster: Send + Sync {
    async fn review(&self, context: &JobContext, outflows: &[Outflow]) -> Result<Review>;
}
/// All runtime capabilities are supplied explicitly by the composition root.
#[derive(Clone)]
pub struct JobContext {
    pub database: Database,
    pub tenant: Tenant,
    pub recorder: TaskRecorder,
    pub audit: Arc<Audit>,
    pub connectors: Arc<RwLock<Connectors>>,
    pub llm: Arc<dyn Llm>,
    pub notifier: Arc<dyn Notifier>,
    pub jobs: JobQueue,
    pub settings: Settings,
    pub hr: Arc<dyn People>,
    pub clock: Arc<dyn Clock>,
    pub guard: Arc<dyn Guard>,
    pub forecaster: Option<Arc<dyn Forecaster>>,
    pub model_path: PathBuf,
}
impl JobContext {
    pub fn now(&self) -> DateTime<Utc> {
        self.clock.now()
    }
    pub fn ceo_id(&self) -> Result<&str> {
        self.settings.string("ceo_id").map_err(state_error)
    }
}
#[derive(Debug, Clone)]
pub enum JobResult {
    Task(Value),
    Tasks(Vec<Value>),
    Value(Value),
    None,
}
impl JobResult {
    fn task_id(&self) -> Option<&str> {
        match self {
            Self::Task(task) => task["id"].as_str(),
            _ => None,
        }
    }
}
#[async_trait]
pub trait Handler: Send + Sync {
    async fn handle(&self, context: &JobContext, job: &Job) -> Result<JobResult>;
}
#[async_trait]
pub trait GiveUp: Send + Sync {
    async fn cleanup(&self, context: &JobContext, job: &Job) -> Result<()>;
}
#[derive(Clone, Default)]
pub struct Handlers {
    pub jobs: BTreeMap<String, Arc<dyn Handler>>,
    pub on_give_up: BTreeMap<String, Arc<dyn GiveUp>>,
}
#[async_trait]
pub trait Watcher: Send + Sync {
    fn name(&self) -> &str;
    async fn tick(&self) -> Result<()>;
}
#[derive(Clone)]
pub struct Watch {
    pub every: Duration,
    pub watcher: Arc<dyn Watcher>,
}
#[derive(Clone)]
pub struct Worker {
    pub context: Arc<JobContext>,
    pub handlers: Handlers,
    pub workers: i64,
    pub hostname: String,
    pub watchers: Vec<Watch>,
    pub installation: Option<Arc<dyn InstallationFence>>,
    pub heartbeat_every: Option<Duration>,
}
impl Worker {
    /// Hostname is supplied by the composition root's actual hostname lookup.
    pub fn new(
        context: Arc<JobContext>,
        handlers: Handlers,
        hostname: impl Into<String>,
        workers: i64,
    ) -> Self {
        Self {
            context,
            handlers,
            workers,
            hostname: hostname.into(),
            watchers: Vec::new(),
            installation: None,
            heartbeat_every: None,
        }
    }
    pub async fn run_one(&self, worker_id: &str) -> Result<bool> {
        for dead in self
            .context
            .jobs
            .reap_expired()
            .await
            .map_err(state_error)?
        {
            tracing::error!(job=dead.id,kind=%dead.kind,"worker stopped on final attempt");
            let error = RuntimeError::new(
                "TimeoutError",
                "the worker stopped in the middle of the job",
            );
            if self.give_up(&dead, &error).await {
                self.context
                    .jobs
                    .fail(
                        dead.id,
                        "lease expired on the last attempt: the worker stopped mid-job",
                        Some(dead.attempts),
                    )
                    .await
                    .map_err(state_error)?;
            }
        }
        let Some(job) = self
            .context
            .jobs
            .claim(&format!("{}:{worker_id}", self.hostname))
            .await
            .map_err(state_error)?
        else {
            return Ok(false);
        };
        if let Some(installation) = self
            .installation
            .as_ref()
            .filter(|_| matches!(job.kind.as_str(), "dm" | "approval" | "dm_reply_notify"))
        {
            let generation = job.payload.get("_slack_generation").filter(|v| truthy(v));
            if generation.is_some_and(|g| g != &json!(installation.generation()))
                || !installation.current().await?
            {
                self.context
                    .jobs
                    .done(job.id, None, Some(job.attempts))
                    .await
                    .map_err(state_error)?;
                return Ok(true);
            }
        }
        let (stop, receiver) = watch::channel(false);
        self.heartbeat(job.clone(), receiver);
        let result = match self.handlers.jobs.get(&job.kind) {
            Some(handler) => handler.handle(&self.context, &job).await,
            None => Err(RuntimeError::new("KeyError", format!("'{}'", job.kind))),
        };
        let _ = stop.send(true);
        match result {
            Err(error) => {
                tracing::error!(job=job.id,kind=%job.kind,error=%error.source_text(),"job failed");
                if job.attempts >= MAX_ATTEMPTS && !self.give_up(&job, &error).await {
                    return Ok(true);
                }
                self.context
                    .jobs
                    .fail(job.id, &error.source_text(), Some(job.attempts))
                    .await
                    .map_err(state_error)?;
            }
            Ok(result) => {
                if !self
                    .context
                    .jobs
                    .done(job.id, result.task_id(), Some(job.attempts))
                    .await
                    .map_err(state_error)?
                {
                    tracing::warn!(job=job.id,kind=%job.kind,"job finished after its lease was taken over");
                }
            }
        }
        Ok(true)
    }
    fn heartbeat(&self, job: Job, mut stop: watch::Receiver<bool>) {
        let jobs = self.context.jobs.clone();
        let every = self
            .heartbeat_every
            .unwrap_or_else(|| Duration::from_secs_f64(lease_seconds(&job.kind) as f64 / 5.));
        tokio::spawn(async move {
            loop {
                tokio::select! {_=stop.changed()=>{if *stop.borrow()||stop.has_changed().is_err(){break}},_=tokio::time::sleep(every)=>{match jobs.renew(job.id,job.attempts).await{Ok(true)=>{},Ok(false)=>{tracing::warn!(job=job.id,"job lease was taken over");break},Err(error)=>tracing::error!(job=job.id,%error,"could not renew job lease")}}}
            }
        });
    }
    pub async fn task_of(&self, job: &Job) -> Result<Option<String>> {
        if let Some(id) = &job.task_id {
            return Ok(Some(id.clone()));
        }
        if let Some(id) = job
            .payload
            .get("task_id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
        {
            return Ok(Some(id.into()));
        }
        if job.kind == "dm_reply_notify" {
            let conn = self
                .context
                .database
                .scoped(&self.context.tenant)
                .await
                .map_err(state_error)?;
            let reply = job.payload["reply_id"].as_str();
            return Ok(conn.query_opt("SELECT r.task_id FROM dm_replies p JOIN dm_requests r ON r.id=p.request_id WHERE p.id=$1",&[&reply]).await.map_err(state_error)?.map(|r|r.get("task_id")));
        }
        if job.kind == "strategy_review" {
            return Ok(self
                .context
                .recorder
                .by_key(&job.dedupe_key)
                .await
                .map_err(state_error)?
                .and_then(|v| v["id"].as_str().map(str::to_string)));
        }
        if job.kind == "bank_txn" {
            let id = job.payload["txn"]["id"]
                .as_str()
                .ok_or_else(|| RuntimeError::new("KeyError", "'id'"))?;
            return Ok(self
                .context
                .recorder
                .by_key(&format!("txn:{id}"))
                .await
                .map_err(state_error)?
                .and_then(|v| v["id"].as_str().map(str::to_string)));
        }
        for key in [
            format!("dm:{}", job.dedupe_key),
            format!("whatif:{}", job.dedupe_key),
        ] {
            if let Some(task) = self
                .context
                .recorder
                .by_key(&key)
                .await
                .map_err(state_error)?
            {
                return Ok(task["id"].as_str().map(str::to_string));
            }
        }
        Ok(None)
    }
    async fn give_up(&self, job: &Job, error: &RuntimeError) -> bool {
        let result:Result<()>=async{if let Some(cleanup)=self.handlers.on_give_up.get(&job.kind){cleanup.cleanup(&self.context,job).await?}if let Some(task)=self.task_of(job).await?{self.context.recorder.step(&task,"error",&format!("Something went wrong: {}. A person needs to look at this.",error.legacy_error_name),&StepOptions{detail:json!({"error":error.message.chars().take(500).collect::<String>()}),..Default::default()}).await.map_err(state_error)?;let fields=json!({"status":"escalated","owner_id":self.context.ceo_id()?}).as_object().cloned().ok_or_else(||state_error("task fields"))?;self.context.recorder.update(&task,&fields).await.map_err(state_error)?;}if let Some(reference)=job.payload["status_ref"].as_str().filter(|s|!s.is_empty()){self.context.notifier.update(reference,"Sorry, something went wrong on my side. I've flagged it for a person.","NOTIFY").await?;}Ok(())}.await;
        if let Err(error) = result {
            tracing::error!(job=job.id,error=%error.source_text(),"could not record final job failure");
            false
        } else {
            true
        }
    }
    async fn job_loop(self, worker: i64, mut stop: watch::Receiver<bool>) {
        while !*stop.borrow() {
            let wait = match self.run_one(&worker.to_string()).await {
                Ok(true) => continue,
                Ok(false) => Duration::from_millis(500),
                Err(error) => {
                    tracing::error!(worker,error=%error.source_text(),"worker loop failed");
                    Duration::from_secs(2)
                }
            };
            tokio::select! {_=tokio::time::sleep(wait)=>{},_=stop.changed()=>{if stop.has_changed().is_err(){break}}}
        }
    }
    async fn watch_loop(self, mut stop: watch::Receiver<bool>) {
        let mut last = vec![None; self.watchers.len()];
        while !*stop.borrow() {
            let now = tokio::time::Instant::now();
            for (i, watch) in self.watchers.iter().enumerate() {
                if last[i].is_none_or(|previous| now.duration_since(previous) >= watch.every) {
                    last[i] = Some(now);
                    if let Err(error) = watch.watcher.tick().await {
                        tracing::error!(watcher=watch.watcher.name(),error=%error.source_text(),"watcher failed");
                    }
                }
            }
            tokio::select! {_=tokio::time::sleep(Duration::from_millis(250))=>{},_=stop.changed()=>{if stop.has_changed().is_err(){break}}}
        }
    }
    pub async fn run(&self, mut stop: watch::Receiver<bool>) {
        let mut tasks = Vec::new();
        for i in 0..self.workers.max(0) {
            tasks.push(tokio::spawn(self.clone().job_loop(i, stop.clone())))
        }
        tasks.push(tokio::spawn(self.clone().watch_loop(stop.clone())));
        while !*stop.borrow() {
            if stop.changed().await.is_err() {
                break;
            }
        }
        for task in tasks {
            let _ = tokio::time::timeout(Duration::from_secs(5), task).await;
        }
    }
}
/// Source worker configuration helper; duplicate keys keep their original insertion position.
pub fn parse_user_map(raw: &str) -> scout_domain::EmployeeMap {
    let mut out = scout_domain::EmployeeMap::new();
    for part in raw.split(',') {
        if let Some((employee, user)) = part.trim().split_once('=') {
            let (employee, user) = (employee.trim(), user.trim());
            if !employee.is_empty() && !user.is_empty() {
                out.insert(employee.into(), user.into());
            }
        }
    }
    out
}
pub fn should_run_primary(tenant: Option<&Tenant>, app_token: &str) -> bool {
    tenant.is_some_and(Tenant::legacy) || app_token.is_empty()
}
