use serde_json::{Value, json};
use std::io::{self, Read};

fn run(data: &Value) -> anyhow::Result<Value> {
    let environment = data["env"]
        .as_object()
        .map(|values| {
            values
                .iter()
                .map(|(key, value)| {
                    Ok((
                        key.clone(),
                        value
                            .as_str()
                            .ok_or_else(|| anyhow::anyhow!("invalid environment"))?
                            .to_owned(),
                    ))
                })
                .collect::<anyhow::Result<Vec<_>>>()
        })
        .transpose()?
        .unwrap_or_default();
    let mut settings = scout_config::Settings::from_parts(
        data["profile"].as_str().unwrap_or(""),
        data["repo"].as_str().unwrap_or(""),
        data["cwd"].as_str().unwrap_or(""),
        &environment,
    )?;
    if let Some(value) = data.get("tenant") {
        let tenant = scout_platform::Tenant::new(
            value["id"].as_str().unwrap_or(""),
            value["slug"].as_str().unwrap_or(""),
            value["schema_name"].as_str().unwrap_or(""),
            value["employee_id"].as_str().map(str::to_owned),
            value["role"].as_str().unwrap_or(""),
            value["legacy"].as_bool().unwrap_or(false),
        )?;
        let legacy = environment
            .iter()
            .find(|(key, _)| key == "SCOUT_LEGACY_CONNECTORS_ORGANIZATION")
            .map(|(_, value)| value.as_str())
            .unwrap_or("");
        settings = settings.for_tenant(&tenant, legacy)?;
    }
    Ok(settings.snapshot())
}

fn main() {
    let mut input = String::new();
    let _ = io::stdin().read_to_string(&mut input);
    let data = serde_json::from_str::<Value>(&input);
    let result = match data {
        Ok(data) if matches!(data["profile"].as_str(), Some("cfo" | "api" | "scout")) => {
            run(&data).unwrap_or_else(|_| json!({"error":"invalid_settings"}))
        }
        _ => json!({"error":"invalid_input"}),
    };
    fn sorted(value: Value) -> Value {
        match value {
            Value::Object(values) => {
                let mut entries: Vec<_> = values.into_iter().collect();
                entries.sort_by(|a, b| a.0.cmp(&b.0));
                Value::Object(
                    entries
                        .into_iter()
                        .map(|(key, value)| (key, sorted(value)))
                        .collect(),
                )
            }
            Value::Array(values) => Value::Array(values.into_iter().map(sorted).collect()),
            other => other,
        }
    }
    println!("{}", sorted(result));
}
