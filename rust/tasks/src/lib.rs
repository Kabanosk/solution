//! Durable agent work and append-only activity, preserving Scout's PostgreSQL protocol.
use anyhow::{Context, Result, bail};
use async_trait::async_trait;
use scout_api::row_json;
use scout_platform::{Database, Tenant};
use serde_json::{Map, Value, json};
use std::sync::{Arc, Mutex};
use tokio_postgres::{Transaction, types::ToSql};

const TASK_FIELDS: &[&str] = &[
    "owner_id",
    "requested_by",
    "category",
    "amount",
    "currency",
    "counterparty",
    "transaction_id",
    "slack_ref",
    "approval_id",
    "level",
    "simulated",
    "status",
    "title",
];

#[async_trait]
pub trait TaskListener: Send + Sync {
    async fn recorded(&self, task: &Value, step: &Value) -> Result<()>;
}

#[derive(Clone)]
pub struct TaskRecorder {
    db: Database,
    tenant: Tenant,
    listeners: Arc<Mutex<Vec<Arc<dyn TaskListener>>>>,
}

fn check_fields(fields: &Map<String, Value>, updating: bool) -> Result<()> {
    let mut unknown: Vec<_> = fields
        .keys()
        .filter(|field| {
            !TASK_FIELDS.contains(&field.as_str())
                && !(updating && ["outcome", "resolved_via", "evidence"].contains(&field.as_str()))
        })
        .collect();
    unknown.sort();
    if !unknown.is_empty() {
        let names = unknown
            .iter()
            .map(|name| format!("'{}'", name.replace('\\', "\\\\").replace('\'', "\\'")))
            .collect::<Vec<_>>()
            .join(", ");
        bail!("unknown task fields: [{names}]");
    }
    Ok(())
}

fn parameter(index: usize, field: &str) -> String {
    if field == "evidence" {
        return format!("${index}::jsonb");
    }
    let cast = match field {
        "amount" => "numeric",
        "simulated" => "boolean",
        _ => "text",
    };
    format!("(${index}::jsonb #>> '{{}}')::{cast}")
}

async fn notify(tx: &Transaction<'_>, task_id: &str) -> Result<()> {
    tx.query_one(
        "SELECT pg_notify('scout_tasks_' || current_schema(),$1)",
        &[&task_id],
    )
    .await?;
    Ok(())
}

impl TaskRecorder {
    pub fn new(db: Database, tenant: Tenant) -> Self {
        Self {
            db,
            tenant,
            listeners: Arc::new(Mutex::new(Vec::new())),
        }
    }
    pub fn add_listener(&self, listener: Arc<dyn TaskListener>) {
        self.listeners
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(listener);
    }
    pub async fn open(
        &self,
        dedupe_key: &str,
        agent_loop: &str,
        title: &str,
        fields: &Map<String, Value>,
    ) -> Result<Value> {
        check_fields(fields, false)?;
        let mut conn = self.db.scoped(&self.tenant).await?;
        let tx = conn.transaction().await?;
        // A continuation must not consume another task number.
        let mut row = tx
            .query_opt(
                "SELECT * FROM agent_tasks WHERE dedupe_key=$1",
                &[&dedupe_key],
            )
            .await?;
        if row.is_none() {
            let mut columns = vec!["dedupe_key", "loop", "title"];
            columns.extend(fields.keys().map(String::as_str));
            let mut values = vec![json!(dedupe_key), json!(agent_loop), json!(title)];
            values.extend(fields.values().cloned());
            let parameters = columns
                .iter()
                .enumerate()
                .map(|(i, field)| parameter(i + 1, field))
                .collect::<Vec<_>>()
                .join(",");
            let sql = format!(
                "INSERT INTO agent_tasks ({}) VALUES ({parameters}) ON CONFLICT (dedupe_key) DO NOTHING RETURNING *",
                columns.join(",")
            );
            let binds = values
                .iter()
                .map(|value| value as &(dyn ToSql + Sync))
                .collect::<Vec<_>>();
            row = tx.query_opt(&sql, &binds).await?;
            if row.is_none() {
                row = tx
                    .query_opt(
                        "SELECT * FROM agent_tasks WHERE dedupe_key=$1",
                        &[&dedupe_key],
                    )
                    .await?;
            }
            let created = row.as_ref().context("task continuation vanished")?;
            notify(&tx, created.try_get::<_, &str>("id")?).await?;
        }
        let result = row_json(row.as_ref().context("task does not exist")?)?;
        tx.commit().await?;
        Ok(result)
    }
    pub async fn get(&self, id: &str) -> Result<Value> {
        let conn = self.db.scoped(&self.tenant).await?;
        let row = conn
            .query_opt("SELECT * FROM agent_tasks WHERE id=$1", &[&id])
            .await?
            .context("task does not exist")?;
        Ok(row_json(&row)?)
    }
    pub async fn by_key(&self, key: &str) -> Result<Option<Value>> {
        let conn = self.db.scoped(&self.tenant).await?;
        conn.query_opt("SELECT * FROM agent_tasks WHERE dedupe_key=$1", &[&key])
            .await?
            .as_ref()
            .map(row_json)
            .transpose()
            .map_err(Into::into)
    }
    pub async fn find_open(
        &self,
        owner_id: Option<&str>,
        statuses: &[String],
        slack_ref: Option<&str>,
    ) -> Result<Vec<Value>> {
        let conn = self.db.scoped(&self.tenant).await?;
        let owner = owner_id.filter(|value| !value.is_empty());
        let slack = slack_ref.filter(|value| !value.is_empty());
        let rows=conn.query("SELECT * FROM agent_tasks WHERE status=ANY($1) AND ($2::text IS NULL OR owner_id=$2) AND ($3::text IS NULL OR slack_ref=$3) ORDER BY updated_at DESC", &[&statuses,&owner,&slack]).await?;
        rows.iter()
            .map(|row| row_json(row).map_err(Into::into))
            .collect()
    }
    pub async fn update(&self, id: &str, fields: &Map<String, Value>) -> Result<Value> {
        check_fields(fields, true)?;
        let mut conn = self.db.scoped(&self.tenant).await?;
        let tx = conn.transaction().await?;
        let mut sets = fields
            .keys()
            .enumerate()
            .map(|(i, field)| format!("{field}={}", parameter(i + 1, field)))
            .collect::<Vec<_>>();
        sets.push("updated_at=now()".into());
        if fields
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|status| matches!(status, "done" | "cancelled"))
        {
            sets.push("closed_at=coalesce(closed_at,now())".into());
        }
        let mut values = fields.values().cloned().collect::<Vec<_>>();
        values.push(json!(id));
        let sql = format!(
            "UPDATE agent_tasks SET {} WHERE id={} RETURNING *",
            sets.join(","),
            parameter(values.len(), "id")
        );
        let binds = values
            .iter()
            .map(|value| value as &(dyn ToSql + Sync))
            .collect::<Vec<_>>();
        let row = tx.query_opt(&sql, &binds).await?;
        notify(&tx, id).await?;
        tx.commit().await?;
        Ok(row_json(row.as_ref().context("task does not exist")?)?)
    }
    pub async fn add_evidence(&self, id: &str, item: Value) -> Result<()> {
        let conn = self.db.scoped(&self.tenant).await?;
        conn.execute(
            "UPDATE agent_tasks SET evidence=evidence || $1::jsonb,updated_at=now() WHERE id=$2",
            &[&json!([item]), &id],
        )
        .await?;
        Ok(())
    }
    pub async fn set_status(
        &self,
        id: &str,
        status: &str,
        options: &ResolutionOptions,
    ) -> Result<Value> {
        let mut fields = Map::new();
        fields.insert("status".into(), json!(status));
        for (name, value) in [
            ("outcome", &options.outcome),
            ("resolved_via", &options.resolved_via),
            ("level", &options.level),
            ("owner_id", &options.owner_id),
        ] {
            if let Some(value) = value {
                fields.insert(name.into(), json!(value));
            }
        }
        self.update(id, &fields).await
    }
    pub async fn step(
        &self,
        id: &str,
        kind: &str,
        text: &str,
        options: &StepOptions,
    ) -> Result<Value> {
        let mut conn = self.db.scoped(&self.tenant).await?;
        let tx = conn.transaction().await?;
        let row=tx.query_one("INSERT INTO agent_task_steps(task_id,kind,source,result_count,text,detail,evidence) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
            &[&id,&kind,&options.source,&options.result_count,&text,&options.detail,&options.evidence]).await?;
        let task = tx
            .query_one(
                "UPDATE agent_tasks SET updated_at=now() WHERE id=$1 RETURNING *",
                &[&id],
            )
            .await?;
        notify(&tx, id).await?;
        tx.commit().await?;
        let step = row_json(&row)?;
        let task = row_json(&task)?;
        if let Some(evidence) = options
            .evidence
            .as_ref()
            .filter(|value| value.as_object().is_some_and(|value| !value.is_empty()))
        {
            self.add_evidence(id, evidence.clone()).await?;
        }
        let listeners = self
            .listeners
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        for listener in listeners {
            if listener.recorded(&task, &step).await.is_err() {
                tracing::error!("task listener failed");
            }
        }
        Ok(step)
    }
    pub async fn steps(&self, id: &str) -> Result<Vec<Value>> {
        let conn = self.db.scoped(&self.tenant).await?;
        let rows = conn
            .query(
                "SELECT * FROM agent_task_steps WHERE task_id=$1 ORDER BY id",
                &[&id],
            )
            .await?;
        rows.iter()
            .map(|row| row_json(row).map_err(Into::into))
            .collect()
    }
}

#[derive(Debug, Clone, Default)]
pub struct ResolutionOptions {
    pub outcome: Option<String>,
    pub resolved_via: Option<String>,
    pub level: Option<String>,
    pub owner_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct StepOptions {
    pub source: Option<String>,
    pub result_count: Option<i32>,
    pub detail: Value,
    pub evidence: Option<Value>,
}
impl Default for StepOptions {
    fn default() -> Self {
        Self {
            source: None,
            result_count: None,
            detail: json!({}),
            evidence: None,
        }
    }
}
