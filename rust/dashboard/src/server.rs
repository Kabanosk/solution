use crate::{App, Organization, PageBootstrap};
use axum::{
    Router,
    body::Body,
    extract::{Path, Query, Request, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{any, get},
};
use leptos::prelude::*;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::{net::SocketAddr, path::PathBuf};
use tower_http::services::ServeDir;
#[derive(Clone)]
struct ServerState {
    client: reqwest::Client,
    upstream: String,
    origin: String,
    google: bool,
}
fn headers(response: &mut Response) {
    let h = response.headers_mut();
    for (k, v) in [
        ("Cache-Control", "private, no-store, max-age=0"),
        ("X-Robots-Tag", "noindex, nofollow"),
        ("Referrer-Policy", "same-origin"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
    ] {
        h.insert(
            axum::http::HeaderName::from_bytes(k.as_bytes())
                .unwrap_or(axum::http::header::CACHE_CONTROL),
            HeaderValue::from_static(v),
        );
    }
}
fn page(bootstrap: PageBootstrap) -> Response {
    let serialized = serde_json::to_string(&bootstrap)
        .unwrap_or_default()
        .replace('<', "\\u003c");
    let owner = Owner::new();
    let body = owner.with(|| view! {<App bootstrap/>}.to_html());
    let html = format!(
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><meta name=\"theme-color\" content=\"#f7f7f7\"><title>Scout · Your finance agent</title><meta name=\"description\" content=\"Scout, your organization’s private finance agent.\"><link rel=\"icon\" href=\"/scout-icon.svg\"><link rel=\"stylesheet\" href=\"/scout.css\"><script type=\"application/json\" id=\"scout-bootstrap\">{serialized}</script><script type=\"module\">import init,{{hydrate}} from '/pkg/scout_dashboard.js';init().then(hydrate);</script></head><body>{body}</body></html>"
    );
    let mut response = Html(html).into_response();
    headers(&mut response);
    response.headers_mut().insert(
        "X-Scout-Rust-Runtime",
        HeaderValue::from_static("dashboard"),
    );
    response
}
fn base(state: &ServerState) -> PageBootstrap {
    PageBootstrap {
        google_available: state.google,
        privacy: Some(std::env::var("SCOUT_PRIVACY_URL").unwrap_or_else(|_| "/privacy".into())),
        terms: Some(std::env::var("SCOUT_TERMS_URL").unwrap_or_else(|_| "/terms".into())),
        ..Default::default()
    }
}
async fn index(
    State(state): State<ServerState>,
    Query(query): Query<HashMap<String, String>>,
    request: Request,
) -> Response {
    if query.get("runtime").is_some_and(|v| v == "original") {
        return proxy(State(state), request).await;
    }
    let mut bootstrap = base(&state);
    bootstrap.initial_creating = query.get("create").is_some_and(|v| v == "1")
        || query.get("auth").is_some_and(|v| v == "signup-failed");
    if query.get("auth").is_some_and(|v| v == "signup-failed") {
        bootstrap.initial_error=Some("Google sign-up could not be completed. If you have an existing account, sign in and link Google first, or choose another organization URL.".into());
    }
    page(bootstrap)
}
fn auth_notice(value: Option<&String>) -> Option<String> {
    match value.map(String::as_str){Some("linked")=>Some("Google sign-in is now linked to your account.".into()),Some("failed")=>Some("Google sign-in could not be completed. Please try again.".into()),Some("no-membership")=>Some("This Google account is not linked to a member of this organization. Sign in with your password first.".into()),Some("google-unavailable")=>Some("Google sign-in is not configured yet.".into()),Some("unavailable")=>Some("Sign-in is temporarily unavailable.".into()),_=>None}
}
async fn organization(
    State(state): State<ServerState>,
    Path(slug): Path<String>,
    Query(query): Query<HashMap<String, String>>,
    request: Request,
) -> Response {
    if query.get("runtime").is_some_and(|v| v == "original") {
        return proxy(State(state), request).await;
    }
    if !valid_slug(&slug) {
        return (StatusCode::NOT_FOUND, "Not found").into_response();
    }
    let mut bootstrap = base(&state);
    let mut call = state
        .client
        .get(format!("{}/api/dashboard", state.upstream))
        .header("X-Scout-Organization", &slug);
    if let Some(cookie) = request.headers().get("cookie") {
        call = call.header("Cookie", cookie);
    }
    if let Ok(response) = call.send().await {
        if response.status().is_success() {
            if let Ok(mut dashboard) = response.json::<Value>().await {
                if let Some(message) = auth_notice(query.get("auth")) {
                    dashboard["workspace"]["authNotice"] = json!(message);
                }
                bootstrap.dashboard = Some(dashboard);
                return page(bootstrap);
            }
        }
    }
    let found = state
        .client
        .post(format!("{}/api/auth/find", state.upstream))
        .header("Origin", &state.origin)
        .json(&json!({"organization":slug}))
        .send()
        .await;
    match found {
        Ok(response) if response.status().is_success() => {
            match response.json::<Organization>().await {
                Ok(org) => {
                    bootstrap.organization = Some(org);
                    bootstrap.initial_error = auth_notice(query.get("auth"));
                    page(bootstrap)
                }
                Err(_) => (
                    StatusCode::BAD_GATEWAY,
                    "Authentication is temporarily unavailable.",
                )
                    .into_response(),
            }
        }
        Ok(response) if response.status() == reqwest::StatusCode::NOT_FOUND => {
            (StatusCode::NOT_FOUND, "Not found").into_response()
        }
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            "Authentication is temporarily unavailable.",
        )
            .into_response(),
    }
}
fn valid_slug(slug: &str) -> bool {
    (3..=63).contains(&slug.len())
        && slug.split('-').all(|s| {
            !s.is_empty()
                && s.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        })
}
async fn proxy(State(state): State<ServerState>, request: Request) -> Response {
    let (path, body) = request.into_parts();
    let url = format!(
        "{}{}",
        state.upstream,
        path.uri.path_and_query().map(|p| p.as_str()).unwrap_or("/")
    );
    let mut forwarded = HeaderMap::new();
    for (k, v) in &path.headers {
        if !["host", "connection", "transfer-encoding", "content-length"].contains(&k.as_str()) {
            forwarded.append(k.clone(), v.clone());
        }
    }
    let bytes = match axum::body::to_bytes(body, 8 * 1024 * 1024).await {
        Ok(bytes) => bytes,
        Err(_) => return (StatusCode::PAYLOAD_TOO_LARGE, "Request too large").into_response(),
    };
    let upstream = state
        .client
        .request(path.method, &url)
        .headers(forwarded)
        .body(bytes)
        .send()
        .await;
    match upstream {
        Ok(response) => {
            let status = response.status();
            let source = response.headers().clone();
            let mut result = Response::new(Body::from_stream(response.bytes_stream()));
            *result.status_mut() = status;
            for (k, v) in &source {
                if !["connection", "transfer-encoding", "content-length"].contains(&k.as_str()) {
                    result.headers_mut().append(k.clone(), v.clone());
                }
            }
            result.headers_mut().insert(
                "X-Scout-Rust-Fallback",
                HeaderValue::from_static("dashboard"),
            );
            result
        }
        Err(_) => (
            StatusCode::BAD_GATEWAY,
            axum::Json(json!({"error":"Company data is temporarily unavailable"})),
        )
            .into_response(),
    }
}
pub async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let bind: SocketAddr = std::env::var("SCOUT_DASHBOARD_BIND")
        .unwrap_or_else(|_| "127.0.0.1:3000".into())
        .parse()?;
    let state = ServerState {
        client: reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()?,
        upstream: std::env::var("SCOUT_DASHBOARD_UPSTREAM")
            .unwrap_or_else(|_| "http://127.0.0.1:3001".into())
            .trim_end_matches('/')
            .into(),
        origin: std::env::var("SCOUT_APP_ORIGIN").unwrap_or_else(|_| format!("http://{bind}")),
        google: std::env::var("SCOUT_GOOGLE_CLIENT_ID").is_ok_and(|v| !v.is_empty())
            && std::env::var("SCOUT_GOOGLE_CLIENT_SECRET").is_ok_and(|v| !v.is_empty()),
    };
    let public = std::env::var_os("SCOUT_DASHBOARD_ASSETS")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("public"));
    let app = Router::new()
        .route("/", get(index))
        .route("/api/{*path}", any(proxy))
        .route("/_next/{*path}", any(proxy))
        .nest_service("/pkg", ServeDir::new(public.join("pkg")))
        .nest_service("/fonts", ServeDir::new(public.join("fonts")))
        .route(
            "/scout.css",
            get(|| async {
                (
                    [(axum::http::header::CONTENT_TYPE, "text/css")],
                    include_str!("../public/scout.css"),
                )
            }),
        )
        .route(
            "/scout-icon.svg",
            get(|| async {
                (
                    [(axum::http::header::CONTENT_TYPE, "image/svg+xml")],
                    include_str!("../public/scout-icon.svg"),
                )
            }),
        )
        .route(
            "/scout-icon.png",
            get(|| async {
                (
                    [(axum::http::header::CONTENT_TYPE, "image/png")],
                    include_bytes!("../public/scout-icon.png").as_slice(),
                )
            }),
        )
        .route(
            "/google-g.png",
            get(|| async {
                (
                    [(axum::http::header::CONTENT_TYPE, "image/png")],
                    include_bytes!("../public/google-g.png").as_slice(),
                )
            }),
        )
        .route("/{organization}", get(organization))
        .fallback(proxy)
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!("Scout Rust dashboard listening on {bind}");
    axum::serve(listener, app).await?;
    Ok(())
}
