//! Source-compatible settings profiles. Parsing never mutates the process environment.
use anyhow::{Context, Result, bail};
use bigdecimal::{BigDecimal, Zero};
use regex::Regex;
use scout_platform::{Tenant, legacy_connectors_enabled};
use serde_json::{Map, Value, json};
use std::{
    path::{Path, PathBuf},
    str::FromStr,
    sync::OnceLock,
};

type Raw = indexmap::IndexMap<String, Option<String>>;
static VARIABLES: OnceLock<Result<Regex, regex::Error>> = OnceLock::new();

fn expand(value: &str, previous: &Raw, environment: &Raw) -> Result<String> {
    let pattern = VARIABLES
        .get_or_init(|| Regex::new(r"\$\{([^}:]*)(?::-([^}]*))?\}"))
        .as_ref()
        .map_err(|error| anyhow::anyhow!(error.to_string()))?;
    Ok(pattern
        .replace_all(value, |captures: &regex::Captures<'_>| {
            let name = &captures[1];
            previous
                .get(name)
                .or_else(|| environment.get(name))
                .map_or_else(
                    || captures.get(2).map_or("", |part| part.as_str()).to_owned(),
                    |value| value.clone().unwrap_or_default(),
                )
        })
        .into_owned())
}

/// The documented python-dotenv grammar: quoted multiline values, optional export,
/// comments and POSIX ${name:-default}; bare $name remains literal.
fn dotenv(text: &str, environment: &Raw) -> Result<Raw> {
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let chars: Vec<char> = text.chars().collect();
    let mut at = 0;
    let mut values = Raw::new();
    fn line(chars: &[char], at: &mut usize) {
        while *at < chars.len() && chars[*at] != '\n' {
            *at += 1;
        }
        if *at < chars.len() {
            *at += 1;
        }
    }
    fn space(chars: &[char], at: &mut usize) {
        while *at < chars.len() && chars[*at].is_whitespace() && chars[*at] != '\n' {
            *at += 1;
        }
    }
    while at < chars.len() {
        let line_number = chars[..at].iter().filter(|ch| **ch == '\n').count() + 1;
        while at < chars.len() && chars[at].is_whitespace() {
            at += 1;
        }
        if at >= chars.len() {
            break;
        }
        if chars[at] == '#' {
            line(&chars, &mut at);
            continue;
        }
        if chars[at..].starts_with(&['e', 'x', 'p', 'o', 'r', 't'])
            && chars.get(at + 6).is_some_and(|ch| ch.is_whitespace())
        {
            at += 6;
            space(&chars, &mut at);
        }
        let key = if chars.get(at) == Some(&'\'') {
            at += 1;
            let start = at;
            while at < chars.len() && chars[at] != '\'' {
                at += 1;
            }
            if at >= chars.len() {
                break;
            }
            let key = chars[start..at].iter().collect::<String>();
            at += 1;
            key
        } else {
            let start = at;
            while at < chars.len() && !chars[at].is_whitespace() && !['=', '#'].contains(&chars[at])
            {
                at += 1;
            }
            chars[start..at].iter().collect::<String>()
        };
        if key.is_empty() {
            line(&chars, &mut at);
            continue;
        }
        space(&chars, &mut at);
        if chars.get(at) != Some(&'=') {
            if at >= chars.len() || chars[at] == '\n' || chars[at] == '#' {
                values.insert(key, None);
            }
            line(&chars, &mut at);
            continue;
        }
        at += 1;
        let raw_start = at;
        space(&chars, &mut at);
        let value = if matches!(chars.get(at), Some('\'' | '"')) {
            let quote = chars[at];
            at += 1;
            let mut value = String::new();
            let mut closed = false;
            while at < chars.len() {
                let ch = chars[at];
                at += 1;
                if ch == quote {
                    closed = true;
                    break;
                }
                if ch == '\\' && at < chars.len() {
                    let next = chars[at];
                    let decoded = match next {
                        '\\' => Some('\\'),
                        '\'' => Some('\''),
                        '"' if quote == '"' => Some('"'),
                        'a' if quote == '"' => Some('\u{07}'),
                        'b' if quote == '"' => Some('\u{08}'),
                        'f' if quote == '"' => Some('\u{0c}'),
                        'n' if quote == '"' => Some('\n'),
                        'r' if quote == '"' => Some('\r'),
                        't' if quote == '"' => Some('\t'),
                        'v' if quote == '"' => Some('\u{0b}'),
                        _ => None,
                    };
                    if let Some(decoded) = decoded {
                        value.push(decoded);
                        at += 1;
                        continue;
                    }
                }
                value.push(ch);
            }
            if !closed {
                break;
            }
            space(&chars, &mut at);
            if at < chars.len() && chars[at] != '#' && chars[at] != '\n' {
                eprintln!("python-dotenv could not parse statement starting at line {line_number}");
                line(&chars, &mut at);
                continue;
            }
            value
        } else {
            at = raw_start;
            let start = at;
            while at < chars.len() && chars[at] != '\n' {
                at += 1;
            }
            let mut end = at;
            for i in start..at {
                if chars[i] == '#' && i > start && chars[i - 1].is_whitespace() {
                    end = i;
                    break;
                }
            }
            chars[start..end]
                .iter()
                .collect::<String>()
                .trim()
                .to_owned()
        };
        let value = expand(&value, &values, environment)?;
        values.insert(key, Some(value));
        line(&chars, &mut at);
    }
    Ok(values)
}

fn decimal_text(raw: &str, value: &BigDecimal) -> String {
    let (coefficient, scale) = value.as_bigint_and_exponent();
    let negative = coefficient.sign() == bigdecimal::num_bigint::Sign::Minus
        || (value.is_zero() && raw.trim_start().starts_with('-'));
    let digits = coefficient.to_string().trim_start_matches('-').to_owned();
    let adjusted = digits.len() as i64 - scale - 1;
    let sign = if negative { "-" } else { "" };
    if scale < 0 || adjusted < -6 {
        let exponent = if value.is_zero() { -scale } else { adjusted };
        let mantissa = if digits.len() > 1 {
            format!("{}.{}", &digits[..1], &digits[1..])
        } else {
            digits
        };
        format!(
            "{sign}{mantissa}E{}{exponent}",
            if exponent >= 0 { "+" } else { "" }
        )
    } else {
        if scale == 0 {
            format!("{sign}{digits}")
        } else if scale >= digits.len() as i64 {
            format!(
                "{sign}0.{}{digits}",
                "0".repeat(scale as usize - digits.len())
            )
        } else {
            let split = digits.len() - scale as usize;
            format!("{sign}{}.{}", &digits[..split], &digits[split..])
        }
    }
}

fn parse(field: &str, raw: &str, spec: &Value) -> Result<Value> {
    let invalid = || anyhow::anyhow!("Invalid setting {field}");
    let result = match spec["type"].as_str().unwrap_or("") {
        "string" => json!(raw),
        "path" => {
            let path: PathBuf = Path::new(raw)
                .components()
                .filter(|part| !matches!(part, std::path::Component::CurDir))
                .collect();
            json!(if path.as_os_str().is_empty() {
                ".".to_owned()
            } else {
                path.to_string_lossy().into_owned()
            })
        }
        "integer" => {
            let text = raw.trim();
            let text = if let Some((whole, fraction)) = text.split_once('.') {
                if fraction.is_empty() || !fraction.bytes().all(|byte| byte == b'0') {
                    return Err(invalid());
                }
                whole.to_owned()
            } else {
                if text.contains("__") || text.starts_with('_') || text.ends_with('_') {
                    return Err(invalid());
                }
                text.replace('_', "")
            };
            json!(text.parse::<i64>().map_err(|_| invalid())?)
        }
        "number" => {
            let bytes = raw.as_bytes();
            if bytes.iter().enumerate().any(|(index, byte)| {
                *byte == b'_'
                    && (index == 0
                        || index + 1 == bytes.len()
                        || !bytes[index - 1].is_ascii_digit()
                        || !bytes[index + 1].is_ascii_digit())
            }) {
                return Err(invalid());
            }
            let value = raw
                .trim()
                .replace('_', "")
                .parse::<f64>()
                .map_err(|_| invalid())?;
            if value.is_nan() {
                json!("NaN")
            } else if value.is_infinite() {
                json!(if value.is_sign_negative() {
                    "-Infinity"
                } else {
                    "Infinity"
                })
            } else {
                json!(value)
            }
        }
        "boolean" => match raw.to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" | "y" | "t" => json!(true),
            "0" | "false" | "no" | "off" | "n" | "f" => json!(false),
            _ => return Err(invalid()),
        },
        "decimal" => {
            let value =
                BigDecimal::from_str(&raw.trim().replace('_', "")).map_err(|_| invalid())?;
            for (constraint, allow_equal) in [("ge", true), ("gt", false)] {
                if let Some(bound) = spec.get(constraint) {
                    let bound = BigDecimal::from_str(&bound.to_string())?;
                    if value < bound || (!allow_equal && value == bound) {
                        return Err(invalid());
                    }
                }
            }
            json!(decimal_text(raw, &value))
        }
        "list" => {
            let value: Value = serde_json::from_str(raw).map_err(|_| invalid())?;
            let values = match value {
                Value::Null => return Ok(spec["default"].clone()),
                Value::Array(values) => values,
                Value::String(value) => value
                    .split(',')
                    .filter(|part| !part.trim().is_empty())
                    .map(|part| json!(part.trim().trim_start_matches('#')))
                    .collect(),
                _ => return Err(invalid()),
            };
            if !values.iter().all(Value::is_string) {
                return Err(invalid());
            }
            json!(values)
        }
        other => bail!("Unsupported settings type {other}"),
    };
    if let Some(pattern) = spec["pattern"].as_str()
        && !Regex::new(pattern)?.is_match(result.as_str().unwrap_or("")) {
        return Err(invalid());
    }
    Ok(result)
}

#[derive(Clone)]
pub struct Settings {
    profile: String,
    values: Map<String, Value>,
}
impl std::fmt::Debug for Settings {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Settings")
            .field("profile", &self.profile)
            .finish_non_exhaustive()
    }
}
impl Settings {
    pub fn from_parts(
        profile: &str,
        repo_file: &str,
        cwd_file: &str,
        environment: &[(String, String)],
    ) -> Result<Self> {
        let profiles: Value = serde_json::from_str(include_str!("../profiles.json"))?;
        let spec = profiles.get(profile).context("Unknown settings profile")?;
        let ignore = spec["ignore_empty"].as_bool().unwrap_or(false);
        let raw_environment: Raw = environment
            .iter()
            .map(|(key, value)| (key.clone(), Some(value.clone())))
            .collect();
        let mut merged = Raw::new();
        for values in [
            dotenv(repo_file, &raw_environment)?,
            dotenv(cwd_file, &raw_environment)?,
            raw_environment,
        ] {
            for (key, value) in values {
                if ignore && value.as_deref() == Some("") {
                    continue;
                }
                merged.insert(key.to_lowercase(), value);
            }
        }
        let mut values = Map::new();
        for (field, field_spec) in spec["fields"]
            .as_object()
            .context("Missing settings fields")?
        {
            let value = if let Some(Some(raw)) = merged.get(field) {
                parse(field, raw, field_spec)?
            } else {
                field_spec["default"].clone()
            };
            values.insert(field.clone(), value);
        }
        if profile == "api" {
            let token = values["api_write_token"]
                .as_str()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned);
            values.insert("api_write_token".into(), json!(token));
            let ceo = values["ceo_id"].as_str().unwrap_or("").trim();
            values.insert(
                "ceo_id".into(),
                json!(if ceo.is_empty() { "vincenzo" } else { ceo }),
            );
        }
        Ok(Self {
            profile: profile.into(),
            values,
        })
    }
    pub fn load(profile: &str, repo_root: &Path, cwd: &Path) -> Result<Self> {
        fn read(path: PathBuf) -> Result<String> {
            if path.is_file() {
                Ok(std::fs::read_to_string(path)?)
            } else {
                Ok(String::new())
            }
        }
        let environment = std::env::vars_os()
            .filter_map(|(key, value)| Some((key.into_string().ok()?, value.into_string().ok()?)))
            .collect::<Vec<_>>();
        Self::from_parts(
            profile,
            &read(repo_root.join(".env"))?,
            &read(cwd.join(".env"))?,
            &environment,
        )
    }
    pub fn snapshot(&self) -> Value {
        Value::Object(self.values.clone())
    }
    pub fn string(&self, name: &str) -> Result<&str> {
        self.values
            .get(name)
            .and_then(Value::as_str)
            .with_context(|| format!("Missing string setting {name}"))
    }
    pub fn optional_string(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }
    pub fn integer(&self, name: &str) -> Result<i64> {
        self.values
            .get(name)
            .and_then(Value::as_i64)
            .with_context(|| format!("Missing integer setting {name}"))
    }
    pub fn number(&self, name: &str) -> Result<f64> {
        self.values
            .get(name)
            .and_then(|value| {
                value
                    .as_f64()
                    .or_else(|| value.as_str().and_then(|text| text.parse().ok()))
            })
            .with_context(|| format!("Missing number setting {name}"))
    }
    pub fn boolean(&self, name: &str) -> Result<bool> {
        self.values
            .get(name)
            .and_then(Value::as_bool)
            .with_context(|| format!("Missing boolean setting {name}"))
    }
    pub fn for_tenant(&self, tenant: &Tenant, legacy_organization: &str) -> Result<Self> {
        if self.profile != "scout" {
            bail!("Tenant settings require Scout profile");
        }
        let mut result = self.clone();
        if legacy_connectors_enabled(Some(tenant), legacy_organization) {
            return Ok(result);
        }
        for key in [
            "slack_bot_token",
            "slack_app_token",
            "slack_user_map",
            "google_mail_label",
            "oauth_state_secret",
            "bank_api_url",
            "workstations_root",
            "data_dir",
        ] {
            result.values.insert(key.into(), json!(""));
        }
        result.values.insert("chat_channels".into(), json!([]));
        result.values.insert("scout_demo".into(), json!(false));
        for key in [
            "strategy_operating_surplus_target",
            "strategy_owner_income_target",
            "strategy_monthly_revenue_target",
        ] {
            result.values.insert(key.into(), Value::Null);
        }
        result
            .values
            .insert("strategy_currency".into(), json!("EUR"));
        for key in ["ceo_id", "document_owner_id"] {
            result
                .values
                .insert(key.into(), json!(tenant.employee_id().unwrap_or("")));
        }
        Ok(result)
    }
}
