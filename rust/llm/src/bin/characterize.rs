use anyhow::{Result, bail};
use async_trait::async_trait;
use scout_llm::client::{Llm, OpenAiCompatLlm, StatusError};
use scout_llm::usage::{UsageContext, UsageRecord, UsageSink, estimate_tokens, message_text};
use scout_llm::{
    ChatOptions, Message, TlsVerify, ToolSpec, calls_from_text, json_in_text_messages, python_json,
    tls_verify_setting, to_wire,
};
use serde_json::{Value, json};
use std::{
    io::{self, Read},
    sync::{Arc, Mutex},
    time::Duration,
};

#[derive(Default)]
struct Sink(Mutex<Vec<Value>>);
#[async_trait]
impl UsageSink for Sink {
    async fn write(&self, record: &UsageRecord) -> Result<()> {
        let mut value = serde_json::to_value(record)?;
        if let Some(fields) = value.as_object_mut() {
            fields.shift_remove("ts");
            fields.shift_remove("latency_ms");
        }
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(value);
        Ok(())
    }
}
fn canonical(value: Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.into_iter().map(canonical).collect()),
        Value::Object(items) => {
            let mut items = items
                .into_iter()
                .map(|(key, value)| (key, canonical(value)))
                .collect::<serde_json::Map<_, _>>();
            items.sort_keys();
            Value::Object(items)
        }
        other => other,
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let data: Value = serde_json::from_str(&input)?;
    let tools: Vec<ToolSpec> =
        serde_json::from_value(data.get("tools").cloned().unwrap_or(json!([])))?;
    let messages: Vec<Message> =
        serde_json::from_value(data.get("messages").cloned().unwrap_or(json!([])))?;
    let output = match data["op"].as_str().unwrap_or("") {
        "encode" => json!(python_json(&data["value"])),
        "text" => json!(calls_from_text(data["content"].as_str(), &tools)),
        "wire" => to_wire(&serde_json::from_value(data["message"].clone())?),
        "prompt" => json!(json_in_text_messages(
            data["wire"].as_array().map(Vec::as_slice).unwrap_or(&[]),
            &tools
        )),
        "tls" => {
            if let Some(value) = data["value"].as_bool() {
                json!(value)
            } else {
                match tls_verify_setting(data["value"].as_str().unwrap_or("")) {
                    TlsVerify::System => json!(true),
                    TlsVerify::Disabled => json!(false),
                    TlsVerify::Bundle(path) => json!(path),
                }
            }
        }
        "estimate" => {
            let text = message_text(&messages, &tools);
            json!({"tokens":estimate_tokens(&text),"text":text})
        }
        "chat" => {
            let sink = Arc::new(Sink::default());
            let client = OpenAiCompatLlm::new(
                data["base_url"].as_str().unwrap_or(""),
                "test-model",
                "test-key",
                Duration::from_secs(3),
                TlsVerify::System,
                Some(sink.clone()),
            )?;
            let mut results = Vec::new();
            for operation in data["calls"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
                let messages: Vec<Message> = serde_json::from_value(
                    operation.get("messages").cloned().unwrap_or(json!([])),
                )?;
                let tools: Vec<ToolSpec> =
                    serde_json::from_value(operation.get("tools").cloned().unwrap_or(json!([])))?;
                let options = ChatOptions {
                    temperature: operation["temperature"].as_f64().unwrap_or(0.0),
                    max_tokens: operation["max_tokens"].as_i64(),
                };
                match client
                    .chat(&messages, &tools, &options, &UsageContext::default())
                    .await
                {
                    Ok(response) => results.push(serde_json::to_value(response)?),
                    Err(error) => {
                        let status = error.downcast_ref::<StatusError>();
                        let name = status.map_or("RuntimeError", StatusError::legacy_type_name);
                        results.push(json!({"error":name,"status":status.map(|error|error.status),"message":status.map(ToString::to_string)}));
                    }
                }
            }
            let records = sink
                .0
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .clone();
            json!({"results":results,"records":records})
        }
        _ => bail!("unknown operation"),
    };
    println!("{}", canonical(output));
    Ok(())
}
