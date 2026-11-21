//! JSON characterization for portable model artifacts; application logic lives in native.
#![forbid(unsafe_code)]
pub mod native;
use serde_json::{Value, json};
fn encode(value: Value) -> Value {
    match value {
        Value::Number(n) if n.is_f64() => {
            json!({"$f64":format!("{:016x}",n.as_f64().unwrap_or_default().to_bits())})
        }
        Value::Array(a) => Value::Array(a.into_iter().map(encode).collect()),
        Value::Object(m) => Value::Object(
            m.into_iter()
                .map(|(k, v)| (k, encode(v)))
                .collect::<std::collections::BTreeMap<_, _>>()
                .into_iter()
                .collect(),
        ),
        v => v,
    }
}
pub fn evaluate(payload: String) -> Result<String, String> {
    let Ok(body) = serde_json::from_str::<Value>(&payload) else {
        return Ok("invalid JSON".into());
    };
    if !body.is_object() {
        return Ok("unknown operation".into());
    }
    match native::dispatch(&body) {
        Ok(Some(value)) => Ok(encode(value).to_string()),
        Ok(None) => Ok("unknown operation".into()),
        Err(_) => Ok("invalid model input".into()),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn portable_model_save_roundtrip() -> Result<(), String> {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let model = native::ForecastModel::load(root.join("fixtures/components.json"))?;
        let folder =
            std::env::temp_dir().join(format!("scout-model-roundtrip-{}", std::process::id()));
        let path = folder.join("sub/model.json");
        model.save(&path)?;
        let loaded = native::ForecastModel::load(&path)?;
        let dates = [chrono::NaiveDate::from_ymd_opt(2025, 6, 28).ok_or("date")?];
        let matches = model.predict(&dates)? == loaded.predict(&dates)?
            && model.metadata()? == loaded.metadata()?;
        std::fs::remove_dir_all(folder).map_err(|e| e.to_string())?;
        if !matches {
            return Err("roundtrip changed model".into());
        }
        Ok(())
    }
    #[test]
    fn replay_original_model_cases() -> Result<(), String> {
        for (index, line) in include_str!("../cases.jsonl").lines().enumerate() {
            let case: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
            let input = case["args"][0].as_str().ok_or("input")?;
            if json!(evaluate(input.into())?) != case["ok"] {
                return Err(format!(
                    "model case {index} differs; inspect saved diff report"
                ));
            }
        }
        Ok(())
    }
}
