//! Native Scout read APIs and enqueue-only approval/strategy mutations.
//! HTTP query parsing, validation envelopes and streaming belong to the application adapter.
#![forbid(unsafe_code)]
use chrono::{DateTime, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use rust_decimal::Decimal;
use scout_platform::{
    Database, PlatformError, Tenant, legacy_connectors_enabled, require_write_identity,
};
use serde_json::{Map, Value, json};
use std::{
    fmt,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};
use tokio_postgres::{Row, types::Type};
use uuid::Uuid;

pub type Result<T> = std::result::Result<T, ApiError>;
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiError {
    pub status: u16,
    pub code: String,
    pub message: String,
}
impl ApiError {
    pub fn new(status: u16, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            status,
            code: code.into(),
            message: message.into(),
        }
    }
    pub fn json(&self) -> Value {
        json!({"error":{"code":self.code,"message":self.message}})
    }
    fn internal() -> Self {
        Self::new(500, "internal_error", "Internal server error.")
    }
}
impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for ApiError {}
impl From<tokio_postgres::Error> for ApiError {
    fn from(error: tokio_postgres::Error) -> Self {
        if error.as_db_error().is_none() && !error.to_string().contains("deserializ") {
            Self::new(
                503,
                "database_unavailable",
                "The database is not reachable.",
            )
        } else {
            Self::internal()
        }
    }
}
impl From<PlatformError> for ApiError {
    fn from(error: PlatformError) -> Self {
        match error {
            PlatformError::Database(error) => error.into(),
            PlatformError::Tls(_) => Self::new(
                503,
                "database_unavailable",
                "The database is not reachable.",
            ),
            _ => Self::internal(),
        }
    }
}
impl From<scout_platform::AuthError> for ApiError {
    fn from(error: scout_platform::AuthError) -> Self {
        Self::new(error.status, error.code, error.message)
    }
}
fn utc_iso(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(
        if value.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    )
}
/// Mirrors scout.api._jsonable for the scalar column types used by existing schemas.
/// BYTEA is omitted completely; PostgreSQL numeric is never converted through f64.
pub fn row_json(row: &Row) -> Result<Value> {
    let mut object = Map::new();
    for (index, column) in row.columns().iter().enumerate() {
        let value = match *column.type_() {
            Type::BYTEA => {
                if row.try_get::<_, Option<Vec<u8>>>(index)?.is_some() {
                    continue;
                }
                Value::Null
            }
            Type::TEXT | Type::VARCHAR | Type::BPCHAR | Type::NAME | Type::UNKNOWN => {
                json!(row.try_get::<_, Option<String>>(index)?)
            }
            Type::BOOL => json!(row.try_get::<_, Option<bool>>(index)?),
            Type::INT2 => json!(row.try_get::<_, Option<i16>>(index)?),
            Type::INT4 => json!(row.try_get::<_, Option<i32>>(index)?),
            Type::INT8 => json!(row.try_get::<_, Option<i64>>(index)?),
            Type::FLOAT4 => json!(row.try_get::<_, Option<f32>>(index)?),
            Type::FLOAT8 => json!(row.try_get::<_, Option<f64>>(index)?),
            Type::NUMERIC => json!(
                row.try_get::<_, Option<Decimal>>(index)?
                    .map(|v| v.to_string())
            ),
            Type::TIMESTAMPTZ => {
                json!(row.try_get::<_, Option<DateTime<Utc>>>(index)?.map(utc_iso))
            }
            Type::TIMESTAMP => json!(
                row.try_get::<_, Option<NaiveDateTime>>(index)?
                    .map(|v| utc_iso(v.and_utc()))
            ),
            Type::DATE => json!(
                row.try_get::<_, Option<NaiveDate>>(index)?
                    .map(|v| v.to_string())
            ),
            Type::JSON | Type::JSONB => row
                .try_get::<_, Option<Value>>(index)?
                .unwrap_or(Value::Null),
            Type::UUID => json!(
                row.try_get::<_, Option<Uuid>>(index)?
                    .map(|v| v.to_string())
            ),
            _ => return Err(ApiError::internal()),
        };
        object.insert(column.name().into(), value);
    }
    Ok(Value::Object(object))
}
fn rows_json(rows: Vec<Row>) -> Result<Vec<Value>> {
    rows.iter().map(row_json).collect()
}
fn comma_filter(value: &Option<String>) -> Option<Vec<String>> {
    value
        .as_ref()
        .filter(|s| !s.is_empty())
        .map(|s| s.split(',').map(str::to_string).collect())
}

#[derive(Debug, Clone)]
pub struct TaskListOptions {
    pub status: Option<String>,
    pub r#loop: Option<String>,
    pub owner: Option<String>,
    pub q: Option<String>,
    pub limit: i64,
}
impl Default for TaskListOptions {
    fn default() -> Self {
        Self {
            status: None,
            r#loop: None,
            owner: None,
            q: None,
            limit: 50,
        }
    }
}
#[derive(Debug, Clone)]
pub struct AuditOptions {
    pub task_id: Option<String>,
    pub decision: Option<String>,
    pub limit: i64,
}
impl Default for AuditOptions {
    fn default() -> Self {
        Self {
            task_id: None,
            decision: None,
            limit: 100,
        }
    }
}

#[derive(Clone)]
pub struct ScoutApi {
    db: Database,
    tenant: Tenant,
}
impl ScoutApi {
    pub fn new(db: Database, tenant: Tenant) -> Self {
        Self { db, tenant }
    }
    fn strategy_exec(&self) -> bool {
        matches!(self.tenant.role(), "owner" | "ceo")
    }
    pub async fn list_tasks(&self, options: &TaskListOptions) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let status = comma_filter(&options.status);
        let loop_filter = comma_filter(&options.r#loop);
        let owner = comma_filter(&options.owner);
        let q = options
            .q
            .as_ref()
            .filter(|s| !s.is_empty())
            .map(|s| format!("%{s}%"));
        let visible = self.strategy_exec();
        let rows=client.query("SELECT * FROM agent_tasks WHERE ($1::bool OR loop <> 'strategy') AND ($2::text[] IS NULL OR status=ANY($2)) AND ($3::text[] IS NULL OR loop=ANY($3)) AND ($4::text[] IS NULL OR owner_id=ANY($4)) AND ($5::text IS NULL OR title ILIKE $5 OR counterparty ILIKE $5 OR id ILIKE $5 OR outcome ILIKE $5) ORDER BY updated_at DESC LIMIT $6",&[&visible,&status,&loop_filter,&owner,&q,&options.limit]).await?;
        let counts=client.query("SELECT status,count(*)::int AS n FROM agent_tasks WHERE ($1::bool OR loop <> 'strategy') GROUP BY status",&[&visible]).await?;
        let mut grouped = Map::new();
        for row in counts {
            grouped.insert(
                row.try_get::<_, String>("status")?,
                json!(row.try_get::<_, i32>("n")?),
            );
        }
        Ok(json!({"items":rows_json(rows)?,"counts":grouped}))
    }
    pub async fn get_task(&self, id: &str) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let task = client
            .query_opt("SELECT * FROM agent_tasks WHERE id=$1", &[&id])
            .await?
            .ok_or_else(|| ApiError::new(404, "not_found", format!("No task {id}.")))?;
        if task.try_get::<_, String>("loop")? == "strategy" && !self.strategy_exec() {
            return Err(ApiError::new(404, "not_found", format!("No task {id}.")));
        }
        let steps = client
            .query(
                "SELECT * FROM agent_task_steps WHERE task_id=$1 ORDER BY id",
                &[&id],
            )
            .await?;
        let audit = client
            .query(
                "SELECT * FROM audit_log WHERE task_id=$1 ORDER BY id",
                &[&id],
            )
            .await?;
        let approval = client
            .query_opt(
                "SELECT * FROM approvals WHERE task_id=$1 ORDER BY created_at DESC LIMIT 1",
                &[&id],
            )
            .await?;
        Ok(
            json!({"task":row_json(&task)?,"steps":rows_json(steps)?,"audit":rows_json(audit)?,"approval":approval.as_ref().map(row_json).transpose()?}),
        )
    }
    pub async fn list_approvals(&self, status: &str) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let rows = client
            .query(
                "SELECT * FROM approvals WHERE status=$1 ORDER BY created_at DESC LIMIT 100",
                &[&status],
            )
            .await?;
        Ok(json!({"items":rows_json(rows)?}))
    }
    pub async fn answer_approval(&self, id: &str, approved: bool) -> Result<Value> {
        let who = require_write_identity(Some(&self.tenant))?;
        let mut client = self.db.scoped(&self.tenant).await?;
        let transaction = client.transaction().await?;
        let approval = transaction
            .query_opt("SELECT * FROM approvals WHERE id=$1", &[&id])
            .await?
            .ok_or_else(|| ApiError::new(404, "not_found", format!("No approval {id}.")))?;
        let status = approval.try_get::<_, String>("status")?;
        if status != "pending" {
            return Err(ApiError::new(
                409,
                "already_answered",
                format!("{id} is already {status}."),
            ));
        }
        if who != approval.try_get::<_, String>("approver_id")? {
            return Err(ApiError::new(
                403,
                "not_approver",
                "Only the approver can answer this request.",
            ));
        }
        let payload = json!({"approval_id":id,"approved":approved,"by":who});
        let dedupe = format!("approval:{id}");
        transaction.execute("INSERT INTO agent_jobs (kind,payload,dedupe_key) VALUES ('approval',$1,$2) ON CONFLICT (dedupe_key) DO NOTHING",&[&payload,&dedupe]).await?;
        transaction
            .execute(
                "SELECT pg_notify('scout_jobs_' || current_schema(), 'approval')",
                &[],
            )
            .await?;
        transaction.commit().await?;
        Ok(
            json!({"status":"queued","approval_id":id,"task_id":approval.try_get::<_,String>("task_id")?,"approved":approved}),
        )
    }
    pub async fn audit(&self, options: &AuditOptions) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let task = options.task_id.as_deref().filter(|s| !s.is_empty());
        let decision = options
            .decision
            .as_deref()
            .filter(|s| !s.is_empty())
            .map(str::to_uppercase);
        let rows=client.query("SELECT * FROM audit_log WHERE ($1::text IS NULL OR task_id=$1) AND ($2::text IS NULL OR decision=$2) ORDER BY id DESC LIMIT $3",&[&task,&decision,&options.limit]).await?;
        Ok(json!({"items":rows_json(rows)?}))
    }
    pub async fn transaction_invoice(&self, id: &str) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let transaction=client.query_opt("SELECT id,booking_date,type,reference,counterparty,amount,currency,invoice_number,category,documented,invoice_id FROM transactions WHERE id=$1",&[&id]).await?.ok_or_else(||ApiError::new(404,"not_found",format!("No transaction {id}.")))?;
        let invoice_id: Option<String> = transaction.try_get("invoice_id")?;
        let invoice=client.query_opt("SELECT id,number,supplier,issue_date,due_date,total,currency,source,category,transaction_id FROM invoices WHERE id=$1 OR transaction_id=$2 ORDER BY (id=$1) DESC LIMIT 1",&[&invoice_id,&id]).await?.ok_or_else(||ApiError::new(404,"no_invoice",format!("No invoice is filed for transaction {id}.")))?;
        let invoice_key: String = invoice.try_get("id")?;
        let lines=client.query("SELECT position,description,quantity,unit_price,amount,category FROM invoice_lines WHERE invoice_id=$1 ORDER BY position,id",&[&invoice_key]).await?;
        let step=client.query_opt("SELECT s.task_id,s.ts,s.source,s.detail->>'text_excerpt' AS text_excerpt,s.evidence FROM agent_task_steps s WHERE s.detail->>'invoice_id'=$1 ORDER BY s.id DESC LIMIT 1",&[&invoice_key]).await?;
        let task=client.query_opt("SELECT id FROM agent_tasks WHERE transaction_id=$1 ORDER BY updated_at DESC LIMIT 1",&[&id]).await?;
        let document = if let Some(step) = &step {
            let s = row_json(step)?;
            json!({"task_id":s["task_id"],"filed_at":s["ts"],"source":s["source"],"text_excerpt":s["text_excerpt"].as_str().filter(|s|!s.is_empty()),"evidence":s["evidence"]})
        } else {
            Value::Null
        };
        let task_id = if let Some(task) = task {
            Some(task.try_get::<_, String>("id")?)
        } else {
            step.as_ref()
                .map(|s| s.try_get::<_, String>("task_id"))
                .transpose()?
        };
        Ok(
            json!({"transaction":row_json(&transaction)?,"invoice":row_json(&invoice)?,"lines":rows_json(lines)?,"document":document,"task_id":task_id}),
        )
    }
    pub async fn strategy_review(&self) -> Result<Value> {
        self.strategy_review_on(
            &Utc::now()
                .with_timezone(&chrono_tz::Europe::Berlin)
                .date_naive()
                .to_string(),
        )
        .await
    }
    pub async fn strategy_review_on(&self, day: &str) -> Result<Value> {
        require_write_identity(Some(&self.tenant))?;
        if !self.strategy_exec() {
            return Err(ApiError::new(
                403,
                "forbidden",
                "Only the organization owner can run a strategy review.",
            ));
        }
        let mut client = self.db.scoped(&self.tenant).await?;
        let transaction = client.transaction().await?;
        let key = format!("strategy:{day}");
        let payload = json!({"day":day});
        let job=transaction.query_opt("INSERT INTO agent_jobs(kind,payload,dedupe_key) VALUES ('strategy_review',$1,$2) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",&[&payload,&key]).await?;
        let task = transaction
            .query_opt("SELECT id FROM agent_tasks WHERE dedupe_key=$1", &[&key])
            .await?;
        transaction
            .execute(
                "SELECT pg_notify('scout_jobs_' || current_schema(), 'strategy_review')",
                &[],
            )
            .await?;
        transaction.commit().await?;
        Ok(
            json!({"status":if job.is_some() { "queued" } else { "already_scheduled" },"day":day,"task_id":task.map(|t|t.try_get::<_,String>("id")).transpose()?}),
        )
    }
    pub async fn connections(&self, settings: &ConnectionSettings) -> Result<Value> {
        let client = self.db.scoped(&self.tenant).await?;
        let rows=client.query("SELECT c.*,(cc.connection_id IS NOT NULL) AS has_credentials FROM connections c LEFT JOIN connection_credentials cc ON cc.connection_id=c.id ORDER BY c.kind,c.id",&[]).await?;
        let mut items = rows_json(rows)?;
        if legacy_connectors_enabled(Some(&self.tenant), &settings.legacy_organization) {
            for item in &mut items {
                if item["id"] == "slack" {
                    let granted = slack_scopes(&settings.slack_bot_token).await;
                    let missing = SLACK_SCOPES
                        .iter()
                        .filter(|scope| {
                            granted
                                .as_ref()
                                .is_some_and(|g| !g.iter().any(|s| s == **scope))
                        })
                        .copied()
                        .collect::<Vec<_>>();
                    item["scopes"] = json!({"granted":granted,"missing":missing});
                }
                if item["id"] == "gmail" {
                    item["oauth_configured"] = json!(
                        !settings.google_client_id.is_empty()
                            && !settings.google_client_secret.is_empty()
                            && !settings.connections_key.is_empty()
                            && !settings.oauth_state_secret.is_empty()
                    );
                }
            }
        }
        Ok(json!({"items":items}))
    }
}
#[derive(Default, Clone)]
pub struct ConnectionSettings {
    pub legacy_organization: String,
    pub slack_bot_token: String,
    pub google_client_id: String,
    pub google_client_secret: String,
    pub connections_key: String,
    pub oauth_state_secret: String,
}
impl ConnectionSettings {
    pub fn from_env() -> Self {
        fn value(key: &str) -> String {
            std::env::var(key).unwrap_or_default()
        }
        Self {
            legacy_organization: value("SCOUT_LEGACY_CONNECTORS_ORGANIZATION"),
            slack_bot_token: value("SLACK_BOT_TOKEN"),
            google_client_id: value("GOOGLE_CLIENT_ID"),
            google_client_secret: value("GOOGLE_CLIENT_SECRET"),
            connections_key: value("CONNECTIONS_KEY"),
            oauth_state_secret: value("OAUTH_STATE_SECRET"),
        }
    }
}
const SLACK_SCOPES: [&str; 11] = [
    "chat:write",
    "chat:write.customize",
    "im:history",
    "im:read",
    "im:write",
    "reactions:write",
    "files:read",
    "channels:history",
    "channels:read",
    "groups:history",
    "users:read",
];
async fn slack_scopes(token: &str) -> Option<Vec<String>> {
    if token.is_empty() {
        return None;
    }
    type Cache = Option<(Instant, Vec<String>)>;
    static CACHE: OnceLock<Mutex<Cache>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(guard) = cache.lock()
        && let Some((at, scopes)) = &*guard
        && at.elapsed() < Duration::from_secs(300)
    {
        return Some(scopes.clone());
    }
    let response = reqwest::Client::new()
        .post("https://slack.com/api/auth.test")
        .bearer_auth(token)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?;
    let scopes = response
        .headers()
        .get("x-oauth-scopes")
        .and_then(|s| s.to_str().ok())
        .unwrap_or("")
        .split(',')
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    if let Ok(mut guard) = cache.lock() {
        *guard = Some((Instant::now(), scopes.clone()));
    }
    Some(scopes)
}
