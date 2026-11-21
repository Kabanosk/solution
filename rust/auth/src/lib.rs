//! JS bindings for `bindings.ts`: generated from the signatures in `port.rs` by rust-anything.
//! Regenerate with `rust-anything refresh` after changing them.

use napi_derive::napi;

pub mod port;
pub mod wire;

#[allow(unused_imports)]
use serde_json::Value;

fn panic_message(payload: &(dyn std::any::Any + Send)) -> String {
    payload
        .downcast_ref::<&str>()
        .map(|s| (*s).to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic".into())
}

#[allow(dead_code)]
fn run<T>(f: impl FnOnce() -> Result<T, String>) -> napi::Result<T> {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
        Ok(result) => result.map_err(napi::Error::from_reason),
        Err(payload) => Err(napi::Error::from_reason(format!(
            "Rust panic: {}",
            panic_message(&*payload)
        ))),
    }
}

#[allow(dead_code)]
async fn run_async<T>(f: impl std::future::Future<Output = Result<T, String>>) -> napi::Result<T> {
    use futures::FutureExt as _;
    match std::panic::AssertUnwindSafe(f).catch_unwind().await {
        Ok(result) => result.map_err(napi::Error::from_reason),
        Err(payload) => Err(napi::Error::from_reason(format!(
            "Rust panic: {}",
            panic_message(&*payload)
        ))),
    }
}

/// `checkValidSlug`.
#[napi(js_name = "checkValidSlug")]
pub fn check_valid_slug(slug: String) -> napi::Result<bool> {
    run(move || {
        let out = port::check_valid_slug(slug)?;
        Ok(out)
    })
}

/// `checkRegisteredSchema`.
#[napi(js_name = "checkRegisteredSchema")]
pub fn check_registered_schema(id: String, schema: String, legacy: bool) -> napi::Result<bool> {
    run(move || {
        let out = port::check_registered_schema(id, schema, legacy)?;
        Ok(out)
    })
}

/// `checkTokenHash`.
#[napi(js_name = "checkTokenHash")]
pub fn check_token_hash(token: String) -> napi::Result<String> {
    run(move || {
        let out = port::check_token_hash(token)?;
        Ok(out)
    })
}

/// `checkNewToken`.
#[napi(js_name = "checkNewToken")]
pub async fn check_new_token(test_entropy: String) -> napi::Result<String> {
    run_async(async move {
        let out = port::check_new_token(test_entropy).await?;
        Ok(out)
    })
    .await
}

/// `checkPasswordHash`.
#[napi(js_name = "checkPasswordHash")]
pub async fn check_password_hash(password: String, test_entropy: String) -> napi::Result<String> {
    run_async(async move {
        let out = port::check_password_hash(password, test_entropy).await?;
        Ok(out)
    })
    .await
}

/// `checkVerifyPassword`.
#[napi(js_name = "checkVerifyPassword")]
pub async fn check_verify_password(password: String, stored: String) -> napi::Result<bool> {
    run_async(async move {
        let out = port::check_verify_password(password, stored).await?;
        Ok(out)
    })
    .await
}

/// `checkSameOrigin`.
#[napi(js_name = "checkSameOrigin")]
pub fn check_same_origin(origin_json: String, configured: String) -> napi::Result<bool> {
    run(move || {
        let out = port::check_same_origin(origin_json, configured)?;
        Ok(out)
    })
}

/// `checkValidateIdentity`.
#[napi(js_name = "checkValidateIdentity")]
pub fn check_validate_identity(
    claims_json: String,
    client: String,
    nonce: String,
    now: f64,
) -> napi::Result<String> {
    run(move || {
        let out = port::check_validate_identity(claims_json, client, nonce, now)?;
        Ok(out)
    })
}

// ---- the binary wire ------------------------------------------------------------------------

type WireBuffers = (
    napi::bindgen_prelude::Uint8Array,
    napi::bindgen_prelude::Uint8Array,
    napi::bindgen_prelude::Uint32Array,
);

thread_local! {
    static WIRE: std::cell::RefCell<Option<WireBuffers>> = const { std::cell::RefCell::new(None) };
    static PENDING: std::cell::RefCell<Vec<u8>> = const { std::cell::RefCell::new(Vec::new()) };
    static SCRATCH: std::cell::RefCell<Vec<u8>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// Registers the argument, result and argument-length buffers `index.cjs` uses.
#[napi(js_name = "rustAnythingWireInit")]
pub fn rust_anything_wire_init(
    input: napi::bindgen_prelude::Uint8Array,
    output: napi::bindgen_prelude::Uint8Array,
    len: napi::bindgen_prelude::Uint32Array,
) {
    WIRE.with(|w| *w.borrow_mut() = Some((input, output, len)));
}

/// Copies a result that did not fit into the (now larger) result buffer.
#[napi(js_name = "rustAnythingWireFlush")]
pub fn rust_anything_wire_flush() -> f64 {
    let bytes = PENDING.with(|p| std::mem::take(&mut *p.borrow_mut()));
    WIRE.with(|w| {
        let mut w = w.borrow_mut();
        let Some((_, output, _)) = w.as_mut() else {
            return -1.0;
        };
        // SAFETY: the call is synchronous; JS does not touch `output` until it returns.
        let out = unsafe { output.as_mut() };
        let n = bytes.len().min(out.len());
        out[..n].copy_from_slice(&bytes[..n]);
        n as f64
    })
}

/// Reads the argument bytes (`wLen[0]` of them), runs `f`, and writes its result; a negative
/// return is the size of a result that did not fit, kept for `rustAnythingWireFlush`.
fn wire_io(
    f: impl FnOnce(&mut wire::Reader, &mut wire::Writer) -> Result<(), String>,
) -> napi::Result<f64> {
    WIRE.with(|cell| {
        let mut guard = cell.borrow_mut();
        let Some((input, output, len)) = guard.as_mut() else {
            return Err(napi::Error::from_reason(
                "rust-anything: the wire buffers are not registered",
            ));
        };
        let len = (len.first().copied().unwrap_or(0) as usize).min(input.len());
        let mut w = wire::Writer(SCRATCH.with(|s| std::mem::take(&mut *s.borrow_mut())));
        w.0.clear();
        let result = run(|| f(&mut wire::Reader::new(&input[..len]), &mut w));
        let keep = |w: wire::Writer| SCRATCH.with(|s| *s.borrow_mut() = w.0);
        match result {
            // A result of another shape than the TS type: JS runs the call through JSON.
            Err(e) if e.reason.starts_with(wire::MISMATCH) => {
                keep(w);
                return Ok(f64::NAN);
            }
            Err(e) => {
                keep(w);
                let mut m = wire::Writer::default();
                m.string16(&e.reason);
                // SAFETY: the call is synchronous; JS does not touch `output` until it returns.
                let out = unsafe { output.as_mut() };
                if m.0.len() > out.len() {
                    return Err(e);
                }
                out[..m.0.len()].copy_from_slice(&m.0);
                return Ok(f64::INFINITY);
            }
            Ok(()) => {}
        }
        // SAFETY: the call is synchronous; JS does not touch `output` until it returns.
        let out = unsafe { output.as_mut() };
        let n = w.0.len();
        if n > out.len() {
            PENDING.with(|p| *p.borrow_mut() = w.0);
            return Ok(-(n as f64));
        }
        out[..n].copy_from_slice(&w.0);
        keep(w);
        Ok(n as f64)
    })
}

/// `checkValidSlug` over the binary wire.
#[napi(js_name = "checkValidSlugWire")]
pub fn check_valid_slug_wire() -> napi::Result<f64> {
    wire_io(|r, w| {
        let slug: String = r.string16()?;
        let out = &port::check_valid_slug(slug)?;
        w.bool(*out);
        Ok(())
    })
}

/// `checkRegisteredSchema` over the binary wire.
#[napi(js_name = "checkRegisteredSchemaWire")]
pub fn check_registered_schema_wire() -> napi::Result<f64> {
    wire_io(|r, w| {
        let id: String = r.string16()?;
        let schema: String = r.string16()?;
        let legacy: bool = r.bool()?;
        let out = &port::check_registered_schema(id, schema, legacy)?;
        w.bool(*out);
        Ok(())
    })
}

/// `checkTokenHash` over the binary wire.
#[napi(js_name = "checkTokenHashWire")]
pub fn check_token_hash_wire() -> napi::Result<f64> {
    wire_io(|r, w| {
        let token: String = r.string16()?;
        let out = &port::check_token_hash(token)?;
        w.string16(out);
        Ok(())
    })
}

/// `checkSameOrigin` over the binary wire.
#[napi(js_name = "checkSameOriginWire")]
pub fn check_same_origin_wire() -> napi::Result<f64> {
    wire_io(|r, w| {
        let origin_json: String = r.string16()?;
        let configured: String = r.string16()?;
        let out = &port::check_same_origin(origin_json, configured)?;
        w.bool(*out);
        Ok(())
    })
}

/// `checkValidateIdentity` over the binary wire.
#[napi(js_name = "checkValidateIdentityWire")]
pub fn check_validate_identity_wire() -> napi::Result<f64> {
    wire_io(|r, w| {
        let claims_json: String = r.string16()?;
        let client: String = r.string16()?;
        let nonce: String = r.string16()?;
        let now: f64 = r.f64()?;
        let out = &port::check_validate_identity(claims_json, client, nonce, now)?;
        w.string16(out);
        Ok(())
    })
}
