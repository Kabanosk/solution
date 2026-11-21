//! Exact integer-cent arithmetic and dashboard financial display models.
#![forbid(unsafe_code)]
use icu_collator::{Collator, options::CollatorOptions};
use icu_locale::locale;
use num_bigint::BigInt;
use num_traits::{ToPrimitive, Zero};
use serde_json::{Value, json};

fn js_space(c: char) -> bool {
    matches!(c, '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}')
}
fn bigint(value: &str) -> Result<BigInt, String> {
    let trimmed = value.trim_matches(js_space);
    if trimmed.is_empty() {
        return Ok(BigInt::zero());
    }
    let (radix, digits) = if trimmed.starts_with("0x") || trimmed.starts_with("0X") {
        (16, &trimmed[2..])
    } else if trimmed.starts_with("0o") || trimmed.starts_with("0O") {
        (8, &trimmed[2..])
    } else if trimmed.starts_with("0b") || trimmed.starts_with("0B") {
        (2, &trimmed[2..])
    } else {
        (10, trimmed)
    };
    let unsigned = if radix == 10 {
        digits.strip_prefix(['+', '-']).unwrap_or(digits)
    } else {
        digits
    };
    if unsigned.is_empty() || !unsigned.chars().all(|c| c.is_ascii() && c.is_digit(radix)) {
        return Err(format!("SyntaxError: Cannot convert {value} to a BigInt"));
    }
    BigInt::parse_bytes(digits.as_bytes(), radix)
        .ok_or_else(|| format!("SyntaxError: Cannot convert {value} to a BigInt"))
}
fn js_number(text: &str) -> f64 {
    let s = text.trim_matches(js_space);
    if s.is_empty() {
        return 0.0;
    }
    if s == "Infinity" || s == "+Infinity" {
        return f64::INFINITY;
    }
    if s == "-Infinity" {
        return f64::NEG_INFINITY;
    }
    if s.starts_with("0x")
        || s.starts_with("0X")
        || s.starts_with("0b")
        || s.starts_with("0B")
        || s.starts_with("0o")
        || s.starts_with("0O")
    {
        return bigint(s).ok().and_then(|n| n.to_f64()).unwrap_or(f64::NAN);
    }
    if !s
        .bytes()
        .all(|b| b.is_ascii_digit() || b"+-.eE".contains(&b))
    {
        return f64::NAN;
    }
    s.parse().unwrap_or(f64::NAN)
}
fn json_number(value: f64) -> Value {
    serde_json::Number::from_f64(value)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}
fn json_input(input: &str) -> Result<Value, String> {
    serde_json::from_str(input).map_err(|_| "Error: Invalid JSON input".into())
}
fn text(value: &Value) -> Result<&str, String> {
    value
        .as_str()
        .ok_or_else(|| "Error: Invalid invoice text".into())
}
fn optional(value: &Value) -> Result<Value, String> {
    if value.is_null() {
        Ok(Value::Null)
    } else {
        text(value).map(|s| Value::String(s.into()))
    }
}
fn object(value: &Value) -> Result<&Value, String> {
    if value.is_object() {
        Ok(value)
    } else {
        Err("Error: Invalid invoice".into())
    }
}
fn encode(value: &Value) -> String {
    match value {
        Value::Number(n) => {
            let mut b = ryu_js::Buffer::new();
            let number = n.as_f64().unwrap_or(f64::NAN);
            if number.is_finite() {
                b.format(number).to_string()
            } else {
                "null".into()
            }
        }
        Value::Array(a) => format!("[{}]", a.iter().map(encode).collect::<Vec<_>>().join(",")),
        Value::Object(o) => {
            let array_index = |key: &str| {
                key.parse::<u32>()
                    .ok()
                    .filter(|n| *n != u32::MAX && n.to_string() == key)
            };
            let mut entries: Vec<_> = o.iter().collect();
            entries.sort_by_key(|(key, _)| array_index(key).map_or((1, 0), |n| (0, n)));
            format!(
                "{{{}}}",
                entries
                    .into_iter()
                    .map(|(k, v)| format!("{}:{}", Value::String(k.clone()), encode(v)))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        }
        _ => value.to_string(),
    }
}
pub fn display_cents(value: &BigInt) -> Result<f64, String> {
    let max = BigInt::from(9_007_199_254_740_991i64);
    if value > &max || value < &-max {
        return Err("Error: Money aggregate exceeds display precision".into());
    }
    value
        .to_f64()
        .map(|v| v / 100.0)
        .ok_or_else(|| "Error: Money aggregate exceeds display precision".into())
}
pub fn decimal_cents(amount: &str) -> Result<BigInt, String> {
    let digits = amount.strip_prefix('-').unwrap_or(amount);
    let valid = digits.split_once('.').filter(|(whole, cents)| {
        !whole.is_empty()
            && whole.bytes().all(|b| b.is_ascii_digit())
            && cents.len() == 2
            && cents.bytes().all(|b| b.is_ascii_digit())
    });
    let Some((whole, cents)) = valid else {
        return Err("Error: Invalid money amount".into());
    };
    let value: BigInt = bigint(whole)? * 100 + bigint(cents)?;
    Ok(if amount.starts_with('-') {
        -value
    } else {
        value
    })
}
fn display_text(value: &Value) -> Result<Value, String> {
    Ok(json_number(display_cents(&bigint(text(value)?)?)?))
}
fn validate_rows(value: &Value, spend: bool) -> Result<&Vec<Value>, String> {
    let error = if spend {
        "Error: Invalid spend rows"
    } else {
        "Error: Invalid finance rows"
    };
    let rows = value.as_array().ok_or_else(|| error.to_string())?;
    for row in rows {
        let strings: &[&str] = if spend {
            &["name", "spent"]
        } else {
            &["count", "income", "expense"]
        };
        let nullable: &[&str] = if spend {
            &["id", "budget"]
        } else {
            &["category"]
        };
        if !row.is_object()
            || strings.iter().any(|k| !row[*k].is_string())
            || nullable
                .iter()
                .any(|k| row.get(*k).is_none_or(|v| !v.is_null() && !v.is_string()))
        {
            return Err(error.into());
        }
    }
    Ok(rows)
}
fn category(category: &Value) -> (String, &'static str) {
    let key = category.as_str();
    let (name, color) = match key {
        Some("payroll") => (Some("Payroll"), "var(--chart-balance)"),
        Some("social_security") => (Some("Social security"), "var(--chart-2)"),
        Some("rent") => (Some("Rent"), "var(--chart-3)"),
        Some("tax") => (Some("Tax"), "var(--chart-4)"),
        Some("utilities") => (Some("Utilities"), "var(--chart-5)"),
        Some("food") => (Some("Food"), "var(--chart-2)"),
        Some("beverages") => (Some("Beverages"), "var(--chart-3)"),
        Some("partner_investment") => (Some("Partner investment"), "var(--chart-5)"),
        _ => (None, "var(--chart-4)"),
    };
    (
        name.map(str::to_owned).unwrap_or_else(|| {
            key.map(|s| s.replace('_', " "))
                .unwrap_or_else(|| "Other".into())
        }),
        color,
    )
}
pub fn finance_period(rows: &Value) -> Result<Value, String> {
    let rows = validate_rows(rows, false)?;
    let mut income = BigInt::zero();
    let mut expense = BigInt::zero();
    let mut transactions = 0.0;
    for row in rows {
        income += bigint(text(&row["income"])?)?;
        expense += bigint(text(&row["expense"])?)?;
        transactions += js_number(text(&row["count"])?);
    }
    let revenue = display_cents(&income)?;
    let expenses = display_cents(&expense)?;
    let mut details = Vec::new();
    for row in rows {
        let cost = bigint(text(&row["expense"])?)?;
        if cost <= BigInt::zero() {
            continue;
        }
        let (name, color) = category(&row["category"]);
        let amount = display_cents(&cost)?;
        let ratio = if expense.is_zero() {
            0.0
        } else {
            ((&cost * BigInt::from(10000)) / &expense)
                .to_f64()
                .unwrap_or(f64::INFINITY)
                / 100.0
        };
        details.push(json!({"name":name,"color":color,"amount":json_number(amount),"percentage":json_number(ratio)}));
    }
    Ok(
        json!({"transactions":json_number(transactions),"totalRevenue":json_number(revenue),"totalExpenses":json_number(expenses),"expenses":details}),
    )
}
fn nullable_input(input: &str) -> Result<Option<String>, String> {
    let value = json_input(input)?;
    if value.is_null() {
        return Ok(None);
    }
    value
        .as_str()
        .map(|s| Some(s.to_owned()))
        .ok_or_else(|| "Error: Expected string or null".into())
}
fn slice_utf16(text: &str, start: usize, end: usize) -> String {
    let units: Vec<_> = text.encode_utf16().skip(start).take(end - start).collect();
    String::from_utf16_lossy(&units)
}
fn quarter_spend(
    start: Option<&str>,
    through: Option<&str>,
    rows: &Value,
) -> Result<Value, String> {
    let rows = validate_rows(rows, true)?;
    let begin = start.unwrap_or("1970-01-01");
    let quarter = (js_number(&slice_utf16(begin, 5, 7)) / 3.0).floor() + 1.0;
    let mut b = ryu_js::Buffer::new();
    let label = format!("Q{} {}", b.format(quarter), slice_utf16(begin, 0, 4));
    let mut teams = Vec::new();
    for row in rows {
        let spent = display_text(&row["spent"])?;
        let budget = if row["budget"].is_null() {
            Value::Null
        } else {
            display_text(&row["budget"])?
        };
        if !row["id"].is_null() || spent.as_f64().unwrap_or(0.0) > 0.0 {
            teams.push(json!({"id":row["id"],"name":row["name"],"spent":spent,"budget":budget}))
        }
    }
    let collator = Collator::try_new(locale!("en-US").into(), CollatorOptions::default())
        .map_err(|e| e.to_string())?;
    teams.sort_by(|a, b| {
        b["spent"]
            .as_f64()
            .partial_cmp(&a["spent"].as_f64())
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                collator.compare(
                    a["name"].as_str().unwrap_or(""),
                    b["name"].as_str().unwrap_or(""),
                )
            })
    });
    Ok(json!({"label":label,"start":begin,"through":through.unwrap_or(begin),"teams":teams}))
}
fn invoice_money(value: &Value) -> Result<Value, String> {
    let raw = text(value)?;
    let unsigned = raw.strip_prefix('-').unwrap_or(raw);
    let mut pieces = unsigned.split('.');
    let whole = pieces.next().unwrap_or("");
    let fraction = pieces.next();
    if whole.is_empty()
        || !whole.bytes().all(|b| b.is_ascii_digit())
        || pieces.next().is_some()
        || fraction
            .is_some_and(|f| !(1..=4).contains(&f.len()) || !f.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err("Error: Invalid invoice amount".into());
    }
    let cents = format!("{}00", fraction.unwrap_or(""));
    let normalized = format!(
        "{}{}.{}",
        if raw.starts_with('-') { "-" } else { "" },
        whole,
        &cents[..2]
    );
    Ok(json_number(display_cents(&decimal_cents(&normalized)?)?))
}
fn invoice_decimal(value: &Value) -> Result<Value, String> {
    let n = js_number(text(value)?);
    if !n.is_finite() {
        return Err("Error: Invalid number".into());
    }
    Ok(json_number(n))
}
fn parse_transaction_invoice(value: &Value) -> Result<Value, String> {
    let body = object(value)?;
    let txn = object(&body["transaction"])?;
    let inv = object(&body["invoice"])?;
    let rows = body["lines"]
        .as_array()
        .ok_or_else(|| "Error: Invalid invoice lines".to_string())?;
    let document = if body["document"].is_null() {
        None
    } else {
        Some(object(&body["document"])?)
    };
    let transaction = json!({"id":text(&txn["id"])?,"bookingDate":text(&txn["booking_date"])?,"reference":text(&txn["reference"])?,"counterparty":text(&txn["counterparty"])?,"amount":invoice_money(&txn["amount"])?,"currency":optional(&txn["currency"])?.as_str().unwrap_or("EUR")});
    let invoice = json!({"id":text(&inv["id"])?,"number":text(&inv["number"])?,"supplier":text(&inv["supplier"])?,"issueDate":text(&inv["issue_date"])?,"dueDate":optional(&inv["due_date"])?,"total":invoice_money(&inv["total"])?,"currency":optional(&inv["currency"])?.as_str().unwrap_or("EUR"),"source":optional(&inv["source"])?.as_str().unwrap_or("db"),"category":optional(&inv["category"])?});
    for row in rows {
        object(row)?;
    }
    let mut lines = Vec::new();
    for line in rows {
        let position = js_numeric_value(line.get("position"));
        lines.push(json!({"position":json_number(position),"description":text(&line["description"])?,"quantity":invoice_decimal(&line["quantity"])?,"unitPrice":invoice_decimal(&line["unit_price"])?,"amount":invoice_money(&line["amount"])?,"category":optional(&line["category"])?}));
    }
    let doc = if let Some(d) = document {
        json!({"taskId":text(&d["task_id"])?,"filedAt":optional(&d["filed_at"])?,"source":optional(&d["source"])?,"textExcerpt":optional(&d["text_excerpt"])?.as_str().map(|s| slice_utf16(s,0,4000)),"evidence":if d["evidence"].is_object() { d["evidence"].clone() } else { Value::Null }})
    } else {
        Value::Null
    };
    Ok(
        json!({"transaction":transaction,"invoice":invoice,"lines":lines,"document":doc,"taskId":optional(&body["task_id"])?}),
    )
}
fn js_coerce_string(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        Value::Array(a) => a.iter().map(js_coerce_string).collect::<Vec<_>>().join(","),
        Value::Object(_) => "[object Object]".into(),
        Value::String(s) => s.clone(),
        _ => encode(value),
    }
}
fn js_numeric_value(value: Option<&Value>) -> f64 {
    match value {
        None => f64::NAN,
        Some(Value::Null) => 0.0,
        Some(Value::Bool(v)) => f64::from(*v),
        Some(Value::Number(n)) => n.as_f64().unwrap_or(f64::NAN),
        Some(v) => js_number(&js_coerce_string(v)),
    }
}
fn quote_units(units: &[u16]) -> String {
    let mut output = String::from("\"");
    for unit in char::decode_utf16(units.iter().copied()) {
        match unit {
            Ok(c) => {
                let encoded = Value::String(c.to_string()).to_string();
                output.push_str(&encoded[1..encoded.len() - 1]);
            }
            Err(e) => output.push_str(&format!("\\u{:04x}", e.unpaired_surrogate())),
        }
    }
    output.push('"');
    output
}
/// JSON retains UTF-16 slices even when they cut a surrogate pair.
pub fn quarter_spend_json(
    start: Option<&str>,
    through: Option<&str>,
    rows: &Value,
) -> Result<String, String> {
    let value = quarter_spend(start, through, rows)?;
    let label = value["label"].as_str().unwrap_or("");
    let prefix = label.split_once(' ').map(|(q, _)| q).unwrap_or(label);
    let mut units: Vec<_> = format!("{prefix} ").encode_utf16().collect();
    units.extend(start.unwrap_or("1970-01-01").encode_utf16().take(4));
    Ok(encode(&value).replacen(
        &format!("\"label\":{}", value["label"]),
        &format!("\"label\":{}", quote_units(&units)),
        1,
    ))
}
pub fn transaction_invoice_json(input: &Value) -> Result<String, String> {
    let value = parse_transaction_invoice(input)?;
    let mut output = encode(&value);
    if let Some(excerpt) = input["document"]["text_excerpt"].as_str() {
        let units: Vec<_> = excerpt.encode_utf16().take(4000).collect();
        output = output.replacen(
            &format!("\"textExcerpt\":{}", value["document"]["textExcerpt"]),
            &format!("\"textExcerpt\":{}", quote_units(&units)),
            1,
        );
    }
    Ok(output)
}
pub fn check_display_cents(cents: String) -> Result<f64, String> {
    display_cents(&bigint(&cents)?)
}
pub fn check_decimal_cents(amount: String) -> Result<String, String> {
    decimal_cents(&amount).map(|v| v.to_string())
}
pub fn check_finance_period(rows: String) -> Result<String, String> {
    finance_period(&json_input(&rows)?).map(|v| encode(&v))
}
pub fn check_quarter_spend(start: String, through: String, rows: String) -> Result<String, String> {
    let start = nullable_input(&start)?;
    let through = nullable_input(&through)?;
    quarter_spend_json(start.as_deref(), through.as_deref(), &json_input(&rows)?)
}
pub fn check_invoice(value: String) -> Result<String, String> {
    transaction_invoice_json(&json_input(&value)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_cent_bounds_and_percentage_truncation() -> Result<(), String> {
        assert_eq!(decimal_cents("-0.01")?, BigInt::from(-1));
        assert!(check_display_cents("9007199254740992".into()).is_err());
        let v = finance_period(
            &json!([{"category":"a","count":"1","income":"0","expense":"1"},{"category":"b","count":"1","income":"0","expense":"2"}]),
        )?;
        assert_eq!(v["expenses"][0]["percentage"], 33.33);
        Ok(())
    }
    #[test]
    fn retains_existing_quarter_label_behavior() -> Result<(), String> {
        assert_eq!(
            quarter_spend(Some("2026-12-01"), None, &json!([]))?["label"],
            "Q5 2026"
        );
        Ok(())
    }
}
