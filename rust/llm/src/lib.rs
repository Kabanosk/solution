//! One model-agnostic asynchronous chat interface, including the source's tool/parameter fallbacks.
pub mod client;
pub mod usage;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

fn parameters() -> Value {
    json!({"type":"object","properties":{}})
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    #[serde(default = "parameters")]
    pub parameters: Value,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub arguments: Map<String, Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub tool_calls: Vec<ToolCall>,
    #[serde(default)]
    pub tool_call_id: Option<String>,
}
impl Message {
    pub fn new(role: Role, content: impl Into<String>) -> Self {
        Self {
            role,
            content: Some(content.into()),
            tool_calls: Vec::new(),
            tool_call_id: None,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct TokenUsage {
    #[serde(default)]
    pub prompt_tokens: i64,
    #[serde(default)]
    pub completion_tokens: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LlmResponse {
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub tool_calls: Vec<ToolCall>,
    #[serde(default)]
    pub usage: Option<TokenUsage>,
}
impl LlmResponse {
    pub fn as_message(&self) -> Message {
        Message {
            role: Role::Assistant,
            content: self.content.clone(),
            tool_calls: self.tool_calls.clone(),
            tool_call_id: None,
        }
    }
}
#[derive(Debug, Clone)]
pub struct ChatOptions {
    pub temperature: f64,
    pub max_tokens: Option<i64>,
}
impl Default for ChatOptions {
    fn default() -> Self {
        Self {
            temperature: 0.0,
            max_tokens: None,
        }
    }
}

/// Python's ensure_ascii JSON strings are part of existing prompts and usage estimates.
fn py_string(text: &str) -> String {
    let mut output = String::from("\"");
    for ch in text.chars() {
        match ch {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{08}' => output.push_str("\\b"),
            '\t' => output.push_str("\\t"),
            '\n' => output.push_str("\\n"),
            '\u{0c}' => output.push_str("\\f"),
            '\r' => output.push_str("\\r"),
            ch if !(' '..='\u{7e}').contains(&ch) => {
                for unit in ch.encode_utf16(&mut [0; 2]) {
                    output.push_str(&format!("\\u{unit:04x}"));
                }
            }
            ch => output.push(ch),
        }
    }
    output.push('"');
    output
}

fn py_float(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into();
    }
    let mut buffer = ryu::Buffer::new();
    let raw = buffer.format_finite(value);
    let (sign, raw) = raw.strip_prefix('-').map_or(("", raw), |raw| ("-", raw));
    let (mantissa, exp) = raw
        .split_once('e')
        .map_or((raw, 0), |(a, b)| (a, b.parse::<i32>().unwrap_or(0)));
    let decimals = mantissa
        .split_once('.')
        .map_or(0, |(_, part)| part.len() as i32);
    let mut digits = mantissa.replace('.', "");
    let mut power = exp - decimals;
    while digits.ends_with('0') {
        digits.pop();
        power += 1;
    }
    let digits = digits.trim_start_matches('0');
    let scientific = power + digits.len() as i32 - 1;
    if !(-4..16).contains(&scientific) {
        let mut out = format!("{sign}{}", &digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push_str(&format!(
            "e{}{:02}",
            if scientific < 0 { '-' } else { '+' },
            scientific.abs()
        ));
        out
    } else {
        let point = digits.len() as i32 + power;
        if point <= 0 {
            format!("{sign}0.{}{digits}", "0".repeat((-point) as usize))
        } else if point as usize >= digits.len() {
            format!(
                "{sign}{digits}{}.0",
                "0".repeat(point as usize - digits.len())
            )
        } else {
            format!(
                "{sign}{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        }
    }
}

pub fn python_json(value: &Value) -> String {
    match value {
        Value::Null => "null".into(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => {
            let raw = value.to_string();
            if raw.contains(['.', 'e', 'E']) {
                match raw.parse::<f64>() {
                    Ok(number) if number.is_finite() => py_float(number),
                    Ok(number) if number.is_sign_negative() => "-Infinity".into(),
                    _ => "Infinity".into(),
                }
            } else if raw == "-0" {
                "0".into()
            } else {
                raw
            }
        }
        Value::String(value) => py_string(value),
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(python_json)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| format!("{}: {}", py_string(key), python_json(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

pub fn to_wire(message: &Message) -> Value {
    if message.role == Role::Tool {
        return json!({"role":"tool","tool_call_id":message.tool_call_id,"content":message.content.as_deref().unwrap_or("")});
    }
    let mut value = json!({"role":message.role,"content":message.content});
    if !message.tool_calls.is_empty() {
        value["tool_calls"]=json!(message.tool_calls.iter().map(|call|json!({"id":call.id,"type":"function", "function":{"name":call.name,"arguments":python_json(&Value::Object(call.arguments.clone()))}})).collect::<Vec<_>>());
    }
    value
}

pub fn calls_from_text(content: Option<&str>, tools: &[ToolSpec]) -> Vec<ToolCall> {
    let text = content.unwrap_or("");
    let data = text
        .find('{')
        .zip(text.rfind('}'))
        .filter(|(start, end)| start <= end)
        .and_then(|(start, end)| serde_json::from_str::<Value>(&text[start..=end]).ok());
    let Some(Value::Object(data)) = data else {
        return Vec::new();
    };
    if data.get("name").is_some_and(|name| !name.is_string()) {
        return Vec::new();
    }
    if let Some(name) = data.get("name").and_then(Value::as_str)
        && tools.iter().any(|tool| tool.name == name)
        && let Some(arguments) = data.get("arguments").and_then(Value::as_object)
    {
        return vec![ToolCall {
            id: "call_text".into(),
            name: name.into(),
            arguments: arguments.clone(),
        }];
    }
    if tools.len() == 1 {
        vec![ToolCall {
            id: "call_text".into(),
            name: tools[0].name.clone(),
            arguments: data,
        }]
    } else {
        Vec::new()
    }
}

pub fn json_in_text_messages(wire: &[Value], tools: &[ToolSpec]) -> Vec<Value> {
    let how = if tools.len() == 1 {
        let tool = &tools[0];
        format!(
            "Answer with only one JSON object: the arguments of the function `{}` ({}). Its JSON schema: {}",
            tool.name,
            tool.description,
            python_json(&tool.parameters)
        )
    } else {
        let listing = tools
            .iter()
            .map(|tool| {
                format!(
                    "`{}` ({}) schema {}",
                    tool.name,
                    tool.description,
                    python_json(&tool.parameters)
                )
            })
            .collect::<Vec<_>>()
            .join("; ");
        format!(
            "Answer with only one JSON object {{\"name\": <function>, \"arguments\": {{...}}}} calling exactly one of these functions: {listing}"
        )
    };
    let mut result = vec![json!({"role":"system","content":how})];
    result.extend_from_slice(wire);
    result
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TlsVerify {
    System,
    Disabled,
    Bundle(String),
}
pub fn tls_verify_setting(value: &str) -> TlsVerify {
    let value = value.trim();
    match value.to_lowercase().as_str() {
        "" | "1" | "true" | "yes" | "on" => TlsVerify::System,
        "0" | "false" | "no" | "off" => TlsVerify::Disabled,
        _ => TlsVerify::Bundle(value.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_prompt_json_preserves_ascii_escaping_and_float_thresholds() {
        assert_eq!(
            python_json(&json!("é😀\u{7f}")),
            r#""\u00e9\ud83d\ude00\u007f""#
        );
        assert_eq!(
            python_json(&json!([1e-6, 1e-4, 1e15, 1e16, -0.0])),
            "[1e-06, 0.0001, 1000000000000000.0, 1e+16, -0.0]"
        );
    }
    #[test]
    fn invalid_function_name_is_not_reinterpreted_as_single_tool_arguments() {
        let tools = vec![ToolSpec {
            name: "one".into(),
            description: "Read".into(),
            parameters: parameters(),
        }];
        assert!(calls_from_text(Some(r#"{"name":[],"arguments":{}}"#), &tools).is_empty());
        assert_eq!(
            calls_from_text(Some(r#"before {"x":1} after"#), &tools)[0].name,
            "one"
        );
    }
}
