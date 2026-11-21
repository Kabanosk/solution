use chrono::NaiveDate;
use scout_audit::Audit;
use scout_domain::{AuditRecord, EmployeeMap, People};
use scout_hr::HR;
use scout_platform::{Database, Tenant};
use serde_json::json;
use uuid::Uuid;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[tokio::test]
#[ignore = "requires dedicated SCOUT_TEST_DATABASE_URL"]
async fn scoped_hr_and_audit_preserve_vacations_identity_and_payroll_privacy() -> Result<()> {
    let db = Database::new(std::env::var("SCOUT_TEST_DATABASE_URL")?);
    let admin = db.unscoped().await?;
    let a = Uuid::new_v4();
    let b = Uuid::new_v4();
    let sa = format!("tenant_{}", a.simple());
    let sb = format!("tenant_{}", b.simple());
    let ta = Tenant::new(
        a.to_string(),
        "hr-a",
        &sa,
        Some("office".into()),
        "office_manager",
        false,
    )?;
    let tb = Tenant::new(
        b.to_string(),
        "hr-b",
        &sb,
        Some("ceo".into()),
        "owner",
        false,
    )?;
    admin
        .batch_execute(&format!("CREATE SCHEMA {sa};CREATE SCHEMA {sb}"))
        .await?;
    for tenant in [&ta, &tb] {
        let conn = db.scoped(tenant).await?;
        conn.batch_execute(include_str!("../../../backend/cfo/db/schema.sql"))
            .await?;
        conn.batch_execute(include_str!("../../../backend/scout/schema.sql"))
            .await?;
        conn.batch_execute("INSERT INTO employees(id,name,role,manager_id,slack_user_id) VALUES('ceo','Owner Person','CEO',NULL,'OLD'),('manager','Chef Person','Head chef','ceo',NULL),('staff','Staff Person','Marketing','manager','U_STAFF'),('office','Office Person','Office manager','ceo','REGISTERED'); INSERT INTO vacations(employee_id,start_date,end_date) VALUES('staff','2026-01-01','2026-01-31'),('staff','2026-01-15','2026-02-05'),('manager','2026-01-01','2026-01-31'); INSERT INTO slack_members(slack_user_id,status,employee_id) VALUES('REGISTERED','registered','office')").await?;
    }
    let outcome:Result<()>=async{
 let mut links=EmployeeMap::new();links.insert("staff".into(),"U0".into());links.insert("ceo".into(),"U0".into());let mut names=EmployeeMap::new();names.insert("staff".into(),"Slack Staff".into());let hr=HR::new(db.clone(),ta.clone(),links,names);
 assert_eq!(hr.by_slack_id("U0").await?.ok_or("explicit map")?["id"],"ceo");assert_eq!(hr.by_slack_id("U_STAFF").await?.ok_or("DB map")?["id"],"staff");assert_eq!(hr.name(Some("staff")).await?,"Slack Staff");assert_eq!(hr.full_name("manager").await?,"Chef Person");assert_eq!(hr.role("staff").await?,"waitstaff");assert_eq!(hr.role("office").await?,"office_manager");assert_eq!(hr.name(None).await?,"someone");
 let day=NaiveDate::from_ymd_opt(2026,1,31).ok_or("day")?;assert!(hr.on_vacation("staff",day).await?);assert_eq!(hr.first_available("staff",day).await?,("ceo".into(),vec!["staff".into(),"manager".into()]));
 let conn=db.scoped(&ta).await?;conn.execute("UPDATE employees SET manager_id='staff' WHERE id='ceo'",&[]).await?;assert_eq!(hr.chain("staff").await?,vec!["staff","manager","ceo"]);conn.execute("UPDATE employees SET manager_id=NULL WHERE id='ceo'",&[]).await?;
 let mut new_links=EmployeeMap::new();new_links.insert("manager".into(),"U_MANAGER".into());hr.link_slack(&new_links,&EmployeeMap::new()).await?;assert_eq!(hr.employee("office").await?.ok_or("office")?["slack_user_id"],"REGISTERED");assert!(hr.employee("ceo").await?.ok_or("ceo")?["slack_user_id"].is_null());assert_eq!(hr.employee("manager").await?.ok_or("manager")?["slack_user_id"],"U_MANAGER");
 let other=HR::new(db.clone(),tb.clone(),EmployeeMap::new(),EmployeeMap::new());assert_eq!(other.employee("ceo").await?.ok_or("other")?["slack_user_id"],"OLD");
 let audit=Audit::new(db.clone(),ta.clone());assert!(audit.check("staff","staff","files:staff","own",None).await?);assert!(!audit.check("staff","staff","files:office","private",None).await?);
 let people=hr.all().await?;let row=json!({"id":"salary1","category":"payroll","counterparty":"Staff Person","reference":"Salary","amount":"-1000.00","booking_date":"2026-01-01"});let view=audit.salary_filter(&[row.clone()],"office","office_manager",&people,None,true).await?;assert!(view.summaries.is_empty());assert_eq!(view.hidden[0].1,"DENY");
 let second=json!({"id":"salary2","category":"payroll","counterparty":"Chef Person","reference":"Salary","amount":"-2000.00","booking_date":"2026-01-02"});let view=audit.salary_filter(&[row,second],"office","office_manager",&people,None,true).await?;assert_eq!(view.summaries,vec!["Payroll 2026-01 (2 employees): 3,000.00 EUR"]);assert!(view.hidden.iter().all(|(_,d,_)|d=="MASK"));
 let record=AuditRecord::new("agent","test","bank","😀".repeat(600));audit.record(&record).await?;let length:i32=conn.query_one("SELECT length(resource) FROM audit_log WHERE action='test'",&[]).await?.get(0);assert_eq!(length,500);let decisions=conn.query("SELECT decision,policy FROM audit_log WHERE action='salary_filter' ORDER BY id",&[]).await?;assert_eq!(decisions.len(),3);assert_eq!(decisions[0].get::<_,String>(0),"DENY");assert_eq!(decisions[1].get::<_,String>(0),"MASK");let cb=db.scoped(&tb).await?;assert_eq!(cb.query_one("SELECT count(*) FROM audit_log",&[]).await?.get::<_,i64>(0),0);
 Ok(())
 }.await;
    admin
        .batch_execute(&format!(
            "DROP SCHEMA {sa} CASCADE;DROP SCHEMA {sb} CASCADE"
        ))
        .await?;
    outcome
}
