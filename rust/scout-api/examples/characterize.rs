//! Read-only characterization driver, not a production application entry point.
use scout_api::{ApiError, AuditOptions, ConnectionSettings, ScoutApi, TaskListOptions, row_json};
use scout_platform::{Database, Tenant, verify_session};
use serde_json::{Value, json};
use std::io::{self, BufRead};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let db = Database::new(std::env::var("SCOUT_TEST_DATABASE_URL")?);
    let owner = verify_session(
        &db,
        "scout_rust_migration_local_test_session_0123456789",
        "rust-migration-fixture",
    )
    .await?
    .ok_or("fixture not available")?;
    for line in io::stdin().lock().lines() {
        let body: Value = serde_json::from_str(&line?)?;
        let tenant = if body["role"] == "staff" {
            Tenant::new(
                owner.id(),
                owner.slug(),
                owner.schema_name(),
                Some("fixture-staff".into()),
                "staff",
                false,
            )?
        } else {
            owner.clone()
        };
        let api = ScoutApi::new(db.clone(), tenant.clone());
        let output:Result<Value,ApiError>=async {
            let text=|name:&str| body[name].as_str().map(str::to_string);
            let id=body["id"].as_str().unwrap_or_default();
            match body["op"].as_str().unwrap_or_default() {
                "tasks"=>api.list_tasks(&TaskListOptions { status:text("status"),r#loop:text("loop"),owner:text("owner"),q:text("q"),limit:body["limit"].as_i64().unwrap_or(50) }).await,
                "task"=>api.get_task(id).await,
                "approvals"=>api.list_approvals(body["status"].as_str().unwrap_or("pending")).await,
                "audit"=>api.audit(&AuditOptions { task_id:text("task_id"),decision:text("decision"),limit:body["limit"].as_i64().unwrap_or(100) }).await,
                "invoice"=>api.transaction_invoice(id).await,
                "connections"=>api.connections(&ConnectionSettings::default()).await,
                "row"=> { let c=db.scoped(&tenant).await?; let r=c.query_one("SELECT 1.2300::numeric(14,4) AS amount,'top-secret'::bytea AS ciphertext,NULL::text AS empty,'2026-11-01'::date AS day,'2026-11-01T10:00:00.123456Z'::timestamptz AS ts",&[]).await?;row_json(&r) },
                _=>Err(ApiError::new(400,"unknown_operation","Unknown characterization operation.")),
            }
        }.await;
        let result = match output {
            Ok(v) => json!({"ok":v}),
            Err(error) => {
                json!({"error":{"status":error.status,"code":error.code,"message":error.message}})
            }
        };
        println!("{result}");
    }
    Ok(())
}
