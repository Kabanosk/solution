//! Native calendar, level, cash-flow, schedule and uncertainty inference kernels.
//! No database, Python interpreter, model training or network access occurs here.
use chrono::{Datelike, Duration, NaiveDate};
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
pub type Result<T> = std::result::Result<T, String>;
const INVALID: &str = "invalid forecast input";
pub const FIXED_CATEGORIES: [&str; 5] = ["payroll", "rent", "tax", "social_security", "utilities"];
pub const CALENDAR_COLUMNS: [&str; 17] = [
    "weekday",
    "is_weekend",
    "month",
    "quarter",
    "day_of_month",
    "day_of_year",
    "week_of_year",
    "days_to_month_end",
    "is_month_start",
    "is_month_end",
    "is_public_holiday",
    "shutdown_flag",
    "payroll_due",
    "rent_due",
    "tax_due",
    "social_security_due",
    "utilities_due",
];
pub const CASH_COLUMNS: [&str; 5] = [
    "net_cash_flow",
    "inflow",
    "outflow",
    "revenue",
    "n_transactions",
];
pub const LEVEL_COLUMNS: [&str; 4] = [
    "recent_revenue_28d",
    "recent_revenue_90d",
    "recent_variable_cost_28d",
    "recent_variable_cost_90d",
];
fn float(value: f64) -> Value {
    if value.is_nan() {
        json!({"$nan":true})
    } else if value.is_infinite() {
        json!({"$f64":format!("{:016x}",value.to_bits())})
    } else {
        json!(value)
    }
}
fn f64_value(value: &Value) -> Result<f64> {
    match value {
        Value::Null => Ok(f64::NAN),
        Value::Number(n) => n.as_f64().ok_or_else(|| INVALID.into()),
        Value::String(s) => s.parse().map_err(|_| INVALID.into()),
        _ => Err(INVALID.into()),
    }
}
fn integer(value: &Value) -> Result<i64> {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|n| n as i64))
        .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        .ok_or_else(|| INVALID.into())
}
fn date(value: &Value) -> Result<NaiveDate> {
    let s = value.as_str().ok_or(INVALID)?;
    let day =
        NaiveDate::parse_from_str(s.get(..10).ok_or(INVALID)?, "%Y-%m-%d").map_err(|_| INVALID)?;
    if !(1..=9999).contains(&day.year()) {
        return Err(INVALID.into());
    }
    Ok(day)
}
fn date_list(body: &Value) -> Result<Vec<NaiveDate>> {
    body["dates"]
        .as_array()
        .ok_or(INVALID)?
        .iter()
        .map(date)
        .collect()
}
fn option_date(body: &Value, name: &str) -> Result<Option<NaiveDate>> {
    match body.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => Ok(Some(date(v)?)),
    }
}
fn days_in_month(day: NaiveDate) -> u32 {
    let next = if day.month() == 12 {
        NaiveDate::from_ymd_opt(day.year() + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(day.year(), day.month() + 1, 1)
    };
    next.and_then(|d| d.pred_opt()).map_or(31, |d| d.day())
}
fn month_id(day: NaiveDate) -> i32 {
    day.year() * 12 + day.month0() as i32
}
fn month_end(month: i32) -> Result<NaiveDate> {
    let first = NaiveDate::from_ymd_opt(month.div_euclid(12), (month.rem_euclid(12) + 1) as u32, 1)
        .ok_or(INVALID)?;
    first
        .with_day(days_in_month(first))
        .ok_or_else(|| INVALID.into())
}
fn range(first: NaiveDate, last: NaiveDate) -> Vec<NaiveDate> {
    (0..=(last - first).num_days())
        .filter_map(|n| first.checked_add_signed(Duration::days(n)))
        .collect()
}
fn frame(columns: &[String], index: &[NaiveDate], rows: Vec<Vec<Value>>) -> Value {
    json!({"columns":columns,"index":index.iter().map(ToString::to_string).collect::<Vec<_>>(),"rows":rows})
}
fn names(columns: &[&str]) -> Vec<String> {
    columns.iter().map(|s| (*s).into()).collect()
}
fn series(name: Option<&str>, index: &[NaiveDate], values: Vec<Value>) -> Value {
    json!({"name":name,"index":index.iter().map(ToString::to_string).collect::<Vec<_>>(),"values":values})
}
#[derive(Clone, Debug)]
pub struct Transaction {
    pub day: NaiveDate,
    pub amount: f64,
    pub category: Option<String>,
}
fn transactions(body: &Value) -> Result<Vec<Transaction>> {
    let empty = Vec::new();
    let rows = body
        .get("transactions")
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    rows.iter()
        .map(|row| {
            Ok(Transaction {
                day: date(&row["booking_date"])?,
                amount: f64_value(&row["amount"])?,
                category: row["category"].as_str().map(str::to_string),
            })
        })
        .collect()
}
pub fn feature_set_columns(name: &str) -> Result<Vec<String>> {
    let mut columns = names(&CALENDAR_COLUMNS);
    match name {
        "calendar" => {}
        "level" | "level_baseline" | "components" => {
            columns.retain(|c| {
                !["month", "quarter", "day_of_year", "week_of_year"].contains(&c.as_str())
            });
            if name == "level" {
                columns.extend(names(&LEVEL_COLUMNS));
            }
            if name == "components" {
                columns.retain(|c| !c.ends_with("_due"));
            }
        }
        _ => {
            return Err(format!(
                "unknown feature_set {name:?}; choose one of ['calendar', 'components', 'level', 'level_baseline']"
            ));
        }
    }
    Ok(columns)
}
fn default_due(category: &str) -> Vec<i64> {
    match category {
        "payroll" => vec![28],
        "rent" => vec![1],
        "tax" => vec![10, 15],
        "social_security" => vec![27],
        _ => vec![10],
    }
}
pub type DueDays = BTreeMap<String, Vec<i64>>;
pub fn public_holidays(year: i32) -> Result<BTreeSet<NaiveDate>> {
    if !(1..=9999).contains(&year) {
        return Err(INVALID.into());
    }
    let a = year % 19;
    let b = year / 100;
    let c = year % 100;
    let d = b / 4;
    let e = b % 4;
    let f = (b + 8) / 25;
    let g = (b - f + 1) / 3;
    let h = (19 * a + b - d - g + 15) % 30;
    let i = c / 4;
    let k = c % 4;
    let el = (32 + 2 * e + 2 * i - h - k) % 7;
    let m = (a + 11 * h + 22 * el) / 451;
    let value = h + el - 7 * m + 114;
    let easter = NaiveDate::from_ymd_opt(year, (value / 31) as u32, (value % 31 + 1) as u32)
        .ok_or(INVALID)?;
    let mut days = BTreeSet::new();
    for shift in [-2, 1, 39, 50] {
        days.insert(easter + Duration::days(shift));
    }
    for (month, day) in [(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)] {
        days.insert(NaiveDate::from_ymd_opt(year, month, day).ok_or(INVALID)?);
    }
    Ok(days)
}
pub fn calendar_features(
    dates: &[NaiveDate],
    shutdown: &BTreeSet<(u32, u32)>,
    due: &DueDays,
) -> Result<Value> {
    let mut holidays = BTreeSet::new();
    for year in dates.iter().map(Datelike::year).collect::<BTreeSet<_>>() {
        holidays.extend(public_holidays(year)?);
    }
    let rows = dates
        .iter()
        .map(|day| {
            let weekday = day.weekday().num_days_from_monday() as i64;
            let dim = days_in_month(*day) as i64;
            let mut row = vec![
                weekday,
                i64::from(weekday >= 5),
                day.month() as i64,
                (day.month0() / 3 + 1) as i64,
                day.day() as i64,
                day.ordinal() as i64,
                day.iso_week().week() as i64,
                dim - day.day() as i64,
                i64::from(day.day() == 1),
                i64::from(day.day() as i64 == dim),
                i64::from(holidays.contains(day)),
                i64::from(shutdown.contains(&(day.month(), day.day()))),
            ];
            for category in FIXED_CATEGORIES {
                let defaults = default_due(category);
                let days = due.get(category).unwrap_or(&defaults);
                row.push(i64::from(
                    days.iter().any(|d| (*d).min(dim) == day.day() as i64),
                ));
            }
            row.into_iter().map(Value::from).collect()
        })
        .collect();
    Ok(frame(&names(&CALENDAR_COLUMNS), dates, rows))
}
pub fn infer_due_days(tx: &[Transaction], minimum: i64) -> DueDays {
    let mut result = DueDays::new();
    for category in FIXED_CATEGORIES {
        let mut by_day: BTreeMap<u32, BTreeSet<i32>> = BTreeMap::new();
        for row in tx
            .iter()
            .filter(|r| r.category.as_deref() == Some(category))
        {
            by_day
                .entry(row.day.day())
                .or_default()
                .insert(month_id(row.day));
        }
        let days = by_day
            .into_iter()
            .filter(|(_, m)| m.len() as i64 >= minimum)
            .map(|(d, _)| d as i64)
            .collect::<Vec<_>>();
        result.insert(
            category.into(),
            if days.is_empty() {
                default_due(category)
            } else {
                days
            },
        );
    }
    result
}
fn pairwise_sum(values: &[f64]) -> f64 {
    let n = values.len();
    if n < 8 {
        return values.iter().fold(-0.0, |sum, value| sum + value);
    }
    if n > 128 {
        let half = (n / 2) / 8 * 8;
        return pairwise_sum(&values[..half]) + pairwise_sum(&values[half..]);
    }
    let mut lanes = [0.0; 8];
    lanes.copy_from_slice(&values[..8]);
    let stop = n / 8 * 8;
    for chunk in values[8..stop].as_chunks::<8>().0 {
        for (lane, value) in lanes.iter_mut().zip(chunk) {
            *lane += value;
        }
    }
    let mut sum = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3]))
        + ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
    for value in &values[stop..] {
        sum += value;
    }
    sum
}
/// NumPy/Pandas sum over a contiguous f64 vector (NaNs contribute zero).
pub fn pandas_sum(values: &[f64]) -> f64 {
    let values = values
        .iter()
        .map(|v| if v.is_nan() { 0.0 } else { *v })
        .collect::<Vec<_>>();
    0.0 + pairwise_sum(&values)
}

#[derive(Clone, Default)]
struct Sum {
    value: f64,
    correction: f64,
    samples: Vec<f64>,
}
impl Sum {
    fn add(&mut self, value: f64) {
        self.samples.push(value);
        if !value.is_nan() {
            let adjusted = value - self.correction;
            let total = self.value + adjusted;
            self.correction = (total - self.value) - adjusted;
            if !self.correction.is_finite() {
                self.correction = 0.0;
            }
            self.value = total;
        }
    }
    fn result(&self, monotonic: bool) -> f64 {
        if monotonic && !self.samples.iter().any(|v| v.is_nan()) {
            self.samples.first().map_or(0.0, |first| {
                if self.samples.len() == 1 {
                    *first
                } else {
                    *first + pairwise_sum(&self.samples[1..])
                }
            })
        } else {
            self.value
        }
    }
}
fn revenue_by_day(tx: &[Transaction]) -> BTreeMap<NaiveDate, f64> {
    let mut grouped: BTreeMap<NaiveDate, Sum> = BTreeMap::new();
    for row in tx
        .iter()
        .filter(|r| r.category.as_deref() == Some("revenue"))
    {
        grouped.entry(row.day).or_default().add(row.amount);
    }
    let revenue = tx
        .iter()
        .filter(|r| r.category.as_deref() == Some("revenue"))
        .collect::<Vec<_>>();
    let monotonic = revenue.windows(2).all(|r| r[0].day <= r[1].day);
    grouped
        .into_iter()
        .map(|(d, s)| (d, s.result(monotonic)))
        .collect()
}
fn span(tx: &[Transaction]) -> Result<(NaiveDate, NaiveDate)> {
    Ok((
        tx.iter().map(|r| r.day).min().ok_or(INVALID)?,
        tx.iter().map(|r| r.day).max().ok_or(INVALID)?,
    ))
}
pub fn shutdown_days(
    tx: &[Transaction],
    minimum: i64,
    include_trailing: bool,
) -> Result<BTreeSet<NaiveDate>> {
    if tx.is_empty() {
        return if include_trailing {
            Err(INVALID.into())
        } else {
            Ok(BTreeSet::new())
        };
    }
    let (first, last) = span(tx)?;
    let revenue = revenue_by_day(tx);
    let mut out = BTreeSet::new();
    let mut run = Vec::new();
    for day in range(first, last) {
        if revenue.get(&day).copied().unwrap_or(0.0) == 0.0 {
            run.push(day);
        } else {
            if run.len() as i64 >= minimum {
                out.extend(run.iter().copied());
            }
            run.clear();
        }
    }
    if include_trailing && run.len() as i64 >= minimum {
        out.extend(run);
    }
    Ok(out)
}
/// Pandas rolling means retain separate addition/removal compensation and avoid
/// cancellation artifacts for constant/all-positive/all-negative windows.
fn rolling_mean(values: &[f64], window: usize) -> Result<Vec<f64>> {
    if window < 7 {
        return Err(INVALID.into());
    }
    let mut result = Vec::new();
    let mut sum = 0.0;
    let mut add_c = 0.0;
    let mut remove_c = 0.0;
    let mut count = 0_usize;
    let mut negatives = 0_usize;
    let mut streak = 0_usize;
    let mut previous = values.first().copied().unwrap_or(0.0);
    for (i, &value) in values.iter().enumerate() {
        if i >= window {
            let old = values[i - window];
            if !old.is_nan() {
                count -= 1;
                let y = -old - remove_c;
                let t = sum + y;
                remove_c = (t - sum) - y;
                sum = t;
                negatives -= usize::from(old.is_sign_negative());
            }
        }
        if !value.is_nan() {
            count += 1;
            let y = value - add_c;
            let t = sum + y;
            add_c = (t - sum) - y;
            sum = t;
            negatives += usize::from(value.is_sign_negative());
            streak = if value == previous { streak + 1 } else { 1 };
            previous = value;
        }
        let mut mean = if count >= 7 {
            sum / count as f64
        } else {
            f64::NAN
        };
        if count >= 7 {
            if streak >= count {
                mean = previous;
            } else if (negatives == 0 && mean < 0.0) || (negatives == count && mean > 0.0) {
                mean = 0.0;
            }
        }
        result.push(mean);
    }
    Ok(result)
}
pub fn recent_levels(tx: &[Transaction], dates: &[NaiveDate], windows: &[usize]) -> Result<Value> {
    let columns = ["revenue", "variable_cost"]
        .into_iter()
        .flat_map(|kind| windows.iter().map(move |n| format!("recent_{kind}_{n}d")))
        .collect::<Vec<_>>();
    if tx.is_empty() || dates.is_empty() {
        return Ok(frame(
            &columns,
            dates,
            dates
                .iter()
                .map(|_| vec![float(f64::NAN); columns.len()])
                .collect(),
        ));
    }
    let mut grouped: BTreeMap<NaiveDate, [Sum; 2]> = BTreeMap::new();
    for row in tx {
        let slot = grouped.entry(row.day).or_default();
        let revenue = if row.category.as_deref() == Some("revenue") {
            row.amount
        } else {
            0.0
        };
        let variable = if row.category.as_deref().is_some_and(|c| {
            c == "revenue" || c == "partner_investment" || FIXED_CATEGORIES.contains(&c)
        }) {
            -0.0
        } else {
            -row.amount
        };
        slot[0].add(revenue);
        slot[1].add(variable);
    }
    let monotonic = tx.windows(2).all(|r| r[0].day <= r[1].day);
    let open = grouped
        .into_iter()
        .map(|(d, s)| (d, [s[0].result(monotonic), s[1].result(monotonic)]))
        .filter(|(_, s)| s[0] > 0.0)
        .collect::<Vec<_>>();
    if open.is_empty() {
        return Ok(frame(
            &columns,
            dates,
            dates
                .iter()
                .map(|_| vec![float(f64::NAN); columns.len()])
                .collect(),
        ));
    }
    let mut rolled = BTreeMap::new();
    for &window in windows {
        for kind in 0..2 {
            let values = open.iter().map(|(_, s)| s[kind]).collect::<Vec<_>>();
            rolled.insert((kind, window), rolling_mean(&values, window)?);
        }
    }
    let mut rows = Vec::new();
    for day in dates {
        let known = open.partition_point(|(d, _)| d < day);
        let mut row = Vec::new();
        for kind in 0..2 {
            for window in windows {
                let value = if known == 0 {
                    f64::NAN
                } else {
                    rolled
                        .get(&(kind, *window))
                        .and_then(|v| v.get(known - 1))
                        .copied()
                        .ok_or(INVALID)?
                };
                row.push(float(value));
            }
        }
        rows.push(row);
    }
    Ok(frame(&columns, dates, rows))
}
pub fn baseline(rows: &[Value], dates: &[NaiveDate], applied: bool) -> Result<Value> {
    if rows.len() != dates.len() {
        return Err(INVALID.into());
    }
    let values = rows
        .iter()
        .map(|row| {
            let revenue = (f64_value(&row["recent_revenue_28d"])?
                + f64_value(&row["recent_revenue_90d"])?)
                / 2.0;
            let cost = (f64_value(&row["recent_variable_cost_28d"])?
                + f64_value(&row["recent_variable_cost_90d"])?)
                / 2.0;
            let mut value = revenue - cost;
            if value.is_nan() {
                value = 0.0;
            }
            if applied {
                value *= 1.0 - f64_value(&row["shutdown_flag"])?;
            }
            Ok(float(value))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(series(Some("recent_level_baseline"), dates, values))
}
pub fn daily_cash_flow(
    tx: &[Transaction],
    start: Option<NaiveDate>,
    end: Option<NaiveDate>,
    excluded: &BTreeSet<String>,
) -> Result<Value> {
    if tx.is_empty() {
        return Ok(frame(&names(&CASH_COLUMNS), &[], vec![]));
    }
    let (first, last) = span(tx)?;
    let dates = range(start.unwrap_or(first), end.unwrap_or(last));
    let mut grouped: BTreeMap<NaiveDate, ([Sum; 4], i64)> = BTreeMap::new();
    for row in tx
        .iter()
        .filter(|r| !r.category.as_ref().is_some_and(|c| excluded.contains(c)))
    {
        let (s, count) = grouped.entry(row.day).or_default();
        *count += 1;
        s[0].add(row.amount);
        if row.amount > 0.0 {
            s[1].add(row.amount);
        }
        if row.amount < 0.0 {
            s[2].add(row.amount);
        }
        if row.category.as_deref() == Some("revenue") {
            s[3].add(row.amount);
        }
    }
    let filtered = tx
        .iter()
        .filter(|r| !r.category.as_ref().is_some_and(|c| excluded.contains(c)))
        .collect::<Vec<_>>();
    let monotonic = [
        filtered.clone(),
        filtered
            .iter()
            .copied()
            .filter(|r| r.amount > 0.0)
            .collect(),
        filtered
            .iter()
            .copied()
            .filter(|r| r.amount < 0.0)
            .collect(),
        filtered
            .iter()
            .copied()
            .filter(|r| r.category.as_deref() == Some("revenue"))
            .collect(),
    ]
    .map(|rows| rows.windows(2).all(|r| r[0].day <= r[1].day));
    let rows = dates
        .iter()
        .map(|d| {
            let (sum, count) = grouped.get(d).cloned().unwrap_or_default();
            let mut row = sum
                .iter()
                .enumerate()
                .map(|(i, s)| {
                    let value = s.result(monotonic[i]);
                    float(if value.is_nan() { 0.0 } else { value })
                })
                .collect::<Vec<_>>();
            row.push(json!(count));
            row
        })
        .collect();
    Ok(frame(&names(&CASH_COLUMNS), &dates, rows))
}
pub fn daily_features(
    tx: &[Transaction],
    end: Option<NaiveDate>,
    excluded: &BTreeSet<String>,
    minimum: i64,
) -> Result<Value> {
    let mut columns = names(&CASH_COLUMNS);
    columns.extend(names(&CALENDAR_COLUMNS));
    if tx.is_empty() {
        return Ok(frame(&columns, &[], vec![]));
    }
    let (first, last) = span(tx)?;
    let dates = range(first, end.unwrap_or(last).max(first));
    let shutdown = shutdown_days(tx, minimum, false)?
        .into_iter()
        .map(|d| (d.month(), d.day()))
        .collect();
    let calendar = calendar_features(&dates, &shutdown, &infer_due_days(tx, 3))?;
    let cash = daily_cash_flow(tx, Some(first), Some(last), excluded)?;
    let historical = cash["rows"].as_array().ok_or(INVALID)?;
    let calendars = calendar["rows"].as_array().ok_or(INVALID)?;
    let extended = dates.last().is_some_and(|d| *d > last);
    let rows = dates
        .iter()
        .enumerate()
        .map(|(i, day)| {
            let mut row = if *day <= last {
                historical
                    .get(i)
                    .and_then(Value::as_array)
                    .cloned()
                    .ok_or(INVALID)?
            } else {
                vec![float(f64::NAN); 5]
            };
            if extended && *day <= last {
                row[4] = float(row[4].as_i64().ok_or(INVALID)? as f64);
            }
            row.extend(calendars[i].as_array().ok_or(INVALID)?.iter().cloned());
            Ok(row)
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(frame(&columns, &dates, rows))
}
type Profile = Vec<(i64, f64)>;
fn round_money(value: f64) -> Result<f64> {
    format!("{value:.2}").parse().map_err(|_| INVALID.into())
}
fn profiles(rows: &[&Transaction], months: &[i32]) -> Result<BTreeMap<i32, Profile>> {
    let mut grouped: BTreeMap<(i32, u32), Sum> = BTreeMap::new();
    for row in rows {
        grouped
            .entry((month_id(row.day), row.day.day()))
            .or_default()
            .add(row.amount);
    }
    let mut result = months
        .iter()
        .map(|m| (*m, Vec::new()))
        .collect::<BTreeMap<_, _>>();
    for ((month, day), sum) in grouped {
        if let Some(profile) = result.get_mut(&month) {
            profile.push((
                day as i64,
                round_money(sum.result(rows.windows(2).all(|r| r[0].day <= r[1].day)))?,
            ));
        }
    }
    Ok(result)
}
fn same_profile(a: &Profile, b: &Profile) -> bool {
    a.len() == b.len()
        && a.iter().zip(b).all(|((da, xa), (db, xb))| {
            da == db && (xa - xb).abs() <= 0.05 * xa.abs().max(xb.abs()).max(1e-9)
        })
}
fn infer_period(months: &[i32], profiles: &BTreeMap<i32, Profile>) -> i32 {
    for period in [1, 2, 3, 4, 6, 12] {
        let pairs = months
            .iter()
            .filter_map(|m| profiles.get(&(m - period)).map(|previous| (*m, previous)))
            .collect::<Vec<_>>();
        if !pairs.is_empty()
            && pairs
                .iter()
                .all(|(m, b)| profiles.get(m).is_some_and(|a| same_profile(a, b)))
        {
            return period;
        }
    }
    1
}
fn apply_posted(profile: &Profile, posted: &Profile) -> Profile {
    let mut out = profile.clone();
    let mut pairs = Vec::new();
    for (j, (day, _)) in posted.iter().enumerate() {
        for (i, (due, _)) in out.iter().enumerate() {
            pairs.push(((due - day).abs(), i, j));
        }
    }
    pairs.sort();
    let mut free = (0..out.len()).collect::<BTreeSet<_>>();
    let mut used = BTreeSet::new();
    for (distance, i, j) in pairs {
        if distance <= 7 && free.contains(&i) && !used.contains(&j) {
            out[i] = posted[j];
            free.remove(&i);
            used.insert(j);
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.total_cmp(&b.1)));
    out
}
pub fn infer_schedule(tx: &[Transaction], categories: &[String]) -> Result<Value> {
    if tx.is_empty() {
        return Ok(json!({"categories":{}}));
    }
    let (first, last) = span(tx)?;
    let complete = (month_id(first)..=month_id(last))
        .filter(|m| !(*m == month_id(first) && first.day() > 3))
        .filter_map(|m| match month_end(m) {
            Ok(day) if day <= last => Some(Ok(m)),
            Ok(_) => None,
            Err(error) => Some(Err(error)),
        })
        .collect::<Result<Vec<_>>>()?;
    let mut result = Map::new();
    for category in categories {
        let rows = tx
            .iter()
            .filter(|r| r.category.as_ref() == Some(category))
            .collect::<Vec<_>>();
        let Some(started) = rows.iter().map(|r| month_id(r.day)).min() else {
            continue;
        };
        let months = complete
            .iter()
            .copied()
            .filter(|m| *m >= started)
            .collect::<Vec<_>>();
        let Some(latest_month) = months.last() else {
            continue;
        };
        let prof = profiles(&rows, &months)?;
        let period = infer_period(&months, &prof);
        let mut by_position: BTreeMap<i32, Profile> = BTreeMap::new();
        for month in &months {
            by_position.insert(
                month.rem_euclid(period),
                prof.get(month).cloned().ok_or(INVALID)?,
            );
        }
        let mut latest = prof.get(latest_month).cloned().ok_or(INVALID)?;
        let current = month_id(last);
        if !complete.contains(&current) {
            let current_profiles = profiles(&rows, &[current])?;
            let posted = current_profiles.get(&current).ok_or(INVALID)?;
            if !posted.is_empty() {
                let position = current.rem_euclid(period);
                let updated = apply_posted(by_position.get(&position).unwrap_or(&latest), posted);
                if period == 1 {
                    latest = updated.clone();
                }
                by_position.insert(position, updated);
            }
        }
        let profiles = by_position
            .into_iter()
            .map(|(pos, p)| (pos.to_string(), json!(p)))
            .collect::<Map<_, _>>();
        result.insert(
            category.into(),
            json!({"period":period,"profiles":profiles,"latest":latest}),
        );
    }
    Ok(json!({"categories":result,"as_of":last.to_string()}))
}
fn read_profile(value: &Value) -> Result<Profile> {
    value
        .as_array()
        .ok_or(INVALID)?
        .iter()
        .map(|pair| Ok((integer(&pair[0])?, f64_value(&pair[1])?)))
        .collect()
}
pub fn scheduled_amounts(spec: &Value, dates: &[NaiveDate], total: bool) -> Result<Value> {
    let empty = Map::new();
    let categories = spec
        .get("categories")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let columns = categories.keys().cloned().collect::<Vec<_>>();
    let mut rows = vec![Vec::new(); dates.len()];
    let mut floating = Vec::new();
    for info in categories.values() {
        let period = integer(&info["period"])?;
        if period == 0 {
            return Err(INVALID.into());
        }
        let mut float_column = false;
        for (i, day) in dates.iter().enumerate() {
            let position = (month_id(*day) as i64).rem_euclid(period).to_string();
            let raw = info["profiles"].get(&position).unwrap_or(&info["latest"]);
            let profile = read_profile(raw)?;
            let mut sum = 0.0;
            for (j, (due, amount)) in profile.into_iter().enumerate() {
                if due.min(days_in_month(*day) as i64) == day.day() as i64 {
                    if !raw[j][1].is_number() {
                        return Err(INVALID.into());
                    }
                    float_column |= raw[j][1].as_number().is_some_and(|n| n.is_f64());
                    sum += amount;
                }
            }
            rows[i].push(sum);
        }
        floating.push(float_column);
    }
    if total {
        let is_float = columns.is_empty() || floating.iter().any(|v| *v);
        return Ok(series(
            Some("scheduled_fixed"),
            dates,
            rows.into_iter()
                .map(|row| {
                    let sum = row.iter().fold(0.0, |sum, value| sum + value);
                    if is_float {
                        float(sum)
                    } else {
                        json!(sum as i64)
                    }
                })
                .collect(),
        ));
    }
    let typed = rows
        .into_iter()
        .map(|row| {
            row.into_iter()
                .enumerate()
                .map(|(i, value)| {
                    if floating[i] {
                        float(value)
                    } else {
                        json!(value as i64)
                    }
                })
                .collect()
        })
        .collect();
    Ok(frame(&columns, dates, typed))
}
pub fn legacy_offsets(sigma: f64, days: i64) -> (f64, f64) {
    if days <= 0 {
        return (0.0, 0.0);
    }
    let width = 1.2816 * sigma * (days as f64).sqrt();
    (-width, width)
}
pub fn band_offsets(spec: &Value, days: i64) -> Result<(f64, f64)> {
    if days <= 0 {
        return Ok((0.0, 0.0));
    }
    let p = f64_value(&spec["p"])?;
    let reach = f64_value(&spec["horizon_measured"])?;
    let day = days as f64;
    let scale = if day <= reach {
        day.powf(p)
    } else {
        if reach == 0.0 {
            return Err(INVALID.into());
        }
        reach.powf(p) * (1.0 + p * (day - reach) / reach)
    };
    Ok((
        -f64_value(&spec["lower"])? * scale,
        f64_value(&spec["upper"])? * scale,
    ))
}
fn array_strings(body: &Value, name: &str) -> Result<Vec<String>> {
    body[name]
        .as_array()
        .ok_or(INVALID)?
        .iter()
        .map(|s| s.as_str().map(str::to_string).ok_or_else(|| INVALID.into()))
        .collect()
}
/// Dispatch is the characterization interface; direct native functions above are
/// available to model inference and do not encode their numeric outputs as bits.
pub fn dispatch(body: &Value) -> Result<Option<Value>> {
    let op = body.get("op").and_then(Value::as_str).unwrap_or_default();
    let tx = transactions(body)?;
    let minimum = body.get("min_run").map(integer).transpose()?.unwrap_or(7);
    let out = match op {
        "feature_columns" => json!(feature_set_columns(body["name"].as_str().ok_or(INVALID)?)?),
        "has_schedule" => json!(body["name"] == "components"),
        "needs_levels" => {
            let name = body["name"].as_str().ok_or(INVALID)?;
            let columns = feature_set_columns(name)?;
            json!(
                matches!(name, "components" | "level_baseline")
                    || columns.iter().any(|c| LEVEL_COLUMNS.contains(&c.as_str()))
            )
        }
        "holidays" => json!(
            public_holidays(integer(&body["year"])? as i32)?
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
        ),
        "calendar" => {
            let dates = date_list(body)?;
            let mut shutdown = BTreeSet::new();
            if let Some(pairs) = body.get("shutdown_days") {
                for pair in pairs.as_array().ok_or(INVALID)? {
                    shutdown.insert((integer(&pair[0])? as u32, integer(&pair[1])? as u32));
                }
            }
            let mut due = DueDays::new();
            if let Some(days) = body.get("due_days").filter(|v| !v.is_null()) {
                for (key, val) in days.as_object().ok_or(INVALID)? {
                    due.insert(
                        key.clone(),
                        val.as_array()
                            .ok_or(INVALID)?
                            .iter()
                            .map(integer)
                            .collect::<Result<_>>()?,
                    );
                }
            }
            calendar_features(&dates, &shutdown, &due)?
        }
        "infer_due" => json!(infer_due_days(
            &tx,
            body.get("min_months")
                .map(integer)
                .transpose()?
                .unwrap_or(3)
        )),
        "infer_shutdown" => json!(
            shutdown_days(&tx, minimum, false)?
                .iter()
                .map(|d| (d.month(), d.day()))
                .collect::<BTreeSet<_>>()
        ),
        "closed" => {
            let (first, last) = span(&tx)?;
            let dates = range(first, last);
            let closed = shutdown_days(&tx, minimum, true)?;
            series(
                None,
                &dates,
                dates
                    .iter()
                    .map(|d| json!(i64::from(closed.contains(d))))
                    .collect(),
            )
        }
        "recent" => {
            let windows = body
                .get("windows")
                .map(|v| {
                    v.as_array()
                        .ok_or(INVALID)?
                        .iter()
                        .map(|n| usize::try_from(integer(n)?).map_err(|_| INVALID.into()))
                        .collect::<Result<Vec<_>>>()
                })
                .transpose()?
                .unwrap_or_else(|| vec![28, 90]);
            recent_levels(&tx, &date_list(body)?, &windows)?
        }
        "baseline" | "applied_baseline" => baseline(
            body["rows"].as_array().ok_or(INVALID)?,
            &date_list(body)?,
            op == "applied_baseline",
        )?,
        "daily_cash" => {
            let excluded = if body.get("exclude_categories").is_some() {
                array_strings(body, "exclude_categories")?
            } else {
                Vec::new()
            };
            daily_cash_flow(
                &tx,
                option_date(body, "start")?,
                option_date(body, "end")?,
                &excluded.into_iter().collect(),
            )?
        }
        "daily_features" => {
            let excluded = if body.get("exclude_categories").is_some() {
                array_strings(body, "exclude_categories")?
            } else {
                Vec::new()
            };
            daily_features(
                &tx,
                option_date(body, "end")?,
                &excluded.into_iter().collect(),
                minimum,
            )?
        }
        "schedule_infer" => {
            let categories = if body.get("categories").is_some() {
                array_strings(body, "categories")?
            } else {
                names(&FIXED_CATEGORIES)
            };
            infer_schedule(&tx, &categories)?
        }
        "schedule_amounts" | "schedule_total" => {
            scheduled_amounts(&body["spec"], &date_list(body)?, op == "schedule_total")?
        }
        "legacy_offsets" => {
            let (lower, upper) =
                legacy_offsets(f64_value(&body["sigma"])?, integer(&body["days"])?);
            json!([float(lower), float(upper)])
        }
        "band_offsets" => {
            let (lower, upper) = band_offsets(&body["spec"], integer(&body["days"])?)?;
            json!([float(lower), float(upper)])
        }
        _ => return Ok(None),
    };
    Ok(Some(out))
}
