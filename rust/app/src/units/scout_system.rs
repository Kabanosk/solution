//! Native liveness response, characterized against the original FastAPI route.
use axum::extract::Request;
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use std::collections::HashMap;

pub async fn handle(_route: &str, _params: HashMap<String, String>, req: Request) -> Response {
    let payload = if req.method() == axum::http::Method::HEAD {
        Ok(r#"{"error":{"code":"method_not_allowed","message":"Method Not Allowed"}}"#.to_owned())
    } else {
        scout_system::port::health_json()
    };
    match payload {
        Ok(body) => {
            let mut response = (
                [
                    (header::CONTENT_TYPE, "application/json"),
                    (header::VARY, "Origin"),
                ],
                body,
            )
                .into_response();
            if req.method() == axum::http::Method::HEAD {
                *response.status_mut() = StatusCode::METHOD_NOT_ALLOWED;
                response
                    .headers_mut()
                    .insert(header::ALLOW, header::HeaderValue::from_static("GET"));
            }
            if let Some(origin) = req.headers().get(header::ORIGIN) {
                response.headers_mut().insert(
                    header::ACCESS_CONTROL_EXPOSE_HEADERS,
                    header::HeaderValue::from_static("X-Stub"),
                );
                let configured = std::env::var("CORS_ORIGINS")
                    .ok()
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| "*".into());
                let origins: Vec<_> = configured
                    .split(',')
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .collect();
                if origins.contains(&"*") {
                    response.headers_mut().insert(
                        header::ACCESS_CONTROL_ALLOW_ORIGIN,
                        header::HeaderValue::from_static("*"),
                    );
                } else if origins
                    .iter()
                    .any(|allowed| allowed.as_bytes() == origin.as_bytes())
                {
                    response
                        .headers_mut()
                        .insert(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin.clone());
                }
            }
            response
        }
        Err(message) => (StatusCode::INTERNAL_SERVER_ERROR, message).into_response(),
    }
}
