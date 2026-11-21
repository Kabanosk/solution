//! Verified organization identities, isolated PostgreSQL connections and session authentication.
//! Every consumer receives an explicit immutable tenant; there is no implicit public scope.
#![forbid(unsafe_code)]
use postgres_native_tls::MakeTlsConnector;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fmt, str::FromStr, time::Duration};
use tokio_postgres::{Client, Config, Row};
use uuid::Uuid;

pub const LEGACY_ID: &str = "00000000-0000-4000-8000-000000000001";
pub const LEGACY_SLUG: &str = "pizzeria-amalfi";

#[derive(Debug)]
pub enum PlatformError {
    Permission(String),
    Value(String),
    Database(tokio_postgres::Error),
    Tls(native_tls::Error),
    Configuration(String),
}
impl fmt::Display for PlatformError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Permission(message) | Self::Value(message) | Self::Configuration(message) => {
                f.write_str(message)
            }
            Self::Database(error) => error.fmt(f),
            Self::Tls(error) => error.fmt(f),
        }
    }
}
impl std::error::Error for PlatformError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Database(error) => Some(error),
            Self::Tls(error) => Some(error),
            _ => None,
        }
    }
}
impl From<tokio_postgres::Error> for PlatformError {
    fn from(error: tokio_postgres::Error) -> Self {
        Self::Database(error)
    }
}
impl From<native_tls::Error> for PlatformError {
    fn from(error: native_tls::Error) -> Self {
        Self::Tls(error)
    }
}

/// Registration-validated tenant. Deserialization runs the same invariant as construction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Tenant {
    id: String,
    slug: String,
    schema_name: String,
    employee_id: Option<String>,
    role: String,
    legacy: bool,
}
impl Tenant {
    pub fn new(
        id: impl AsRef<str>,
        slug: impl Into<String>,
        schema_name: impl Into<String>,
        employee_id: Option<String>,
        role: impl Into<String>,
        legacy: bool,
    ) -> Result<Self, PlatformError> {
        let id = Uuid::parse_str(id.as_ref())
            .map_err(|_| PlatformError::Value("badly formed hexadecimal UUID string".into()))?;
        let slug = slug.into();
        let schema_name = schema_name.into();
        let canonical_id = id.to_string();
        let valid = if canonical_id == LEGACY_ID {
            legacy && schema_name == "public" && slug == LEGACY_SLUG
        } else {
            !legacy && schema_name == format!("tenant_{}", id.simple()) && slug != LEGACY_SLUG
        };
        if !valid {
            return Err(PlatformError::Permission(
                "Invalid organization schema registration".into(),
            ));
        }
        Ok(Self {
            id: canonical_id,
            slug,
            schema_name,
            employee_id,
            role: role.into(),
            legacy,
        })
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn slug(&self) -> &str {
        &self.slug
    }
    pub fn schema_name(&self) -> &str {
        &self.schema_name
    }
    pub fn employee_id(&self) -> Option<&str> {
        self.employee_id.as_deref()
    }
    pub fn role(&self) -> &str {
        &self.role
    }
    pub fn legacy(&self) -> bool {
        self.legacy
    }
}
impl<'de> Deserialize<'de> for Tenant {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct Raw {
            id: String,
            slug: String,
            schema_name: String,
            employee_id: Option<String>,
            role: String,
            #[serde(default)]
            legacy: bool,
        }
        let raw = Raw::deserialize(deserializer)?;
        Self::new(
            raw.id,
            raw.slug,
            raw.schema_name,
            raw.employee_id,
            raw.role,
            raw.legacy,
        )
        .map_err(serde::de::Error::custom)
    }
}

pub fn scoped_schema(
    tenant: Option<&Tenant>,
    explicit: Option<&str>,
) -> Result<String, PlatformError> {
    if let Some(tenant) = tenant {
        if explicit.is_some_and(|schema| schema != tenant.schema_name()) {
            return Err(PlatformError::Permission(
                "Cross-organization schema access".into(),
            ));
        }
        return Ok(tenant.schema_name().into());
    }
    let schema = explicit
        .ok_or_else(|| PlatformError::Permission("No verified organization bound".into()))?;
    let bytes = schema.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 63
        || !bytes[0].is_ascii_lowercase()
        || !bytes
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'_')
    {
        return Err(PlatformError::Value("Unsafe schema identifier".into()));
    }
    Ok(schema.into())
}
pub fn legacy_connectors_enabled(tenant: Option<&Tenant>, configured_organization: &str) -> bool {
    tenant.is_some_and(|tenant| {
        tenant.legacy() && tenant.id() == LEGACY_ID && configured_organization == LEGACY_ID
    })
}

/// Connection factory. Each call opens a new connection with startup search_path;
/// no mutable connection/session state crosses tenant boundaries.
#[derive(Clone)]
pub struct Database {
    url: String,
    connect_timeout: Duration,
}
impl fmt::Debug for Database {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Database").finish_non_exhaustive()
    }
}
impl Database {
    pub fn new(url: impl Into<String>) -> Self {
        Self {
            url: url.into(),
            connect_timeout: Duration::from_secs(5),
        }
    }
    pub fn with_connect_timeout(mut self, timeout: Duration) -> Self {
        self.connect_timeout = timeout;
        self
    }
    async fn connect(&self, schema: Option<&str>) -> Result<Client, PlatformError> {
        let mut config = Config::from_str(&self.url)?;
        config.connect_timeout(self.connect_timeout);
        if let Some(schema) = schema {
            config.options(format!("-csearch_path={schema}"));
        }
        let tls = native_tls::TlsConnector::builder().build()?;
        let (client, connection) = config.connect(MakeTlsConnector::new(tls)).await?;
        tokio::spawn(async move {
            // The client reports connection failures to pending/future queries.
            // Avoid logging connection strings, credentials or tenant contents.
            let _ = connection.await;
        });
        Ok(client)
    }
    pub async fn scoped(&self, tenant: &Tenant) -> Result<Client, PlatformError> {
        let schema = scoped_schema(Some(tenant), None)?;
        self.connect(Some(&schema)).await
    }
    /// Only global authentication registry queries may use this connection.
    pub async fn unscoped(&self) -> Result<Client, PlatformError> {
        self.connect(None).await
    }
    /// Operator/test utility matching explicit-schema cfo.tenancy.connect.
    pub async fn explicit_schema(&self, schema: &str) -> Result<Client, PlatformError> {
        let schema = scoped_schema(None, Some(schema))?;
        self.connect(Some(&schema)).await
    }
}
fn tenant_from_row(row: &Row, role: Option<&str>) -> Result<Tenant, PlatformError> {
    let id: Uuid = row.try_get("id")?;
    Tenant::new(
        id.to_string(),
        row.try_get::<_, String>("slug")?,
        row.try_get::<_, String>("schema_name")?,
        row.try_get::<_, Option<String>>("employee_id")?,
        match role {
            Some(role) => role.to_string(),
            None => row.try_get::<_, String>("role")?,
        },
        row.try_get::<_, bool>("legacy")?,
    )
}
pub fn valid_session_input(token: &str, slug: &str) -> bool {
    (40..=256).contains(&token.len())
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        && (1..=80).contains(&slug.len())
        && slug.as_bytes()[0].is_ascii_lowercase_or_digit()
        && slug
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
trait LowercaseOrDigit {
    fn is_ascii_lowercase_or_digit(&self) -> bool;
}
impl LowercaseOrDigit for u8 {
    fn is_ascii_lowercase_or_digit(&self) -> bool {
        self.is_ascii_lowercase() || self.is_ascii_digit()
    }
}

pub async fn verify_session(
    db: &Database,
    token: &str,
    slug: &str,
) -> Result<Option<Tenant>, PlatformError> {
    if !valid_session_input(token, slug) {
        return Ok(None);
    }
    let digest = format!("{:x}", Sha256::digest(token.as_bytes()));
    let connection = db.unscoped().await?;
    let row=connection.query_opt("SELECT o.id,o.slug,o.schema_name,o.legacy,m.employee_id,m.role FROM scout_auth.sessions s JOIN scout_auth.organizations o ON o.id=s.organization_id JOIN scout_auth.memberships m ON m.organization_id=o.id AND m.user_id=s.user_id WHERE s.token_hash=$1 AND s.expires_at>now() AND o.slug=$2",&[&digest,&slug]).await?;
    match row {
        None => Ok(None),
        Some(row) => match tenant_from_row(&row, None) {
            Ok(tenant) => Ok(Some(tenant)),
            Err(PlatformError::Permission(_) | PlatformError::Value(_)) => Ok(None),
            Err(error) => Err(error),
        },
    }
}
pub async fn load_worker_tenant(
    db: &Database,
    organization_id: &str,
) -> Result<Tenant, PlatformError> {
    if organization_id.is_empty() {
        return Err(PlatformError::Permission(
            "Set SCOUT_ORGANIZATION_ID to an explicitly enrolled organization UUID".into(),
        ));
    }
    let id = Uuid::parse_str(organization_id)
        .map_err(|_| PlatformError::Permission("Invalid worker organization UUID".into()))?;
    let connection = db.unscoped().await?;
    let row=connection.query_opt("SELECT o.id,o.slug,o.schema_name,o.legacy,(SELECT m.employee_id FROM scout_auth.memberships m WHERE m.organization_id=o.id AND m.role='owner' ORDER BY m.user_id LIMIT 1) AS employee_id FROM scout_auth.organizations o WHERE o.id=$1",&[&id]).await?;
    let row = row.ok_or_else(|| {
        PlatformError::Permission("Worker organization has no explicitly enrolled owner".into())
    })?;
    if row
        .try_get::<_, Option<String>>("employee_id")?
        .as_deref()
        .is_none_or(str::is_empty)
    {
        return Err(PlatformError::Permission(
            "Worker organization has no explicitly enrolled owner".into(),
        ));
    }
    tenant_from_row(&row, Some("worker"))
}
pub async fn worker_tenant(db: &Database) -> Result<Tenant, PlatformError> {
    load_worker_tenant(
        db,
        &std::env::var("SCOUT_ORGANIZATION_ID").unwrap_or_default(),
    )
    .await
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AuthError {
    pub status: u16,
    pub code: &'static str,
    pub message: &'static str,
}
impl fmt::Display for AuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for AuthError {}
impl AuthError {
    pub fn json(&self) -> serde_json::Value {
        serde_json::json!({"error":{"code":self.code,"message":self.message}})
    }
    pub fn www_authenticate(&self) -> Option<&'static str> {
        (self.status == 401).then_some("Bearer")
    }
    pub fn websocket_close_code(&self) -> u16 {
        4401
    }
    fn unauthorized() -> Self {
        Self {
            status: 401,
            code: "unauthorized",
            message: "A valid session and organization are required.",
        }
    }
}
/// HTTP request authentication. Callers retain the returned Tenant for the entire
/// streamed response lifetime and pass clones explicitly to background jobs.
pub async fn authenticate_request(
    db: &Database,
    method: &str,
    path: &str,
    headers: &[(String, String)],
) -> Result<Option<Tenant>, AuthError> {
    if path == "/healthz" && matches!(method, "GET" | "HEAD") {
        return Ok(None);
    }
    authenticate_protected(db, method, path, headers)
        .await
        .map(Some)
}
pub async fn authenticate_websocket(
    db: &Database,
    path: &str,
    headers: &[(String, String)],
) -> Result<Tenant, AuthError> {
    authenticate_protected(db, "", path, headers).await
}
async fn authenticate_protected(
    db: &Database,
    method: &str,
    path: &str,
    headers: &[(String, String)],
) -> Result<Tenant, AuthError> {
    let mut authorization = None;
    let mut organization = None;
    for (key, value) in headers {
        let field = if key.eq_ignore_ascii_case("authorization") {
            &mut authorization
        } else if key.eq_ignore_ascii_case("x-scout-organization") {
            &mut organization
        } else {
            continue;
        };
        if field.is_some() {
            return Err(AuthError::unauthorized());
        }
        *field = Some(value.as_str());
    }
    let (scheme, token) = authorization
        .and_then(|header| header.split_once(' '))
        .ok_or_else(AuthError::unauthorized)?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() {
        return Err(AuthError::unauthorized());
    }
    let slug = organization
        .filter(|s| !s.is_empty())
        .ok_or_else(AuthError::unauthorized)?;
    let tenant = verify_session(db, token, slug)
        .await
        .map_err(|_| AuthError {
            status: 503,
            code: "database_unavailable",
            message: "Authentication database unavailable.",
        })?
        .ok_or_else(AuthError::unauthorized)?;
    if path.starts_with("/v1/demo")
        || path.starts_with("/v1/connections/")
        || (path.trim_end_matches('/') == "/v1/connections" && method != "GET")
    {
        return Err(AuthError {
            status: 403,
            code: "not_configured",
            message: "Connection mutation and demo endpoints are disabled.",
        });
    }
    Ok(tenant)
}
pub fn require_write_identity(tenant: Option<&Tenant>) -> Result<&str, AuthError> {
    let tenant = tenant.ok_or(AuthError {
        status: 401,
        code: "unauthorized",
        message: "A valid organization session is required.",
    })?;
    tenant
        .employee_id()
        .filter(|id| !id.is_empty())
        .ok_or(AuthError {
            status: 403,
            code: "not_enrolled",
            message: "Membership has no employee identity.",
        })
}
