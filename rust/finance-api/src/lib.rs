//! Tenant-scoped financial read services and complete dashboard response contracts.
#![forbid(unsafe_code)]
pub mod cost_metrics;
pub use scout_decimal as decimal;
use chrono::{DateTime, Datelike, Duration, NaiveDate, SecondsFormat, Utc};
use decimal::{self as d, Decimal};
use num_traits::{ToPrimitive, Zero};
use scout_api::{ApiError, row_json};
use scout_forecast_kernel::{self as kernel, Transaction};
use scout_forecast_model_native::{Forecast, ForecastModel, liquidity_forecast};
use scout_forecast_training_native::{self as training, TrainingOptions};
use scout_platform::{Database, Tenant};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::UNIX_EPOCH,
};
use tokio_postgres::Client;
pub type Result<T> = std::result::Result<T, ApiError>;
fn internal(_: impl std::fmt::Display) -> ApiError {
    ApiError::new(500, "internal_error", "Internal server error.")
}
fn pure<T>(value: std::result::Result<T, String>) -> Result<T> {
    value.map_err(internal)
}
#[derive(Debug, Clone)]
pub struct FinanceOptions {
    pub model_path: PathBuf,
    pub safety_buffer_eur: Option<String>,
    pub today: Option<NaiveDate>,
}
impl Default for FinanceOptions {
    fn default() -> Self {
        Self {
            model_path: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../models/liquidity_xgb.json"),
            safety_buffer_eur: None,
            today: None,
        }
    }
}
#[derive(Clone)]
pub struct FinanceApi {
    db: Database,
    tenant: Tenant,
    options: FinanceOptions,
}
#[derive(Clone)]
struct Tx {
    data: Transaction,
    team: Option<String>,
    amount: Decimal,
    raw: Value,
}
impl FinanceApi {
    pub fn new(db: Database, tenant: Tenant, options: FinanceOptions) -> Self {
        Self {
            db,
            tenant,
            options,
        }
    }
    async fn transactions(&self, conn: &Client, required: bool) -> Result<Vec<Tx>> {
        let rows=conn.query("SELECT booking_date,amount::text AS amount,category,team_id,counterparty,invoice_id,invoice_number,documented FROM transactions ORDER BY booking_date,id",&[]).await?;
        if required && rows.is_empty() {
            return Err(ApiError::new(
                503,
                "no_data",
                "The database has no transactions yet; seed it with `python -m cfo.data.load --data data/generated`.",
            ));
        }
        rows.iter()
            .map(|r| {
                let raw = row_json(r)?;
                let amount = pure(d::number(&raw["amount"]))?;
                let float = amount.to_f64().ok_or_else(|| internal("amount"))?;
                Ok(Tx {
                    data: Transaction {
                        day: r.get("booking_date"),
                        amount: float,
                        category: r.get("category"),
                    },
                    team: r.get("team_id"),
                    amount: pure(d::parse(&float.to_string()))?,
                    raw,
                })
            })
            .collect()
    }
    async fn invoices(&self, conn: &Client) -> Result<Vec<Value>> {
        let rows=conn.query("SELECT id,number,supplier,issue_date,total::text AS total,category,team_id,transaction_id FROM invoices ORDER BY issue_date,number",&[]).await?;
        rows.iter().map(row_json).collect()
    }
    async fn metrics(&self, conn: &Client) -> Result<Value> {
        let metrics=conn.query("SELECT kind,month,team_id,cost::text AS cost,metric_value::text AS metric_value,detail FROM cost_metrics ORDER BY month",&[]).await?.iter().map(row_json).collect::<Result<Vec<_>>>()?;
        let numbers = metrics
            .iter()
            .filter_map(|m| {
                m["detail"]
                    .get("invoice_number")
                    .filter(|v| cost_metrics::truthy(v))
                    .map(cost_metrics::python_string)
            })
            .collect::<BTreeSet<_>>();
        let mut invoices = Vec::new();
        for number in numbers {
            let rows = conn
                .query("SELECT id,number FROM invoices WHERE number=$1", &[&number])
                .await?;
            if let Some(row) = rows.first() {
                let id: String = row.get("id");
                let lines=conn.query("SELECT description,amount::text AS amount FROM invoice_lines WHERE invoice_id=$1 ORDER BY position",&[&id]).await?.iter().map(row_json).collect::<Result<Vec<_>>>()?;
                invoices.push(json!({"number":number,"lines":lines}));
            }
        }
        pure(cost_metrics::collection(&metrics, &invoices, None))
    }
    pub async fn cost_metrics(&self, kind: Option<&str>) -> Result<Value> {
        let conn = self.db.scoped(&self.tenant).await?;
        let mut result = self.metrics(&conn).await?;
        if let Some(kind) = kind
            && let Some(items) = result["items"].as_array_mut()
        {
            items.retain(|v| v["kind"] == kind);
        }
        Ok(result)
    }
    fn safety_buffer(&self, tx: &[Tx]) -> Result<Decimal> {
        if let Some(configured) = &self.options.safety_buffer_eur {
            return pure(d::parse(configured));
        }
        let fixed = tx
            .iter()
            .filter(|r| {
                r.data
                    .category
                    .as_ref()
                    .is_some_and(|c| kernel::FIXED_CATEGORIES.contains(&c.as_str()))
            })
            .collect::<Vec<_>>();
        let mut groups: BTreeMap<(i32, u32), Vec<f64>> = BTreeMap::new();
        for r in &fixed {
            groups
                .entry((r.data.day.year(), r.data.day.month()))
                .or_default()
                .push(-r.data.amount);
        }
        let monotonic = fixed.windows(2).all(|r| {
            (r[0].data.day.year(), r[0].data.day.month())
                <= (r[1].data.day.year(), r[1].data.day.month())
        });
        let max = groups
            .values()
            .map(|v| group_sum(v, monotonic))
            .reduce(f64::max)
            .unwrap_or(0.);
        pure(d::parse(&format!("{max:.2}")))
    }
    async fn frame(
        &self,
        tx: &[Tx],
        days: usize,
    ) -> Result<(Forecast, ForecastModel, Option<DateTime<Utc>>)> {
        let tx = tx.iter().map(|r| r.data.clone()).collect::<Vec<_>>();
        let base = self.options.model_path.clone();
        let path = base
            .parent()
            .unwrap_or(Path::new("."))
            .join("tenants")
            .join(self.tenant.id())
            .join(base.file_name().unwrap_or_default());
        tokio::task::spawn_blocking(move || {
            let (model, saved) = cached_model(&path, &tx)?;
            let model = model.with_levels_from(&tx).map_err(internal)?;
            let forecast = liquidity_forecast(&model, &tx, days, None, None).map_err(internal)?;
            Ok((forecast, model, saved))
        })
        .await
        .map_err(internal)?
    }
    pub async fn forecast(&self, horizon_days: usize, granularity: &str) -> Result<Value> {
        let conn = self.db.scoped(&self.tenant).await?;
        let tx = self.transactions(&conn, true).await?;
        let (frame, model, saved) = self.frame(&tx, horizon_days).await?;
        let buffer = self.safety_buffer(&tx)?;
        let (low, _) = frame.lowest_balance().map_err(internal)?;
        let point = |index: usize| -> Result<Value> {
            let day = frame.dates[index];
            let (lower, upper) = model
                .band((day - frame.last_known_date).num_days())
                .map_err(internal)?;
            Ok(
                json!({"date":day.to_string(),"balance":pure(d::money_float(frame.balance[index]))?,"lower":pure(d::money_float(frame.balance[index]+lower))?,"upper":pure(d::money_float(frame.balance[index]+upper))?}),
            )
        };
        let low_index = frame
            .dates
            .iter()
            .position(|d| *d == low)
            .ok_or_else(|| internal("lowest date"))?;
        let mut sampled = if granularity == "week" {
            (6..frame.dates.len()).step_by(7).collect::<Vec<_>>()
        } else {
            (0..frame.dates.len()).collect()
        };
        if granularity == "week" && !sampled.contains(&low_index) {
            sampled.push(low_index);
            sampled.sort_unstable();
        }
        let mut gaps = Vec::new();
        let limit = buffer.to_f64().ok_or_else(|| internal("buffer"))?;
        let mut start = None;
        for i in 0..=frame.dates.len() {
            if i < frame.dates.len() && frame.balance[i] < limit {
                if start.is_none() {
                    start = Some(i)
                }
            } else if let Some(first) = start.take() {
                let low = (first..i)
                    .min_by(|a, b| frame.balance[*a].total_cmp(&frame.balance[*b]))
                    .ok_or_else(|| internal("gap"))?;
                gaps.push(json!({"start":frame.dates[first].to_string(),"end":frame.dates[i-1].to_string(),"lowest_date":frame.dates[low].to_string(),"lowest_balance":pure(d::money_float(frame.balance[low]))?}));
            }
        }
        Ok(
            json!({"stub":false,"as_of":frame.last_known_date.to_string(),"horizon_days":horizon_days,"granularity":granularity,"currency":"EUR","opening_balance":pure(d::money_float(frame.start_balance))?,"safety_buffer":d::money(&buffer),"lowest_point":point(low_index)?,"gaps":gaps,"model":{"name":"xgboost-liquidity","version":model.model_version.clone().unwrap_or_else(||format!("trained-until-{}",model.meta.trained_until)),"mlflow_run_id":model.mlflow_run_id,"trained_at":saved.map(timestamp)},"points":sampled.into_iter().map(point).collect::<Result<Vec<_>>>()?}),
        )
    }
    pub async fn kpis(&self) -> Result<Value> {
        let conn = self.db.scoped(&self.tenant).await?;
        let tx = self.transactions(&conn, true).await?;
        let as_of = tx
            .iter()
            .map(|r| r.data.day)
            .max()
            .ok_or_else(|| internal("date"))?;
        let balance = d::sum(tx.iter().map(|r| &r.amount));
        let change = d::sum(
            tx.iter()
                .filter(|r| r.data.day > as_of - Duration::days(30))
                .map(|r| &r.amount),
        );
        let all_months = tx
            .iter()
            .map(|r| (r.data.day.year(), r.data.day.month()))
            .collect::<BTreeSet<_>>();
        let last = (as_of.year(), as_of.month());
        let complete = if next_month(as_of).map(|d| d - Duration::days(1)) == Some(as_of)
            || all_months.len() == 1
        {
            all_months
        } else {
            all_months.into_iter().filter(|m| *m < last).collect()
        };
        let inflows = d::sum(
            tx.iter()
                .filter(|r| {
                    complete.contains(&(r.data.day.year(), r.data.day.month())) && r.amount > 0
                })
                .map(|r| &r.amount),
        );
        let outflows = d::sum(
            tx.iter()
                .filter(|r| {
                    complete.contains(&(r.data.day.year(), r.data.day.month())) && r.amount < 0
                })
                .map(|r| &r.amount),
        );
        let months = Decimal::from(complete.len().max(1) as u64);
        let undocumented = tx
            .iter()
            .filter(|r| !r.raw["invoice_number"].is_null() && r.raw["documented"] == false)
            .collect::<Vec<_>>();
        let undocumented_total = pure(
            undocumented
                .iter()
                .map(|r| d::number(&r.raw["amount"]).map(|v| -v))
                .collect::<std::result::Result<Vec<_>, _>>(),
        )?;
        let count = |table: &str, predicate: &str| {
            let table = table.to_string();
            let predicate = predicate.to_string();
            let conn = &conn;
            async move {
                let exists: Option<String> = conn
                    .query_one("SELECT to_regclass($1)::text AS t", &[&table])
                    .await?
                    .get("t");
                if exists.is_none() {
                    Ok::<_, ApiError>(None)
                } else {
                    let n: i64 = conn
                        .query_one(
                            &format!("SELECT count(*) AS n FROM {table} WHERE {predicate}"),
                            &[],
                        )
                        .await?
                        .get("n");
                    Ok(Some(n))
                }
            }
        };
        let pending = count("approvals", "status='pending'").await?.unwrap_or(0);
        let tasks = count("agent_tasks", "status IN ('waiting','escalated')").await?;
        let series = self.metrics(&conn).await?;
        let alerts = series["items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|s| {
                s["points"]
                    .as_array()
                    .and_then(|p| p.last())
                    .is_some_and(|p| !p["anomaly"].is_null())
            })
            .count();
        let (frame, _, _) = self.frame(&tx, 365).await?;
        let buffer = self.safety_buffer(&tx)?;
        let limit = buffer.to_f64().ok_or_else(|| internal("buffer"))?;
        let (low, balance_low) = frame.lowest_balance().map_err(internal)?;
        let first = |limit: f64| {
            frame
                .balance
                .iter()
                .position(|v| *v < limit)
                .map(|i| frame.dates[i].to_string())
        };
        Ok(
            json!({"stub":false,"as_of":as_of.to_string(),"balance":d::money(&balance),"balance_change_30d":d::money(&change),"avg_monthly_inflows":d::money(&pure(d::divide(&inflows,&months))?),"avg_monthly_outflows":d::money(&pure(d::divide(&outflows,&months))?),"forecast":{"horizon_days":365,"lowest_balance":pure(d::money_float(balance_low))?,"lowest_date":low.to_string(),"first_negative_date":first(0.),"safety_buffer":d::money(&buffer),"first_below_buffer_date":first(limit)},"undocumented_payments":{"count":undocumented.len(),"total":d::money(&d::sum(undocumented_total.iter()))},"pending_approvals":pending,"open_cost_alerts":alerts,"open_tasks":tasks}),
        )
    }
    pub async fn costs_by_team(
        &self,
        start: Option<NaiveDate>,
        end: Option<NaiveDate>,
    ) -> Result<Value> {
        if start.zip(end).is_some_and(|(s, e)| s > e) {
            return Err(ApiError::new(
                422,
                "validation_error",
                "Invalid request: 'from' must not be after 'to'.",
            ));
        }
        let conn = self.db.scoped(&self.tenant).await?;
        let tx = self.transactions(&conn, false).await?;
        let invoices = self.invoices(&conn).await?;
        let teams=conn.query("SELECT id,name,owner_id,monthly_budget::text AS monthly_budget FROM teams ORDER BY id",&[]).await?.iter().map(row_json).collect::<Result<Vec<_>>>()?;
        let today = self
            .options
            .today
            .unwrap_or_else(|| chrono::Local::now().date_naive());
        let dates = if tx.is_empty() {
            invoices
                .iter()
                .filter(|v| v["transaction_id"].is_null())
                .map(|v| date_value(&v["issue_date"]))
                .collect::<Result<Vec<_>>>()?
        } else {
            tx.iter().map(|r| r.data.day).collect()
        };
        let first = dates.iter().copied().min().unwrap_or(today);
        let last = dates.iter().copied().max().unwrap_or(today);
        let start = start.unwrap_or_else(|| month_start(first));
        let end = end.unwrap_or(last);
        validate_range(start, end)?;
        let mut months = vec![month_start(start)];
        while let Some(next) = next_month(*months.last().ok_or_else(|| internal("month"))?) {
            if next > end {
                break;
            }
            months.push(next)
        }
        let mut grouped: BTreeMap<(String, NaiveDate), Vec<f64>> = BTreeMap::new();
        for row in &tx {
            if !matches!(
                row.data.category.as_deref(),
                Some("revenue" | "partner_investment")
            ) {
                grouped
                    .entry((
                        row.team.clone().unwrap_or_else(|| "company".into()),
                        month_start(row.data.day),
                    ))
                    .or_default()
                    .push(-row.data.amount);
            }
        }
        for invoice in invoices.iter().filter(|v| v["transaction_id"].is_null()) {
            let total = pure(d::number(&invoice["total"]))?
                .to_f64()
                .ok_or_else(|| internal("total"))?;
            grouped
                .entry((
                    invoice["team_id"].as_str().unwrap_or("company").into(),
                    month_start(date_value(&invoice["issue_date"])?),
                ))
                .or_default()
                .push(total);
        }
        let mut costs = BTreeMap::new();
        for (key, values) in grouped {
            let sum = group_sum(&values, false);
            costs.insert(key, pure(d::parse(&format!("{sum:.2}")))?);
        }
        let team_lookup = teams
            .iter()
            .filter_map(|t| t["id"].as_str().map(|id| (id.to_string(), t)))
            .collect::<BTreeMap<_, _>>();
        let ids = costs
            .keys()
            .filter(|(_, m)| months.contains(m))
            .map(|(id, _)| id.clone())
            .chain(team_lookup.keys().cloned())
            .collect::<BTreeSet<_>>();
        let totals = ids
            .iter()
            .map(|id| {
                (
                    id.clone(),
                    d::sum(months.iter().filter_map(|m| costs.get(&(id.clone(), *m)))),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let grand = d::sum(totals.values());
        let zero = Decimal::from(0);
        let mut items = Vec::new();
        for id in ids {
            let team = team_lookup.get(&id);
            let total = &totals[&id];
            if team.is_none() && total.is_zero() {
                continue;
            }
            let name = team
                .and_then(|t| t["name"].as_str())
                .unwrap_or(if id == "company" {
                    "Company-wide (tax, social security)"
                } else {
                    &id
                });
            let budget = team
                .and_then(|t| t["monthly_budget"].as_str())
                .map(d::parse)
                .transpose()
                .map_err(internal)?
                .as_ref()
                .map(d::money);
            let share = if grand.is_zero() {
                "0.0000".into()
            } else {
                d::fixed(
                    &pure(d::divide(total, &grand))?,
                    4,
                    bigdecimal::RoundingMode::HalfUp,
                )
            };
            items.push((total.clone(),json!({"team_id":id,"team_name":name,"owner_id":team.map(|t|t["owner_id"].clone()).unwrap_or(Value::Null),"monthly_budget":budget,"total":d::money(total),"avg_monthly":d::money(&pure(d::divide(total,&Decimal::from(months.len()as u64)))?),"share":share,"by_month":months.iter().map(|m|json!({"month":m.to_string(),"cost":d::money(costs.get(&(id.clone(),*m)).unwrap_or(&zero))})).collect::<Vec<_>>()})));
        }
        items.sort_by(|a, b| b.0.cmp(&a.0));
        Ok(
            json!({"stub":false,"from":start.to_string(),"to":end.to_string(),"total":d::money(&grand),"items":items.into_iter().map(|(_,v)|v).collect::<Vec<_>>()}),
        )
    }
}
fn group_sum(values: &[f64], monotonic: bool) -> f64 {
    if monotonic {
        if let Some(first) = values.first() {
            *first + kernel::pandas_sum(&values[1..])
        } else {
            0.
        }
    } else {
        let (mut sum, mut c) = (0., 0.);
        for v in values {
            let y = v - c;
            let n = sum + y;
            c = (n - sum) - y;
            sum = n;
        }
        sum
    }
}
fn date_value(v: &Value) -> Result<NaiveDate> {
    NaiveDate::parse_from_str(v.as_str().ok_or_else(|| internal("date"))?, "%Y-%m-%d")
        .map_err(internal)
}
fn month_start(d: NaiveDate) -> NaiveDate {
    d.with_day(1).unwrap_or(d)
}
fn next_month(d: NaiveDate) -> Option<NaiveDate> {
    if d.month() == 12 {
        NaiveDate::from_ymd_opt(d.year() + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(d.year(), d.month() + 1, 1)
    }
}
fn validate_range(start: NaiveDate, end: NaiveDate) -> Result<()> {
    if start > end {
        return Err(ApiError::new(
            422,
            "validation_error",
            format!("Invalid request: 'from' ({start}) must not be after 'to' ({end})."),
        ));
    }
    let months = (end.year() - start.year()) * 12 + end.month() as i32 - start.month() as i32 + 1;
    if months > 120 {
        return Err(ApiError::new(
            422,
            "validation_error",
            format!("Invalid request: the range spans {months} months, at most 120 are allowed."),
        ));
    }
    Ok(())
}
fn timestamp(d: DateTime<Utc>) -> String {
    d.to_rfc3339_opts(
        if d.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        true,
    )
}
type ModelCache = BTreeMap<(PathBuf, u128), ForecastModel>;
static MODELS: OnceLock<Mutex<ModelCache>> = OnceLock::new();
fn cached_model(path: &Path, tx: &[Transaction]) -> Result<(ForecastModel, Option<DateTime<Utc>>)> {
    let mut cache = MODELS
        .get_or_init(|| Mutex::new(BTreeMap::new()))
        .lock()
        .map_err(internal)?;
    let meta = PathBuf::from(format!("{}.meta.json", path.to_string_lossy()));
    if !path.is_file() || !meta.is_file() {
        let trained = training::train(tx, &TrainingOptions::default()).map_err(internal)?;
        trained.model.save(path).map_err(internal)?;
    }
    let modified = std::fs::metadata(path)
        .and_then(|m| m.modified())
        .map_err(internal)?
        .duration_since(UNIX_EPOCH)
        .map_err(internal)?;
    let nanos = modified.as_nanos();
    let key = (path.to_path_buf(), nanos);
    if !cache.contains_key(&key) {
        let model = ForecastModel::load(path).map_err(internal)?;
        cache.clear();
        cache.insert(key.clone(), model);
    }
    let seconds = nanos as f64 / 1e9;
    let mut sec = seconds.trunc() as i64;
    let mut micros = ((seconds - sec as f64) * 1e6).round_ties_even() as u32;
    if micros == 1_000_000 {
        sec += 1;
        micros = 0;
    }
    Ok((
        cache.get(&key).cloned().ok_or_else(|| internal("model"))?,
        DateTime::from_timestamp(sec, micros * 1000),
    ))
}
