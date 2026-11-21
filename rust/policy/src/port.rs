//! Faithful pure Scout policy classification and transaction context.
//! `evaluate` is the characterization adapter; `classify` and `context_from_transactions`
//! expose the native operations. LLM-backed `parse_rule` is a separate, unported unit.
#![forbid(unsafe_code)]

use bigdecimal::{BigDecimal, Context, RoundingMode};
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime, SecondsFormat};
use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::{Signed, Zero};
use serde_json::{Map, Value, json};
use std::{collections::BTreeMap, str::FromStr, sync::OnceLock};

type Result<T> = std::result::Result<T, String>;
type Decimal = BigDecimal;
const INVALID: &str = "invalid policy input";

fn context() -> Context {
    Context::default().with_prec(28_u64).unwrap_or_default()
}
fn rounded(n: Decimal) -> Decimal {
    if n.digits() > 28 {
        context().round_decimal(n)
    } else {
        n
    }
}
fn add(a: &Decimal, b: &Decimal) -> Decimal {
    rounded(a + b)
}
fn multiply(a: &Decimal, b: &Decimal) -> Decimal {
    rounded(a * b)
}
fn number(value: &Value) -> Result<Decimal> {
    match value {
        Value::String(s) => Decimal::from_str(s),
        Value::Number(n) => Decimal::from_str(&n.to_string()),
        _ => return Err(INVALID.into()),
    }
    .map_err(|_| INVALID.into())
}
fn pow10(n: i64) -> Result<BigInt> {
    Ok(BigInt::from(10).pow(u32::try_from(n).map_err(|_| INVALID)?))
}
/// Decimal division with Python's precision=28, HALF_EVEN and preferred exponent.
fn divide(a: &Decimal, b: &Decimal) -> Result<Decimal> {
    if b.is_zero() {
        return Err(INVALID.into());
    }
    let (an, sa) = a.as_bigint_and_scale();
    let (bn, sb) = b.as_bigint_and_scale();
    let preferred = sa - sb;
    if an.is_zero() {
        return Ok(Decimal::new(BigInt::zero(), preferred));
    }
    let sign = an.signum() * bn.signum();
    let n = an.abs();
    let d = bn.abs();
    let order = n.to_string().len() as i64 - d.to_string().len() as i64;
    let at_least = if order >= 0 {
        n >= &d * pow10(order)?
    } else {
        &n * pow10(-order)? >= d
    };
    let adjusted = order - i64::from(!at_least) - preferred;
    let mut scale = 27 - adjusted;
    let shift = scale - preferred;
    let numerator = &n * pow10(shift.max(0))?;
    let denominator = &d * pow10((-shift).max(0))?;
    let (mut q, rem) = numerator.div_rem(&denominator);
    let twice = &rem * 2;
    if twice > denominator || (twice == denominator && q.is_odd()) {
        q += 1;
    }
    if rem.is_zero() {
        while scale > preferred && (&q % 10_u8).is_zero() {
            q /= 10;
            scale -= 1;
        }
    }
    Ok(rounded(Decimal::new(q * sign, scale)))
}
fn decimal_string(n: &Decimal) -> String {
    let (coefficient, scale) = n.as_bigint_and_scale();
    let digits = coefficient.abs().to_string();
    let adjusted = digits.len() as i64 - 1 - scale;
    if scale < 0 || adjusted < -6 {
        let fraction = if digits.len() > 1 {
            format!(".{}", &digits[1..])
        } else {
            String::new()
        };
        let sign = if coefficient.is_negative() { "-" } else { "" };
        format!("{sign}{}{fraction}E{adjusted:+}", &digits[..1])
    } else {
        n.to_plain_string()
    }
}
fn fixed(n: &Decimal, places: i64) -> String {
    n.with_scale_round(places, RoundingMode::HalfEven)
        .to_plain_string()
}
fn eur(n: &Decimal) -> String {
    let s = fixed(n, 2);
    let (whole, fraction) = s.split_once('.').unwrap_or((&s, "00"));
    let mut grouped = String::new();
    for (i, c) in whole.chars().enumerate() {
        if i > 0 && (whole.len() - i).is_multiple_of(3) && c != '-' {
            grouped.push(',');
        }
        grouped.push(c);
    }
    format!("{grouped}.{fraction} EUR")
}
fn whitespace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
fn norm(s: &str) -> Option<String> {
    static CASEFOLD: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    let table = CASEFOLD.get_or_init(|| {
        serde_json::from_str(include_str!("../unicode-casefold.json")).unwrap_or_default()
    });
    let collapsed = s
        .split(whitespace)
        .filter(|x| !x.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    if collapsed.is_empty() {
        return None;
    }
    Some(
        collapsed
            .chars()
            .map(|c| {
                table
                    .get(&c.to_string())
                    .cloned()
                    .unwrap_or_else(|| c.to_string())
            })
            .collect(),
    )
}
fn optional_text<'a>(v: &'a Value, field: &str) -> Result<Option<&'a str>> {
    match v.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        _ => Err(INVALID.into()),
    }
}
fn text<'a>(v: &'a Value, field: &str) -> Result<&'a str> {
    optional_text(v, field)?.ok_or_else(|| INVALID.into())
}
fn money(v: &Value, field: &str) -> Result<Option<Decimal>> {
    match v.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => Ok(Some(number(value)?)),
    }
}
fn rules(v: &Value) -> Result<&[Value]> {
    match v.get("rules") {
        None => Ok(&[]),
        Some(Value::Array(r)) => Ok(r),
        _ => Err(INVALID.into()),
    }
}
fn canonical(v: Value) -> Value {
    match v {
        Value::Object(m) => Value::Object(
            m.into_iter()
                .collect::<BTreeMap<_, _>>()
                .into_iter()
                .map(|(k, v)| (k, canonical(v)))
                .collect(),
        ),
        Value::Array(a) => Value::Array(a.into_iter().map(canonical).collect()),
        v => v,
    }
}
fn decision(level: &str, reason: String) -> Value {
    json!({"level":level,"reason":reason})
}
fn active(v: &Value) -> Result<bool> {
    match v.get("active") {
        None => Ok(true),
        Some(Value::Bool(b)) => Ok(*b),
        Some(Value::Number(n)) if n.as_i64() == Some(0) => Ok(false),
        Some(Value::Number(n)) if n.as_i64() == Some(1) => Ok(true),
        Some(Value::String(s))
            if ["true", "1", "on", "yes", "t", "y"].contains(&s.to_lowercase().as_str()) =>
        {
            Ok(true)
        }
        Some(Value::String(s))
            if ["false", "0", "off", "no", "f", "n"].contains(&s.to_lowercase().as_str()) =>
        {
            Ok(false)
        }
        _ => Err(INVALID.into()),
    }
}
struct RuleTime {
    value: DateTime<chrono::FixedOffset>,
    aware: bool,
}
impl RuleTime {
    fn formatted(&self) -> String {
        let precision = if self.value.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        };
        let value = self.value.to_rfc3339_opts(precision, true);
        if self.aware {
            value
        } else {
            value.trim_end_matches('Z').into()
        }
    }
}
fn timestamp(v: &Value) -> Result<RuleTime> {
    let raw = text(v, "created_at")?;
    if let Ok(value) = DateTime::parse_from_rfc3339(raw) {
        return Ok(RuleTime { value, aware: true });
    }
    let value = NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S%.f"))
        .or_else(|_| {
            NaiveDate::parse_from_str(raw, "%Y-%m-%d")
                .map(|date| date.and_time(chrono::NaiveTime::MIN))
        })
        .map_err(|_| INVALID)?;
    Ok(RuleTime {
        value: value.and_utc().fixed_offset(),
        aware: false,
    })
}
fn validate_rule(v: &Value) -> Result<()> {
    text(v, "text")?;
    if !["AUTO", "NOTIFY", "APPROVAL"].contains(&text(v, "level")?) {
        return Err(INVALID.into());
    }
    for field in ["id", "created_by", "category", "supplier", "action_type"] {
        optional_text(v, field)?;
    }
    active(v)?;
    timestamp(v)?;
    Ok(())
}

/// Native classification over the same JSON model fields as PolicyAction/PolicyContext.
pub fn classify(action: &Value, ctx: &Value) -> Result<Value> {
    if !action.is_object() || !ctx.is_object() {
        return Err(INVALID.into());
    }
    let action_type = text(action, "action_type")?;
    let amount = money(action, "amount")?;
    let supplier = optional_text(action, "supplier")?;
    let category = optional_text(action, "category")?;
    let inflows = money(ctx, "avg_monthly_inflows")?;
    let mut typical = BTreeMap::new();
    if let Some(t) = ctx.get("typical_amounts") {
        for (k, v) in t.as_object().ok_or(INVALID)? {
            typical.insert(norm(k), rounded(number(v)?.abs()));
        }
    }
    let rules = rules(ctx)?;
    for r in rules {
        validate_rule(r)?;
    }
    let mut best: Option<(&Value, RuleTime)> = None;
    for rule in rules {
        if !active(rule)? {
            continue;
        }
        let mut count = 0;
        let mut matches = true;
        for (field, value) in [
            ("category", category),
            ("supplier", supplier),
            ("action_type", Some(action_type)),
        ] {
            if let Some(wanted) = optional_text(rule, field)?.and_then(norm) {
                count += 1;
                matches &= value.and_then(norm) == Some(wanted);
            }
        }
        if count == 0 || !matches {
            continue;
        }
        let at = timestamp(rule)?;
        if let Some((_, previous)) = &best
            && at.aware != previous.aware
        {
            return Err(INVALID.into());
        }
        if best.as_ref().is_none_or(|(_, b)| at.value >= b.value) {
            best = Some((rule, at));
        }
    }
    if let Some((rule, _)) = best {
        let level = text(rule, "level")?;
        return Ok(decision(
            level,
            format!(
                "CEO rule \"{}\" applies, so this is {level}.",
                text(rule, "text")?
            ),
        ));
    }
    if [
        "request_credit_line",
        "open_deposit",
        "make_investment",
        "fx_hedge",
        "change_team_budget",
    ]
    .contains(&norm(action_type).as_deref().unwrap_or(""))
    {
        return Ok(decision(
            "APPROVAL",
            format!(
                "{} always needs the CEO's approval.",
                action_type.replace('_', " ")
            ),
        ));
    }
    let Some(amount) = amount else {
        return Ok(decision(
            "AUTO",
            "No money moves, so the agent acts on its own.".into(),
        ));
    };
    let amount = rounded(amount.abs());
    let key = supplier.and_then(norm);
    let known = key.is_some() && typical.contains_key(&key);
    let typical_amount = typical.get(&key);
    let who = supplier
        .filter(|s| !s.is_empty())
        .unwrap_or("this supplier");
    if let Some(t) = typical_amount.filter(|t| known && **t > Decimal::zero()) {
        let difference = rounded((&amount - t).abs());
        let tolerance = multiply(t, &Decimal::new(20.into(), 2));
        if difference <= tolerance {
            return Ok(decision(
                "AUTO",
                format!(
                    "Routine payment: {} to {who} is within 20% of the typical {}.",
                    eur(&amount),
                    eur(t)
                ),
            ));
        }
    }
    let share = match &inflows {
        Some(i) if *i > Decimal::zero() => Some(divide(&amount, i)?),
        _ => None,
    };
    if share
        .as_ref()
        .is_some_and(|s| *s < Decimal::new(1.into(), 2))
    {
        if !known {
            return Ok(decision(
                "NOTIFY",
                format!(
                    "New supplier {who}: {} is below 1% of monthly inflows, so the agent acts and informs.",
                    eur(&amount)
                ),
            ));
        }
        if let Some(i) = inflows {
            return Ok(decision(
                "AUTO",
                format!(
                    "{} is below 1% of average monthly inflows ({}).",
                    eur(&amount),
                    eur(&i)
                ),
            ));
        }
    }
    let Some(share) = share else {
        return Ok(decision(
            "APPROVAL",
            format!(
                "{} to {who} is not routine and average monthly inflows are unknown, so the CEO decides.",
                eur(&amount)
            ),
        ));
    };
    let percent = format!("{}%", fixed(&multiply(&share, &Decimal::from(100)), 1));
    if share > Decimal::new(5.into(), 2) {
        return Ok(decision(
            "APPROVAL",
            format!(
                "{} is {percent} of average monthly inflows, above the 5% limit.",
                eur(&amount)
            ),
        ));
    }
    if !known {
        return Ok(decision(
            "APPROVAL",
            format!(
                "New supplier {who}: first payments of at least 1% of monthly inflows need the CEO's approval."
            ),
        ));
    }
    let t = typical_amount.ok_or(INVALID)?;
    Ok(decision(
        "NOTIFY",
        format!(
            "Unusual amount: {} to {who} differs from the typical {} but is only {percent} of monthly inflows, so the agent acts and informs.",
            eur(&amount),
            eur(t)
        ),
    ))
}
fn normalized_rule(v: &Value) -> Result<Value> {
    validate_rule(v)?;
    let mut out = Map::new();
    for (field, default) in [
        ("id", Value::Null),
        ("text", Value::Null),
        ("level", Value::Null),
        ("category", Value::Null),
        ("supplier", Value::Null),
        ("action_type", Value::Null),
        ("created_by", json!("ceo")),
    ] {
        out.insert(field.into(), v.get(field).cloned().unwrap_or(default));
    }
    out.insert("created_at".into(), json!(timestamp(v)?.formatted()));
    out.insert("active".into(), json!(active(v)?));
    Ok(Value::Object(out))
}
/// Native bank-statement context; returned money remains exact Decimal strings.
pub fn context_from_transactions(transactions: &[Value], rules: &[Value]) -> Result<Value> {
    let rules = rules
        .iter()
        .map(normalized_rule)
        .collect::<Result<Vec<_>>>()?;
    if transactions.is_empty() {
        return Ok(json!({"avg_monthly_inflows":null,"typical_amounts":{},"rules":rules}));
    }
    let mut dates = Vec::new();
    let mut inflows = Decimal::zero();
    let mut outflows: BTreeMap<String, (String, Vec<Decimal>)> = BTreeMap::new();
    for t in transactions {
        for field in ["type", "reference", "counterparty"] {
            text(t, field)?;
        }
        let day =
            NaiveDate::parse_from_str(text(t, "booking_date")?, "%Y-%m-%d").map_err(|_| INVALID)?;
        dates.push(day);
        let amount = number(t.get("amount").ok_or(INVALID)?)?;
        if amount > Decimal::zero() {
            inflows = add(&inflows, &amount);
        }
        if amount < Decimal::zero() {
            let name = text(t, "counterparty")?;
            let key = norm(name).unwrap_or_else(|| name.into());
            outflows
                .entry(key)
                .or_insert_with(|| (name.into(), Vec::new()))
                .1
                .push(rounded(-amount));
        }
    }
    dates.sort();
    let first = dates.first().ok_or(INVALID)?;
    let last = dates.last().ok_or(INVALID)?;
    let months = (last.year() - first.year()) * 12 + last.month() as i32 - first.month() as i32 + 1;
    let avg = divide(&inflows, &Decimal::from(months))?;
    let mut typical = Map::new();
    for (_, (name, mut amounts)) in outflows {
        amounts.sort();
        let n = amounts.len();
        let median = if n % 2 == 1 {
            amounts[n / 2].clone()
        } else {
            divide(
                &add(&amounts[n / 2 - 1], &amounts[n / 2]),
                &Decimal::from(2),
            )?
        };
        typical.insert(name, json!(decimal_string(&median)));
    }
    Ok(json!({"avg_monthly_inflows":decimal_string(&avg),"typical_amounts":typical,"rules":rules}))
}

/// Read-only JSON characterization boundary; original invalid-input sentinels are retained.
pub fn evaluate(payload: String) -> Result<String> {
    let Ok(body) = serde_json::from_str::<Value>(&payload) else {
        return Ok("invalid JSON".into());
    };
    let op = body.get("op").and_then(Value::as_str);
    if !matches!(op, Some("classify" | "context")) {
        return Ok("unknown operation".into());
    }
    let output = (|| match op {
        Some("classify") => classify(
            body.get("action").ok_or(INVALID)?,
            body.get("context").ok_or(INVALID)?,
        ),
        _ => context_from_transactions(
            body.get("transactions")
                .and_then(Value::as_array)
                .ok_or(INVALID)?,
            rules(&body)?,
        ),
    })();
    Ok(match output {
        Ok(v) => canonical(v).to_string(),
        Err(_) => INVALID.into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn replay_recorded_original_cases() -> Result<()> {
        for line in include_str!("../cases.jsonl").lines() {
            let case: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
            let arg = case["args"][0].as_str().ok_or("missing payload")?;
            let actual = evaluate(arg.into())?;
            assert_eq!(json!(actual), case["ok"], "payload: {arg}");
        }
        Ok(())
    }
    #[test]
    fn unicode_and_decimal_boundaries() -> Result<()> {
        assert_eq!(
            norm("\u{1c}STRAßE\tGmbH\u{1f}"),
            Some("strasse gmbh".into())
        );
        assert_eq!(norm("İ K ς ﬀ"), Some("i\u{307} k σ ff".into()));
        assert_eq!(
            decimal_string(&divide(&Decimal::from(1), &Decimal::from(3))?),
            "0.3333333333333333333333333333"
        );
        assert_eq!(
            decimal_string(&divide(&number(&json!("100.00"))?, &Decimal::from(2))?),
            "50.00"
        );
        assert_eq!(eur(&number(&json!("1234567.895"))?), "1,234,567.90 EUR");
        assert_eq!(eur(&number(&json!("1.005"))?), "1.00 EUR");
        Ok(())
    }
}
