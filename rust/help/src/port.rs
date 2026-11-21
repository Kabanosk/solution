//! Public dashboard guides, with the original insertion order and an owned result per call.
#![forbid(unsafe_code)]

use serde_json::{Value, json};
use std::sync::OnceLock;

static GUIDES: OnceLock<Result<Value, String>> = OnceLock::new();

/// Return one bounded public guide. No private data or actions are accessed.
pub fn dashboard_help(topic: String) -> Result<Value, String> {
    let guides = GUIDES
        .get_or_init(|| {
            serde_json::from_str(include_str!("../guides.json"))
                .map_err(|error| format!("ValueError: Invalid embedded dashboard guides: {error}"))
        })
        .as_ref()
        .map_err(Clone::clone)?;
    if let Some(guide) = guides.get(&topic).and_then(Value::as_object) {
        let mut result = serde_json::Map::new();
        result.insert("topic".into(), Value::String(topic));
        result.extend(guide.clone());
        return Ok(Value::Object(result));
    }
    let topics: Vec<_> = guides
        .as_object()
        .into_iter()
        .flat_map(|map| map.keys())
        .collect();
    Ok(json!({"error": "unknown_dashboard_topic", "topics": topics}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_navigation_and_unknown_topics() -> Result<(), String> {
        let chat = dashboard_help("chat".into())?;
        assert_eq!(chat["href"], "/#chat");
        assert_eq!(chat["topic"], "chat");
        let unknown = dashboard_help("../chat".into())?;
        assert_eq!(unknown["error"], "unknown_dashboard_topic");
        assert_eq!(unknown["topics"][0], "getting_started");
        assert_eq!(unknown["topics"].as_array().map(Vec::len), Some(9));
        Ok(())
    }

    #[test]
    fn results_do_not_share_mutable_state() -> Result<(), String> {
        let mut first = dashboard_help("chat".into())?;
        first["steps"] = json!([]);
        let second = dashboard_help("chat".into())?;
        assert_eq!(second["steps"].as_array().map(Vec::len), Some(3));
        Ok(())
    }
}
