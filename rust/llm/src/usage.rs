use crate::{LlmResponse, Message, ToolSpec, python_json};
use anyhow::Result;
use async_trait::async_trait;
use chrono::{DateTime, Utc};
use scout_platform::{Database, Tenant};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    sync::Mutex,
    time::{Duration, Instant},
};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UsageContext {
    pub event_id: Option<String>,
    pub event_type: Option<String>,
    pub purpose: Option<String>,
    pub decision_id: Option<String>,
}
impl UsageContext {
    pub fn merged(&self, inner: &Self) -> Self {
        Self {
            event_id: inner.event_id.clone().or_else(|| self.event_id.clone()),
            event_type: inner.event_type.clone().or_else(|| self.event_type.clone()),
            purpose: inner.purpose.clone().or_else(|| self.purpose.clone()),
            decision_id: inner
                .decision_id
                .clone()
                .or_else(|| self.decision_id.clone()),
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageRecord {
    pub ts: DateTime<Utc>,
    pub model: String,
    pub prompt_tokens: i64,
    pub completion_tokens: i64,
    pub latency_ms: f64,
    pub estimated: bool,
    pub success: bool,
    #[serde(flatten)]
    pub context: UsageContext,
}
pub fn estimate_tokens(text: &str) -> i64 {
    text.chars().count().div_ceil(4) as i64
}
pub fn message_text(messages: &[Message], tools: &[ToolSpec]) -> String {
    let mut parts = Vec::new();
    for message in messages {
        parts.push(message.content.clone().unwrap_or_default());
        for call in &message.tool_calls {
            parts.push(format!(
                "{}{}",
                call.name,
                python_json(&Value::Object(call.arguments.clone()))
            ));
        }
    }
    for tool in tools {
        parts.push(python_json(
            &json!({"name":tool.name,"description":tool.description,"parameters":tool.parameters}),
        ));
    }
    parts.join("\n")
}
pub fn response_text(response: &LlmResponse) -> String {
    let mut parts = vec![response.content.clone().unwrap_or_default()];
    for call in &response.tool_calls {
        parts.push(format!(
            "{}{}",
            call.name,
            python_json(&Value::Object(call.arguments.clone()))
        ));
    }
    parts.join("\n")
}
impl UsageRecord {
    pub fn from_call(
        model: &str,
        messages: &[Message],
        tools: &[ToolSpec],
        response: Option<&LlmResponse>,
        started: Instant,
        context: &UsageContext,
        success: bool,
    ) -> Self {
        let usage = response.and_then(|response| response.usage.as_ref());
        Self {
            ts: Utc::now(),
            model: model.into(),
            prompt_tokens: usage.map_or_else(
                || estimate_tokens(&message_text(messages, tools)),
                |usage| usage.prompt_tokens,
            ),
            completion_tokens: usage.map_or_else(
                || response.map_or(0, |response| estimate_tokens(&response_text(response))),
                |usage| usage.completion_tokens,
            ),
            latency_ms: started.elapsed().as_secs_f64() * 1000.0,
            estimated: usage.is_none(),
            success,
            context: context.clone(),
        }
    }
}

#[async_trait]
pub trait UsageSink: Send + Sync {
    async fn write(&self, record: &UsageRecord) -> Result<()>;
    async fn link(&self, _event_id: &str, _decision_id: &str) -> Result<()> {
        Ok(())
    }
}

/// Explicit tenant binding replaces implicit thread context and cannot fall back to public.
pub struct DbUsageSink {
    db: Database,
    tenant: Tenant,
    blocked_until: Mutex<Option<Instant>>,
    cooldown: Duration,
}
impl DbUsageSink {
    pub fn new(db: Database, tenant: Tenant) -> Self {
        Self {
            db: db.with_connect_timeout(Duration::from_secs(3)),
            tenant,
            blocked_until: Mutex::new(None),
            cooldown: Duration::from_secs(30),
        }
    }
    fn blocked(&self) -> bool {
        self.blocked_until
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .is_some_and(|deadline| Instant::now() < deadline)
    }
    fn failed(&self) {
        *self
            .blocked_until
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) =
            Some(Instant::now() + self.cooldown);
        tracing::warn!("llm usage not recorded (database skipped for 30 s)");
    }
}
#[async_trait]
impl UsageSink for DbUsageSink {
    async fn write(&self, record: &UsageRecord) -> Result<()> {
        if self.blocked() {
            return Ok(());
        }
        let result=async {
            let conn=self.db.scoped(&self.tenant).await?;
            conn.execute("INSERT INTO llm_usage(ts,model,prompt_tokens,completion_tokens,latency_ms,estimated,success,event_id,event_type,purpose,decision_id) VALUES ($1,$2,$3::bigint,$4::bigint,$5,$6,$7,$8,$9,$10,$11)",
                &[&record.ts,&record.model,&record.prompt_tokens,&record.completion_tokens,&record.latency_ms,&record.estimated,&record.success,&record.context.event_id,&record.context.event_type,&record.context.purpose,&record.context.decision_id]).await?;
            Ok::<(),anyhow::Error>(())
        }.await;
        if result.is_err() {
            self.failed();
        }
        Ok(())
    }
    async fn link(&self, event_id: &str, decision_id: &str) -> Result<()> {
        if self.blocked() {
            return Ok(());
        }
        let result = async {
            let conn = self.db.scoped(&self.tenant).await?;
            conn.execute(
                "UPDATE llm_usage SET decision_id=$1 WHERE event_id=$2 AND decision_id IS NULL",
                &[&decision_id, &event_id],
            )
            .await?;
            Ok::<(), anyhow::Error>(())
        }
        .await;
        if result.is_err() {
            self.failed();
        }
        Ok(())
    }
}
