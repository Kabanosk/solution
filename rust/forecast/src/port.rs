//! Recorded boundary for deployed pure forecast kernels. Training remains Python tooling.
#![forbid(unsafe_code)]
pub mod native;
use serde_json::{Value, json};

fn encode(value: Value) -> Value {
    match value {
        Value::Number(n) if n.is_f64() => {
            json!({"$f64":format!("{:016x}",n.as_f64().unwrap_or_default().to_bits())})
        }
        Value::Array(values) => Value::Array(values.into_iter().map(encode).collect()),
        Value::Object(values) => Value::Object(
            values
                .into_iter()
                .map(|(k, v)| (k, encode(v)))
                .collect::<std::collections::BTreeMap<_, _>>()
                .into_iter()
                .collect(),
        ),
        value => value,
    }
}
/// Uses exact IEEE754 float bit markers, never a widening numeric tolerance.
pub fn evaluate(payload: String) -> Result<String, String> {
    let Ok(body) = serde_json::from_str::<Value>(&payload) else {
        return Ok("invalid JSON".into());
    };
    if !body.is_object() {
        return Ok("unknown operation".into());
    }
    match native::dispatch(&body) {
        Ok(Some(output)) => Ok(encode(output).to_string()),
        Ok(None) => Ok("unknown operation".into()),
        Err(_) => Ok("invalid forecast input".into()),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_original_recording() -> Result<(), String> {
        for line in include_str!("../cases.jsonl").lines() {
            let case: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
            let input = case["args"][0].as_str().ok_or("input")?;
            if json!(evaluate(input.into())?) != case["ok"] {
                let op = serde_json::from_str::<Value>(input)
                    .ok()
                    .and_then(|v| v["op"].as_str().map(str::to_string))
                    .unwrap_or_default();
                return Err(format!(
                    "recorded case mismatch for operation {op}; inspect the saved diff report"
                ));
            }
        }
        Ok(())
    }
}
