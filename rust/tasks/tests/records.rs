use anyhow::{Result, bail, ensure};
use async_trait::async_trait;
use scout_platform::{Database, Tenant};
use scout_tasks::{ResolutionOptions, StepOptions, TaskListener, TaskRecorder};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

struct Listener(AtomicUsize);
#[async_trait]
impl TaskListener for Listener {
    async fn recorded(&self, task: &Value, step: &Value) -> Result<()> {
        assert_eq!(task["id"], step["task_id"]);
        self.0.fetch_add(1, Ordering::SeqCst);
        bail!("simulated notification outage")
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated local SCOUT_RUST_TEST_DATABASE_URL"]
async fn durable_continuations_evidence_closure_and_listener_failures() -> Result<()> {
    let url = std::env::var("SCOUT_RUST_TEST_DATABASE_URL")?;
    ensure!(
        url.contains("@127.0.0.1:") || url.contains("@localhost:"),
        "local DB required"
    );
    let db = Database::new(url);
    let oid = uuid::Uuid::new_v4();
    let schema = format!("tenant_{}", oid.simple());
    let tenant = Tenant::new(
        oid.to_string(),
        "task-test",
        schema.clone(),
        None,
        "worker",
        false,
    )?;
    let admin = db.unscoped().await?;
    admin
        .batch_execute(&format!(
            "CREATE SCHEMA {schema};SET search_path TO {schema}"
        ))
        .await?;
    admin
        .batch_execute(include_str!("../../../backend/cfo/db/schema.sql"))
        .await?;
    admin
        .batch_execute(include_str!("../../../backend/scout/schema.sql"))
        .await?;
    let recorder = TaskRecorder::new(db, tenant);
    let outcome = async {
        let fields = json!({"owner_id":"owner","amount":"-123.45","status":"waiting"});
        let fields = fields
            .as_object()
            .ok_or_else(|| anyhow::anyhow!("fixture must be object"))?;
        let first = recorder
            .open("once", "invoicing", "Find invoice", fields)
            .await?;
        assert_eq!(first["id"], "SC-100");
        assert_eq!(first["amount"], "-123.45");
        let again = recorder
            .open("once", "chat", "Changed title", fields)
            .await?;
        assert_eq!(first, again);
        let next = recorder
            .open("second", "chat", "Next", &Default::default())
            .await?;
        assert_eq!(next["id"], "SC-101", "continuation must not burn sequence");
        let found = recorder
            .find_open(Some("owner"), &["waiting".into(), "escalated".into()], None)
            .await?;
        assert_eq!(found.len(), 1);
        assert_eq!(recorder.by_key("once").await?, Some(first));
        assert!(recorder.by_key("absent").await?.is_none());
        let listener = Arc::new(Listener(AtomicUsize::new(0)));
        recorder.add_listener(listener.clone());
        recorder
            .step(
                "SC-100",
                "lookup",
                "Read folder",
                &StepOptions {
                    evidence: Some(json!({"path":"company/invoice.pdf"})),
                    source: Some("files".into()),
                    ..Default::default()
                },
            )
            .await?;
        assert_eq!(listener.0.load(Ordering::SeqCst), 1);
        assert_eq!(recorder.steps("SC-100").await?.len(), 1);
        assert_eq!(
            recorder.get("SC-100").await?["evidence"][0]["path"],
            "company/invoice.pdf"
        );
        let closed = recorder
            .set_status(
                "SC-100",
                "done",
                &ResolutionOptions {
                    outcome: Some("filed".into()),
                    resolved_via: Some("files".into()),
                    ..Default::default()
                },
            )
            .await?;
        assert!(!closed["closed_at"].is_null());
        let reopened = recorder
            .set_status("SC-100", "waiting", &Default::default())
            .await?;
        assert_eq!(closed["closed_at"], reopened["closed_at"]);
        assert_eq!(
            reopened["outcome"], "filed",
            "unspecified fields must survive"
        );
        let invalid = json!({"dedupe_key":"overwrite"});
        assert!(
            recorder
                .update(
                    "SC-100",
                    invalid
                        .as_object()
                        .ok_or_else(|| anyhow::anyhow!("object"))?
                )
                .await
                .is_err()
        );
        assert_eq!(recorder.get("SC-100").await?["dedupe_key"], "once");
        Ok::<(), anyhow::Error>(())
    }
    .await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    outcome
}
