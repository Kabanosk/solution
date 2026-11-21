//! JS bindings for `bindings.ts`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

use wasm_bindgen::prelude::*;

pub mod port;

/// `checkDisplayCents`.
fn check_display_cents_inner(cents: String) -> Result<f64, String> {
    let out = port::check_display_cents(cents)?;
    Ok(out)
}

#[wasm_bindgen(js_name = "checkDisplayCents")]
pub fn check_display_cents(cents: String) -> Result<f64, JsError> {
    check_display_cents_inner(cents).map_err(|e| JsError::new(&e))
}

/// `checkDecimalCents`.
fn check_decimal_cents_inner(amount: String) -> Result<String, String> {
    let out = port::check_decimal_cents(amount)?;
    Ok(out)
}

#[wasm_bindgen(js_name = "checkDecimalCents")]
pub fn check_decimal_cents(amount: String) -> Result<String, JsError> {
    check_decimal_cents_inner(amount).map_err(|e| JsError::new(&e))
}

/// `checkFinancePeriod`.
fn check_finance_period_inner(rows: String) -> Result<String, String> {
    let out = port::check_finance_period(rows)?;
    Ok(out)
}

#[wasm_bindgen(js_name = "checkFinancePeriod")]
pub fn check_finance_period(rows: String) -> Result<String, JsError> {
    check_finance_period_inner(rows).map_err(|e| JsError::new(&e))
}

/// `checkQuarterSpend`.
fn check_quarter_spend_inner(
    start: String,
    through: String,
    rows: String,
) -> Result<String, String> {
    let out = port::check_quarter_spend(start, through, rows)?;
    Ok(out)
}

#[wasm_bindgen(js_name = "checkQuarterSpend")]
pub fn check_quarter_spend(
    start: String,
    through: String,
    rows: String,
) -> Result<String, JsError> {
    check_quarter_spend_inner(start, through, rows).map_err(|e| JsError::new(&e))
}

/// `checkInvoice`.
fn check_invoice_inner(value: String) -> Result<String, String> {
    let out = port::check_invoice(value)?;
    Ok(out)
}

#[wasm_bindgen(js_name = "checkInvoice")]
pub fn check_invoice(value: String) -> Result<String, JsError> {
    check_invoice_inner(value).map_err(|e| JsError::new(&e))
}
