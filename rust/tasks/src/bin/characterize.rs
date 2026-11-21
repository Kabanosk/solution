use anyhow::{Context, Result, bail, ensure};
use scout_platform::{Database, Tenant};
use scout_tasks::{StepOptions, TaskRecorder};
use serde_json::{Map, Value, json};
use std::io::{self, Read};

fn view(value: Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.into_iter().map(view).collect()),
        Value::Object(items) => {
            let task = items.contains_key("dedupe_key") && items.contains_key("id");
            let record = task
                || (items.contains_key("task_id")
                    && items.contains_key("kind")
                    && items.contains_key("id"));
            let mut result = Map::new();
            for (key, item) in items {
                if record && ["created_at", "updated_at", "ts"].contains(&key.as_str()) {
                    continue;
                }
                result.insert(
                    key.clone(),
                    if task && key == "closed_at" {
                        json!(!item.is_null())
                    } else {
                        canonical(item)
                    },
                );
            }
            result.sort_keys();
            Value::Object(result)
        }
        other => other,
    }
}

fn canonical(value: Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.into_iter().map(canonical).collect()),
        Value::Object(items) => {
            let mut fields: Map<String, Value> = items
                .into_iter()
                .map(|(key, value)| (key, canonical(value)))
                .collect();
            fields.sort_keys();
            Value::Object(fields)
        }
        other => other,
    }
}

async fn run(recorder: &TaskRecorder, operations: &[Value]) -> Result<Value> {
    let mut results = Vec::new();
    let mut ids = Vec::<String>::new();
    for operation in operations {
        let field = |key: &str| {
            operation[key]
                .as_str()
                .with_context(|| format!("missing {key}"))
        };
        let empty = Map::new();
        let fields = operation["fields"].as_object().unwrap_or(&empty);
        let result: Result<Value> = match field("op")? {
            "open" => {
                recorder
                    .open(
                        field("key")?,
                        operation["loop"].as_str().unwrap_or("invoicing"),
                        operation["title"].as_str().unwrap_or("Find invoice"),
                        fields,
                    )
                    .await
            }
            "get" => recorder.get(field("id")?).await,
            "by_key" => Ok(recorder.by_key(field("key")?).await?.unwrap_or(Value::Null)),
            "update" => recorder.update(field("id")?, fields).await,
            "find_open" => {
                let statuses = operation["statuses"]
                    .as_array()
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_else(|| vec!["waiting".into(), "escalated".into()]);
                Ok(json!(
                    recorder
                        .find_open(
                            operation["owner"].as_str(),
                            &statuses,
                            operation["slack_ref"].as_str()
                        )
                        .await?
                ))
            }
            "evidence" => recorder
                .add_evidence(field("id")?, operation["item"].clone())
                .await
                .map(|_| Value::Null),
            "step" => {
                let options = &operation["options"];
                recorder
                    .step(
                        field("id")?,
                        operation["kind"].as_str().unwrap_or("lookup"),
                        operation["text"].as_str().unwrap_or("Read source"),
                        &StepOptions {
                            source: options["source"].as_str().map(str::to_owned),
                            result_count: options["result_count"]
                                .as_i64()
                                .map(i32::try_from)
                                .transpose()?,
                            detail: options.get("detail").cloned().unwrap_or(json!({})),
                            evidence: options.get("evidence").cloned(),
                        },
                    )
                    .await
            }
            "steps" => Ok(json!(recorder.steps(field("id")?).await?)),
            _ => bail!("unknown operation"),
        };
        let value = match result {
            Ok(value) => value,
            Err(error) if error.to_string() == "task does not exist" => {
                json!({"error":"not_found"})
            }
            Err(error) if error.to_string().starts_with("unknown task fields:") => {
                json!({"error":error.to_string()})
            }
            Err(error) => return Err(error),
        };
        if field("op")? == "open"
            && let Some(id) = value["id"]
                .as_str()
                .filter(|id| !ids.iter().any(|old| old == *id))
        {
            ids.push(id.into());
        }
        results.push(view(value));
    }
    ids.sort();
    let mut tasks = Vec::new();
    for id in ids {
        tasks.push(view(recorder.get(&id).await?));
    }
    Ok(canonical(json!({"results":results,"tasks":tasks})))
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
    ensure!(
        url.contains("@127.0.0.1:") || url.contains("@localhost:"),
        "requires local test DB"
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
        .batch_execute(include_str!("../../../../backend/cfo/db/schema.sql"))
        .await?;
    admin
        .batch_execute(include_str!("../../../../backend/scout/schema.sql"))
        .await?;
    let recorder = TaskRecorder::new(db, tenant);
    let result = run(&recorder, operations).await;
    admin
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .await?;
    println!("{}", result?);
    Ok(())
}
