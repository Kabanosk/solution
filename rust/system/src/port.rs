//! Liveness does not access PostgreSQL or connectors.
#![forbid(unsafe_code)]

pub fn health_json() -> Result<String, String> {
    Ok(r#"{"status":"ok","version":"0.1.0"}"#.to_owned())
}

#[cfg(test)]
mod tests {
    #[test]
    fn stable_health_contract() -> Result<(), String> {
        assert_eq!(
            super::health_json()?,
            r#"{"status":"ok","version":"0.1.0"}"#
        );
        Ok(())
    }
}
