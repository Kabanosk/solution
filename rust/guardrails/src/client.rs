use crate::{
    BOT_OUTPUT, BOT_OUTPUT_FINANCE, TOOL_RESULT, USER_INPUT, chars, messages, parse_response,
    verdict,
};
use async_trait::async_trait;
use scout_domain::Verdict;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::Path,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};

#[async_trait]
pub trait CheckClient: Send + Sync {
    fn enabled(&self) -> bool;
    async fn check(&self, config: &str, messages: &[Value]) -> Verdict;
}
#[derive(Clone)]
pub struct HttpGuardrails {
    pub url: String,
    pub token: String,
    pub model: String,
    pub timeout: Duration,
    client: reqwest::Client,
}
static WARM: OnceLock<Mutex<HashMap<String, (bool, Instant)>>> = OnceLock::new();
impl HttpGuardrails {
    pub fn new(url: &str, token: &str, model: &str, ca: Option<&Path>) -> anyhow::Result<Self> {
        let mut builder = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none());
        if let Some(path) = ca.filter(|path| path.is_file()) {
            for certificate in reqwest::Certificate::from_pem_bundle(&std::fs::read(path)?)? {
                builder = builder.add_root_certificate(certificate);
            }
        }
        Ok(Self {
            url: url.trim_end_matches('/').into(),
            token: token.into(),
            model: model.into(),
            timeout: Duration::from_secs(5),
            client: builder.build()?,
        })
    }
    async fn request(
        &self,
        config: &str,
        messages: &[Value],
        timeout: Duration,
    ) -> (Verdict, bool) {
        if self.url.is_empty() {
            return (verdict("disabled"), false);
        }
        let started = Instant::now();
        let mut request = self
            .client
            .post(format!("{}/v1/guardrail/checks", self.url))
            .json(
                &json!({"model":self.model,"messages":messages,"guardrails":{"config_id":config}}),
            )
            .timeout(timeout);
        if !self.token.is_empty() {
            request = request.bearer_auth(&self.token)
        }
        let result: Result<Value,(String,bool)> = async {
            let response = request.send().await.map_err(|error| {
                let timed_out=error.is_timeout();
                let detail=if timed_out { "ReadTimeout: ".to_owned() } else if error.is_connect() { "ConnectError: All connection attempts failed".to_owned() } else { format!("RequestError: {error}") };
                (detail,timed_out)
            })?;
            let status=response.status();
            if !status.is_success() {
                let kind=match status.as_u16()/100 {1=>"Informational response",3=>"Redirect response",4=>"Client error",5=>"Server error",_=>"Invalid status code"};
                let location=if status.is_redirection(){response.headers().get("location").and_then(|value|value.to_str().ok()).map(|value|format!("Redirect location: '{value}'\n")).unwrap_or_default()}else{String::new()};
                return Err((format!("HTTPStatusError: {kind} '{} {}' for url '{}'\n{location}For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/{}",status.as_u16(),status.canonical_reason().unwrap_or(""),response.url(),status.as_u16()),false));
            }
            let body=response.bytes().await.map_err(|error|(format!("ReadError: {error}"),false))?;
            serde_json::from_slice::<Value>(&body).map_err(|error| {
                let text=String::from_utf8_lossy(&body);
                let position=text.char_indices().find(|(_,ch)|!ch.is_whitespace()).map(|(at,_)|at).unwrap_or(text.len());
                let before=&text[..position];
                let line=before.chars().filter(|ch|*ch=='\n').count()+1;
                let column=before.rsplit('\n').next().unwrap_or("").chars().count()+1;
                let detail=if text.trim().is_empty() || !text.trim_start().starts_with(['{','[','"','0','1','2','3','4','5','6','7','8','9','-','t','f','n']) {format!("Expecting value: line {line} column {column} (char {})",before.chars().count())}else{error.to_string()};
                (format!("JSONDecodeError: {detail}"),false)
            })
        }.await;
        match result {
            Ok(value) if value.is_object() => {
                let mut result = parse_response(&value);
                result.latency = started.elapsed().as_secs_f64();
                (result, false)
            }
            Ok(value) => {
                // The source parses JSON inside the fail-open boundary, then accesses .get outside it.
                // Keep a diagnosable unavailable result for this invalid provider shape.
                let name=if value.is_array(){"list"}else if value.is_null(){"NoneType"}else if value.is_string(){"str"}else if value.is_boolean(){"bool"}else{"int"};
                (Verdict {latency:started.elapsed().as_secs_f64(),detail:format!("AttributeError: '{name}' object has no attribute 'get'"),..verdict("unavailable")},false)
            }
            Err((detail,timed_out)) => (Verdict{latency:started.elapsed().as_secs_f64(),detail:chars(&detail,200),..verdict("unavailable")},timed_out),
        }
    }

    fn warm_done(&self) {
        if let Ok(mut state) = WARM.get_or_init(Default::default).lock() {
            state
                .entry(self.url.clone())
                .and_modify(|state| state.0 = false);
        }
    }
    async fn warm_all(&self, timeout: Duration) -> Verdict {
        let (first, _) = self
            .request(
                USER_INPUT,
                &messages(USER_INPUT, &["hello".into()]),
                timeout,
            )
            .await;
        for config in [TOOL_RESULT, BOT_OUTPUT, BOT_OUTPUT_FINANCE] {
            let _ = self
                .request(config, &messages(config, &["hello".into()]), timeout)
                .await;
        }
        self.warm_done();
        first
    }
    pub async fn warm_up(&self, timeout: Duration) -> Verdict {
        if let Ok(mut state) = WARM.get_or_init(Default::default).lock() {
            state.insert(self.url.clone(), (true, Instant::now()));
        }
        self.warm_all(timeout).await
    }
    pub fn warm_background(&self) -> bool {
        let Ok(mut state) = WARM.get_or_init(Default::default).lock() else {
            return false;
        };
        if state.get(&self.url).is_some_and(|(warming, started)| {
            *warming || started.elapsed() < Duration::from_secs(60)
        }) {
            return false;
        };
        state.insert(self.url.clone(), (true, Instant::now()));
        drop(state);
        let client = self.clone();
        tokio::spawn(async move {
            client.warm_all(Duration::from_secs(30)).await;
        });
        true
    }
}
#[async_trait]
impl CheckClient for HttpGuardrails {
    fn enabled(&self) -> bool {
        !self.url.is_empty()
    }
    async fn check(&self, config: &str, messages: &[Value]) -> Verdict {
        let (verdict, timed_out) = self.request(config, messages, self.timeout).await;
        if timed_out {
            self.warm_background();
        }
        verdict
    }
}
