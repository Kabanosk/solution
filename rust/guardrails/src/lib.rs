//! Guardrail service protocol, deterministic text transforms and scoped orchestration.
pub mod client;
pub mod facade;
pub use client::HttpGuardrails;
pub use facade::{GuardSettings, Guardrails};
use regex::Regex;
use scout_domain::Verdict;
use serde_json::{Value, json};
use sha1::{Digest, Sha1};
use std::sync::OnceLock;

pub const USER_INPUT: &str = "user-input";
pub const TOOL_RESULT: &str = "tool-result";
pub const BOT_OUTPUT: &str = "bot-output";
pub const BOT_OUTPUT_FINANCE: &str = "bot-output-finance";
pub const DASHBOARD_PLACEHOLDER: &str = "http://dashboard.svc.cluster.local";
pub const WITHHELD: &str = "[content withheld: flagged by guardrail]";
pub const OUTPUT_REPLY: &str =
    "I could not share that answer safely. Please rephrase your question.";
pub const CHECK_CHARS: usize = 8000;
pub const CHUNK_OVERLAP: usize = 300;
pub const MAX_CHUNKS: usize = 100;
pub const MAX_RECHECKS: usize = 8;
pub fn verdict(status: &str) -> Verdict {
    Verdict {
        status: status.into(),
        ..Default::default()
    }
}
pub fn blocked_reply(attempt: usize) -> String {
    format!(
        "This request was blocked by our safety policy (attempt {attempt} of 3; repeated attempts are reported to compliance)."
    )
}
pub fn chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}
pub fn digest(text: &str) -> String {
    format!(
        "{} chars, sha1:{:.10}",
        text.chars().count(),
        format!("{:x}", Sha1::digest(text.as_bytes()))
    )
}
pub fn sha(parts: &[String]) -> String {
    format!("{:x}", Sha1::digest(parts.join("|").as_bytes()))[..16].into()
}
pub fn pystr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}
pub fn truth(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(value) => value.as_f64() != Some(0.),
    }
}
pub fn chunks(text: &str) -> Vec<String> {
    let text: Vec<_> = text.chars().collect();
    if text.len() <= CHECK_CHARS {
        return vec![text.iter().collect()];
    };
    (0..text.len() - CHUNK_OVERLAP)
        .step_by(CHECK_CHARS - CHUNK_OVERLAP)
        .map(|i| text[i..(i + CHECK_CHARS).min(text.len())].iter().collect())
        .collect()
}
pub fn messages(config: &str, texts: &[String]) -> Vec<Value> {
    texts
        .iter()
        .flat_map(|text| chunks(text))
        .enumerate()
        .map(|(i, text)| match config {
            TOOL_RESULT => {
                json!({"role":"tool","tool_call_id":format!("scout-{}",i+1),"content":text})
            }
            BOT_OUTPUT | BOT_OUTPUT_FINANCE => json!({"role":"assistant","content":text}),
            _ => json!({"role":"user","content":text}),
        })
        .collect()
}
pub fn rail_names(body: &Value) -> Vec<String> {
    let mut names = Vec::new();
    let mut add = |value: &Value| {
        if let Some(name) = value.as_str().filter(|name| !name.is_empty())
            && !names.iter().any(|old| old == name)
        {
            names.push(name.to_owned());
        }
    };
    if let Some(rails) = body["guardrails_data"]["log"]["activated_rails"].as_array() {
        for rail in rails {
            if rail.is_string() {
                add(rail)
            } else if rail.is_object() && rail.get("stop").is_none_or(truth) {
                add(&rail["name"]);
            }
        }
    }
    if names.is_empty()
        && let Some(status) = body["rails_status"].as_object()
    {
        for (name, value) in status {
            let state = if value.is_object() {
                &value["status"]
            } else {
                value
            };
            if state == "blocked" && !name.is_empty() && !names.contains(name) {
                    names.push(name.clone());
            }
        }
    }
    names
}
pub fn parse_response(body: &Value) -> Verdict {
    let status = if truth(&body["status"]) {
        pystr(&body["status"])
    } else {
        "error".into()
    };
    match status.as_str() {
        "blocked" => {
            let mut rails = rail_names(body);
            if rails.is_empty() {
                rails.push("unknown".into())
            };
            Verdict {
                activated_rails: rails,
                ..verdict("blocked")
            }
        }
        "success" => verdict("success"),
        _ => {
            let detail = if truth(&body["error"]) {
                pystr(&body["error"])
            } else if truth(&body["detail"]) {
                pystr(&body["detail"])
            } else {
                status
            };
            Verdict {
                detail: chars(&detail, 200),
                ..verdict("error")
            }
        }
    }
}
pub fn ksef_text(doc: &Value) -> String {
    let text = |value: &Value| {
        if truth(value) {
            pystr(value)
        } else {
            String::new()
        }
    };
    let mut values = vec![text(&doc["supplier"]), text(&doc["number"])];
    if let Some(lines) = doc["lines"].as_array() {
        values.extend(lines.iter().map(|line| text(&line["description"])))
    }
    values.join("\n")
}
pub fn outside_items(tool: &str, result: &Value) -> Vec<(String, String, String)> {
    match tool {
        "read_file" if truth(&result["path"]) => {
            let path = pystr(&result["path"]);
            vec![(
                format!("file:{path}"),
                format!("file {path}"),
                if truth(&result["text"]) {
                    pystr(&result["text"])
                } else {
                    String::new()
                },
            )]
        }
        "search_files" => result["files"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|path| {
                let path = pystr(path);
                (format!("file:{path}"), format!("file {path}"), path)
            })
            .collect(),
        "search_email" => result["mails"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|mail| {
                (
                    format!(
                        "mail:{}",
                        sha(&[
                            pystr(&mail["from"]),
                            pystr(&mail["subject"]),
                            pystr(&mail["date"])
                        ])
                    ),
                    format!(
                        "mail “{}” from {}",
                        chars(&pystr(&mail["subject"]), 80),
                        pystr(&mail["from"])
                    ),
                    scout_llm::python_json(mail),
                )
            })
            .collect(),
        "search_chat" => result["messages"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|chat| {
                (
                    format!(
                        "chat:{}",
                        sha(&[
                            pystr(&chat["channel"]),
                            pystr(&chat["from"]),
                            pystr(&chat["text"])
                        ])
                    ),
                    format!(
                        "Slack message in {} from {}",
                        pystr(&chat["channel"]),
                        pystr(&chat["from"])
                    ),
                    if truth(&chat["text"]) {
                        pystr(&chat["text"])
                    } else {
                        String::new()
                    },
                )
            })
            .collect(),
        _ => vec![],
    }
}
pub fn batches(turns: &[Value], indexes: &[usize]) -> Vec<Vec<usize>> {
    let mut result = vec![vec![]];
    let mut used = 0;
    for index in indexes {
        let n = chunks(turns[*index]["content"].as_str().unwrap_or("")).len();
        if !result.last().is_none_or(Vec::is_empty) && used + n > MAX_CHUNKS {
            result.push(vec![]);
            used = 0
        };
        result.last_mut().unwrap().push(*index);
        used += n;
    }
    result
}
static MASKS: OnceLock<[Regex; 4]> = OnceLock::new();
fn masks() -> &'static [Regex; 4] {
    MASKS.get_or_init(|| {
        [
            r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b",
            r"\b\d(?:[ -]?\d){12,18}\b",
            r"[\p{L}\p{N}_.+-]+@[\p{L}\p{N}_-]+(?:\.[\p{L}\p{N}_-]+)+",
            r"\+\d[\d ()./-]{7,}\d|\b\d{3}[ -]\d{3}[ -]\d{3}\b",
        ]
        .map(|pattern| Regex::new(pattern).expect("constant regular expression"))
    })
}
fn digit(ch: char) -> Option<u32> {
    let code = ch as u32;
    const ZEROES: &[u32] = include!("digit_zeroes.rs");
    ZEROES
        .iter()
        .find_map(|zero| (code >= *zero && code < *zero + 10).then_some(code.wrapping_sub(*zero)))
}
pub fn luhn(text: &str) -> bool {
    text.chars()
        .rev()
        .enumerate()
        .try_fold(0, |total, (index, ch)| {
            let mut value = digit(ch)?;
            if index % 2 == 1 {
                value = if value > 4 { value * 2 - 9 } else { value * 2 }
            };
            Some(total + value)
        })
        .is_some_and(|total| total % 10 == 0)
}
pub fn mask_sensitive(text: &str) -> (String, Vec<String>) {
    let mut work = text.to_owned();
    let mut kinds = Vec::new();
    for (index, kind, replacement) in [
        (0, "iban", "[IBAN hidden]"),
        (1, "card", "[card hidden]"),
        (2, "email", "[email hidden]"),
        (3, "phone", "[phone hidden]"),
    ] {
        let mut count = 0;
        work = masks()[index]
            .replace_all(&work, |capture: &regex::Captures<'_>| {
                count += 1;
                if index == 1 {
                    let digits: String = capture[0]
                        .chars()
                        .filter(|ch| digit(*ch).is_some())
                        .collect();
                    if !(13..=19).contains(&digits.chars().count()) || !luhn(&digits) {
                        return capture[0].to_owned();
                    }
                }
                replacement.into()
            })
            .into_owned();
        if index == 1 {
            count = work.matches("[card hidden]").count()
        };
        if count > 0 {
            kinds.push(kind.into())
        }
    }
    (work, kinds)
}
