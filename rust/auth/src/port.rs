//! Authentication primitives compatible with dashboard/lib/auth-crypto.ts.
#![forbid(unsafe_code)]

use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::Value;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use url::Url;

pub fn valid_slug(slug: &str) -> bool {
    const RESERVED: &[&str] = &[
        "api",
        "admin",
        "healthz",
        "tasks",
        "inbox",
        "audit",
        "connections",
        "login",
        "logout",
        "register",
        "new",
        "auth",
        "scout-icon",
        "favicon",
        "join",
    ];
    (3..=63).contains(&slug.len())
        && !RESERVED.contains(&slug)
        && slug.split('-').all(|part| {
            !part.is_empty()
                && part
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        })
}

pub fn registered_schema(id: &str, schema: &str, legacy: bool) -> bool {
    if id == "00000000-0000-4000-8000-000000000001" {
        return legacy && schema == "public";
    }
    !legacy
        && id.len() == 36
        && id.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
        && schema == format!("tenant_{}", id.replace('-', ""))
}

pub fn token_hash(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}

pub fn new_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("Error: {e}"))?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn valid_password(password: &str) -> Result<(), String> {
    if password.encode_utf16().count() < 12 || password.len() > 1024 {
        return Err("Error: Use a password with 12–1024 characters.".into());
    }
    Ok(())
}

fn derive(password: &str, salt: &str) -> Result<[u8; 64], String> {
    let params = scrypt::Params::new(15, 8, 1).map_err(|e| format!("Error: {e}"))?;
    let mut output = [0u8; 64];
    scrypt::scrypt(password.as_bytes(), salt.as_bytes(), &params, &mut output)
        .map_err(|e| format!("Error: {e}"))?;
    Ok(output)
}

fn password_hash_with_salt(password: &str, salt: &[u8; 16]) -> Result<String, String> {
    valid_password(password)?;
    let encoded = hex::encode(salt);
    Ok(format!(
        "scrypt${encoded}${}",
        hex::encode(derive(password, &encoded)?)
    ))
}

pub fn password_hash(password: &str) -> Result<String, String> {
    valid_password(password)?;
    let mut salt = [0u8; 16];
    getrandom::fill(&mut salt).map_err(|e| format!("Error: {e}"))?;
    password_hash_with_salt(password, &salt)
}

pub fn verify_password(password: &str, stored: &str) -> Result<bool, String> {
    let pieces: Vec<_> = stored.split('$').collect();
    if password.len() > 1024
        || pieces.len() != 3
        || pieces[0] != "scrypt"
        || pieces[1].len() != 32
        || pieces[2].len() != 128
        || pieces[1..].iter().any(|s| {
            !s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
    {
        return Ok(false);
    }
    let expected = hex::decode(pieces[2]).map_err(|e| format!("Error: {e}"))?;
    Ok(bool::from(
        derive(password, pieces[1])?
            .as_slice()
            .ct_eq(expected.as_slice()),
    ))
}

pub fn same_origin(origin: Option<&str>, configured: &str) -> bool {
    let Some(origin) = origin else { return false };
    let (Ok(parsed), Ok(configured)) = (Url::parse(origin), Url::parse(configured)) else {
        return false;
    };
    origin == parsed.origin().ascii_serialization()
        && origin == configured.origin().ascii_serialization()
}

// ECMAScript whitespace differs from Rust's Unicode White_Space property.
fn js_space(c: char) -> bool {
    matches!(c, '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}')
}

// serde_json's byte-string decoder preserves lone surrogates as WTF-8.
// RawValue avoids interpreting ignored nested values or overflowing JS numbers.
fn identity_json(
    claims_json: &str,
    client: &str,
    nonce: &str,
    now_ms: f64,
) -> Result<String, String> {
    use serde_bytes::ByteBuf;
    use serde_json::value::RawValue;
    let claims: std::collections::HashMap<ByteBuf, Box<RawValue>> =
        serde_json::from_str(claims_json).map_err(|_| "Error: Invalid claims JSON".to_string())?;
    let raw = |key: &str| {
        claims
            .get(&ByteBuf::from(key.as_bytes()))
            .map(|v| v.get())
            .unwrap_or("null")
    };
    let string = |key: &str| -> Option<ByteBuf> {
        let value = raw(key);
        value
            .starts_with('"')
            .then(|| serde_json::from_str(value).ok())
            .flatten()
    };
    let issuer = string("iss");
    if !matches!(
        issuer.as_deref().map(Vec::as_slice),
        Some(b"https://accounts.google.com" | b"accounts.google.com")
    ) {
        return Err("Error: Invalid issuer".into());
    }
    let subject = string("sub").unwrap_or_default();
    let email = string("email").unwrap_or_default();
    let parts: Vec<_> = email.split(|b| *b == b'@').collect();
    let email_valid = parts.len() == 2
        && !parts[0].is_empty()
        && parts[1]
            .iter()
            .rposition(|b| *b == b'.')
            .is_some_and(|i| i > 0 && i + 1 < parts[1].len())
        && !String::from_utf8_lossy(&email).chars().any(js_space);
    if string("aud").as_deref().map(Vec::as_slice) != Some(client.as_bytes())
        || string("nonce").as_deref().map(Vec::as_slice) != Some(nonce.as_bytes())
        || raw("email_verified") != "true"
        || raw("exp")
            .parse::<f64>()
            .ok()
            .is_none_or(|v| v <= now_ms / 1000.0)
        || subject.is_empty()
        || !email_valid
    {
        return Err("Error: Invalid identity".into());
    }
    Ok(format!(
        "{{\"subject\":{},\"email\":{}}}",
        quote_js(&subject, false)?,
        quote_js(&email, true)?
    ))
}

fn quote_js(mut bytes: &[u8], lowercase: bool) -> Result<String, String> {
    let mut output = String::from("\"");
    while !bytes.is_empty() {
        let (prefix, trailing) = match std::str::from_utf8(bytes) {
            Ok(text) => (text, &[][..]),
            Err(error) => (
                std::str::from_utf8(&bytes[..error.valid_up_to()]).map_err(|e| e.to_string())?,
                &bytes[error.valid_up_to()..],
            ),
        };
        let text = if lowercase {
            prefix.to_lowercase()
        } else {
            prefix.to_owned()
        };
        let escaped = serde_json::to_string(&text).map_err(|e| e.to_string())?;
        output.push_str(&escaped[1..escaped.len() - 1]);
        if trailing.is_empty() {
            break;
        }
        let Some([0xed, b @ 0xa0..=0xbf, c @ 0x80..=0xbf]) = trailing.get(..3) else {
            return Err("Error: Invalid claims JSON".into());
        };
        let unit = 0xd000 | (u16::from(*b & 0x3f) << 6) | u16::from(*c & 0x3f);
        output.push_str(&format!("\\u{unit:04x}"));
        bytes = &trailing[3..];
    }
    output.push('"');
    Ok(output)
}

/// Validate already-signature-verified Google claims at an explicit millisecond clock.
/// The raw JSON variant additionally preserves JavaScript lone-surrogate strings.
pub fn validate_identity(
    claims: &Value,
    client: &str,
    nonce: &str,
    now_ms: f64,
) -> Result<Value, String> {
    let input = serde_json::to_string(claims).map_err(|e| e.to_string())?;
    let output = identity_json(&input, client, nonce, now_ms)?;
    serde_json::from_str(&output).map_err(|e| e.to_string())
}

fn test_entropy(value: &str) -> Result<[u8; 32], String> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("Error: Invalid test entropy".into());
    }
    let mut bytes = [0u8; 32];
    hex::decode_to_slice(value, &mut bytes)
        .map_err(|_| "Error: Invalid test entropy".to_string())?;
    Ok(bytes)
}

pub fn check_valid_slug(slug: String) -> Result<bool, String> {
    Ok(valid_slug(&slug))
}
pub fn check_registered_schema(id: String, schema: String, legacy: bool) -> Result<bool, String> {
    Ok(registered_schema(&id, &schema, legacy))
}
pub fn check_token_hash(token: String) -> Result<String, String> {
    Ok(token_hash(&token))
}
pub async fn check_new_token(entropy: String) -> Result<String, String> {
    Ok(URL_SAFE_NO_PAD.encode(test_entropy(&entropy)?))
}
pub async fn check_password_hash(password: String, entropy: String) -> Result<String, String> {
    let bytes = test_entropy(&entropy)?;
    let mut salt = [0u8; 16];
    salt.copy_from_slice(&bytes[..16]);
    password_hash_with_salt(&password, &salt)
}
pub async fn check_verify_password(password: String, stored: String) -> Result<bool, String> {
    verify_password(&password, &stored)
}
pub fn check_same_origin(origin_json: String, configured: String) -> Result<bool, String> {
    // An origin with an unpaired surrogate can never equal its normalized URL origin.
    if origin_json.trim_start().starts_with('"')
        && let Ok(bytes) = serde_json::from_str::<serde_bytes::ByteBuf>(&origin_json)
        && std::str::from_utf8(&bytes).is_err()
    {
        return Ok(false);
    }
    let origin: Option<String> =
        serde_json::from_str(&origin_json).map_err(|_| "Error: Invalid origin JSON".to_string())?;
    Ok(same_origin(origin.as_deref(), &configured))
}
pub fn check_validate_identity(
    claims_json: String,
    client: String,
    nonce: String,
    now: f64,
) -> Result<String, String> {
    identity_json(&claims_json, &client, &nonce, now)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn node_scrypt_vector() -> Result<(), String> {
        let known = "scrypt$07070707070707070707070707070707$c3c2c3e576082c27e89ea07ef20a513439ee28a74525072552b0aff9ecf9cc0440f5b856e395df71db91471373ebc714863c6c9eae9baf3cbb798a65d6dd4048";
        assert_eq!(
            password_hash_with_salt("correct horse battery staple", &[7; 16])?,
            known
        );
        assert!(verify_password("correct horse battery staple", known)?);
        assert!(!verify_password("wrong password", known)?);
        Ok(())
    }
    #[test]
    fn production_randomness_and_utf16() -> Result<(), String> {
        let a = new_token()?;
        let b = new_token()?;
        assert_eq!(a.len(), 43);
        assert_ne!(a, b);
        assert!(valid_password("😀😀😀😀😀😀").is_ok());
        assert!(valid_password("😀😀😀😀😀").is_err());
        let hash = password_hash("😀😀😀😀😀😀")?;
        assert!(verify_password("😀😀😀😀😀😀", &hash)?);
        Ok(())
    }
    #[test]
    fn identity_preserves_escaped_lone_surrogates() -> Result<(), String> {
        let input = r#"{"iss":"https://accounts.google.com","aud":"c","nonce":"n","email_verified":true,"exp":1e999,"sub":"\ud800","email":"ΟΣ\udfff@EXAMPLE.COM"}"#;
        assert_eq!(
            identity_json(input, "c", "n", 0.0)?,
            r#"{"subject":"\ud800","email":"ος\udfff@example.com"}"#
        );
        Ok(())
    }
}
