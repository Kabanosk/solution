//! Source-shaped records and small async provider/guard/notifier interfaces.
//! No provider, worker, configuration or database implementation is hidden here.
#![forbid(unsafe_code)]
use async_trait::async_trait;
pub use bigdecimal::BigDecimal as Decimal;
use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{fmt, sync::Arc};
pub type Result<T> = std::result::Result<T, RuntimeError>;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuntimeError {
    pub legacy_error_name: String,
    pub message: String,
    pub retryable: bool,
}
impl RuntimeError {
    pub fn new(name: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            legacy_error_name: name.into(),
            message: message.into(),
            retryable: true,
        }
    }
    pub fn permanent(name: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            legacy_error_name: name.into(),
            message: message.into(),
            retryable: false,
        }
    }
    pub fn source_text(&self) -> String {
        format!("{}: {}", self.legacy_error_name, self.message)
    }
}
impl fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(f)
    }
}
impl std::error::Error for RuntimeError {}
fn eur() -> String {
    "EUR".into()
}
fn transfer() -> String {
    "transfer".into()
}
fn db() -> String {
    "db".into()
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BankTxn {
    pub id: String,
    pub booked_at: DateTime<Utc>,
    pub amount: Decimal,
    #[serde(default = "eur")]
    pub currency: String,
    pub counterparty: String,
    pub reference: String,
    #[serde(default = "transfer", rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub card_last4: Option<String>,
    #[serde(default)]
    pub holder_employee_id: Option<String>,
}
impl BankTxn {
    pub fn outgoing(&self) -> bool {
        self.amount < 0
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MailHit {
    pub id: String,
    pub mailbox: String,
    pub sender: String,
    pub subject: String,
    #[serde(default)]
    pub date: Option<DateTime<Utc>>,
    #[serde(default)]
    pub snippet: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub attachments: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatHit {
    pub channel: String,
    pub ts: String,
    #[serde(default)]
    pub user: Option<String>,
    #[serde(default)]
    pub user_name: Option<String>,
    pub text: String,
    #[serde(default)]
    pub permalink: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvoiceDoc {
    pub number: String,
    pub supplier: String,
    pub issue_date: NaiveDate,
    pub total: Decimal,
    #[serde(default)]
    pub currency: Option<String>,
    #[serde(default)]
    pub lines: Vec<Value>,
    #[serde(default = "db")]
    pub source: String,
    #[serde(default)]
    pub evidence: Option<Value>,
    #[serde(default)]
    pub invoice_id: Option<String>,
    #[serde(default)]
    pub transaction_id: Option<String>,
}
#[derive(Debug, Clone)]
pub struct InvoiceSearch {
    pub number: Option<String>,
    pub supplier: Option<String>,
    pub amount: Option<Decimal>,
    pub around: Option<NaiveDate>,
    pub days: i64,
    pub task_id: Option<String>,
}
impl Default for InvoiceSearch {
    fn default() -> Self {
        Self {
            number: None,
            supplier: None,
            amount: None,
            around: None,
            days: 7,
            task_id: None,
        }
    }
}
#[derive(Debug, Clone)]
pub struct ChatSearch {
    pub since: DateTime<Utc>,
    pub invoice_number: Option<String>,
    pub supplier: Option<String>,
    pub amount: Option<Decimal>,
    pub task_id: Option<String>,
}
#[async_trait]
pub trait Bank: Send + Sync {
    fn name(&self) -> &str;
    fn label(&self) -> &str {
        self.name()
    }
    async fn list_transactions(&self, since: DateTime<Utc>) -> Result<Vec<BankTxn>>;
}
#[async_trait]
pub trait Invoicing: Send + Sync {
    fn name(&self) -> &str;
    fn label(&self) -> &str {
        self.name()
    }
    async fn find(&self, query: &InvoiceSearch) -> Result<Vec<InvoiceDoc>>;
    async fn file(
        &self,
        doc: &InvoiceDoc,
        transaction_id: &str,
        task_id: Option<&str>,
    ) -> Result<(bool, String)>;
}
#[async_trait]
pub trait Files: Send + Sync {
    fn name(&self) -> &str;
    async fn search(
        &self,
        owner: &str,
        terms: &[String],
        task_id: Option<&str>,
    ) -> Result<Vec<Value>>;
    async fn read(&self, owner: &str, path: &str, task_id: Option<&str>)
    -> Result<Option<Vec<u8>>>;
}
#[async_trait]
pub trait Mail: Send + Sync {
    fn name(&self) -> &str;
    async fn available(&self) -> Result<bool>;
    async fn search(
        &self,
        terms: &[String],
        since: DateTime<Utc>,
        task_id: Option<&str>,
    ) -> Result<Vec<MailHit>>;
    async fn attachment(
        &self,
        hit_id: &str,
        name: &str,
        task_id: Option<&str>,
    ) -> Result<Option<Vec<u8>>>;
    async fn changes(&self, cursor: &Value) -> Result<(Vec<MailHit>, Value)>;
    fn pdf_search_capable(&self) -> bool {
        false
    }
    async fn pdf_search(
        &self,
        terms: &[String],
        since: DateTime<Utc>,
        task_id: Option<&str>,
    ) -> Result<Vec<MailHit>> {
        self.search(terms, since, task_id).await
    }
}
#[async_trait]
pub trait Chat: Send + Sync {
    fn name(&self) -> &str;
    async fn search(&self, query: &ChatSearch) -> Result<Vec<ChatHit>>;
}
#[async_trait]
pub trait KsefLookup: Send + Sync {
    fn name(&self) -> &str;
    fn label(&self) -> &str {
        self.name()
    }
    async fn find(
        &self,
        number: &str,
        task_id: Option<&str>,
        days: i64,
        attempts: usize,
    ) -> Result<Vec<(Value, String)>>;
}
#[derive(Clone)]
pub struct Connectors {
    pub bank: Arc<dyn Bank>,
    pub invoicing: Arc<dyn Invoicing>,
    pub files: Arc<dyn Files>,
    pub mail: Arc<dyn Mail>,
    pub chat: Arc<dyn Chat>,
    pub ksef: Option<Arc<dyn KsefLookup>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Button {
    pub action_id: String,
    pub label: String,
    pub value: String,
}
#[derive(Debug, Clone, Default)]
pub struct DmOptions {
    pub task_id: Option<String>,
    pub buttons: Vec<Button>,
    pub thread: Option<String>,
}
#[async_trait]
pub trait Notifier: Send + Sync {
    async fn dm(
        &self,
        employee_id: &str,
        text: &str,
        level: &str,
        options: &DmOptions,
    ) -> Result<Option<String>>;
    async fn update(&self, reference: &str, text: &str, level: &str) -> Result<()>;
    async fn track(&self, task_id: &str, status_ref: &str) -> Result<()>;
    async fn upload(
        &self,
        employee_id: &str,
        files: &[(String, Vec<u8>)],
        comment: &str,
    ) -> Result<Option<String>>;
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Verdict {
    pub status: String,
    pub activated_rails: Vec<String>,
    pub latency: f64,
    pub detail: String,
}
impl Verdict {
    pub fn blocked(&self) -> bool {
        self.status == "blocked"
    }
    pub fn failed(&self) -> bool {
        matches!(self.status.as_str(), "error" | "unavailable")
    }
    pub fn rails(&self) -> String {
        self.activated_rails.join(", ")
    }
}
#[derive(Debug, Clone, Default)]
pub struct GuardOptions {
    pub task_id: Option<String>,
    pub requester: Option<String>,
    pub source: String,
    pub role: Option<String>,
}
#[async_trait]
pub trait Guard: Send + Sync {
    async fn active(&self) -> bool;
    async fn screen_human(
        &self,
        person: Option<&str>,
        text: &str,
        options: &GuardOptions,
    ) -> Option<String>;
    async fn screen_tool(
        &self,
        tool: &str,
        content: &str,
        result: Option<&Value>,
        options: &GuardOptions,
    ) -> String;
    async fn screen_document(&self, text: &str, options: &GuardOptions) -> Verdict;
    async fn screen_output(&self, text: &str, options: &GuardOptions) -> String;
    async fn screen_history(&self, turns: &[Value], options: &GuardOptions) -> Vec<Value>;
    async fn alert_outside(&self, key: &str, label: &str, rails: &str, task_id: Option<&str>);
}
#[derive(Debug, Clone)]
pub struct AuditRecord {
    pub actor: String,
    pub action: String,
    pub target: String,
    pub resource: String,
    pub decision: String,
    pub task_id: Option<String>,
    pub requester: Option<String>,
    pub role: Option<String>,
    pub policy: Option<String>,
    pub reason: Option<String>,
}
#[async_trait]
pub trait AuditSink: Send + Sync {
    async fn record(&self, record: &AuditRecord) -> Result<()>;
}
#[async_trait]
pub trait People: Send + Sync {
    async fn employee(&self, id: &str) -> Result<Option<Value>>;
    async fn by_slack_id(&self, id: &str) -> Result<Option<Value>>;
    async fn all(&self) -> Result<Vec<Value>>;
    async fn name(&self, id: Option<&str>) -> Result<String>;
    async fn full_name(&self, id: &str) -> Result<String>;
    async fn role(&self, id: &str) -> Result<String>;
    async fn on_vacation(&self, id: &str, day: NaiveDate) -> Result<bool>;
    async fn manager(&self, id: &str) -> Result<Option<String>>;
    async fn chain(&self, id: &str) -> Result<Vec<String>>;
    async fn first_available(&self, id: &str, day: NaiveDate) -> Result<(String, Vec<String>)>;
}

pub trait Clock: Send + Sync {
    fn now(&self) -> DateTime<Utc>;
}
impl<F> Clock for F
where
    F: Fn() -> DateTime<Utc> + Send + Sync,
{
    fn now(&self) -> DateTime<Utc> {
        self()
    }
}
pub struct UtcClock;
impl Clock for UtcClock {
    fn now(&self) -> DateTime<Utc> {
        Utc::now()
    }
}
impl AuditRecord {
    pub fn new(
        actor: impl Into<String>,
        action: impl Into<String>,
        target: impl Into<String>,
        resource: impl Into<String>,
    ) -> Self {
        Self {
            actor: actor.into(),
            action: action.into(),
            target: target.into(),
            resource: resource.into(),
            decision: "ALLOW".into(),
            task_id: None,
            requester: None,
            role: None,
            policy: None,
            reason: None,
        }
    }
}
/// Original loops.context.eur: absolute EUR, grouping, two HALF_EVEN places.
pub fn format_eur(value: &Decimal) -> String {
    let text = scout_decimal::fixed(&value.abs(), 2, bigdecimal::RoundingMode::HalfEven);
    let (whole, fraction) = text.split_once('.').unwrap_or((&text, "00"));
    let mut output = String::from("€");
    for (i, ch) in whole.chars().enumerate() {
        if i > 0 && (whole.len() - i).is_multiple_of(3) {
            output.push(',')
        }
        output.push(ch)
    }
    format!("{output}.{fraction}")
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn source_bank_dto_preserves_decimal_strings_and_defaults()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let input = serde_json::json!({"id":"txn","booked_at":"2025-01-01T00:00:00Z","amount":"-1.2300","counterparty":"Vendor","reference":"Payment"});
        let txn: BankTxn = serde_json::from_value(input)?;
        let encoded = serde_json::to_value(&txn)?;
        assert_eq!(encoded["amount"], "-1.2300");
        assert_eq!(encoded["currency"], "EUR");
        assert_eq!(encoded["type"], "transfer");
        assert!(txn.outgoing());
        assert_eq!(encoded["booked_at"], "2025-01-01T00:00:00Z");
        Ok(())
    }
}

/// Unicode 15 casefold captured from the original Python runtime.
pub fn casefold(text: &str) -> String {
    static MAP: std::sync::OnceLock<std::collections::BTreeMap<String, String>> =
        std::sync::OnceLock::new();
    let map = MAP.get_or_init(|| {
        serde_json::from_str(include_str!("../../policy/unicode-casefold.json")).unwrap_or_default()
    });
    text.chars()
        .map(|c| {
            map.get(&c.to_string())
                .cloned()
                .unwrap_or_else(|| c.to_string())
        })
        .collect()
}
pub type EmployeeMap = indexmap::IndexMap<String, String>;

#[async_trait]
pub trait InstallationFence:Send+Sync{
 fn generation(&self)->&str;
 async fn current(&self)->Result<bool>;
}
