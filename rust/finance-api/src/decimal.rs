use bigdecimal::{BigDecimal, Context, RoundingMode};
use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::{Signed, Zero};
use serde_json::{Value, json};
use std::str::FromStr;
pub type Decimal = BigDecimal;
type Result<T> = std::result::Result<T, String>;
const INVALID: &str = "invalid decimal";
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
pub fn number(value: &Value) -> Result<Decimal> {
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
pub fn divide(a: &Decimal, b: &Decimal) -> Result<Decimal> {
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

pub fn parse(s: &str) -> Result<Decimal> {
    Decimal::from_str(s).map_err(|e| e.to_string())
}
pub fn fixed(v: &Decimal, places: i64, rounding: RoundingMode) -> String {
    let negative = v.is_negative();
    let n = v.with_scale_round(places, rounding);
    let s = n.to_plain_string();
    if negative && n.is_zero() {
        format!("-{s}")
    } else {
        s
    }
}
pub fn money(v: &Decimal) -> Value {
    json!({"amount":fixed(v,2,RoundingMode::HalfUp),"currency":"EUR"})
}
pub fn money_float(v: f64) -> Result<Value> {
    let n = parse(&v.to_string())?;
    let mut result = money(&n);
    if v.is_sign_negative() && n.is_zero() {
        result["amount"] = json!("-0.00");
    }
    Ok(result)
}
pub fn percentage(v: Option<&Decimal>) -> Value {
    v.map(|v| json!(fixed(v, 1, RoundingMode::HalfUp)))
        .unwrap_or(Value::Null)
}
pub fn change(new: &Decimal, old: &Decimal) -> Result<Option<Decimal>> {
    if old.is_zero() {
        Ok(None)
    } else {
        Ok(Some(multiply(
            &divide(&(new - old), old)?,
            &Decimal::from(100),
        )))
    }
}
pub fn sum<'a>(values: impl Iterator<Item = &'a Decimal>) -> Decimal {
    values.fold(Decimal::from(0), |a, b| add(&a, b))
}
pub fn python_string(n: &Decimal) -> String {
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
