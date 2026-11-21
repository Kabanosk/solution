use crate::{primitives::*, tasks_view::full_timestamp, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use wasm_bindgen::JsCast;
#[derive(Clone, Copy)]
pub struct Guardrails {
    ctx: WorkspaceContext,
    settings: RwSignal<Option<Value>>,
    busy: RwSignal<bool>,
    loading: RwSignal<bool>,
    writes: RwSignal<u64>,
    error: RwSignal<String>,
    owner: bool,
}
impl Guardrails {
    fn on(self) -> bool {
        self.settings
            .with(|v| v.as_ref().is_none_or(|v| v["enabled"] != false))
    }
    fn load(self) {
        if self.loading.get_untracked() {
            return;
        }
        self.loading.set(true);
        let seen = self.writes.get_untracked();
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/guardrails",
                "GET",
                None,
                Some(&self.ctx.slug.get_value()),
            )
            .await
            {
                Ok((code, value)) if code < 400 => {
                    if self.writes.get_untracked() == seen {
                        self.settings.set(Some(value));
                        self.error.set(String::new());
                    }
                }
                _ => self
                    .error
                    .set("The guardrails switch could not be loaded.".into()),
            }
            self.loading.set(false);
        });
    }
    fn set(self, enabled: bool, on_changed: Callback<()>) {
        self.writes.update(|n| *n += 1);
        self.busy.set(true);
        self.error.set(String::new());
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/guardrails",
                "PUT",
                Some(&json!({"enabled":enabled})),
                Some(&self.ctx.slug.get_value()),
            )
            .await
            {
                Ok((code, value)) if code < 400 => {
                    self.settings.set(Some(value));
                    on_changed.run(());
                }
                Ok((_, value)) => self.error.set(
                    value["error"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or("Could not change the guardrails switch.")
                        .into(),
                ),
                Err(text) => self.error.set(text),
            }
            self.busy.set(false);
        });
    }
}
pub fn provide_guardrails(ctx: WorkspaceContext) {
    let state = Guardrails {
        ctx,
        settings: RwSignal::new(None),
        busy: RwSignal::new(false),
        loading: RwSignal::new(false),
        writes: RwSignal::new(0),
        error: RwSignal::new(String::new()),
        owner: ctx
            .data
            .with_untracked(|v| v["workspace"]["role"] == "owner"),
    };
    provide_context(state);
    Effect::new(move |_| {
        if !state.owner {
            return;
        }
        state.load();
        let refresh = move || {
            if !state.busy.get_untracked()
                && web_sys::window()
                    .and_then(|w| w.document())
                    .is_some_and(|d| d.visibility_state() == web_sys::VisibilityState::Visible)
            {
                state.load()
            }
        };
        leptos::task::spawn_local(async move {
            loop {
                gloo_timers::future::TimeoutFuture::new(15000).await;
                refresh();
            }
        });
        if let Some(w) = web_sys::window() {
            let focus =
                wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::Event)>::new(move |_| {
                    refresh()
                });
            let _ = w.add_event_listener_with_callback("focus", focus.as_ref().unchecked_ref());
            focus.forget();
            if let Some(d) = w.document() {
                let visibility =
                    wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::Event)>::new(move |_| {
                        refresh()
                    });
                let _ = d.add_event_listener_with_callback(
                    "visibilitychange",
                    visibility.as_ref().unchecked_ref(),
                );
                visibility.forget();
            }
        }
    });
}
#[component]
pub fn GuardrailsBanner() -> impl IntoView {
    let state = expect_context::<Guardrails>();
    view! {<Show when=move||state.owner&&state.settings.with(|v|v.as_ref().is_some_and(|v|v["enabled"]==false))><div id="scout-guardrails-off" role="status" class="mb-5 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-warning-border bg-warning-muted px-4 py-3 text-sm"><span class="text-warning"><Icon name="policies"/></span><div class="min-w-0 flex-1"><p class="font-medium">"Guardrails are off for this workspace"</p><p class="text-xs leading-5 text-muted-foreground">"Messages, tool results and replies are not checked. Access controls stay on."</p></div><button id="scout-guardrails-turn-on" type="button" class=button_classes(OUTLINE,"h-8 w-auto gap-2 bg-card") disabled=move||state.busy.get() on:click=move|_|state.set(true,Callback::new(|_|{}))><span class="text-primary"><Icon name="policies"/></span>{move||if state.busy.get(){"Turning on…"}else{"Turn back on"}}</button></div></Show>}
}
#[component]
pub fn GuardrailsSwitch(on_changed: Callback<()>) -> impl IntoView {
    let state = expect_context::<Guardrails>();
    view! {<Show when=move||state.owner><section id="scout-guardrails" aria-labelledby="scout-guardrails-label" class="flex items-center justify-between gap-4 rounded-xl border bg-card px-4 py-3"><div class="flex min-w-0 items-start gap-3"><span class=move||if state.on(){"mt-0.5 text-primary"}else{"mt-0.5 text-warning"}><Icon name="policies"/></span><div class="min-w-0"><label id="scout-guardrails-label" for="scout-guardrails-switch" class="text-sm font-medium">"NeMo Guardrails"</label><p id="scout-guardrails-help" class="text-xs leading-5 text-muted-foreground">"Checks every message, tool result and reply"</p><Show when=move||!state.on()><p id="scout-guardrails-state" class="text-xs leading-5 text-warning">{move||state.settings.with(|s|{let v=s.as_ref().unwrap_or(&Value::Null);format!("Off{}{}. Access controls stay on.",v["updatedAt"].as_str().map(|s|format!(" since {}",full_timestamp(s).replace(" (Rome)",""))).unwrap_or_default(),v["updatedBy"].as_str().filter(|s|!s.is_empty()).map(|s|format!(" · turned off by {s}")).unwrap_or_default())})}</p></Show><Show when=move||!state.error.get().is_empty()><p id="scout-guardrails-error" role="alert" class="text-xs leading-5 text-danger">{move||state.error.get()}</p></Show></div></div><div class="flex shrink-0 items-center gap-2"><span class="text-xs text-muted-foreground" aria-hidden="true">{move||if state.settings.get().is_none(){"…"}else if state.on(){"On"}else{"Off"}}</span><button id="scout-guardrails-switch" type="button" role="switch" aria-checked=move||state.on().to_string() aria-describedby="scout-guardrails-help" disabled=move||state.settings.get().is_none()||state.busy.get() class=move||format!("inline-flex h-5 w-9 items-center rounded-full border-2 border-transparent transition-colors disabled:opacity-50 {}",if state.on(){"bg-primary"}else{"bg-input"}) on:click=move|_|state.set(!state.on(),on_changed)><span class=move||format!("size-4 rounded-full bg-background shadow-xs transition-transform {}",if state.on(){"translate-x-4"}else{"translate-x-0"})/></button></div></section></Show>}
}
