#![recursion_limit = "512"]
pub mod auth_view;
pub mod chat_view;
mod connections_view;
mod demo_view;
mod guardrails_view;
pub mod markdown;
pub mod navigation;
pub mod overview_model;
mod overview_view;
pub mod people_view;
pub mod primitives;
pub mod public_auth;
pub mod records_view;
#[cfg(feature = "ssr")]
pub mod server;
mod setup_view;
mod strategy_view;
mod task_content;
mod task_signals;
pub mod tasks_view;
mod usage_view;
pub mod workspace;
use leptos::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Organization {
    pub name: String,
    pub slug: String,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PageBootstrap {
    pub organization: Option<Organization>,
    pub dashboard: Option<Value>,
    pub public_auth: Option<public_auth::PublicAuth>,
    pub google_available: bool,
    pub initial_creating: bool,
    pub initial_error: Option<String>,
    pub privacy: Option<String>,
    pub terms: Option<String>,
}
#[component]
pub fn App(bootstrap: PageBootstrap) -> impl IntoView {
    provide_context(bootstrap.clone());
    Effect::new(move |_| primitives::install_dom_ids());
    if bootstrap.public_auth.is_some() {
        view! {<public_auth::PublicAuthView bootstrap/>}.into_any()
    } else if let Some(data) = bootstrap.dashboard {
        view! { <workspace::Workspace data/> }.into_any()
    } else {
        view! { <auth_view::OrganizationAuth bootstrap/> }.into_any()
    }
}
#[cfg(feature = "hydrate")]
#[wasm_bindgen::prelude::wasm_bindgen]
pub fn hydrate() {
    console_error_panic_hook::set_once();
    let bootstrap = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id("scout-bootstrap"))
        .and_then(|e| e.text_content())
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default();
    leptos::mount::hydrate_body(move || view! { <App bootstrap/> });
}
