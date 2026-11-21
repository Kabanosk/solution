use scout_guardrails::*;
use serde_json::{Value, json};
use std::io::{self, Read};
fn run(data: &Value) -> Option<Value> {
    Some(match data["op"].as_str()? {
        "mask" => json!(mask_sensitive(data["text"].as_str()?)),
        "chunks" => json!(chunks(data["text"].as_str()?)),
        "messages" => json!(messages(
            data["config"].as_str()?,
            &data["texts"]
                .as_array()?
                .iter()
                .map(|value| value.as_str().unwrap_or("").into())
                .collect::<Vec<_>>()
        )),
        "rails" => json!(rail_names(&data["body"])),
        "reply" => json!(blocked_reply(data["attempt"].as_u64()? as usize)),
        "digest" => json!(digest(data["text"].as_str()?)),
        "outside" => json!(outside_items(data["tool"].as_str()?, &data["result"])),
        "ksef" => json!(ksef_text(&data["doc"])),
        "batches" => json!(batches(
            data["turns"].as_array()?,
            &data["indexes"]
                .as_array()?
                .iter()
                .map(|value| value.as_u64().unwrap_or(0) as usize)
                .collect::<Vec<_>>()
        )),
        "response" => {
            let value = parse_response(&data["body"]);
            json!({"status":value.status,"activated_rails":value.activated_rails,"detail":value.detail})
        }
        _ => return None,
    })
}
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
fn main() {
    let mut text = String::new();
    let _ = io::stdin().read_to_string(&mut text);
    let result = serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|data| run(&data))
        .unwrap_or_else(|| json!({"error":"invalid_input"}));
    println!("{}", sorted(result));
}
