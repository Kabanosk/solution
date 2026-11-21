//! Source access policy, salary redaction and scoped audit persistence.
use async_trait::async_trait;
use scout_domain::{AuditRecord, AuditSink, Result, RuntimeError, format_eur};
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};
fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
fn casefold(s: &str) -> String {
    static MAP: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    let map = MAP.get_or_init(|| {
        serde_json::from_str(include_str!("../../../policy/unicode-casefold.json"))
            .unwrap_or_default()
    });
    s.chars()
        .map(|c| {
            map.get(&c.to_string())
                .cloned()
                .unwrap_or_else(|| c.to_string())
        })
        .collect()
}
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
fn python_string(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        v => v.to_string(),
    }
}
pub fn check(requester: &str, role: &str, target: &str, _resource: &str) -> (bool, String) {
    let role = casefold(role);
    if matches!(role.as_str(), "ceo" | "owner") {
        return (true, "exec: all company data".into());
    }
    let (kind, owner) = target.split_once(':').unwrap_or((target, ""));
    match kind {
        "laptop" | "files" => {
            if owner == requester {
                (true, format!("{owner}-devices: owner"))
            } else if role == "office_manager" && owner != "vincenzo" {
                (true, "company-folders: finance".into())
            } else {
                (false, format!("{owner}-devices: owner-or-exec-only"))
            }
        }
        "mail" | "invoicing" => (true, format!("{kind}: all members")),
        "bank" => {
            if role == "office_manager" {
                (true, "bank: finance".into())
            } else {
                (false, "bank: finance-or-exec-only".into())
            }
        }
        "chat" => (true, "chat: authorized channels".into()),
        _ => (false, "default: deny".into()),
    }
}
pub fn salary_sql(alias: &str) -> String {
    let p = if alias.is_empty() {
        String::new()
    } else {
        format!("{alias}.")
    };
    format!(
        "(coalesce({p}category, '') = 'payroll' OR position('salary' in lower({p}type || ' ' || {p}reference)) > 0)"
    )
}
pub fn is_salary_row(row: &Value) -> bool {
    row["category"] == "payroll"
        || casefold(&format!(
            "{} {}",
            text(&row["type"]),
            text(&row["reference"])
        ))
        .contains("salary")
}
fn tokens(s: &str) -> Vec<String> {
    static MAP: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    let map = MAP.get_or_init(|| {
        serde_json::from_str(include_str!("../../../audit-reference/nfkd-ascii.json"))
            .unwrap_or_default()
    });
    let plain = s
        .chars()
        .map(|c| {
            if c.is_ascii() {
                c.to_string()
            } else {
                map.get(&c.to_string()).cloned().unwrap_or_default()
            }
        })
        .collect::<String>()
        .to_ascii_lowercase();
    plain
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}
pub fn payee_of(row: &Value, people: &[Value]) -> Option<String> {
    let mut who = BTreeSet::new();
    if truthy(&row["employee_id"]) {
        who.insert(python_string(&row["employee_id"]));
    }
    let cp = tokens(text(&row["counterparty"]));
    if !cp.is_empty() {
        for p in people {
            if tokens(text(&p["name"])) == cp {
                who.insert(text(&p["id"]).to_string());
            }
        }
    }
    let refs = tokens(text(&row["reference"]));
    let mut names = BTreeSet::new();
    for p in people {
        let name = tokens(text(&p["name"]));
        if !name.is_empty() && refs.windows(name.len()).any(|w| w == name) {
            names.insert(text(&p["id"]).to_string());
        }
    }
    if who.len() != 1 || names.difference(&who).next().is_some() {
        None
    } else {
        who.into_iter().next()
    }
}
pub fn reports_of(viewer: &str, people: &[Value]) -> BTreeSet<String> {
    let managers = people
        .iter()
        .map(|p| {
            (
                text(&p["id"]).to_string(),
                p["manager_id"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .map(str::to_string),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut result = BTreeSet::new();
    for p in people {
        let id = text(&p["id"]);
        let mut cur = managers.get(id).cloned().flatten();
        let mut seen = BTreeSet::new();
        while let Some(manager) = cur {
            if !seen.insert(manager.clone()) {
                break;
            }
            if manager == viewer {
                result.insert(id.to_string());
                break;
            }
            cur = managers.get(&manager).cloned().flatten();
        }
    }
    result.remove(viewer);
    result
}
pub fn can_see_salary(
    viewer: &str,
    role: &str,
    payee: Option<&str>,
    reports: &BTreeSet<String>,
) -> (bool, String) {
    if matches!(casefold(role).as_str(), "ceo" | "owner") {
        (true, "salary: exec sees all pay".into())
    } else if let Some(payee) = payee {
        if payee == viewer {
            (true, "salary: own pay".into())
        } else if reports.contains(payee) {
            (true, "salary: manager of the payee".into())
        } else {
            (false, "salary: other people's pay is exec-only".into())
        }
    } else {
        (
            false,
            "salary: unknown payee is treated as someone else's pay".into(),
        )
    }
}
#[derive(Clone)]
pub struct SalaryView {
    pub visible: Vec<Value>,
    pub hidden: Vec<(Value, String, String)>,
    pub summaries: Vec<String>,
}
impl SalaryView {
    pub fn json(&self) -> Value {
        json!({"visible":self.visible,"hidden":self.hidden,"summaries":self.summaries})
    }
}
pub fn filter_salary_rows(
    rows: &[Value],
    viewer: &str,
    role: &str,
    people: &[Value],
    fold: bool,
) -> std::result::Result<SalaryView, String> {
    let reports = reports_of(viewer, people);
    let role = casefold(role);
    let mut visible = Vec::new();
    let mut hidden = Vec::new();
    let mut months: BTreeMap<String, Vec<(usize, Value, String)>> = BTreeMap::new();
    for row in rows {
        if !is_salary_row(row) {
            visible.push(row.clone());
            continue;
        }
        let payee = payee_of(row, people);
        let (ok, policy) = can_see_salary(viewer, &role, payee.as_deref(), &reports);
        if ok {
            visible.push(row.clone())
        } else if role == "office_manager" && fold {
            let month = python_string(&row["booking_date"])
                .chars()
                .take(7)
                .collect::<String>();
            let key = payee.unwrap_or_else(|| format!("?{}", python_string(&row["counterparty"])));
            months
                .entry(month)
                .or_default()
                .push((hidden.len(), row.clone(), key));
            hidden.push((
                row.clone(),
                "MASK".into(),
                format!("{policy}; folded into the monthly payroll total"),
            ))
        } else {
            hidden.push((row.clone(), "DENY".into(), policy))
        }
    }
    let mut summaries = Vec::new();
    for (month, entries) in months.iter().rev() {
        let people = entries.iter().map(|(_, _, p)| p).collect::<BTreeSet<_>>();
        if people.len() >= 2 {
            let values = entries
                .iter()
                .map(|(_, r, _)| {
                    scout_decimal::number(if truthy(&r["amount"]) {
                        &r["amount"]
                    } else {
                        &Value::Null
                    })
                    .or_else(|_| {
                        if !truthy(&r["amount"]) {
                            Ok(scout_domain::Decimal::from(0))
                        } else {
                            Err("invalid amount".into())
                        }
                    })
                    .map(|v| v.abs())
                })
                .collect::<std::result::Result<Vec<_>, String>>()?;
            let total = scout_decimal::sum(values.iter());
            let text = format_eur(&total);
            summaries.push(format!(
                "Payroll {month} ({} employees): {} EUR",
                people.len(),
                text.strip_prefix('€').unwrap_or(&text)
            ));
        } else {
            for (index, _, _) in entries {
                hidden[*index].1 = "DENY".into();
                hidden[*index].2 =
                    "salary: a one-person payroll total would reveal that person's pay".into();
            }
        }
    }
    Ok(SalaryView {
        visible,
        hidden,
        summaries,
    })
}
pub fn can_see_task(
    viewer: &str,
    role: &str,
    requested_by: Option<&str>,
    owner_id: Option<&str>,
) -> (bool, String) {
    if matches!(casefold(role).as_str(), "ceo" | "owner") {
        (true, "tasks: exec sees all".into())
    } else if requested_by == Some(viewer) || owner_id == Some(viewer) {
        (true, "tasks: requester or assignee".into())
    } else {
        (false, "tasks: requester-or-exec-only".into())
    }
}
fn database_error(e: tokio_postgres::Error) -> RuntimeError {
    let name = e
        .as_db_error()
        .map(|d| match d.code().code() {
            "42P01" => "UndefinedTable",
            "42703" => "UndefinedColumn",
            "23505" => "UniqueViolation",
            "23503" => "ForeignKeyViolation",
            "42501" => "InsufficientPrivilege",
            _ => "DatabaseError",
        })
        .unwrap_or("OperationalError");
    let message = e
        .as_db_error()
        .map(|d| d.message().to_string())
        .unwrap_or_else(|| e.to_string());
    RuntimeError::new(name, message)
}
#[derive(Clone)]
pub struct Audit {
    db: Database,
    tenant: Tenant,
}
impl Audit {
    pub fn new(db: Database, tenant: Tenant) -> Self {
        Self { db, tenant }
    }
    pub async fn record(&self, r: &AuditRecord) -> Result<()> {
        let conn = self
            .db
            .scoped(&self.tenant)
            .await
            .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
        let resource = r.resource.chars().take(500).collect::<String>();
        conn.execute("INSERT INTO audit_log(task_id,actor,requester,role,action,target,resource,decision,policy,reason) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",&[&r.task_id,&r.actor,&r.requester,&r.role,&r.action,&r.target,&resource,&r.decision,&r.policy,&r.reason]).await.map_err(database_error)?;
        Ok(())
    }
    pub async fn check(
        &self,
        requester: &str,
        role: &str,
        target: &str,
        resource: &str,
        task_id: Option<&str>,
    ) -> Result<bool> {
        let (allowed, policy) = check(requester, role, target, resource);
        let mut record = AuditRecord::new("agent", "access_check", target, resource);
        record.decision = if allowed { "ALLOW" } else { "DENY" }.into();
        record.task_id = task_id.map(str::to_string);
        record.requester = Some(requester.into());
        record.role = Some(role.into());
        record.policy = Some(policy);
        self.record(&record).await?;
        Ok(allowed)
    }
    pub async fn salary_filter(
        &self,
        rows: &[Value],
        viewer: &str,
        role: &str,
        people: &[Value],
        task_id: Option<&str>,
        fold: bool,
    ) -> Result<SalaryView> {
        let view = filter_salary_rows(rows, viewer, role, people, fold)
            .map_err(|e| RuntimeError::new("InvalidOperation", e))?;
        for (row, decision, policy) in &view.hidden {
            let payee = payee_of(row, people).unwrap_or_else(|| "unknown payee".into());
            let id = if truthy(&row["id"]) {
                python_string(&row["id"])
            } else {
                "?".into()
            };
            let mut record = AuditRecord::new(
                "agent",
                "salary_filter",
                "bank",
                format!("transaction {id} ({payee})"),
            );
            record.decision = decision.clone();
            record.task_id = task_id.map(str::to_string);
            record.requester = Some(viewer.into());
            record.role = Some(role.into());
            record.policy = Some(policy.clone());
            self.record(&record).await?;
        }
        Ok(view)
    }
    pub async fn check_task(
        &self,
        viewer: &str,
        role: &str,
        task_id: &str,
        requested_by: Option<&str>,
        owner_id: Option<&str>,
        current_task: Option<&str>,
    ) -> Result<bool> {
        let (allowed, policy) = can_see_task(viewer, role, requested_by, owner_id);
        let mut record = AuditRecord::new(
            "agent",
            "access_check",
            format!("task:{task_id}"),
            "task log",
        );
        record.decision = if allowed { "ALLOW" } else { "DENY" }.into();
        record.task_id = current_task.map(str::to_string);
        record.requester = Some(viewer.into());
        record.role = Some(role.into());
        record.policy = Some(policy);
        self.record(&record).await?;
        Ok(allowed)
    }
}
#[async_trait]
impl AuditSink for Audit {
    async fn record(&self, record: &AuditRecord) -> Result<()> {
        Audit::record(self, record).await
    }
}
pub fn dispatch(b: &Value) -> std::result::Result<Option<Value>, String> {
    let rows = b
        .get("rows")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let people = b
        .get("people")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let viewer = text(&b["viewer"]);
    let role = text(&b["role"]);
    let out = match text(&b["op"]) {
        "check" => json!(check(
            text(&b["requester"]),
            role,
            text(&b["target"]),
            text(&b["resource"])
        )),
        "salary_sql" => json!(salary_sql(text(&b["alias"]))),
        "salary" => json!(is_salary_row(&b["row"])),
        "payee" => json!(payee_of(&b["row"], people)),
        "reports" => json!(reports_of(viewer, people)),
        "can_salary" => {
            let reports = b
                .get("reports")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|v| text(v).to_string())
                .collect();
            json!(can_see_salary(viewer, role, b["payee"].as_str(), &reports))
        }
        "can_task" => json!(can_see_task(
            viewer,
            role,
            b["requested_by"].as_str(),
            b["owner_id"].as_str()
        )),
        "filter" => filter_salary_rows(
            rows,
            viewer,
            role,
            people,
            b["fold"].as_bool().unwrap_or(true),
        )?
        .json(),
        _ => return Ok(None),
    };
    Ok(Some(out))
}
