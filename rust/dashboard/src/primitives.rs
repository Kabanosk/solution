use leptos::prelude::*;
use serde_json::Value;
use wasm_bindgen::JsCast;
pub const BUTTON: &str = "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 h-11 px-4 py-2";
pub const PRIMARY: &str = "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 h-11 px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 w-full";
pub const OUTLINE: &str = "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-all outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 h-11 px-4 py-2 border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50";
pub const INPUT: &str = "h-11 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none selection:bg-primary selection:text-primary-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:opacity-50 md:text-sm dark:bg-input/30 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive";
/// Resolve the utility groups overridden by Scout's copied button variants.
/// The React source uses tailwind-merge for these same size/display overrides.
pub fn button_classes(base: &str, extra: &str) -> String {
    let tokens: Vec<_> = extra.split_whitespace().collect();
    let overrides = |prefix: &str| tokens.iter().any(|s| s.starts_with(prefix));
    let display = tokens
        .iter()
        .any(|s| matches!(*s, "hidden" | "flex" | "inline-flex" | "block"));
    let kept: Vec<_> = base
        .split_whitespace()
        .filter(|s| {
            !(display && matches!(*s, "inline-flex" | "flex" | "hidden" | "block")
                || (overrides("h-") || overrides("size-")) && s.starts_with("h-")
                || (overrides("w-") || overrides("size-")) && s.starts_with("w-")
                || overrides("px-") && s.starts_with("px-")
                || overrides("py-") && s.starts_with("py-")
                || overrides("rounded-") && s.starts_with("rounded-")
                || overrides("shadow-") && s.starts_with("shadow-")
                || overrides("bg-") && s.starts_with("bg-")
                || tokens.contains(&"border-0") && *s == "border")
        })
        .collect();
    format!("{} {extra}", kept.join(" "))
}

#[component]
pub fn Icon(#[prop(into)] name: String) -> impl IntoView {
    let svg = match name.as_str() {
        "search" => include_str!("../public/icons/search.svg"),
        "sun" => include_str!("../public/icons/sun.svg"),
        "moon" => include_str!("../public/icons/moon.svg"),
        "layout" => include_str!("../public/icons/layout.svg"),
        "tasks" => include_str!("../public/icons/tasks.svg"),
        "chat" => include_str!("../public/icons/chat.svg"),
        "transactions" => include_str!("../public/icons/transactions.svg"),
        "connections" => include_str!("../public/icons/connections.svg"),
        "audit" => include_str!("../public/icons/audit.svg"),
        "policies" => include_str!("../public/icons/policies.svg"),
        "usage" => include_str!("../public/icons/usage.svg"),
        "refresh" => include_str!("../public/icons/refresh.svg"),
        "chevron" => include_str!("../public/icons/chevron.svg"),
        "menu" => include_str!("../public/icons/menu.svg"),
        "close" => include_str!("../public/icons/close.svg"),
        "send" => include_str!("../public/icons/send.svg"),
        "stop" => include_str!("../public/icons/stop.svg"),
        "plus" => include_str!("../public/icons/plus.svg"),
        "users" => include_str!("../public/icons/users.svg"),
        "logout" => include_str!("../public/icons/logout.svg"),
        "key" => include_str!("../public/icons/key.svg"),
        "link" => include_str!("../public/icons/link.svg"),
        "check" => include_str!("../public/icons/check.svg"),
        "clock" => include_str!("../public/icons/clock.svg"),
        "file" => include_str!("../public/icons/file.svg"),
        "bell" => include_str!("../public/icons/bell.svg"),
        "bank" => include_str!("../public/icons/bank.svg"),
        "invoicing" => include_str!("../public/icons/invoicing.svg"),
        "ksef" => include_str!("../public/icons/ksef.svg"),
        "mail" => include_str!("../public/icons/mail.svg"),
        "laptop" => include_str!("../public/icons/laptop.svg"),
        "bot" => include_str!("../public/icons/bot.svg"),
        "resolved" => include_str!("../public/icons/resolved.svg"),
        "forecast" => include_str!("../public/icons/forecast.svg"),
        "folder" => include_str!("../public/icons/folder.svg"),
        "slack" => include_str!("../public/icons/slack.svg"),
        "alert" => include_str!("../public/icons/alert.svg"),
        _ => include_str!("../public/icons/right.svg"),
    };
    view! { <span aria-hidden="true" class="flex size-4 shrink-0 items-center justify-center" inner_html=svg/> }
}
#[component]
pub fn ScoutMark(#[prop(default = 28)] size: u16) -> impl IntoView {
    view! { <img src="/scout-icon.svg" alt="" aria-hidden="true" width=size height=size style=format!("width:{size}px;height:{size}px") class="shrink-0 rounded-md"/> }
}
pub fn focus(id: &str) {
    if let Some(e) = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id(id))
        .and_then(|e| e.dyn_into::<web_sys::HtmlElement>().ok())
    {
        let _ = e.focus();
    }
}
pub fn navigate(url: &str) {
    if let Some(w) = web_sys::window() {
        let _ = w.location().assign(url);
    }
}
pub fn element_width(id: &'static str, initial: f64) -> RwSignal<f64> {
    let width = RwSignal::new(initial);
    let listener = StoredValue::new_local(
        None::<(
            web_sys::ResizeObserver,
            wasm_bindgen::closure::Closure<dyn FnMut(js_sys::Array)>,
        )>,
    );
    on_cleanup(move || {
        listener.update_value(|slot| {
            if let Some((observer, _)) = slot.take() {
                observer.disconnect();
            }
        })
    });
    Effect::new(move |_| {
        let Some(element) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id(id))
        else {
            return;
        };
        let measure = move || {
            if let Some(element) = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.get_element_by_id(id))
            {
                let value = element.get_bounding_client_rect().width();
                if value > 0.0 && (value - width.get_untracked()).abs() > 0.1 {
                    width.set(value);
                }
            }
        };
        measure();
        let callback =
            wasm_bindgen::closure::Closure::<dyn FnMut(js_sys::Array)>::new(move |_| measure());
        if let Ok(observer) = web_sys::ResizeObserver::new(callback.as_ref().unchecked_ref()) {
            observer.observe(&element);
            listener.set_value(Some((observer, callback)));
        }
    });
    width
}
pub async fn json_request(
    path: &str,
    method: &str,
    body: Option<&Value>,
    tenant: Option<&str>,
) -> Result<(u16, Value), String> {
    let mut request = gloo_net::http::RequestBuilder::new(path)
        .method(method.parse().map_err(|_| "Invalid request method")?)
        .header("Content-Type", "application/json");
    if let Some(slug) = tenant {
        request = request.header("X-Scout-Organization", slug);
    }
    let request = if let Some(body) = body {
        request
            .body(body.to_string())
            .map_err(|_| "Could not connect. Please try again.")?
    } else {
        request
            .build()
            .map_err(|_| "Could not connect. Please try again.")?
    };
    let response = request
        .send()
        .await
        .map_err(|_| "Could not connect. Please try again.")?;
    let status = response.status();
    let value = response
        .json::<Value>()
        .await
        .map_err(|_| "Could not connect. Please try again.")?;
    Ok((status, value))
}
#[component]
pub fn ThemeToggle(#[prop(default = false)] compact: bool) -> impl IntoView {
    let dark = RwSignal::new(false);
    let listener = StoredValue::new_local(
        None::<(
            web_sys::Window,
            wasm_bindgen::closure::Closure<dyn FnMut(web_sys::StorageEvent)>,
        )>,
    );
    on_cleanup(move || {
        listener.update_value(|slot| {
            if let Some((window, callback)) = slot.take() {
                let _ = window.remove_event_listener_with_callback(
                    "storage",
                    callback.as_ref().unchecked_ref(),
                );
            }
        })
    });
    Effect::new(move |_| {
        if let Some(w) = web_sys::window() {
            let saved = w
                .local_storage()
                .ok()
                .flatten()
                .and_then(|s| s.get_item("scout-theme").ok().flatten());
            apply_theme(saved.as_deref() == Some("dark"));
            dark.set(saved.as_deref() == Some("dark"));
            let callback = wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::StorageEvent)>::new(
                move |event: web_sys::StorageEvent| {
                    if event.key().is_some_and(|k| k != "scout-theme") {
                        return;
                    }
                    let next = event.new_value().as_deref() == Some("dark");
                    apply_theme(next);
                    dark.set(next);
                },
            );
            let _ =
                w.add_event_listener_with_callback("storage", callback.as_ref().unchecked_ref());
            listener.set_value(Some((w, callback)));
        }
    });
    view! { <button id="scout-theme-toggle" type="button" class=button_classes(OUTLINE,if compact { "size-8 shrink-0 border-0 px-0 shadow-none" }else{"size-11 shrink-0 px-0"}) aria-label="Dark theme" aria-pressed=move||dark.get().to_string()
        title=move||if dark.get(){"Switch to light theme"}else{"Switch to dark theme"}
        on:click=move |_|{ let next=!dark.get(); dark.set(next); apply_theme(next); if let Some(w)=web_sys::window() { if let Ok(Some(storage))=w.local_storage(){let _=storage.set_item("scout-theme",if next{"dark"}else{"light"});}} }>
        {move||if dark.get(){view!{<Icon name="sun"/>}.into_any()}else{view!{<Icon name="moon"/>}.into_any()}}
    </button> }
}
fn apply_theme(dark: bool) {
    if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
        if let Some(root) = doc.document_element() {
            let _ = root.class_list().toggle_with_force("dark", dark);
        }
        if let Ok(Some(meta)) = doc.query_selector("meta[name=theme-color]") {
            let _ = meta.set_attribute("content", if dark { "#101010" } else { "#f7f7f7" });
        }
    }
}
pub fn install_dom_ids() {
    #[cfg(target_arch = "wasm32")]
    {
        use std::cell::Cell;
        thread_local! { static NEXT: Cell<u64> = const { Cell::new(0) }; }
        fn assign() {
            if let Some(doc) = web_sys::window().and_then(|w| w.document()) {
                if let Ok(nodes) = doc.query_selector_all("body *:not([id])") {
                    for i in 0..nodes.length() {
                        if let Some(e) = nodes
                            .item(i)
                            .and_then(|n| n.dyn_into::<web_sys::Element>().ok())
                        {
                            NEXT.with(|n| {
                                n.set(n.get() + 1);
                                let _ = e.set_attribute("id", &format!("scout-auto-{}", n.get()));
                            });
                        }
                    }
                }
            }
        }
        assign();
        let callback = wasm_bindgen::closure::Closure::<
            dyn FnMut(js_sys::Array, web_sys::MutationObserver),
        >::new(move |_, _| assign());
        if let Ok(observer) = web_sys::MutationObserver::new(callback.as_ref().unchecked_ref()) {
            if let Some(body) = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.body())
            {
                let config = web_sys::MutationObserverInit::new();
                config.set_child_list(true);
                config.set_subtree(true);
                let _ = observer.observe_with_options(&body, &config);
                callback.forget();
                std::mem::forget(observer);
            }
        }
    }
}
