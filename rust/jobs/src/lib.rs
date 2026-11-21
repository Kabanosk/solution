//! The existing PostgreSQL queue protocol: dedupe, leases, attempt fencing and retries.

use anyhow::{Context, Result};
use chrono::{DateTime, Duration, Utc};
use scout_platform::{Database, Tenant};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio_postgres::Row;

pub const MAX_ATTEMPTS: i32 = 3;
pub const BACKOFF_SECONDS: [i64; 3] = [5, 30, 120];
pub const LEASE_SECONDS: i32 = 900;

pub fn lease_seconds(kind: &str) -> i32 {
    if kind == "dm_reply_notify" {
        180
    } else {
        LEASE_SECONDS
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Job {
    pub id: i64,
    pub kind: String,
    pub payload: Value,
    pub dedupe_key: String,
    pub status: String,
    pub attempts: i32,
    pub run_after: DateTime<Utc>,
    pub task_id: Option<String>,
    pub error: Option<String>,
}

impl Job {
    fn from_row(row: &Row) -> Result<Self> {
        Ok(Self {
            id: row.try_get("id")?,
            kind: row.try_get("kind")?,
            payload: row.try_get("payload")?,
            dedupe_key: row.try_get("dedupe_key")?,
            status: row.try_get("status")?,
            attempts: row.try_get("attempts")?,
            run_after: row.try_get("run_after")?,
            task_id: row.try_get("task_id")?,
            error: row.try_get("error")?,
        })
    }
}

/// Every operation opens a connection scoped to the identity captured at construction.
/// The queue never chooses a default organization or provisions database tables.
#[derive(Clone)]
pub struct JobQueue {
    database: Database,
    tenant: Tenant,
}

impl JobQueue {
    pub fn new(database: Database, tenant: Tenant) -> Self {
        Self { database, tenant }
    }

    pub async fn enqueue(
        &self,
        kind: &str,
        payload: &Value,
        dedupe_key: &str,
        run_after: Option<DateTime<Utc>>,
    ) -> Result<Option<i64>> {
        let mut conn = self.database.scoped(&self.tenant).await?;
        let tx = conn.transaction().await?;
        let row = tx.query_opt(
            "INSERT INTO agent_jobs (kind,payload,dedupe_key,run_after) VALUES ($1,$2,$3,coalesce($4,now())) \
             ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
            &[&kind, payload, &dedupe_key, &run_after],
        ).await?;
        // Preserve the original queue channel and notification even on a duplicate.
        tx.query_one("SELECT pg_notify('scout_jobs',$1)", &[&kind])
            .await?;
        tx.commit().await?;
        Ok(row.map(|row| row.try_get("id")).transpose()?)
    }

    pub async fn claim(&self, worker_id: &str) -> Result<Option<Job>> {
        let conn = self.database.scoped(&self.tenant).await?;
        let row = conn.query_opt(
            "UPDATE agent_jobs SET status='running', attempts=attempts+1, worker=$1, updated_at=now() \
             WHERE id=(SELECT id FROM agent_jobs WHERE (status='queued' AND run_after<=now()) \
             OR (status='running' AND updated_at<now()-make_interval(secs=>coalesce(($2::jsonb->>kind)::int,$3)) \
                 AND attempts<$4) ORDER BY run_after,id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
            &[&worker_id, &json!({"dm_reply_notify":180}), &LEASE_SECONDS, &MAX_ATTEMPTS],
        ).await?;
        row.as_ref().map(Job::from_row).transpose()
    }

    pub async fn reap_expired(&self) -> Result<Vec<Job>> {
        let conn = self.database.scoped(&self.tenant).await?;
        let rows = conn
            .query(
                "UPDATE agent_jobs SET updated_at=now(), attempts=attempts+1 WHERE id IN \
             (SELECT id FROM agent_jobs WHERE status='running' \
              AND updated_at<now()-make_interval(secs=>coalesce(($1::jsonb->>kind)::int,$2)) \
              AND attempts>=$3 FOR UPDATE SKIP LOCKED) RETURNING *",
                &[
                    &json!({"dm_reply_notify":180}),
                    &LEASE_SECONDS,
                    &MAX_ATTEMPTS,
                ],
            )
            .await?;
        rows.iter().map(Job::from_row).collect()
    }

    pub async fn renew(&self, job_id: i64, attempt: i32) -> Result<bool> {
        let conn = self.database.scoped(&self.tenant).await?;
        Ok(conn.query_opt(
            "UPDATE agent_jobs SET updated_at=now() WHERE id=$1 AND status='running' AND attempts=$2 RETURNING id",
            &[&job_id, &attempt],
        ).await?.is_some())
    }

    pub async fn done(
        &self,
        job_id: i64,
        task_id: Option<&str>,
        attempt: Option<i32>,
    ) -> Result<bool> {
        let conn = self.database.scoped(&self.tenant).await?;
        let query = if attempt.is_some() {
            "UPDATE agent_jobs SET status='done', task_id=coalesce($1,task_id), error=NULL,updated_at=now() \
             WHERE id=$2 AND status='running' AND attempts=$3 RETURNING id"
        } else {
            "UPDATE agent_jobs SET status='done', task_id=coalesce($1,task_id), error=NULL,updated_at=now() \
             WHERE id=$2 RETURNING id"
        };
        let row = match attempt {
            Some(attempt) => {
                conn.query_opt(query, &[&task_id, &job_id, &attempt])
                    .await?
            }
            None => conn.query_opt(query, &[&task_id, &job_id]).await?,
        };
        Ok(row.is_some())
    }

    /// True means terminal failure, including the original's unfenced absent-row behavior.
    pub async fn fail(&self, job_id: i64, error: &str, attempt: Option<i32>) -> Result<bool> {
        let mut conn = self.database.scoped(&self.tenant).await?;
        let tx = conn.transaction().await?;
        let row = match attempt {
            Some(attempt) => tx.query_opt(
                "SELECT attempts FROM agent_jobs WHERE id=$1 AND status='running' AND attempts=$2 FOR UPDATE",
                &[&job_id, &attempt],
            ).await?,
            None => tx.query_opt("SELECT attempts FROM agent_jobs WHERE id=$1 FOR UPDATE", &[&job_id]).await?,
        };
        if row.is_none() && attempt.is_some() {
            tx.rollback().await?;
            return Ok(false);
        }
        let attempts: i32 = row
            .as_ref()
            .map(|row| row.try_get("attempts"))
            .transpose()?
            .unwrap_or(MAX_ATTEMPTS);
        let terminal = attempts >= MAX_ATTEMPTS;
        // Python indexing: a queued row at attempt 0 selects BACKOFF_SECONDS[-1].
        let index = attempts.min(3) - 1;
        let index = if index < 0 { index + 3 } else { index };
        let delay = *BACKOFF_SECONDS
            .get(usize::try_from(index)?)
            .context("retry backoff index out of range")?;
        let status = if terminal { "failed" } else { "queued" };
        let message: String = error.chars().take(2000).collect();
        let run_after = Utc::now() + Duration::seconds(delay);
        tx.execute(
            "UPDATE agent_jobs SET status=$1,error=$2,run_after=$3,updated_at=now() WHERE id=$4",
            &[&status, &message, &run_after, &job_id],
        )
        .await?;
        tx.commit().await?;
        Ok(terminal)
    }

    pub async fn get(&self, job_id: i64) -> Result<Job> {
        let conn = self.database.scoped(&self.tenant).await?;
        let row = conn
            .query_opt("SELECT * FROM agent_jobs WHERE id=$1", &[&job_id])
            .await?
            .context("job does not exist")?;
        Job::from_row(&row)
    }
}
