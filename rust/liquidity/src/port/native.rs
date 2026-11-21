//! What-if liquidity forecasts, safety-buffer gaps and policy-classified proposals.
use bigdecimal::RoundingMode;
use chrono::{Datelike, Duration, NaiveDate};
use num_traits::{ToPrimitive, Zero};
use scout_decimal::{self as d, Decimal};
use scout_forecast_kernel::{self as kernel, Transaction};
use scout_forecast_model_native::{
    Forecast, ForecastModel, liquidity_forecast, partner_tranche_schedule,
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, path::Path};
pub type Result<T> = std::result::Result<T, String>;
fn error(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn day(s: &str) -> Result<NaiveDate> {
    NaiveDate::parse_from_str(s.get(..10).ok_or("date")?, "%Y-%m-%d").map_err(error)
}
fn number(v: &Value) -> Result<f64> {
    d::number(v)?.to_f64().ok_or_else(|| "number".into())
}
fn dec(v: &Value) -> Result<String> {
    let n = d::number(v)?;
    let s = d::fixed(&n, 2, RoundingMode::HalfEven);
    if n.is_zero() && v.as_str().is_some_and(|s| s.starts_with('-')) {
        Ok(format!("-{s}"))
    } else {
        Ok(s)
    }
}
fn decimal(v: f64) -> Result<String> {
    let s = dec(&json!(v))?;
    if v.is_sign_negative() && v == 0. {
        Ok("-0.00".into())
    } else {
        Ok(s)
    }
}
fn money(v: &Decimal) -> String {
    let s = d::fixed(v, 0, RoundingMode::HalfEven);
    let (sign, digits) = s
        .strip_prefix('-')
        .map(|v| ("-", v))
        .unwrap_or(("", s.as_str()));
    let mut result = String::from(sign);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            result.push(',')
        }
        result.push(c)
    }
    format!("{result} EUR")
}
fn negative_zero_input(v: &Value) -> bool {
    v.as_str().is_some_and(|s| s.trim_start().starts_with('-'))
        || v.as_f64().is_some_and(|n| n.is_sign_negative())
}
fn money_value(v: &Value) -> Result<String> {
    let n = d::number(v)?;
    let formatted = money(&n);
    Ok(if n.is_zero() && negative_zero_input(v) {
        format!("-{formatted}")
    } else {
        formatted
    })
}
fn step_string(amount: &Value, result: &Decimal) -> String {
    let text = d::python_string(result);
    if result.is_zero() && negative_zero_input(amount) {
        format!("-{text}")
    } else {
        text
    }
}
fn round_step(v: &Decimal, step: &Decimal, up: bool) -> Result<Decimal> {
    let quotient = d::divide(v, step)?;
    let integral = if quotient.as_bigint_and_scale().1 <= 0 {
        quotient
    } else {
        quotient.with_scale_round(
            0,
            if up {
                RoundingMode::Ceiling
            } else {
                RoundingMode::Floor
            },
        )
    };
    Ok(integral * step)
}
fn month(d: NaiveDate) -> NaiveDate {
    d.with_day(1).unwrap_or(d)
}
fn month_end(d: NaiveDate) -> Result<NaiveDate> {
    let next = if d.month() == 12 {
        NaiveDate::from_ymd_opt(d.year() + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(d.year(), d.month() + 1, 1)
    };
    next.map(|n| n - Duration::days(1))
        .ok_or_else(|| "month".into())
}
fn dates(first: NaiveDate, last: NaiveDate) -> Vec<NaiveDate> {
    (0..=(last - first).num_days())
        .map(|i| first + Duration::days(i))
        .collect()
}
fn group_sum(v: &[f64], monotonic: bool) -> f64 {
    if monotonic {
        v.first()
            .map(|f| f + kernel::pandas_sum(&v[1..]))
            .unwrap_or(0.)
    } else {
        let (mut sum, mut c) = (0., 0.);
        for v in v {
            let y = v - c;
            let n = sum + y;
            c = (n - sum) - y;
            sum = n;
        }
        sum
    }
}
pub fn fixed_costs_monthly(tx: &[Transaction]) -> Result<String> {
    let fixed = tx
        .iter()
        .filter(|t| {
            t.category
                .as_ref()
                .is_some_and(|c| kernel::FIXED_CATEGORIES.contains(&c.as_str()))
        })
        .collect::<Vec<_>>();
    if fixed.is_empty() {
        return Err(
            "no fixed-cost bookings: cannot derive a safety buffer, pass buffer= explicitly".into(),
        );
    }
    let mut monthly: BTreeMap<NaiveDate, Vec<f64>> = BTreeMap::new();
    for r in &fixed {
        monthly.entry(month(r.day)).or_default().push(-r.amount);
    }
    let monotonic = fixed.windows(2).all(|r| month(r[0].day) <= month(r[1].day));
    let max = monthly
        .values()
        .map(|v| group_sum(v, monotonic))
        .reduce(f64::max)
        .ok_or("fixed costs")?;
    decimal(max)
}
#[derive(Clone)]
pub struct Outflow {
    pub on: NaiveDate,
    pub amount: String,
    pub label: String,
    pub direction: String,
}
impl Outflow {
    pub fn json(&self) -> Value {
        json!({"on":self.on.to_string(),"amount":self.amount,"label":self.label,"direction":self.direction})
    }
    fn signed(&self) -> Result<f64> {
        let amount = d::parse(&self.amount)?.to_f64().ok_or("amount")?;
        Ok(if self.direction == "in" {
            amount
        } else {
            -amount
        })
    }
}
pub fn normalize_outflows(rows: &[Value]) -> Result<Vec<Outflow>> {
    rows.iter()
        .map(|r| {
            let (on, amount, label, direction) = if let Some(m) = r.as_object() {
                (
                    m.get("on")
                        .or_else(|| m.get("date"))
                        .and_then(Value::as_str)
                        .ok_or("date")?,
                    m.get("amount").ok_or("amount")?,
                    m.get("label")
                        .and_then(Value::as_str)
                        .unwrap_or("extra outflow"),
                    m.get("direction").and_then(Value::as_str).unwrap_or("out"),
                )
            } else {
                let a = r.as_array().ok_or("outflow")?;
                (
                    a.first().and_then(Value::as_str).ok_or("date")?,
                    a.get(1).ok_or("amount")?,
                    a.get(2).and_then(Value::as_str).unwrap_or("extra outflow"),
                    "out",
                )
            };
            let amount = d::number(amount)?;
            if amount <= 0 {
                return Err(
                    "extra outflow amounts are positive EUR (direction says which way)".into(),
                );
            }
            if !["in", "out"].contains(&direction) {
                return Err("direction".into());
            }
            Ok(Outflow {
                on: day(on)?,
                amount: d::python_string(&amount),
                label: label.into(),
                direction: direction.into(),
            })
        })
        .collect()
}
pub struct ForecastView {
    pub forecast: Forecast,
    pub what_if: Vec<f64>,
    pub default_buffer: Option<String>,
    pub outflows: Vec<Outflow>,
}
impl ForecastView {
    pub fn json(&self) -> Value {
        let mut output = self.forecast.json();
        output["frame"]["columns"]
            .as_array_mut()
            .into_iter()
            .for_each(|a| a.push(json!("what_if")));
        if let Some(rows) = output["frame"]["rows"].as_array_mut() {
            for (i, row) in rows.iter_mut().enumerate() {
                if let Some(row) = row.as_array_mut() {
                    row.push(json!(self.what_if[i]));
                }
            }
        }
        output["attrs"]["outflows"] =
            json!(self.outflows.iter().map(Outflow::json).collect::<Vec<_>>());
        if let Some(buffer) = &self.default_buffer {
            output["attrs"]["default_buffer"] =
                json!(d::parse(buffer).ok().and_then(|d| d.to_f64()).unwrap_or(0.));
        }
        output
    }
}
pub fn forecast_with(
    tx: &[Transaction],
    model: &ForecastModel,
    outflows: &[Outflow],
    days: usize,
    start: Option<f64>,
    scheduled: Option<&BTreeMap<NaiveDate, f64>>,
    buffer: Option<&Value>,
) -> Result<ForecastView> {
    let mut combined = match scheduled {
        Some(s) => s.clone(),
        None => partner_tranche_schedule(tx, 12)?,
    };
    let mut extra: BTreeMap<NaiveDate, f64> = BTreeMap::new();
    for o in outflows {
        *extra.entry(o.on).or_default() += o.signed()?;
    }
    for (on, amount) in &extra {
        *combined.entry(*on).or_default() += amount;
    }
    let forecast = liquidity_forecast(model, tx, days, start, Some(&combined))?;
    let first = *forecast.dates.first().ok_or("empty forecast")?;
    let last = *forecast.dates.last().ok_or("empty forecast")?;
    for o in outflows {
        if o.on < first || o.on > last {
            return Err(format!(
                "outflow '{}' on {} is outside the forecast horizon {first}..{last}",
                o.label, o.on
            ));
        }
    }
    let what_if = forecast
        .dates
        .iter()
        .map(|d| extra.get(d).copied().unwrap_or(0.))
        .collect();
    let default_buffer = if let Some(buffer) = buffer {
        Some(dec(buffer)?)
    } else {
        fixed_costs_monthly(tx).ok()
    };
    Ok(ForecastView {
        forecast,
        what_if,
        default_buffer,
        outflows: outflows.to_vec(),
    })
}
fn resolve_buffer(
    view: &ForecastView,
    buffer: Option<&Value>,
    tx: Option<&[Transaction]>,
) -> Result<String> {
    if let Some(b) = buffer {
        return dec(b);
    }
    if let Some(b) = &view.default_buffer {
        return dec(&json!(b));
    }
    if let Some(tx) = tx {
        return fixed_costs_monthly(tx);
    }
    Err("no buffer: pass buffer=, transactions=, or a forecast made by forecast_with".into())
}
pub fn detect_gaps(
    view: &ForecastView,
    buffer: Option<&Value>,
    tx: Option<&[Transaction]>,
) -> Result<Vec<Value>> {
    let limit = resolve_buffer(view, buffer, tx)?;
    let float = d::parse(&limit)?.to_f64().ok_or("buffer")?;
    let f = &view.forecast;
    let mut gaps = Vec::new();
    let mut start = None;
    for i in 0..=f.dates.len() {
        if i < f.dates.len() && f.balance[i] < float {
            if start.is_none() {
                start = Some(i)
            }
        } else if let Some(first) = start.take() {
            let low = (first..i)
                .min_by(|a, b| f.balance[*a].total_cmp(&f.balance[*b]))
                .ok_or("gap")?;
            let lowest = decimal(f.balance[low])?;
            let shortfall = dec(&json!(
                (d::parse(&limit)? - d::parse(&lowest)?).to_plain_string()
            ))?;
            gaps.push(json!({"start":f.dates[first].to_string(),"end":f.dates[i-1].to_string(),"lowest_date":f.dates[low].to_string(),"lowest_balance":lowest,"buffer":limit,"shortfall":shortfall,"recovery_date":f.dates.get(i).map(ToString::to_string)}));
        }
    }
    Ok(gaps)
}
pub fn find_surplus(
    view: &ForecastView,
    buffer: Option<&Value>,
    tx: Option<&[Transaction]>,
    minimum: &Decimal,
    step: &Decimal,
) -> Result<Option<Value>> {
    let limit = resolve_buffer(view, buffer, tx)?;
    let limit_decimal = d::parse(&limit)?;
    let limit_float = limit_decimal.to_f64().ok_or("buffer")?;
    let f = &view.forecast;
    if f.balance.iter().any(|v| *v < limit_float) {
        return Ok(None);
    }
    let Some(first) = f.dates.first() else {
        return Ok(None);
    };
    let mut monthly: BTreeMap<NaiveDate, Vec<f64>> = BTreeMap::new();
    for (d, v) in f.dates.iter().zip(&f.predicted_net) {
        monthly.entry(month(*d)).or_default().push(*v);
    }
    let mut weak = None;
    for (m, values) in monthly {
        if m <= *first
            || values.iter().filter(|v| !v.is_nan()).count()
                != (month_end(m)? - m).num_days() as usize + 1
        {
            continue;
        }
        let net = group_sum(&values, false);
        if weak.is_none_or(|(_, min)| net < min) {
            weak = Some((m, net));
        }
    }
    let Some((weak, net)) = weak.filter(|(_, v)| *v < 0.) else {
        return Ok(None);
    };
    let mut peak = None;
    for (i, _) in f.dates.iter().enumerate().filter(|(_, d)| **d < weak) {
        if peak.is_none_or(|j| f.balance[i] > f.balance[j]) {
            peak = Some(i)
        }
    }
    let Some(peak) = peak else { return Ok(None) };
    let low = (peak..f.dates.len())
        .min_by(|a, b| f.balance[*a].total_cmp(&f.balance[*b]))
        .ok_or("surplus")?;
    let lowest = decimal(f.balance[low])?;
    let amount = round_step(&(d::parse(&lowest)? - &limit_decimal), step, false)?;
    if amount < *minimum {
        return Ok(None);
    }
    Ok(Some(
        json!({"invest_on":f.dates[peak].to_string(),"amount":d::python_string(&amount),"weak_month":weak.format("%Y-%m").to_string(),"weak_month_net":decimal(net)?,"balance_on_invest_date":decimal(f.balance[peak])?,"lowest_balance":lowest,"lowest_date":f.dates[low].to_string(),"buffer":limit}),
    ))
}
fn phrase(name: &str) -> String {
    let text = match name {
        "shutdown_flag" => "the summer shutdown",
        "month" => "the usual seasonal pattern of the month",
        "quarter" => "the usual seasonal pattern of the quarter",
        "day_of_year" => "the usual seasonal pattern of the year",
        "week_of_year" => "the usual seasonal pattern of the week",
        "weekday" => "the weekday pattern",
        "is_weekend" => "weekends",
        "day_of_month" => "the day-of-month pattern",
        "days_to_month_end" => "the position in the month",
        "is_month_start" => "the start of the month",
        "is_month_end" => "the end of the month",
        "is_public_holiday" => "public holidays",
        "payroll_due" => "payroll being due",
        "rent_due" => "rent being due",
        "tax_due" => "tax payments being due",
        "social_security_due" => "social security being due",
        "utilities_due" => "utilities being due",
        "recent_level_baseline" => "the recent revenue level",
        "recent_revenue_28d" => "the revenue of the last four weeks",
        "recent_revenue_90d" => "the revenue of the last three months",
        "recent_variable_cost_28d" => "the food and drink costs of the last four weeks",
        "recent_variable_cost_90d" => "the food and drink costs of the last three months",
        "scheduled_payroll" => "payroll",
        "scheduled_rent" => "rent",
        "scheduled_tax" => "tax payments",
        "scheduled_social_security" => "social security",
        "scheduled_utilities" => "utilities",
        _ => return name.replace('_', " "),
    };
    text.into()
}
fn driver_phrase(driver: &Value) -> Result<String> {
    let v = d::number(&driver["contribution_eur"])?;
    Ok(format!(
        "{} ({}{})",
        phrase(driver["feature"].as_str().ok_or("feature")?),
        if v < 0 { "-" } else { "+" },
        money(&v.abs())
    ))
}
fn typical_revenue(tx: &[Transaction]) -> Result<Option<Decimal>> {
    let rev = tx
        .iter()
        .filter(|t| t.category.as_deref() == Some("revenue"))
        .collect::<Vec<_>>();
    if rev.is_empty() {
        return Ok(None);
    }
    let mut groups: BTreeMap<NaiveDate, Vec<f64>> = BTreeMap::new();
    for t in &rev {
        groups.entry(month(t.day)).or_default().push(t.amount);
    }
    let sorted = rev.windows(2).all(|r| month(r[0].day) <= month(r[1].day));
    let mut values = groups
        .values()
        .map(|v| group_sum(v, sorted))
        .collect::<Vec<_>>();
    values.sort_by(f64::total_cmp);
    let n = values.len();
    let median = if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.
    };
    Ok(Some(d::parse(&decimal(median)?)?))
}
pub struct Evidence<'a> {
    pub model: Option<&'a ForecastModel>,
    pub transactions: Option<&'a [Transaction]>,
    pub outflows: &'a [Outflow],
}
fn month_facts(e: &Evidence<'_>, first: NaiveDate) -> Result<(Vec<String>, Vec<Value>, Value)> {
    let first = month(first);
    let last = month_end(first)?;
    let label = first.format("%B %Y").to_string();
    let fixed = e
        .transactions
        .and_then(|t| fixed_costs_monthly(t).ok())
        .map(|s| d::parse(&s))
        .transpose()?;
    let closed = e
        .model
        .map(|m| {
            (1..=last.day())
                .filter(|d| m.meta.shutdown_days.contains(&(first.month(), *d)))
                .count()
        })
        .unwrap_or(0);
    let mut sentences = Vec::new();
    if closed >= 7 {
        let weeks = ((closed as f64 / 7.).round_ties_even() as usize).max(1);
        let mut text = format!(
            "the restaurant is closed about {weeks} week{}",
            if weeks == 1 { "" } else { "s" }
        );
        if let Some(typical) = e.transactions.map(typical_revenue).transpose()?.flatten() {
            let drop = d::divide(
                &(typical * Decimal::from(closed as u64)),
                &Decimal::from(last.day()),
            )?;
            text.push_str(&format!(
                ", so revenue drops by about {} against a normal month",
                money(&round_step(&drop, &Decimal::from(100), false)?)
            ));
        }
        if let Some(fixed) = fixed {
            text.push_str(&format!(
                ", while fixed costs of {} per month continue",
                money(&fixed)
            ));
        }
        sentences.push(format!("{label}: {text}."));
    }
    let mut drivers = Vec::new();
    let mut importances = json!({});
    if let Some(model) = e.model {
        let why = model.explain(first, last, 3)?;
        drivers = why["top_drivers"].as_array().cloned().ok_or("drivers")?;
        importances = why["feature_importances"].clone();
        let net = d::number(&why["predicted_net_eur"])?;
        if !drivers.is_empty() && net < 0 {
            sentences.push(format!(
                "The model expects a net cash flow of -{} in {label}, mainly because of {}.",
                money(&net.abs()),
                drivers
                    .iter()
                    .map(driver_phrase)
                    .collect::<Result<Vec<_>>>()?
                    .join("; ")
            ));
        }
    }
    Ok((sentences, drivers, importances))
}
fn driver_month(e: &Evidence<'_>, gap: &Value) -> Result<NaiveDate> {
    let start = day(gap["start"].as_str().ok_or("start")?)?;
    let Some(model) = e.model else {
        return Ok(month(start));
    };
    let dates = dates(
        start - Duration::days(45),
        day(gap["lowest_date"].as_str().ok_or("low date")?)?,
    );
    let predicted = model.predict(&dates)?;
    let mut groups: BTreeMap<NaiveDate, Vec<f64>> = BTreeMap::new();
    for (d, v) in dates.into_iter().zip(predicted) {
        groups.entry(month(d)).or_default().push(v);
    }
    let weak = groups
        .iter()
        .map(|(d, v)| (*d, group_sum(v, false)))
        .min_by(|a, b| a.1.total_cmp(&b.1));
    Ok(weak
        .filter(|(_, v)| *v < 0.)
        .map(|(d, _)| d)
        .unwrap_or_else(|| month(start)))
}
fn outflow_sentence(outflows: &[Outflow], until: NaiveDate) -> Result<Option<String>> {
    let parts = outflows
        .iter()
        .filter(|o| o.on <= until)
        .map(|o| {
            Ok(format!(
                "{} {}{} on {}",
                o.label,
                if o.direction == "in" { "+" } else { "" },
                money(&d::parse(&o.amount)?),
                o.on
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok((!parts.is_empty()).then(|| format!("Planned in this scenario: {}.", parts.join(", "))))
}
pub fn propose_credit_line(
    gaps: &[Value],
    e: &Evidence<'_>,
    context: &Value,
    reserve: &Decimal,
) -> Result<Value> {
    if gaps.is_empty() {
        return Err("no gap: nothing to propose a credit line for".into());
    }
    let mut gaps = gaps.to_vec();
    gaps.sort_by(|a, b| a["start"].as_str().cmp(&b["start"].as_str()));
    let mut deepest = &gaps[0];
    for g in &gaps[1..] {
        if d::number(&g["shortfall"])? > d::number(&deepest["shortfall"])? {
            deepest = g
        }
    }
    let amount = round_step(
        &(d::number(&deepest["shortfall"])? * (Decimal::from(1) + reserve)),
        &Decimal::from(1000),
        true,
    )?;
    let start = day(gaps[0]["start"].as_str().ok_or("start")?)? - Duration::days(14);
    let last = gaps.last().ok_or("last gap")?;
    let end = day(last["recovery_date"]
        .as_str()
        .or_else(|| last["end"].as_str())
        .ok_or("end")?)?
        + Duration::days(30);
    let (sentences, drivers, importances) = month_facts(e, driver_month(e, deepest)?)?;
    let mut parts = sentences.iter().take(1).cloned().collect::<Vec<_>>();
    if let Some(planned) = outflow_sentence(
        e.outflows,
        day(deepest["lowest_date"].as_str().ok_or("lowest date")?)?,
    )? {
        parts.push(planned)
    }
    parts.extend(sentences.into_iter().skip(1));
    parts.push(format!("The balance is predicted to fall to {} on {}, {} below the safety buffer of {} (one month of fixed costs).",money_value(&deepest["lowest_balance"])?,deepest["lowest_date"].as_str().ok_or("low date")?,money_value(&deepest["shortfall"])?,money_value(&deepest["buffer"])?));
    parts.push(format!(
        "A credit line of {} from {start} to {end} covers the shortfall with a reserve.",
        money(&amount)
    ));
    let policy = scout_policy_core::classify(
        &json!({"action_type":"request_credit_line","amount":d::python_string(&amount)}),
        context,
    )?;
    Ok(
        json!({"action_type":"request_credit_line","instrument":"credit line","amount":d::python_string(&amount),"period_start":start.to_string(),"period_end":end.to_string(),"month":day(deepest["start"].as_str().ok_or("start")?)?.format("%Y-%m").to_string(),"min_balance":deepest["lowest_balance"],"buffer":deepest["buffer"],"reason":parts.join(" "),"drivers":drivers,"importances":importances,"level":policy["level"],"policy_reason":policy["reason"]}),
    )
}
pub fn propose_investment(
    surplus: &Value,
    e: &Evidence<'_>,
    context: &Value,
    instrument: &str,
) -> Result<Value> {
    let weak = day(&format!(
        "{}-01",
        surplus["weak_month"].as_str().ok_or("month")?
    ))?;
    let (sentences, drivers, importances) = month_facts(e, weak)?;
    let after = d::number(&surplus["lowest_balance"])? - d::number(&surplus["amount"])?;
    let mut parts = if sentences.is_empty() {
        vec![format!(
            "{} is the weakest month ahead (net {}).",
            weak.format("%B %Y"),
            money_value(&surplus["weak_month_net"])?
        )]
    } else {
        vec![sentences[0].clone()]
    };
    parts.extend(sentences.into_iter().skip(1));
    parts.push(format!("From {} the balance stays above the safety buffer of {} (lowest {} on {}), so {} can earn interest in a {instrument} and the balance still never falls below {}, {} above the buffer.",surplus["invest_on"].as_str().ok_or("invest date")?,money_value(&surplus["buffer"])?,money_value(&surplus["lowest_balance"])?,surplus["lowest_date"].as_str().ok_or("low date")?,money_value(&surplus["amount"])?,money(&after),money(&(&after-d::number(&surplus["buffer"])?))));
    let policy = scout_policy_core::classify(
        &json!({"action_type":"make_investment","amount":surplus["amount"]}),
        context,
    )?;
    Ok(
        json!({"action_type":"make_investment","instrument":instrument,"amount":surplus["amount"],"period_start":surplus["invest_on"],"period_end":month_end(weak)?.to_string(),"month":surplus["weak_month"],"min_balance":d::python_string(&after),"buffer":surplus["buffer"],"reason":parts.join(" "),"drivers":drivers,"importances":importances,"level":policy["level"],"policy_reason":policy["reason"]}),
    )
}
pub fn proposal_payload(p: &Value) -> Value {
    json!({"signal":"liquidity_forecast","action_type":p["action_type"],"month":p["month"],"min_balance":p["min_balance"],"requested_amount":p["amount"],"buffer":p["buffer"],"instrument":p["instrument"],"period_start":p["period_start"],"period_end":p["period_end"],"why":p["reason"]})
}
fn proposal_view(p: Value) -> Value {
    json!({"payload":proposal_payload(&p),"needs_approval":p["level"]=="APPROVAL","model":p})
}
pub struct Review {
    pub forecast: ForecastView,
    pub buffer: String,
    pub gaps: Vec<Value>,
    pub proposals: Vec<Value>,
}
impl Review {
    pub fn json(self) -> Value {
        json!({"forecast":self.forecast.json(),"buffer":self.buffer,"gaps":self.gaps,"proposals":self.proposals.into_iter().map(proposal_view).collect::<Vec<_>>()})
    }
}
pub fn review_liquidity(
    tx: &[Transaction],
    model: &ForecastModel,
    outflows: &[Outflow],
    days: usize,
    start: Option<f64>,
    buffer: Option<&Value>,
    context: &Value,
) -> Result<Review> {
    let model = model.with_levels_from(tx)?;
    let forecast = forecast_with(tx, &model, outflows, days, start, None, buffer)?;
    let limit = resolve_buffer(&forecast, buffer, Some(tx))?;
    let gaps = detect_gaps(&forecast, Some(&json!(limit)), None)?;
    let evidence = Evidence {
        model: Some(&model),
        transactions: Some(tx),
        outflows,
    };
    let mut proposals = Vec::new();
    if !gaps.is_empty() {
        proposals.push(propose_credit_line(
            &gaps,
            &evidence,
            context,
            &d::parse("0.20")?,
        )?)
    } else if let Some(surplus) = find_surplus(
        &forecast,
        Some(&json!(limit)),
        None,
        &Decimal::from(1000),
        &Decimal::from(500),
    )? {
        proposals.push(propose_investment(
            &surplus,
            &evidence,
            context,
            "money market fund",
        )?)
    }
    Ok(Review {
        forecast,
        buffer: limit,
        gaps,
        proposals,
    })
}
fn view_from_body(body: &Value) -> Result<ForecastView> {
    let rows = body["forecast"].as_array().ok_or("forecast")?;
    if rows.is_empty() {
        return Err("missing forecast columns".into());
    }
    let dates = rows
        .iter()
        .map(|r| day(r["date"].as_str().ok_or("date")?))
        .collect::<Result<Vec<_>>>()?;
    let balance = rows
        .iter()
        .map(|r| number(&r["balance"]))
        .collect::<Result<Vec<_>>>()?;
    let predicted_net = rows
        .iter()
        .map(|r| number(&r["predicted_net"]))
        .collect::<Result<Vec<_>>>()?;
    let n = dates.len();
    let last = dates.first().copied().unwrap_or(NaiveDate::MIN);
    Ok(ForecastView {
        forecast: Forecast {
            dates,
            predicted_net,
            scheduled: vec![0.; n],
            net: vec![0.; n],
            balance,
            start_balance: 0.,
            last_known_date: last,
        },
        what_if: vec![0.; n],
        default_buffer: body["attrs"].get("default_buffer").map(dec).transpose()?,
        outflows: Vec::new(),
    })
}
pub fn dispatch(body: &Value) -> Result<Option<Value>> {
    let Some(op) = body["op"].as_str() else {
        return Ok(None);
    };
    if ![
        "helpers",
        "fixed",
        "gaps",
        "surplus",
        "credit",
        "investment",
        "forecast",
        "review",
    ]
    .contains(&op)
    {
        return Ok(None);
    }
    let tx = body
        .get("transactions")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|r| {
            Ok(Transaction {
                day: day(r["booking_date"].as_str().ok_or("date")?)?,
                amount: number(&r["amount"])?,
                category: r["category"].as_str().map(str::to_string),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let buffer = body.get("buffer").filter(|v| !v.is_null());
    let output = match op {
        "helpers" => {
            let amount = d::number(&body["amount"])?;
            let step = d::number(body.get("step").unwrap_or(&json!(500)))?;
            json!({"dec":dec(&body["amount"])?,"eur":money_value(&body["amount"] )?,"up":step_string(&body["amount"],&round_step(&amount,&step,true)?),"down":step_string(&body["amount"],&round_step(&amount,&step,false)?)})
        }
        "fixed" => json!(fixed_costs_monthly(&tx)?),
        "gaps" => json!(detect_gaps(
            &view_from_body(body)?,
            buffer,
            (body["use_transactions"] == true).then_some(tx.as_slice())
        )?),
        "surplus" => json!(find_surplus(
            &view_from_body(body)?,
            buffer,
            (body["use_transactions"] == true).then_some(tx.as_slice()),
            &d::number(body.get("min_amount").unwrap_or(&json!(1000)))?,
            &d::number(body.get("step").unwrap_or(&json!(500)))?
        )?),
        _ => {
            let model = body["model"]
                .as_str()
                .map(|name| {
                    if ![
                        "calendar.json",
                        "level.json",
                        "level_baseline.json",
                        "components.json",
                    ]
                    .contains(&name)
                    {
                        return Err("fixture model".into());
                    }
                    ForecastModel::load(
                        Path::new(env!("CARGO_MANIFEST_DIR"))
                            .join("../forecast-model/fixtures")
                            .join(name),
                    )
                })
                .transpose()?;
            let outflows = normalize_outflows(
                body.get("outflows")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or(&[]),
            )?;
            let evidence = Evidence {
                model: model.as_ref(),
                transactions: (!tx.is_empty()).then_some(tx.as_slice()),
                outflows: &outflows,
            };
            let context = body
                .get("policy_context")
                .cloned()
                .unwrap_or_else(|| json!({}));
            match op {
                "credit" => proposal_view(propose_credit_line(
                    body["gaps"].as_array().ok_or("gaps")?,
                    &evidence,
                    &context,
                    &d::number(body.get("reserve").unwrap_or(&json!("0.20")))?,
                )?),
                "investment" => proposal_view(propose_investment(
                    &body["surplus"],
                    &evidence,
                    &context,
                    body["instrument"].as_str().unwrap_or("money market fund"),
                )?),
                _ => {
                    let model = model.as_ref().ok_or("missing model")?;
                    let days = body["days"].as_u64().unwrap_or(90) as usize;
                    let start = body
                        .get("start_balance")
                        .filter(|v| !v.is_null())
                        .map(number)
                        .transpose()?;
                    if op == "review" {
                        review_liquidity(&tx, model, &outflows, days, start, buffer, &context)?
                            .json()
                    } else {
                        let scheduled = body
                            .get("scheduled")
                            .filter(|v| !v.is_null())
                            .map(|v| {
                                v.as_object().ok_or("schedule".into()).and_then(|m| {
                                    m.iter()
                                        .map(|(k, v)| Ok((day(k)?, number(v)?)))
                                        .collect::<Result<BTreeMap<_, _>>>()
                                })
                            })
                            .transpose()?;
                        forecast_with(
                            &tx,
                            model,
                            &outflows,
                            days,
                            start,
                            scheduled.as_ref(),
                            buffer,
                        )?
                        .json()
                    }
                }
            }
        }
    };
    Ok(Some(output))
}
