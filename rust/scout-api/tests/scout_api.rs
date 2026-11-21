use scout_api::{AuditOptions, ConnectionSettings, ScoutApi, TaskListOptions, row_json};
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
use uuid::Uuid;
type TestResult = Result<(), Box<dyn std::error::Error>>;

#[tokio::test]
#[ignore = "requires SCOUT_TEST_DATABASE_URL set to the dedicated local migration database"]
async fn scout_routes_preserve_scope_money_filters_approval_and_strategy() -> TestResult {
    let db = Database::new(std::env::var("SCOUT_TEST_DATABASE_URL")?);
    let organization = Uuid::new_v4();
    let schema = format!("tenant_{}", organization.simple());
    let tenant = Tenant::new(
        organization.to_string(),
        "scout-api-test",
        &schema,
        Some("owner".into()),
        "owner",
        false,
    )?;
    let staff = Tenant::new(
        organization.to_string(),
        "scout-api-test",
        &schema,
        Some("staff".into()),
        "staff",
        false,
    )?;
    let admin = db.unscoped().await?;
    admin
        .batch_execute(&format!("CREATE SCHEMA {schema}"))
        .await?;
    let conn = db.scoped(&tenant).await?;
    conn.batch_execute(include_str!("../../../backend/cfo/db/schema.sql"))
        .await?;
    conn.batch_execute(include_str!("../../../backend/scout/schema.sql"))
        .await?;
    conn.batch_execute("INSERT INTO agent_tasks(id,dedupe_key,loop,title,status,owner_id,amount,counterparty,created_at,updated_at,transaction_id) VALUES ('SC-100','invoice-fixture','invoicing','Find héllo 😀 invoice','waiting','owner',-123.45,'Supplier','2026-11-01T10:00:00.123Z','2026-11-01T10:00:00Z','booking-1'),('SC-101','strategy-fixture','strategy','Daily briefing','done','owner',NULL,NULL,'2026-11-01T09:00:00Z','2026-11-01T09:00:00Z',NULL); INSERT INTO agent_task_steps(task_id,ts,kind,source,text,detail,evidence) VALUES ('SC-100','2026-11-01T10:00:00Z','filing','files','Invoice found','{\"invoice_id\":\"invoice-1\",\"text_excerpt\":\"document excerpt\"}','{\"path\":\"company/invoice.pdf\"}'); INSERT INTO approvals(id,task_id,approver_id,question,created_at) VALUES ('approval-fixture','SC-100','owner','Approve?','2026-11-01T10:00:00Z'); INSERT INTO audit_log(ts,task_id,actor,action,target,resource,decision) VALUES ('2026-11-01T10:00:00Z','SC-100','agent','read','files','company','ALLOW'); INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,documented,invoice_id) VALUES ('booking-1','2026-11-01','transfer','Payment','Supplier',-123.45,true,NULL),('booking-2','2026-11-01','transfer','Missing','Other',-10.00,false,NULL); INSERT INTO invoices(id,number,supplier,issue_date,total,transaction_id) VALUES ('invoice-1','INV-TEST','Supplier','2026-11-01',123.45,'booking-1'); UPDATE transactions SET invoice_id='invoice-1' WHERE id='booking-1'; INSERT INTO invoice_lines(invoice_id,position,description,quantity,unit_price,amount) VALUES ('invoice-1',1,'Supplies',2.0000,61.7250,123.45); INSERT INTO connections(id,kind,provider,display_name,status) VALUES ('gmail','mail','google','Company mail','connected'),('slack','chat','slack','Slack','connected'); INSERT INTO connection_credentials(connection_id,ciphertext) VALUES ('gmail','never-expose-this-ciphertext'::bytea)").await?;
    let api = ScoutApi::new(db.clone(), tenant.clone());
    let staff_api = ScoutApi::new(db.clone(), staff);
    let outcome:TestResult=async {
        let all=api.list_tasks(&TaskListOptions::default()).await?;
        assert_eq!(all["items"].as_array().ok_or("items")?.len(),2);
        assert_eq!(all["counts"],json!({"waiting":1,"done":1}));
        assert_eq!(all["items"][0]["amount"],"-123.45");
        assert_eq!(all["items"][0]["created_at"],"2026-11-01T10:00:00.123000+00:00");
        let filtered=api.list_tasks(&TaskListOptions { status:Some("waiting,unknown".into()),q:Some("héllo".into()),..Default::default() }).await?;
        assert_eq!(filtered["items"].as_array().ok_or("filtered")?.len(),1);assert_eq!(filtered["counts"],all["counts"]);
        let hidden=staff_api.list_tasks(&TaskListOptions::default()).await?;
        assert_eq!(hidden["items"].as_array().ok_or("hidden")?.len(),1);assert_eq!(hidden["counts"],json!({"waiting":1}));
        assert_eq!(staff_api.get_task("SC-101").await.err().ok_or("strategy visible")?.status,404);
        assert_eq!(api.get_task("missing").await.err().ok_or("missing task")?.message,"No task missing.");
        let detail=api.get_task("SC-100").await?;
        assert_eq!(detail["steps"][0]["text"],"Invoice found");assert_eq!(detail["approval"]["id"],"approval-fixture");
        assert_eq!(api.list_approvals("pending").await?["items"].as_array().ok_or("approvals")?.len(),1);
        assert_eq!(staff_api.answer_approval("approval-fixture",true).await.err().ok_or("wrong approver")?.code,"not_approver");
        assert_eq!(api.answer_approval("missing",true).await.err().ok_or("missing approval")?.status,404);
        assert_eq!(api.answer_approval("approval-fixture",true).await?["status"],"queued");
        assert_eq!(api.answer_approval("approval-fixture",false).await?["status"],"queued");
        let jobs=conn.query("SELECT payload FROM agent_jobs WHERE kind='approval'",&[]).await?;
        assert_eq!(jobs.len(),1);assert_eq!(jobs[0].get::<_,Value>(0),json!({"approval_id":"approval-fixture","approved":true,"by":"owner"}));
        conn.execute("UPDATE approvals SET status='approved' WHERE id='approval-fixture'",&[]).await?;
        let error=api.answer_approval("approval-fixture",true).await.err().ok_or("answered approval accepted")?;
        assert_eq!(error.status,409);assert_eq!(error.message,"approval-fixture is already approved.");
        assert_eq!(api.audit(&AuditOptions { decision:Some("allow".into()),..Default::default() }).await?["items"].as_array().ok_or("audit")?.len(),1);
        let invoice=api.transaction_invoice("booking-1").await?;
        assert_eq!(invoice["transaction"]["amount"],"-123.45");assert_eq!(invoice["invoice"]["total"],"123.45");
        assert_eq!(invoice["lines"][0]["quantity"],"2.0000");assert_eq!(invoice["lines"][0]["unit_price"],"61.7250");
        assert_eq!(invoice["document"]["text_excerpt"],"document excerpt");assert_eq!(invoice["task_id"],"SC-100");
        assert_eq!(api.transaction_invoice("missing").await.err().ok_or("missing booking")?.code,"not_found");
        assert_eq!(api.transaction_invoice("booking-2").await.err().ok_or("missing invoice")?.code,"no_invoice");
        let connections=api.connections(&ConnectionSettings::default()).await?;
        assert!(!connections.to_string().contains("never-expose"));assert!(!connections.to_string().contains("ciphertext"));
        assert_eq!(connections["items"][0]["id"],"slack");assert_eq!(connections["items"][1]["has_credentials"],true);
        assert!(connections["items"][0].get("scopes").is_none());
        assert_eq!(api.strategy_review_on("2026-11-21").await?["status"],"queued");
        assert_eq!(api.strategy_review_on("2026-11-21").await?["status"],"already_scheduled");
        assert_eq!(staff_api.strategy_review_on("2026-11-21").await.err().ok_or("staff strategy accepted")?.status,403);
        let raw=conn.query_one("SELECT 1.2300::numeric(14,4) AS amount,'top-secret'::bytea AS ciphertext,NULL::text AS empty,'2026-11-01'::date AS day,'2026-11-01T10:00:00.123456Z'::timestamptz AS ts",&[]).await?;
        assert_eq!(row_json(&raw)?,json!({"amount":"1.2300","empty":null,"day":"2026-11-01","ts":"2026-11-01T10:00:00.123456+00:00"}));
        let nullable=conn.query_one("SELECT NULL::bytea AS binary_null",&[]).await?;
        assert_eq!(row_json(&nullable)?,json!({"binary_null":null}),"source skips actual bytes and retains NULL");
        Ok(())
    }.await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    outcome
}
