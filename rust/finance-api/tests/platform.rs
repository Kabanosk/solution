use chrono::NaiveDate;
use scout_finance_api::{FinanceApi, FinanceOptions};
use scout_forecast_model_native::ForecastModel;
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
use uuid::Uuid;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
#[tokio::test]
#[ignore = "requires dedicated SCOUT_TEST_DATABASE_URL and native libxgboost"]
async fn scoped_no_data_ranges_and_native_cold_start() -> Result<()> {
    let url = std::env::var("SCOUT_TEST_DATABASE_URL")?;
    let db = Database::new(url);
    let id = Uuid::new_v4();
    let schema = format!("tenant_{}", id.simple());
    let tenant = Tenant::new(
        id.to_string(),
        "finance-test",
        &schema,
        Some("owner".into()),
        "owner",
        false,
    )?;
    let admin = db.unscoped().await?;
    admin
        .batch_execute(&format!("CREATE SCHEMA {schema}"))
        .await?;
    let conn = db.scoped(&tenant).await?;
    conn.batch_execute(include_str!("../../../backend/cfo/db/schema.sql"))
        .await?;
    let folder = std::env::temp_dir().join(format!("scout-finance-cold-{id}"));
    let base = folder.join("custom.json");
    let api = FinanceApi::new(
        db.clone(),
        tenant.clone(),
        FinanceOptions {
            model_path: base.clone(),
            safety_buffer_eur: None,
            today: NaiveDate::from_ymd_opt(2026, 11, 21),
        },
    );
    let outcome:Result<()>=async {
  let error=api.kpis().await.err().ok_or("empty KPI accepted")?;assert_eq!(error.status,503);assert_eq!(error.code,"no_data");
  assert_eq!(api.forecast(7,"week").await.err().ok_or("empty forecast accepted")?.status,503);
  let costs=api.costs_by_team(None,None).await?;assert_eq!(costs,json!({"stub":false,"from":"2026-11-01","to":"2026-11-21","total":{"amount":"0.00","currency":"EUR"},"items":[]}));
  let start=NaiveDate::from_ymd_opt(2027,1,1).ok_or("date")?;let end=NaiveDate::from_ymd_opt(2026,1,1).ok_or("date")?;
  assert_eq!(api.costs_by_team(Some(start),Some(end)).await.err().ok_or("inverted accepted")?.message,"Invalid request: 'from' must not be after 'to'.");
  assert!(!base.exists());
  let rows:Value=serde_json::from_str(include_str!("../../forecast-model/fixtures/transactions.json"))?;
  for(i,row)in rows.as_array().ok_or("rows")?.iter().enumerate(){let date=NaiveDate::parse_from_str(row["booking_date"].as_str().ok_or("date")?,"%Y-%m-%d")?;let amount=row["amount"].to_string();let category=row["category"].as_str().ok_or("category")?;conn.execute("INSERT INTO transactions(id,booking_date,type,reference,counterparty,amount,category) VALUES($1,$2,'transfer','Native training','Fixture',$3::text::numeric,$4)",&[&format!("t-{i}"),&date,&amount,&category]).await?;}
  let forecast=api.forecast(7,"week").await?;assert_eq!(forecast["as_of"],"2025-04-30");assert_eq!(forecast["model"]["version"],"trained-until-2025-04-30");
  let path=folder.join("tenants").join(id.to_string()).join("custom.json");assert!(path.is_file());assert!(std::path::PathBuf::from(format!("{}.meta.json",path.display())).is_file());assert!(!base.exists());
  let model=ForecastModel::load(&path)?;assert_eq!(model.meta.feature_set,"components");assert_eq!(model.meta.params["n_estimators"],326);assert_eq!(model.meta.trained_until.to_string(),"2025-04-30");
  let first=std::fs::metadata(&path)?.modified()?;let(a,b)=tokio::join!(api.forecast(8,"day"),api.forecast(14,"week"));a?;b?;assert_eq!(first,std::fs::metadata(&path)?.modified()?,"cached requests retrained model");
  Ok(())
 }.await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    if folder.exists() {
        std::fs::remove_dir_all(folder)?;
    }
    outcome
}
