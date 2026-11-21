use crate::usage::{UsageContext, UsageRecord, UsageSink};
use crate::{
    ChatOptions, LlmResponse, Message, TlsVerify, TokenUsage, ToolCall, ToolSpec, calls_from_text,
    json_in_text_messages, to_wire,
};
use anyhow::{Context, Result, bail};
use async_trait::async_trait;
use reqwest::header::HeaderMap;
use serde_json::{Map, Value, json};
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime},
};

#[async_trait]
pub trait Llm: Send + Sync {
    async fn chat(
        &self,
        messages: &[Message],
        tools: &[ToolSpec],
        options: &ChatOptions,
        context: &UsageContext,
    ) -> Result<LlmResponse>;
}
#[derive(Default)]
struct Adaptation {
    tools_ok: Option<bool>,
    unsupported: BTreeSet<String>,
}
#[derive(Debug)]
pub struct StatusError {
    pub status: u16,
    pub body: String,
}
impl std::fmt::Display for StatusError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Error code: {}", self.status)?;
        // The pinned OpenAI SDK exposes only error.message in exception text.
        // Private fields such as error.param must not trigger parameter fallback.
        if let Ok(body) = serde_json::from_str::<Value>(&self.body) {
            let error = body.get("error").unwrap_or(&body);
            if let Some(message) = error
                .get("message")
                .and_then(Value::as_str)
                .filter(|message| !message.is_empty())
            {
                write!(f, " - {message}")?;
            }
        }
        Ok(())
    }
}
impl std::error::Error for StatusError {}
impl StatusError {
    pub fn legacy_type_name(&self) -> &'static str {
        match self.status {
            400 => "BadRequestError",
            401 => "AuthenticationError",
            403 => "PermissionDeniedError",
            404 => "NotFoundError",
            409 => "ConflictError",
            422 => "UnprocessableEntityError",
            429 => "RateLimitError",
            500.. => "InternalServerError",
            _ => "APIStatusError",
        }
    }
}

pub struct OpenAiCompatLlm {
    model: String,
    base_url: String,
    api_key: String,
    client: reqwest::Client,
    adaptation: Mutex<Adaptation>,
    sink: Option<Arc<dyn UsageSink>>,
}
impl OpenAiCompatLlm {
    pub fn new(
        base_url: &str,
        model: &str,
        api_key: &str,
        timeout: Duration,
        tls: TlsVerify,
        sink: Option<Arc<dyn UsageSink>>,
    ) -> Result<Self> {
        let mut client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(timeout)
            .read_timeout(timeout);
        match tls {
            TlsVerify::System => {}
            TlsVerify::Disabled => {
                client = client.danger_accept_invalid_certs(true);
            }
            TlsVerify::Bundle(path) => {
                client = client.tls_built_in_root_certs(false);
                for certificate in reqwest::Certificate::from_pem_bundle(&std::fs::read(path)?)? {
                    client = client.add_root_certificate(certificate);
                }
            }
        }
        Ok(Self {
            model: model.into(),
            base_url: format!("{}/chat/completions", base_url.trim_end_matches('/')),
            api_key: if api_key.is_empty() { "EMPTY" } else { api_key }.into(),
            client: client.build()?,
            adaptation: Mutex::new(Adaptation::default()),
            sink,
        })
    }
    fn drop_param(kwargs: &mut Map<String, Value>, key: &str) {
        if key == "max_tokens" {
            if let Some(value) = kwargs.shift_remove(key) {
                kwargs.insert("max_completion_tokens".into(), value);
            }
        } else {
            kwargs.shift_remove(key);
        }
    }
    async fn request(&self, kwargs: &Map<String, Value>) -> Result<Value> {
        for attempt in 0..3 {
            let sent = self
                .client
                .post(&self.base_url)
                .bearer_auth(&self.api_key)
                .json(kwargs)
                .send()
                .await;
            let response = match sent {
                Ok(response) => response,
                Err(error) if attempt < 2 => {
                    let _ = error;
                    tokio::time::sleep(retry_delay(attempt, None)).await;
                    continue;
                }
                Err(error) => return Err(error.into()),
            };
            let status = response.status();
            let headers = response.headers().clone();
            let text = match response.text().await {
                Ok(text) => text,
                Err(error) if attempt < 2 => {
                    let _ = error;
                    tokio::time::sleep(retry_delay(attempt, None)).await;
                    continue;
                }
                Err(error) => return Err(error.into()),
            };
            if status.is_success() {
                return serde_json::from_str(&text).context("invalid completion JSON");
            }
            if attempt < 2 && should_retry(status.as_u16(), &headers) {
                tokio::time::sleep(retry_delay(attempt, Some(&headers))).await;
                continue;
            }
            return Err(StatusError {
                status: status.as_u16(),
                body: text,
            }
            .into());
        }
        bail!("completion retry budget exhausted")
    }
    async fn create(&self, kwargs: &mut Map<String, Value>) -> Result<Value> {
        let unsupported = self
            .adaptation
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .unsupported
            .clone();
        for key in unsupported {
            Self::drop_param(kwargs, &key);
        }
        loop {
            match self.request(kwargs).await {
                Ok(value) => return Ok(value),
                Err(error) => {
                    let Some(status) = error
                        .downcast_ref::<StatusError>()
                        .filter(|error| error.status == 400)
                    else {
                        return Err(error);
                    };
                    let text = status.to_string();
                    let param = ["temperature", "max_tokens"].into_iter().find(|param| {
                        text.contains(&format!("'{param}'"))
                            || text.contains(&format!("\"{param}\""))
                    });
                    let Some(param) = param else {
                        return Err(error);
                    };
                    let mut state = self
                        .adaptation
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                    if state.unsupported.contains(param) || !kwargs.contains_key(param) {
                        return Err(error);
                    }
                    state.unsupported.insert(param.into());
                    tracing::warn!(
                        "model {} rejected `{param}`; adapting the request",
                        self.model
                    );
                    Self::drop_param(kwargs, param);
                }
            }
        }
    }
    async fn record(
        &self,
        messages: &[Message],
        tools: &[ToolSpec],
        response: Option<&LlmResponse>,
        started: Instant,
        context: &UsageContext,
        success: bool,
    ) {
        if let Some(sink) = &self.sink {
            let record = UsageRecord::from_call(
                &self.model,
                messages,
                tools,
                response,
                started,
                context,
                success,
            );
            if sink.write(&record).await.is_err() {
                tracing::warn!("llm usage not recorded");
            }
        }
    }
}

#[async_trait]
impl Llm for OpenAiCompatLlm {
    async fn chat(
        &self,
        messages: &[Message],
        tools: &[ToolSpec],
        options: &ChatOptions,
        context: &UsageContext,
    ) -> Result<LlmResponse> {
        let wire: Vec<_> = messages.iter().map(to_wire).collect();
        let mut kwargs = Map::new();
        kwargs.insert("model".into(), json!(self.model));
        kwargs.insert("messages".into(), json!(wire));
        kwargs.insert("temperature".into(), json!(options.temperature));
        if let Some(maximum) = options.max_tokens {
            kwargs.insert("max_tokens".into(), json!(maximum));
        }
        if !tools.is_empty() {
            kwargs.insert("tools".into(),json!(tools.iter().map(|tool|json!({"type":"function","function":{"name":tool.name,"description":tool.description,"parameters":tool.parameters}})).collect::<Vec<_>>()));
        }
        let started = Instant::now();
        let mut use_tools = !tools.is_empty()
            && self
                .adaptation
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .tools_ok
                != Some(false);
        if !tools.is_empty() && !use_tools {
            kwargs.shift_remove("tools");
            kwargs.insert(
                "messages".into(),
                json!(json_in_text_messages(&wire, tools)),
            );
        }
        let mut completion = self.create(&mut kwargs).await;
        if let Err(error) = &completion {
            let rejected = error.downcast_ref::<StatusError>().is_some_and(|error| {
                error.status == 400 && error.to_string().to_lowercase().contains("tool")
            });
            let unknown = self
                .adaptation
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .tools_ok
                .is_none();
            if !tools.is_empty() && use_tools && unknown && rejected {
                tracing::warn!(
                    "endpoint rejected `tools` ({error}); using the JSON-in-text path from now on"
                );
                self.adaptation
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .tools_ok = Some(false);
                kwargs.shift_remove("tools");
                kwargs.insert(
                    "messages".into(),
                    json!(json_in_text_messages(&wire, tools)),
                );
                use_tools = false;
                completion = self.create(&mut kwargs).await;
            }
        }
        let completion = match completion {
            Ok(completion) => completion,
            Err(error) => {
                self.record(messages, tools, None, started, context, false)
                    .await;
                return Err(error);
            }
        };
        if use_tools {
            self.adaptation
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .tools_ok = Some(true);
        }
        let message = completion
            .get("choices")
            .and_then(Value::as_array)
            .and_then(|choices| choices.first())
            .and_then(|choice| choice.get("message"))
            .context("completion has no first message")?;
        let mut calls = Vec::new();
        for call in message
            .get("tool_calls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let function = &call["function"];
            let raw = function.get("arguments").and_then(Value::as_str);
            let parsed = serde_json::from_str::<Value>(
                raw.filter(|value| !value.is_empty()).unwrap_or("{}"),
            )
            .ok();
            let arguments = parsed
                .and_then(|value| value.as_object().cloned())
                .unwrap_or_else(|| Map::from_iter([("_raw".into(), json!(raw))]));
            calls.push(ToolCall {
                id: call["id"].as_str().context("tool call has no id")?.into(),
                name: function["name"]
                    .as_str()
                    .context("tool call has no name")?
                    .into(),
                arguments,
            });
        }
        let content = message
            .get("content")
            .filter(|content| !content.is_null())
            .map(|content| content.as_str().context("completion content must be text"))
            .transpose()?
            .map(str::to_owned);
        if !tools.is_empty() && !use_tools && calls.is_empty() {
            calls = calls_from_text(content.as_deref(), tools);
        }
        let usage = completion
            .get("usage")
            .filter(|usage| !usage.is_null())
            .map(|usage| -> Result<TokenUsage> {
                fn count(value: &Value) -> Result<i64> {
                    if value.is_null() {
                        return Ok(0);
                    }
                    if let Some(value) = value.as_bool() {
                        return Ok(i64::from(value));
                    }
                    if let Some(value) = value.as_i64() {
                        return Ok(value);
                    }
                    if let Some(value) = value.as_f64().filter(|value| {
                        value.fract() == 0.0
                            && *value >= i64::MIN as f64
                            && *value < (i64::MAX as f64)
                    }) {
                        return Ok(value as i64);
                    }
                    if let Some(text) = value.as_str() {
                        let text = text.trim();
                        if let Some((whole, fraction)) = text.split_once('.') {
                            if !fraction.is_empty() && fraction.bytes().all(|byte| byte == b'0') {
                                return whole.parse().context("invalid token usage");
                            }
                        } else if !text.contains("__")
                            && !text.starts_with('_')
                            && !text.ends_with('_')
                        {
                            return text.replace('_', "").parse().context("invalid token usage");
                        }
                    }
                    bail!("invalid token usage")
                }
                Ok(TokenUsage {
                    prompt_tokens: count(&usage["prompt_tokens"])?,
                    completion_tokens: count(&usage["completion_tokens"])?,
                })
            })
            .transpose()?;
        let response = LlmResponse {
            content,
            tool_calls: calls,
            usage,
        };
        self.record(messages, tools, Some(&response), started, context, true)
            .await;
        Ok(response)
    }
}

pub fn legacy_error_name(error: &anyhow::Error) -> &'static str {
    if let Some(error) = error.downcast_ref::<StatusError>() {
        return error.legacy_type_name();
    }
    if let Some(error) = error.downcast_ref::<reqwest::Error>() {
        return if error.is_timeout() {
            "APITimeoutError"
        } else {
            "APIConnectionError"
        };
    }
    match error.to_string().as_str() {
        "completion has no first message" => "IndexError",
        "invalid token usage"
        | "completion content must be text"
        | "tool call has no id"
        | "tool call has no name" => "ValidationError",
        _ => "RuntimeError",
    }
}

fn retry_after(headers: &HeaderMap) -> Option<f64> {
    if let Some(value) = headers
        .get("retry-after-ms")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<f64>().ok())
    {
        return Some(value / 1000.0);
    }
    let value = headers.get("retry-after")?.to_str().ok()?;
    value.parse().ok().or_else(|| {
        httpdate::parse_http_date(value).ok().map(|time| {
            time.duration_since(SystemTime::now())
                .map_or(0.0, |duration| duration.as_secs_f64())
        })
    })
}
fn should_retry(status: u16, headers: &HeaderMap) -> bool {
    if retry_after(headers).is_some_and(|delay| delay.is_finite() && delay > 120.0) {
        return false;
    }
    match headers
        .get("x-should-retry")
        .and_then(|value| value.to_str().ok())
    {
        Some("true") => true,
        Some("false") => false,
        _ => matches!(status, 408 | 409 | 429) || status >= 500,
    }
}
fn retry_delay(attempt: u32, headers: Option<&HeaderMap>) -> Duration {
    if let Some(delay) = headers
        .and_then(retry_after)
        .filter(|delay| delay.is_finite() && *delay > 0.0 && *delay <= 120.0)
    {
        return Duration::from_secs_f64(delay);
    }
    let mut bytes = [0u8; 8];
    let _ = getrandom::fill(&mut bytes);
    let random = (u64::from_le_bytes(bytes) >> 11) as f64 / 9_007_199_254_740_992.0;
    Duration::from_secs_f64(
        (0.5 * 2_f64.powi(attempt.min(10) as i32)).min(8.0) * (1.0 - 0.25 * random),
    )
}
