//! Test-only protocol driver, replayed against scout.jobs.JobQueue on a local test DB.
use std::io::{self, Read};

use anyhow::{Context, Result, bail};
use scout_jobs::{Job, JobQueue};
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
use tokio_postgres::Client;

fn view(job: Job) -> Result<Value> {
    let mut value = serde_json::to_value(job)?;
    if let Some(fields) = value.as_object_mut() {
        fields.remove("run_after");
    }
    Ok(value)
}

async fn run(queue: &JobQueue, admin: &Client, operations: &[Value]) -> Result<Value> {
    let mut results = Vec::new();
    for operation in operations {
        let field = |key: &str| {
            operation[key]
                .as_str()
                .with_context(|| format!("missing {key}"))
        };
        let id = || operation["id"].as_i64().context("missing id");
        let attempt = operation["attempt"]
            .as_i64()
            .map(i32::try_from)
            .transpose()?;
        let result = match field("op")? {
            "enqueue" => serde_json::to_value(
                queue
                    .enqueue(
                        field("kind")?,
                        operation.get("payload").unwrap_or(&json!({})),
                        field("key")?,
                        None,
                    )
                    .await?,
            )?,
            "claim" => match queue
                .claim(operation["worker"].as_str().unwrap_or("worker"))
                .await?
            {
                Some(job) => view(job)?,
                None => Value::Null,
            },
            "renew" => json!(
                queue
                    .renew(id()?, attempt.context("missing attempt")?)
                    .await?
            ),
            "done" => json!(
                queue
                    .done(id()?, operation["task_id"].as_str(), attempt)
                    .await?
            ),
            "fail" => json!(
                queue
                    .fail(
                        id()?,
                        operation["error"].as_str().unwrap_or("failed"),
                        attempt
                    )
                    .await?
            ),
            "get" => view(queue.get(id()?).await?)?,
            "expire" => {
                admin.execute("UPDATE agent_jobs SET updated_at=now()-interval '1 day',run_after=now()-interval '1 day' WHERE id=$1", &[&id()?]).await?;
                Value::Null
            }
            "reap" => {
                let mut jobs = queue.reap_expired().await?;
                jobs.sort_by_key(|job| job.id);
                json!(jobs.into_iter().map(view).collect::<Result<Vec<_>>>()?)
            }
            _ => bail!("unknown operation"),
        };
        results.push(result);
    }
    let rows = admin.query("SELECT id,kind,payload,dedupe_key,status,attempts,task_id,worker,error FROM agent_jobs ORDER BY id", &[]).await?;
    let rows = rows.iter().map(|row| -> Result<Value> {
        Ok(json!({
            "id":row.try_get::<_,i64>("id")?, "kind":row.try_get::<_,String>("kind")?,
            "payload":row.try_get::<_,Value>("payload")?, "dedupe_key":row.try_get::<_,String>("dedupe_key")?,
            "status":row.try_get::<_,String>("status")?, "attempts":row.try_get::<_,i32>("attempts")?,
            "task_id":row.try_get::<_,Option<String>>("task_id")?, "worker":row.try_get::<_,Option<String>>("worker")?,
            "error":row.try_get::<_,Option<String>>("error")?,
        }))
    }).collect::<Result<Vec<_>>>()?;
    Ok(json!({"results":results,"rows":rows}))
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let parsed = serde_json::from_str::<Value>(&input).ok();
    let Some(operations) = parsed
        .as_ref()
        .and_then(|data| data.get("operations"))
        .and_then(Value::as_array)
    else {
        println!("{{\"error\":\"invalid_input\"}}");
        return Ok(());
    };
    let url = std::env::var("SCOUT_RUST_TEST_DATABASE_URL")?;
    if !url.contains("@127.0.0.1:") && !url.contains("@localhost:") {
        bail!("characterization requires local PostgreSQL");
    }
    let database = Database::new(url);
    let organization_id = uuid::Uuid::new_v4();
    let schema = format!("tenant_{}", organization_id.simple());
    let tenant = Tenant::new(
        organization_id.to_string(),
        "queue-test",
        schema.clone(),
        None,
        "worker",
        false,
    )?;
    let admin = database.unscoped().await?;
    admin.batch_execute(&format!("CREATE SCHEMA {schema};SET search_path TO {schema};CREATE TABLE agent_jobs (id BIGSERIAL PRIMARY KEY,kind TEXT NOT NULL,payload JSONB NOT NULL DEFAULT '{{}}',dedupe_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,run_after TIMESTAMPTZ NOT NULL DEFAULT now(),task_id TEXT,worker TEXT,error TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")).await?;
    let queue = JobQueue::new(database, tenant);
    let outcome = run(&queue, &admin, operations).await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    println!("{}", outcome?);
    Ok(())
}
