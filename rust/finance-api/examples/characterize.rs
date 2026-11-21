use chrono::NaiveDate;
use scout_finance_api::{FinanceApi, FinanceOptions};
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
fn day(v: &Value) -> Result<Option<NaiveDate>, Box<dyn std::error::Error>> {
    v.as_str()
        .map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d"))
        .transpose()
        .map_err(Into::into)
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixture: Value = serde_json::from_slice(&std::fs::read(root.join("fixture.json"))?)?;
    let tenant = Tenant::new(
        fixture["id"].as_str().ok_or("id")?,
        "finance-fixture",
        fixture["schema"].as_str().ok_or("schema")?,
        Some("owner".into()),
        "owner",
        false,
    )?;
    let db = Database::new(fixture["url"].as_str().ok_or("url")?);
    let mut mismatches = Vec::new();
    let mut count = 0;
    for (line_index, line) in std::fs::read_to_string(root.join("characterization.jsonl"))?
        .lines()
        .enumerate()
    {
        count += 1;
        let case: Value = serde_json::from_str(line)?;
        let api = FinanceApi::new(
            db.clone(),
            tenant.clone(),
            FinanceOptions {
                model_path: fixture["model_path"].as_str().ok_or("path")?.into(),
                safety_buffer_eur: case["buffer"].as_str().map(str::to_string),
                today: None,
            },
        );
        let args = &case["args"];
        let result = match case["op"].as_str().ok_or("op")? {
            "kpis" => api.kpis().await,
            "forecast" => {
                api.forecast(
                    args["horizon_days"].as_u64().ok_or("days")? as usize,
                    args["granularity"].as_str().ok_or("granularity")?,
                )
                .await
            }
            "cost_metrics" => api.cost_metrics(args["kind"].as_str()).await,
            _ => {
                api.costs_by_team(day(&args["start"])?, day(&args["end"])?)
                    .await
            }
        };
        let actual = match result {
            Ok(v) => json!({"ok":v}),
            Err(e) => json!({"error":{"status":e.status,"code":e.code,"message":e.message}}),
        };
        let expected = if case.get("ok").is_some() {
            json!({"ok":case["ok"]})
        } else {
            json!({"error":case["error"]})
        };
        // Compare bytes as well as values: source DTO field order is part of this check.
        let actual_json = actual.to_string();
        let expected_json = expected.to_string();
        if actual_json.as_bytes() != expected_json.as_bytes() {
            mismatches.push(
                json!({"case":line_index,"op":case["op"],"expected":expected,"actual":actual}),
            );
        }
    }
    std::fs::write(
        root.join("mismatch-summary.json"),
        serde_json::to_vec_pretty(&mismatches)?,
    )?;
    println!(
        "{}/{} financial handler cases match",
        count - mismatches.len(),
        count
    );
    if !mismatches.is_empty() {
        return Err("financial source comparison failed".into());
    }
    Ok(())
}
