//! Native authentication; retain the explicit tenant for the complete response lifetime.
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::{
    Json,
    extract::{Request, State},
    middleware::Next,
};
use scout_platform::{Database, authenticate_request};

pub async fn authenticate(
    State(database): State<Database>,
    mut request: Request,
    next: Next,
) -> Response {
    // Uvicorn rejects raw non-ASCII request targets before ASGI/authentication.
    // Percent-encoded Unicode remains valid and is decoded below.
    if !request.uri().to_string().is_ascii() {
        return (StatusCode::BAD_REQUEST, "Invalid HTTP request received.").into_response();
    }
    // ASGI decodes headers as Latin-1; retain every duplicate instead of joining fields.
    let headers: Vec<_> = request
        .headers()
        .iter()
        .map(|(name, value)| {
            (
                name.as_str().to_owned(),
                value
                    .as_bytes()
                    .iter()
                    .map(|byte| char::from(*byte))
                    .collect(),
            )
        })
        .collect();
    let path = percent_encoding::percent_decode_str(request.uri().path())
        .decode_utf8_lossy()
        .into_owned();
    match authenticate_request(&database, request.method().as_str(), &path, &headers).await {
        Ok(tenant) => {
            if let Some(tenant) = tenant {
                request.extensions_mut().insert(tenant);
            }
            let mut response = next.run(request).await;
            // MethodRouter adds Allow on method fallbacks; the source emits it only for 405.
            if response.status() != StatusCode::METHOD_NOT_ALLOWED {
                response.headers_mut().remove(header::ALLOW);
            }
            response
        }
        Err(error) => {
            let status =
                StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
            let mut response = (status, Json(error.json())).into_response();
            if error.www_authenticate().is_some() {
                response.headers_mut().insert(
                    header::WWW_AUTHENTICATE,
                    header::HeaderValue::from_static("Bearer"),
                );
            }
            response
        }
    }
}
