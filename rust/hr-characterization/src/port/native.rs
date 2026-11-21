//! Scoped company HR, explicit Slack identities and vacation/manager routing.
use async_trait::async_trait;
use chrono::NaiveDate;
use scout_domain::{EmployeeMap, People, Result, RuntimeError, casefold};
use scout_platform::{Database, Tenant};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Mutex, OnceLock},
};
fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}
fn pg_error(e: tokio_postgres::Error) -> RuntimeError {
    RuntimeError::new(
        if e.as_db_error().is_some() {
            "DatabaseError"
        } else {
            "OperationalError"
        },
        e.as_db_error()
            .map(|e| e.message().to_string())
            .unwrap_or_else(|| e.to_string()),
    )
}
fn row_json(r: &tokio_postgres::Row) -> Result<Value> {
    scout_api::row_json(r).map_err(|e| RuntimeError::new("TypeError", e.message))
}
#[derive(Clone)]
enum Storage {
    Database(Database, Tenant),
    #[cfg(feature = "characterization")]
    Fixture(Value),
}
#[derive(Clone)]
pub struct HR {
    storage: Storage,
    by_slack: BTreeMap<String, String>,
    slack_names: EmployeeMap,
}
impl HR {
    pub fn new(
        db: Database,
        tenant: Tenant,
        user_map: EmployeeMap,
        slack_names: EmployeeMap,
    ) -> Self {
        Self {
            storage: Storage::Database(db, tenant),
            by_slack: user_map
                .into_iter()
                .map(|(employee, user)| (user, employee))
                .collect(),
            slack_names,
        }
    }
    async fn employee_raw(&self, id: &str) -> Result<Option<Value>> {
        match &self.storage {
            Storage::Database(db, tenant) => {
                let conn = db
                    .scoped(tenant)
                    .await
                    .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
                conn.query_opt("SELECT * FROM employees WHERE id=$1", &[&id])
                    .await
                    .map_err(pg_error)?
                    .as_ref()
                    .map(row_json)
                    .transpose()
            }
            #[cfg(feature = "characterization")]
            Storage::Fixture(body) => Ok(body["people"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|p| p["id"] == id)
                .cloned()),
        }
    }
    pub async fn link_slack(&self, user_map: &EmployeeMap, names: &EmployeeMap) -> Result<()> {
        let (db, tenant) = match &self.storage {
            Storage::Database(db, tenant) => (db, tenant),
            #[cfg(feature = "characterization")]
            _ => {
                return Err(RuntimeError::new(
                    "RuntimeError",
                    "HR database is unavailable",
                ));
            }
        };
        let conn = db
            .scoped(tenant)
            .await
            .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
        static READY: OnceLock<Mutex<BTreeSet<String>>> = OnceLock::new();
        let ready = READY.get_or_init(|| Mutex::new(BTreeSet::new()));
        let ensure = !ready
            .lock()
            .map_err(|e| RuntimeError::new("RuntimeError", e.to_string()))?
            .contains(tenant.schema_name());
        if ensure {
            let _ = conn
                .batch_execute(include_str!(
                    "../../../../backend/cfo/db/scout_slack_members.sql"
                ))
                .await;
            ready
                .lock()
                .map_err(|e| RuntimeError::new("RuntimeError", e.to_string()))?
                .insert(tenant.schema_name().to_string());
        }
        let mut conn = conn;
        let tx = conn.transaction().await.map_err(pg_error)?;
        tx.execute("UPDATE employees SET slack_user_id=NULL,slack_name=NULL WHERE NOT EXISTS (SELECT 1 FROM slack_members m WHERE m.employee_id=employees.id AND m.status='registered')",&[]).await.map_err(pg_error)?;
        for (employee, user) in user_map {
            let user = (!user.is_empty()).then_some(user);
            let name = names.get(employee);
            tx.execute(
                "UPDATE employees SET slack_user_id=$1,slack_name=$2 WHERE id=$3",
                &[&user, &name, &employee],
            )
            .await
            .map_err(pg_error)?;
        }
        tx.commit().await.map_err(pg_error)?;
        Ok(())
    }
}
#[async_trait]
impl People for HR {
    async fn employee(&self, id: &str) -> Result<Option<Value>> {
        self.employee_raw(id).await
    }
    async fn by_slack_id(&self, id: &str) -> Result<Option<Value>> {
        if let Some(employee) = self.by_slack.get(id) {
            return self.employee_raw(employee).await;
        }
        match &self.storage {
            Storage::Database(db, tenant) => {
                let conn = db
                    .scoped(tenant)
                    .await
                    .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
                conn.query("SELECT * FROM employees WHERE slack_user_id=$1", &[&id])
                    .await
                    .map_err(pg_error)?
                    .first()
                    .map(row_json)
                    .transpose()
            }
            #[cfg(feature = "characterization")]
            Storage::Fixture(body) => Ok(body["people"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|p| p["slack_user_id"] == id)
                .cloned()),
        }
    }
    async fn all(&self) -> Result<Vec<Value>> {
        match &self.storage {
            Storage::Database(db, tenant) => {
                let conn = db
                    .scoped(tenant)
                    .await
                    .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
                conn.query("SELECT * FROM employees ORDER BY id", &[])
                    .await
                    .map_err(pg_error)?
                    .iter()
                    .map(row_json)
                    .collect()
            }
            #[cfg(feature = "characterization")]
            Storage::Fixture(body) => {
                let mut people = body["people"].as_array().cloned().unwrap_or_default();
                people.sort_by(|a, b| text(&a["id"]).cmp(text(&b["id"])));
                Ok(people)
            }
        }
    }
    async fn name(&self, id: Option<&str>) -> Result<String> {
        let Some(id) = id.filter(|s| !s.is_empty()) else {
            return Ok("someone".into());
        };
        if let Some(name) = self.slack_names.get(id).filter(|s| !s.is_empty()) {
            return Ok(name.clone());
        }
        let employee = self.employee_raw(id).await?;
        let name = employee.as_ref().map(|e| text(&e["name"])).unwrap_or("");
        if name.is_empty() {
            Ok(id.into())
        } else {
            name.split_whitespace()
                .next()
                .map(str::to_string)
                .ok_or_else(|| RuntimeError::new("IndexError", "list index out of range"))
        }
    }
    async fn full_name(&self, id: &str) -> Result<String> {
        if let Some(name) = self.slack_names.get(id).filter(|s| !s.is_empty()) {
            return Ok(name.clone());
        }
        let employee = self.employee_raw(id).await?.unwrap_or(Value::Null);
        Ok(employee["slack_name"]
            .as_str()
            .filter(|s| !s.is_empty())
            .or_else(|| employee["name"].as_str().filter(|s| !s.is_empty()))
            .unwrap_or(id)
            .to_string())
    }
    async fn role(&self, id: &str) -> Result<String> {
        let employee = self.employee_raw(id).await?.unwrap_or(Value::Null);
        let role = casefold(text(&employee["role"]));
        let codes = [
            ("ceo", "ceo"),
            ("owner", "ceo"),
            ("office manager", "office_manager"),
            ("chef", "head_chef"),
            ("baker", "baker"),
            ("waitstaff", "waitstaff"),
            ("marketing", "waitstaff"),
        ];
        Ok(codes
            .iter()
            .find(|(needle, _)| role.contains(needle))
            .map(|(_, code)| *code)
            .unwrap_or("staff")
            .into())
    }
    async fn on_vacation(&self, id: &str, day: NaiveDate) -> Result<bool> {
        match &self.storage {
            Storage::Database(db, tenant) => {
                let conn = db
                    .scoped(tenant)
                    .await
                    .map_err(|e| RuntimeError::new("OperationalError", e.to_string()))?;
                Ok(!conn.query("SELECT 1 FROM vacations WHERE employee_id=$1 AND start_date<=$2 AND end_date>=$2",&[&id,&day]).await.map_err(pg_error)?.is_empty())
            }
            #[cfg(feature = "characterization")]
            Storage::Fixture(body) => {
                Ok(body["vacations"].as_array().into_iter().flatten().any(|v| {
                    v["employee_id"] == id
                        && text(&v["start"]) <= day.to_string().as_str()
                        && text(&v["end"]) >= day.to_string().as_str()
                }))
            }
        }
    }
    async fn manager(&self, id: &str) -> Result<Option<String>> {
        Ok(self
            .employee_raw(id)
            .await?
            .and_then(|e| e["manager_id"].as_str().map(str::to_string)))
    }
    async fn chain(&self, id: &str) -> Result<Vec<String>> {
        let mut out = Vec::new();
        let mut cur = Some(id.to_string());
        while let Some(id) = cur.filter(|s| !s.is_empty()) {
            if out.contains(&id) {
                break;
            }
            out.push(id.clone());
            cur = self.manager(&id).await?;
        }
        Ok(out)
    }
    async fn first_available(&self, id: &str, day: NaiveDate) -> Result<(String, Vec<String>)> {
        let chain = self.chain(id).await?;
        for (i, who) in chain.iter().enumerate() {
            if !self.on_vacation(who, day).await? {
                return Ok((who.clone(), chain[..i].to_vec()));
            }
        }
        let last = chain
            .last()
            .cloned()
            .ok_or_else(|| RuntimeError::new("IndexError", "list index out of range"))?;
        Ok((last, chain[..chain.len() - 1].to_vec()))
    }
}
#[cfg(feature = "characterization")]
pub async fn dispatch(body: &Value) -> std::result::Result<Option<Value>, String> {
    let user_map = body
        .get("user_map")
        .and_then(Value::as_object)
        .into_iter()
        .flatten()
        .filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_string())))
        .collect::<EmployeeMap>();
    let names = body
        .get("slack_names")
        .and_then(Value::as_object)
        .into_iter()
        .flatten()
        .filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_string())))
        .collect();
    let hr = HR {
        storage: Storage::Fixture(body.clone()),
        by_slack: user_map.into_iter().map(|(e, s)| (s, e)).collect(),
        slack_names: names,
    };
    let person = text(&body["person"]);
    let date =
        || NaiveDate::parse_from_str(text(&body["day"]), "%Y-%m-%d").map_err(|e| e.to_string());
    let out = match text(&body["op"]) {
        "employee" => serde_json::json!(hr.employee(person).await.map_err(|e| e.message)?),
        "by_slack" => serde_json::json!(hr.by_slack_id(person).await.map_err(|e| e.message)?),
        "all" => serde_json::json!(hr.all().await.map_err(|e| e.message)?),
        "name" => serde_json::json!(
            hr.name(body["person"].as_str())
                .await
                .map_err(|e| e.message)?
        ),
        "full_name" => serde_json::json!(hr.full_name(person).await.map_err(|e| e.message)?),
        "role" => serde_json::json!(hr.role(person).await.map_err(|e| e.message)?),
        "vacation" => serde_json::json!(
            hr.on_vacation(person, date()?)
                .await
                .map_err(|e| e.message)?
        ),
        "manager" => serde_json::json!(hr.manager(person).await.map_err(|e| e.message)?),
        "chain" => serde_json::json!(hr.chain(person).await.map_err(|e| e.message)?),
        "available" => serde_json::json!(
            hr.first_available(person, date()?)
                .await
                .map_err(|e| e.message)?
        ),
        _ => return Ok(None),
    };
    Ok(Some(out))
}
