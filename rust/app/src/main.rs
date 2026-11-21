mod auth;
mod native_scout;
mod original;
mod units;

use std::net::SocketAddr;

use axum::serve::ListenerExt;
use original::shutdown_signal;

#[tokio::main]
async fn main() {
    original::report_fallbacks();
    let original = original::Original::start().await;
    let database_url = std::env::var("SCOUT_RUST_TEST_DATABASE_URL")
        .or_else(|_| std::env::var("DATABASE_URL"))
        .unwrap_or_else(|_| "postgresql://cfo:cfo@localhost:5433/cfo".into());
    let database = scout_platform::Database::new(database_url);
    let inner = units::router()
        .fallback(native_scout::dispatch)
        .with_state(original.clone());
    // Authentication surrounds routing, just as the source's outer ASGI middleware does.
    let app =
        axum::Router::new()
            .fallback_service(inner)
            .layer(axum::middleware::from_fn_with_state(
                database,
                auth::authenticate,
            ));
    let port = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8080);
    let listener = tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], port)))
        .await
        .expect("binding the port");
    let listener = listener.tap_io(|tcp| {
        let _ = tcp.set_nodelay(true);
    });
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("serving");
    original.stop();
}
