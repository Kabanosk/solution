use std::collections::HashSet;

use anyhow::{Result, ensure};
use scout_jobs::JobQueue;
use scout_platform::{Database, Tenant};
use serde_json::json;

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires SCOUT_RUST_TEST_DATABASE_URL pointing to isolated local PostgreSQL"]
async fn concurrent_claims_dedupe_and_stale_worker_fencing() -> Result<()> {
    let url = std::env::var("SCOUT_RUST_TEST_DATABASE_URL")?;
    ensure!(
        url.contains("@127.0.0.1:") || url.contains("@localhost:"),
        "test requires local PostgreSQL"
    );
    let database = Database::new(url);
    let oid = uuid::Uuid::new_v4();
    let schema = format!("tenant_{}", oid.simple());
    let tenant = Tenant::new(
        oid.to_string(),
        "queue-test",
        schema.clone(),
        None,
        "worker",
        false,
    )?;
    let admin = database.unscoped().await?;
    admin.batch_execute(&format!("CREATE SCHEMA {schema};SET search_path TO {schema};CREATE TABLE agent_jobs (id BIGSERIAL PRIMARY KEY,kind TEXT NOT NULL,payload JSONB NOT NULL DEFAULT '{{}}',dedupe_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,run_after TIMESTAMPTZ NOT NULL DEFAULT now(),task_id TEXT,worker TEXT,error TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")).await?;
    let queue = JobQueue::new(database, tenant);
    let outcome = async {
        let mut inserts = Vec::new();
        for _ in 0..16 {
            let queue = queue.clone();
            inserts.push(tokio::spawn(async move {
                queue.enqueue("dm", &json!({}), "same", None).await
            }));
        }
        let mut inserted = Vec::new();
        for insert in inserts {
            if let Some(id) = insert.await?? {
                inserted.push(id);
            }
        }
        assert_eq!(
            inserted.len(),
            1,
            "unique dedupe must survive racing writers"
        );
        for index in 0..15 {
            queue
                .enqueue("dm", &json!({}), &format!("job-{index}"), None)
                .await?;
        }
        let mut claims = Vec::new();
        for index in 0..16 {
            let queue = queue.clone();
            claims.push(tokio::spawn(async move {
                queue.claim(&format!("worker-{index}")).await
            }));
        }
        let mut claimed = Vec::new();
        for claim in claims {
            claimed.push(
                claim
                    .await??
                    .ok_or_else(|| anyhow::anyhow!("missing claim"))?,
            );
        }
        assert_eq!(
            claimed
                .iter()
                .map(|job| job.id)
                .collect::<HashSet<_>>()
                .len(),
            16
        );
        assert!(queue.claim("extra").await?.is_none());
        let first = &claimed[0];
        admin
            .execute(
                "UPDATE agent_jobs SET updated_at=now()-interval '1 day' WHERE id=$1",
                &[&first.id],
            )
            .await?;
        let replacement = queue
            .claim("replacement")
            .await?
            .ok_or_else(|| anyhow::anyhow!("missing replacement"))?;
        assert_eq!(replacement.id, first.id);
        assert_eq!(replacement.attempts, first.attempts + 1);
        assert!(!queue.renew(first.id, first.attempts).await?);
        assert!(!queue.done(first.id, None, Some(first.attempts)).await?);
        assert!(!queue.fail(first.id, "stale", Some(first.attempts)).await?);
        assert!(queue.renew(replacement.id, replacement.attempts).await?);
        admin
            .execute(
                "UPDATE agent_jobs SET attempts=3,updated_at=now()-interval '1 day' WHERE id=$1",
                &[&first.id],
            )
            .await?;
        let reaped = queue.reap_expired().await?;
        assert_eq!(reaped.len(), 1);
        assert_eq!(reaped[0].attempts, 4);
        assert!(!queue.fail(first.id, "stale final worker", Some(3)).await?);
        assert!(queue.fail(first.id, "terminal", Some(4)).await?);
        assert_eq!(queue.get(first.id).await?.status, "failed");
        let retry_id = queue.enqueue("poll", &json!({}), "backoff", None).await?
            .ok_or_else(||anyhow::anyhow!("missing retry job"))?;
        for (attempt, seconds) in [(1,5.0), (2,30.0), (3,120.0)] {
            let claim = queue.claim("retry-worker").await?.ok_or_else(||anyhow::anyhow!("missing retry claim"))?;
            assert_eq!(claim.id, retry_id);
            assert_eq!(claim.attempts, attempt);
            assert_eq!(queue.fail(retry_id, "retry", Some(attempt)).await?, attempt == 3);
            let delay: f64 = admin.query_one(
                "SELECT EXTRACT(EPOCH FROM (run_after-now()))::float8 FROM agent_jobs WHERE id=$1", &[&retry_id],
            ).await?.try_get(0)?;
            assert!((seconds-2.0..seconds+1.0).contains(&delay), "wrong delay for attempt {attempt}: {delay}");
            admin.execute("UPDATE agent_jobs SET run_after=now()-interval '1 second' WHERE id=$1", &[&retry_id]).await?;
        }
        Ok::<(), anyhow::Error>(())
    }
    .await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    outcome
}
