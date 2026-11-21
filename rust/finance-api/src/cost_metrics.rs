//! Exact Decimal cost-vs-metric pairs and source narrative templates.
use crate::decimal::{self as d, Decimal};
use chrono::{Datelike, NaiveDate};
use num_traits::Zero;
use serde_json::{Value, json};
use std::collections::BTreeMap;
type Result<T> = std::result::Result<T, String>;
#[derive(Clone)]
struct Metric {
    month: NaiveDate,
    team: Option<String>,
    cost: Decimal,
    metric: Decimal,
    detail: Value,
}
struct Pair {
    kind: &'static str,
    label: &'static str,
    team: &'static str,
    unit: &'static str,
    cost_noun: &'static str,
    metric_noun: &'static str,
    unit_noun: &'static str,
}
const PAIRS: [Pair; 3] = [
    Pair {
        kind: "food_cost",
        label: "Food cost vs orders",
        team: "kitchen",
        unit: "order",
        cost_noun: "Food cost",
        metric_noun: "orders",
        unit_noun: "food cost per order",
    },
    Pair {
        kind: "ads_cac",
        label: "Advertising vs new customers (CAC)",
        team: "marketing",
        unit: "new_customer",
        cost_noun: "Ad spend",
        metric_noun: "new customers",
        unit_noun: "cost per new customer (CAC)",
    },
    Pair {
        kind: "cloud_per_order",
        label: "App cloud vs online orders",
        team: "it",
        unit: "online_order",
        cost_noun: "Cloud cost for the ordering app",
        metric_noun: "online orders",
        unit_noun: "cloud cost per online order",
    },
];
pub(crate) fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
pub(crate) fn python_string(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(python_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", python_repr(&json!(k)), python_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => v.to_string(),
    }
}
fn python_repr(v: &Value) -> String {
    if let Some(s) = v.as_str() {
        let quote = if s.contains('\'') && !s.contains('"') {
            '"'
        } else {
            '\''
        };
        let escaped = s
            .replace('\\', "\\\\")
            .replace(quote, &format!("\\{quote}"))
            .replace('\n', "\\n")
            .replace('\r', "\\r")
            .replace('\t', "\\t");
        format!("{quote}{escaped}{quote}")
    } else {
        python_string(v)
    }
}
fn numeric(v: &Value) -> Option<Decimal> {
    if v.is_boolean() || v.is_null() || v.is_array() || v.is_object() {
        None
    } else {
        d::parse(v.as_str().unwrap_or(&v.to_string()).trim()).ok()
    }
}
fn metric(v: &Value) -> Result<Metric> {
    Ok(Metric {
        month: NaiveDate::parse_from_str(v["month"].as_str().ok_or("month")?, "%Y-%m-%d")
            .map_err(|e| e.to_string())?,
        team: v["team_id"].as_str().map(str::to_string),
        cost: d::number(&v["cost"])?,
        metric: d::number(&v["metric_value"])?,
        detail: v["detail"].clone(),
    })
}
fn cpu(m: &Metric) -> Result<Option<Decimal>> {
    if m.metric.is_zero() {
        Ok(None)
    } else {
        d::divide(&m.cost, &m.metric).map(Some)
    }
}
fn optional_change(a: Option<&Decimal>, b: Option<&Decimal>) -> Result<Option<Decimal>> {
    match (a, b) {
        (Some(a), Some(b)) => d::change(a, b),
        _ => Ok(None),
    }
}
fn line_items(metric: &Metric, invoices: &BTreeMap<String, Value>) -> BTreeMap<String, Decimal> {
    let mut out = BTreeMap::new();
    let detail = metric.detail.as_object().cloned().unwrap_or_default();
    if let Some(lines) = detail.get("lines").and_then(Value::as_array) {
        for row in lines {
            let Some(row) = row.as_object() else { continue };
            let name = ["service", "description", "name"]
                .iter()
                .find_map(|k| row.get(*k).filter(|v| truthy(v)));
            let amount = ["cost", "amount"]
                .iter()
                .find_map(|k| row.get(*k))
                .and_then(numeric);
            if let (Some(name), Some(amount)) = (name, amount) {
                let name = python_string(name);
                *out.entry(name).or_insert_with(|| Decimal::from(0)) += amount;
            }
        }
        if !out.is_empty() {
            return out;
        }
    }
    let flat = detail
        .get("by_supplier")
        .and_then(Value::as_object)
        .unwrap_or(&detail);
    for (k, v) in flat {
        if k != "invoice_number"
            && let Some(amount) = numeric(v)
        {
            out.insert(k.clone(), amount);
        }
    }
    if !out.is_empty() {
        return out;
    }
    if let Some(invoice) = detail
        .get("invoice_number")
        .map(python_string)
        .and_then(|n| invoices.get(&n))
        && let Some(lines) = invoice["lines"].as_array()
    {
        for line in lines {
            if let (Some(name), Ok(amount)) =
                (line["description"].as_str(), d::number(&line["amount"]))
            {
                *out.entry(name.into()).or_insert_with(|| Decimal::from(0)) += amount;
            }
        }
    }
    out
}
struct Driver {
    label: String,
    before: Decimal,
    now: Decimal,
}
fn money(v: &Decimal) -> String {
    format!("EUR {}", d::fixed(v, 2, bigdecimal::RoundingMode::HalfEven))
}
fn p1(v: &Decimal) -> String {
    d::fixed(v, 1, bigdecimal::RoundingMode::HalfEven)
}
fn anomaly(
    prev: &Metric,
    cur: &Metric,
    pair: &Pair,
    invoices: &BTreeMap<String, Value>,
) -> Result<Option<Value>> {
    let cost_pct = d::change(&cur.cost, &prev.cost)?;
    if cost_pct.as_ref().is_some_and(|v| v < &Decimal::from(20))
        || cost_pct.is_none() && cur.cost <= 0
    {
        return Ok(None);
    }
    let metric_pct = if prev.metric.is_zero() {
        if !cur.metric.is_zero() {
            return Ok(None);
        }
        Decimal::from(0)
    } else {
        d::change(&cur.metric, &prev.metric)?.ok_or("metric percent")?
    };
    if metric_pct >= 10 {
        return Ok(None);
    }
    let before = line_items(prev, invoices);
    let now = line_items(cur, invoices);
    let mut drivers = now
        .into_iter()
        .filter_map(|(label, now)| {
            let before = before
                .get(&label)
                .cloned()
                .unwrap_or_else(|| Decimal::from(0));
            (now > before).then_some(Driver { label, before, now })
        })
        .collect::<Vec<_>>();
    drivers.sort_by(|a, b| {
        (&b.now - &b.before)
            .cmp(&(&a.now - &a.before))
            .then_with(|| a.label.cmp(&b.label))
    });
    drivers.truncate(3);
    let metric_phrase = if metric_pct > 0 {
        format!("grew only {}%", p1(&metric_pct))
    } else if metric_pct < 0 {
        format!("fell {}%", p1(&metric_pct.abs()))
    } else {
        "did not change".into()
    };
    let mut parts = Vec::new();
    if let Some(driver) = drivers.first() {
        parts.push(format!(
            "{} went from {} to {}",
            driver.label,
            money(&driver.before),
            money(&driver.now)
        ))
    }
    if let (Some(a), Some(b)) = (cpu(prev)?, cpu(cur)?) {
        parts.push(format!(
            "{} from {} to {}",
            pair.unit_noun,
            money(&a),
            money(&b)
        ))
    }
    let tail = if parts.is_empty() {
        ".".into()
    } else {
        format!(": {}.", parts.join(", "))
    };
    let rose = if let Some(pct) = cost_pct {
        format!(
            "rose {}% month over month (from {} to {})",
            p1(&pct),
            money(&prev.cost),
            money(&cur.cost)
        )
    } else {
        format!(
            "is a new cost of {} (previous month {})",
            money(&cur.cost),
            money(&prev.cost)
        )
    };
    Ok(Some(
        json!({"summary":format!("{} {} while {} {}{}",pair.cost_noun,rose,pair.metric_noun,metric_phrase,tail),"drivers":drivers.into_iter().map(|v|json!({"label":v.label,"previous":d::money(&v.before),"current":d::money(&v.now)})).collect::<Vec<_>>()}),
    ))
}
/// Full CostMetricSeries collection, in source pair and field order.
pub fn collection(metrics: &[Value], invoices: &[Value], kind: Option<&str>) -> Result<Value> {
    if metrics
        .iter()
        .any(|m| !PAIRS.iter().any(|p| m["kind"] == p.kind))
    {
        return Err("invalid cost metric kind".into());
    }
    let invoices = invoices
        .iter()
        .filter_map(|v| v["number"].as_str().map(|n| (n.to_string(), v.clone())))
        .collect::<BTreeMap<_, _>>();
    let mut output = Vec::new();
    for pair in PAIRS {
        if kind.is_some_and(|k| k != pair.kind) {
            continue;
        }
        let mut rows = metrics
            .iter()
            .filter(|m| m["kind"] == pair.kind)
            .map(metric)
            .collect::<Result<Vec<_>>>()?;
        rows.sort_by_key(|r| r.month);
        if rows.is_empty() {
            continue;
        }
        let mut points = Vec::new();
        for (i, cur) in rows.iter().enumerate() {
            let prev = i.checked_sub(1).map(|j| &rows[j]).filter(|p| {
                (cur.month.year() * 12 + cur.month.month() as i32)
                    - (p.month.year() * 12 + p.month.month() as i32)
                    == 1
            });
            let cp = cpu(cur)?;
            let cost_change = prev
                .map(|p| d::change(&cur.cost, &p.cost))
                .transpose()?
                .flatten();
            let metric_change = prev
                .map(|p| d::change(&cur.metric, &p.metric))
                .transpose()?
                .flatten();
            let _cpu_change =
                optional_change(cp.as_ref(), prev.map(cpu).transpose()?.flatten().as_ref())?;
            let assessed = prev
                .map(|p| anomaly(p, cur, &pair, &invoices))
                .transpose()?
                .flatten();
            let metric_value = cur.metric.with_scale(0).to_plain_string();
            points.push(json!({"month":cur.month.to_string(),"cost":d::money(&cur.cost),"metric_value":metric_value,"cost_per_unit":cp.as_ref().map(d::money),"cost_change_pct":d::percentage(cost_change.as_ref()),"metric_change_pct":d::percentage(metric_change.as_ref()),"anomaly":assessed}));
        }
        let team = rows
            .last()
            .and_then(|m| m.team.as_ref())
            .filter(|s| !s.is_empty())
            .map(String::as_str)
            .unwrap_or(pair.team);
        output.push(json!({"kind":pair.kind,"label":pair.label,"team_id":team,"unit":pair.unit,"points":points}));
    }
    Ok(json!({"stub":false,"items":output}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn source_cost_metric_corpus() -> Result<()> {
        for (i, line) in include_str!("../cost-metrics-cases.jsonl")
            .lines()
            .enumerate()
        {
            let case: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
            let actual = collection(
                case["metrics"].as_array().ok_or("metrics")?,
                case["invoices"].as_array().ok_or("invoices")?,
                None,
            )?;
            if actual != case["ok"] {
                return Err(format!("cost metric case {i}: {}", actual));
            }
        }
        Ok(())
    }
}
