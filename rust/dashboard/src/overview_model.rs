//! Rust calculations used by the browser overview; source semantics kept deliberately.
use crate::workspace::string;
use fancy_regex::Regex;
use serde_json::{Value, json};
use std::sync::LazyLock;
fn re(pattern: &str) -> Regex {
    let pattern = pattern
        .replace(
            r"\s",
            r"[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]",
        )
        .replace(
            r"\S",
            r"[^\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]",
        );
    Regex::new(&pattern).expect("constant source overview expression")
}
static WHAT_IF: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^What if:\s*([^\n\r\u2028\u2029]+?)\s+in\s+([A-Za-z]+)\s+([0-9]{4})\s*$")
});
static PRICE: LazyLock<Regex> = LazyLock::new(|| re(r"\s*€[0-9,.]+$"));
static PREFIX: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^([Rr][Ee][Ss][Oo][Ll][Vv][Ee][Dd]|[Cc][Ll][Oo][Ss][Ee][Dd]|[Dd][Oo][Nn][Ee])\s*·\s*")
});
static PAREN: LazyLock<Regex> = LazyLock::new(|| re(r"\s*\([^)]*\)?"));
static WHOLE_EUROS: LazyLock<Regex> = LazyLock::new(|| re(r"€([0-9,]+)\.00(?=[^A-Za-z0-9_]|$)"));
static CREDIT: LazyLock<Regex> =
    LazyLock::new(|| re(r"^Credit line of (€[0-9,]+(?:\.[0-9][0-9])?)"));
static FILED: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(€[0-9,]+(?:\.[0-9][0-9])?) filed as (\S+)"));
static SENTENCE: LazyLock<Regex> = LazyLock::new(|| re(r"(?<=[.:;])\s"));
pub fn latest_what_if(tasks: &[Value]) -> Option<Value> {
    let mut candidates = tasks
        .iter()
        .filter(|t| {
            t["loop"] == "liquidity"
                && t["status"] != "cancelled"
                && t["amount"].as_f64().is_some_and(|v| v != 0.0)
        })
        .collect::<Vec<_>>();
    candidates.sort_by(|a, b| string(b, "createdAt").cmp(&string(a, "createdAt")));
    for task in candidates {
        let title = string(task, "title");
        let Ok(Some(c)) = WHAT_IF.captures(&title) else {
            continue;
        };
        let months = [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ];
        let Some(month) = months.iter().position(|m| *m == c[2].to_ascii_lowercase()) else {
            continue;
        };
        let what = PRICE.replace(&c[1], "").trim().to_owned();
        let label = title.trim_start_matches("What if:").trim_start();
        return Some(
            json!({"taskId":task["id"],"label":label,"what":if what.is_empty(){"the purchase"}else{&what},"amount":task["amount"].as_f64().unwrap_or(0.0).abs(),"on":format!("{}-{:02}-01",&c[3],month+1)}),
        );
    }
    None
}
pub fn with_what_if(points: &[Value], what_if: Option<&Value>) -> Vec<Value> {
    points
        .iter()
        .cloned()
        .map(|mut p| {
            if let Some(w) = what_if {
                if string(&p, "date") >= string(w, "on") {
                    p["balance"] = json!(
                        ((p["balance"].as_f64().unwrap_or(0.0)
                            - w["amount"].as_f64().unwrap_or(0.0))
                            * 100.0
                            + 0.5)
                            .floor()
                            / 100.0
                    );
                }
            }
            p
        })
        .collect()
}
pub fn nice_ticks(low: f64, high: f64) -> Vec<f64> {
    let span = (high - low).max(1.0);
    let raw = span / 4.0;
    let power = 10f64.powf(raw.log10().floor());
    let step = [1.0, 2.0, 2.5, 5.0, 10.0]
        .into_iter()
        .map(|f| f * power)
        .find(|v| *v >= raw)
        .unwrap_or(power * 10.0);
    let start = ((low - span * 0.04) / step).floor() * step;
    let end = ((high + span * 0.04) / step).ceil() * step;
    let len = ((end - start) / step).round().max(0.0) as usize + 1;
    (0..len).map(|i| start + i as f64 * step).collect()
}
pub fn coverage(balance: f64, buffer: f64) -> f64 {
    if buffer > 0.0 {
        balance / buffer
    } else {
        f64::INFINITY
    }
}
pub fn buffer_status(ratio: f64) -> &'static str {
    if ratio < 1.0 {
        "below"
    } else if ratio < 1.5 {
        "tight"
    } else {
        "healthy"
    }
}
pub fn gauge_status(balance: f64, lowest: f64, buffer: f64) -> &'static str {
    buffer_status(coverage(balance, buffer).min(coverage(lowest, buffer)))
}
pub fn status_label(status: &str) -> &'static str {
    match status {
        "below" => "Below buffer",
        "tight" => "Tight",
        _ => "Healthy",
    }
}
fn fixed(value: f64, digits: u32) -> String {
    if value.abs() >= 1e21 {
        return ryu_js::Buffer::new().format_finite(value).into();
    }
    let negative = value < 0.0;
    let bits = value.abs().to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    let mantissa = (bits & ((1u64 << 52) - 1)) | if exponent == 0 { 0 } else { 1u64 << 52 };
    let exponent = if exponent == 0 {
        -1074
    } else {
        exponent - 1023 - 52
    };
    let numerator = mantissa as u128 * 10u128.pow(digits);
    let rounded = if exponent >= 0 {
        numerator << exponent
    } else {
        let shift = (-exponent) as u32;
        if shift >= 128 {
            0
        } else {
            let denominator = 1u128 << shift;
            numerator / denominator + u128::from(numerator % denominator >= denominator / 2)
        }
    };
    let sign = if negative { "-" } else { "" };
    if digits == 0 {
        format!("{sign}{rounded}")
    } else {
        format!(
            "{sign}{}.{:0width$}",
            rounded / 10u128.pow(digits),
            rounded % 10u128.pow(digits),
            width = digits as usize
        )
    }
}
pub fn coverage_label(ratio: f64) -> String {
    if !ratio.is_finite() {
        "—".into()
    } else {
        format!("{}×", fixed(ratio, if ratio >= 10.0 { 0 } else { 1 }))
    }
}
pub fn scout_outcome(task: &Value) -> Option<&'static str> {
    let status = task["status"].as_str().unwrap_or("");
    if status == "escalated"
        || matches!(task["resolvedVia"].as_str(), Some("approval" | "slack_dm"))
        || task["level"] == "APPROVAL"
    {
        return if matches!(status, "escalated" | "done" | "cancelled") {
            Some("asked")
        } else {
            None
        };
    }
    if status != "done" {
        return None;
    }
    Some(if task["level"] == "NOTIFY" {
        "told"
    } else {
        "alone"
    })
}
pub fn scout_ring(tasks: &[Value], now: &str) -> Value {
    let month = now.chars().take(7).collect::<String>();
    let (mut alone, mut told, mut asked, mut running) = (0, 0, 0, 0);
    for task in tasks {
        if task["loop"] == "chat"
            || string(task, "createdAt")
                .chars()
                .take(7)
                .collect::<String>()
                != month
        {
            continue;
        }
        match scout_outcome(task) {
            Some("alone") => alone += 1,
            Some("told") => told += 1,
            Some("asked") => asked += 1,
            _ => {
                if task["status"] != "cancelled" {
                    running += 1
                }
            }
        }
    }
    json!({"counts":{"alone":alone,"told":told,"asked":asked},"total":alone+told+asked,"running":running})
}
pub fn notable_actions(tasks: &[Value], limit: usize) -> Vec<Value> {
    let mut result = tasks
        .iter()
        .filter(|t| {
            t["loop"] != "chat"
                && t["outcome"].as_str().is_some_and(|s| !s.is_empty())
                && scout_outcome(t).is_some()
        })
        .cloned()
        .collect::<Vec<_>>();
    result.sort_by(|a, b| {
        b["closedAt"]
            .as_str()
            .unwrap_or(b["updatedAt"].as_str().unwrap_or(""))
            .cmp(
                a["closedAt"]
                    .as_str()
                    .unwrap_or(a["updatedAt"].as_str().unwrap_or("")),
            )
    });
    result.truncate(limit);
    for task in &mut result {
        task["how"] = json!(scout_outcome(task));
    }
    result
}
pub fn action_summary(task: &Value) -> String {
    let raw = task["outcome"]
        .as_str()
        .unwrap_or(task["title"].as_str().unwrap_or(""));
    let raw = PREFIX.replace(raw, "");
    let simulated = task["simulated"] == true || raw.to_ascii_uppercase().contains("(SIMULATED");
    let no_parentheses = PAREN.replace_all(&raw, "");
    let mut text = WHOLE_EUROS
        .replace_all(&no_parentheses, "€$1")
        .trim()
        .to_owned();
    if let Ok(Some(c)) = CREDIT.captures(&text) {
        text = format!(
            "Credit line {} {}",
            &c[1],
            if task["status"] == "done" {
                "approved"
            } else {
                "proposed"
            }
        );
    } else if let Ok(Some(c)) = FILED.captures(&text) {
        text = format!("Invoice {} filed · {}", &c[2], &c[1]);
    } else {
        text = SENTENCE
            .split(&text)
            .next()
            .and_then(Result::ok)
            .unwrap_or(&text)
            .to_owned();
        if text.ends_with(['.', ':', ';']) {
            text.pop();
        };
    }
    if text.encode_utf16().count() > 72 {
        text = format!(
            "{}…",
            String::from_utf16_lossy(&text.encode_utf16().take(70).collect::<Vec<_>>()).trim_end()
        );
    }
    if simulated {
        format!("{text} (simulated)")
    } else {
        text
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn forecast_boundaries() {
        assert_eq!(
            nice_ticks(0.0, 100.0),
            vec![-25.0, 0.0, 25.0, 50.0, 75.0, 100.0, 125.0]
        );
        assert_eq!(gauge_status(180.0, 99.0, 100.0), "below");
        assert_eq!(coverage_label(coverage(10.0, 0.0)), "—");
        let task = json!({"id":"SC-1","loop":"liquidity","status":"done","amount":-30000,"title":"What if: new oven €30,000.00 in March 2027","createdAt":"2026-11-01"});
        let w = latest_what_if(&[task]).unwrap();
        assert_eq!(w["what"], "new oven");
        assert_eq!(w["on"], "2027-03-01");
        assert_eq!(
            with_what_if(&[json!({"date":"2027-03-01","balance":40000})], Some(&w))[0]["balance"],
            10000.0
        );
    }
    #[test]
    fn activity_source_rules() {
        assert_eq!(
            scout_outcome(&json!({"status":"cancelled","level":"APPROVAL"})),
            Some("asked")
        );
        assert_eq!(
            action_summary(
                &json!({"outcome":"Resolved · €1,840.00 filed as FLV-42026 (SIMULATED)","status":"done"})
            ),
            "Invoice FLV-42026 filed · €1,840 (simulated)"
        );
    }
}
