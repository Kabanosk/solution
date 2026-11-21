use crate::{navigation::Route, primitives::*, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use wasm_bindgen::JsCast;
pub const STEPS: [(&str, &str, &str, &str, &str, &str); 6] = [
    (
        "bank",
        "Bank",
        "Read-only bunq sandbox account. No transfers, no production banking.",
        "Connect sandbox bank",
        "bunq",
        "bank",
    ),
    (
        "invoicing",
        "Invoicing",
        "Scout's built-in invoicing for this workspace. It starts empty.",
        "Use built-in invoicing",
        "builtin",
        "invoicing",
    ),
    (
        "ksef",
        "KSeF",
        "National e-invoice system, test environment only.",
        "Set up test environment",
        "ksef",
        "ksef",
    ),
    (
        "email",
        "Email",
        "Gmail / Google Workspace: Google grants read-only mailbox permission; Scout restricts reads to one label. Google sign-in alone is not mailbox consent. Disconnect or failed-setup revocation may affect other Scout workspaces using the same Google account.",
        "Continue with Google",
        "google",
        "mail",
    ),
    (
        "slack",
        "Slack",
        "Connect Scout to public channels, your private channels, and its DMs. Your Slack account is linked automatically; other people need a verified Slack ID link. Setup sends no test messages.",
        "Connect workspace",
        "slack",
        "chat",
    ),
    (
        "network",
        "Company network",
        "Tell us whether Scout needs private-network access. An administrator sets it up.",
        "Answer a few questions",
        "",
        "connections",
    ),
];
fn settled(step: &Value) -> bool {
    matches!(
        step["state"].as_str(),
        Some("connected" | "sandbox_connected" | "skipped")
    )
}
fn skip_on_next(step: &Value) -> bool {
    !settled(step)
        && step["state"] != "connecting"
        && !(step["id"] == "network" && step["state"] == "needs_configuration")
        && !(step["id"] == "slack" && step["display"]["authorized"] == true)
}
fn valid_setup(value: &Value) -> bool {
    value["version"] == 1
        && value["revision"].is_number()
        && matches!(
            value["status"].as_str(),
            Some("not_started" | "pending" | "in_progress" | "dismissed" | "completed")
        )
        && STEPS.iter().all(|meta| {
            value["steps"].as_array().is_some_and(|rows| {
                rows.iter().any(|s| {
                    s["id"] == meta.0
                        && matches!(
                            s["state"].as_str(),
                            Some(
                                "not_started"
                                    | "needs_configuration"
                                    | "connecting"
                                    | "sandbox_connected"
                                    | "connected"
                                    | "failed"
                                    | "skipped"
                            )
                        )
                })
            })
        })
}
#[derive(Clone, Copy)]
struct SetupContext {
    workspace: WorkspaceContext,
    setup: RwSignal<Option<Value>>,
    current: RwSignal<usize>,
    busy: RwSignal<bool>,
    notice: RwSignal<Option<(bool, String)>>,
    editable: bool,
}
impl SetupContext {
    fn step(self) -> Value {
        self.setup.with_untracked(|v| {
            v.as_ref()
                .and_then(|v| v["steps"].as_array())
                .and_then(|rows| {
                    rows.iter()
                        .find(|s| s["id"] == STEPS[self.current.get_untracked()].0)
                })
                .cloned()
                .unwrap_or(Value::Null)
        })
    }
    async fn request(self, path: &str, method: &str, body: Option<Value>) -> bool {
        self.busy.set(true);
        self.notice.set(None);
        let result = json_request(
            path,
            method,
            body.as_ref(),
            Some(&self.workspace.slug.get_value()),
        )
        .await;
        let mut ok = false;
        match result {
            Ok((status, value)) if status < 400 => {
                if let Some(raw) = value["url"].as_str() {
                    if let Ok(url) = url::Url::parse(raw) {
                        let safe = url.scheme() == "https"
                            || url.scheme() == "http"
                                && matches!(
                                    url.host_str(),
                                    Some("localhost" | "127.0.0.1" | "[::1]")
                                );
                        if safe {
                            navigate(raw);
                            ok = true
                        } else {
                            self.notice.set(Some((false,"Scout did not return a safe authorization link. Nothing was connected.".into())));
                        }
                    } else {
                        self.notice.set(Some((false,"Scout did not return a safe authorization link. Nothing was connected.".into())));
                    }
                } else if valid_setup(&value) {
                    self.setup.set(Some(value));
                    ok = true
                } else {
                    self.notice.set(Some((
                        false,
                        "Scout returned an unexpected setup response.".into(),
                    )));
                }
            }
            Ok((409, value)) if value["code"] != "already_connected" => {
                if let Ok((200, latest)) = json_request(
                    "/api/onboarding",
                    "GET",
                    None,
                    Some(&self.workspace.slug.get_value()),
                )
                .await
                {
                    if valid_setup(&latest) {
                        self.setup.set(Some(latest));
                    }
                }
                self.notice.set(Some((false,"Setup was changed in another session. We loaded the latest version and your last change was not applied. Review it and try again.".into())));
            }
            Ok((status, value)) => {
                let default = match status {
                    401 => "Your session has ended. Sign in again.",
                    403 => "Only workspace owners can change setup.",
                    409 => "Setup was changed elsewhere.",
                    429 => "Too many requests. Wait a moment and try again.",
                    _ => "Setup is unavailable right now. Try again shortly.",
                };
                self.notice.set(Some((
                    false,
                    value["error"]
                        .as_str()
                        .or(value["message"].as_str())
                        .filter(|s| !s.is_empty())
                        .unwrap_or(default)
                        .chars()
                        .take(240)
                        .collect(),
                )));
            }
            Err(_) => self.notice.set(Some((
                false,
                "Could not reach Scout. Check your connection and try again.".into(),
            ))),
        }
        self.busy.set(false);
        ok
    }
    async fn patch(self, mut update: Value) -> bool {
        update["revision"] = self.setup.with_untracked(|v| {
            v.as_ref()
                .map(|v| v["revision"].clone())
                .unwrap_or(json!(0))
        });
        self.request("/api/onboarding", "PATCH", Some(update)).await
    }
    fn go(self, index: usize) {
        self.current.set(index.min(5));
        self.notice.set(None);
        focus(&format!("scout-onboarding-title-{}", STEPS[index.min(5)].0));
    }
    async fn proceed(self) {
        let index = self.current.get_untracked();
        if index < 5 {
            self.go(index + 1)
        } else {
            let action = if self.setup.with_untracked(|v| {
                v.as_ref()
                    .and_then(|v| v["steps"].as_array())
                    .is_some_and(|rows| rows.iter().all(settled))
            }) {
                "complete"
            } else {
                "skip"
            };
            self.leave(action).await;
        }
    }
    async fn leave(self, action: &str) {
        if !self.editable || self.patch(json!({"action":action})).await {
            self.workspace.navigate(Route::view("chat"));
            self.workspace.refresh();
        }
    }
    fn connect(self, provider: &str, body: Value) {
        let path = format!("/api/onboarding/connect/{provider}");
        leptos::task::spawn_local(async move {
            self.request(&path, "POST", Some(body)).await;
        });
    }
}
fn step_status(step: &Value) -> (&'static str, &'static str) {
    if step["display"]["revocation_unconfirmed"] == true {
        return ("Provider revocation unconfirmed", "warning");
    }
    match step["state"].as_str() {
        Some("connected") => ("Connected", "primary"),
        Some("sandbox_connected") => ("Sandbox connected", "primary"),
        Some("needs_configuration") => (
            if step["id"] == "network" {
                "Administrator setup required"
            } else {
                "Needs setup"
            },
            "warning",
        ),
        Some("connecting") => ("Connecting", "muted"),
        Some("failed") => ("Failed", "danger"),
        Some("skipped") => ("Skipped", "muted"),
        _ => ("Not started", "muted"),
    }
}
fn secret(value: &str) -> bool {
    static PATTERN: std::sync::LazyLock<fancy_regex::Regex> = std::sync::LazyLock::new(|| {
        fancy_regex::Regex::new(r"(?i)private\s*key|BEGIN [A-Z ]*(KEY|CERTIFICATE)|pass(word|phrase)?\s*[:=]|secret\s*[:=]|token\s*[:=]|api[_ -]?key\s*[:=]|\.(ovpn|conf|pem|key)\b").expect("constant network expression")
    });
    PATTERN.is_match(value).unwrap_or(false)
}
#[component]
fn NetworkForm(initial: Value, on_saved: Callback<()>) -> impl IntoView {
    let ctx = expect_context::<SetupContext>();
    let required = RwSignal::new(initial["required"].as_bool().unwrap_or(false));
    let draft = RwSignal::new(
        json!({"provider":initial["provider"].as_str().unwrap_or(""),"protocol":initial["protocol"].as_str().unwrap_or(""),"administrator":initial["administrator"].as_str().unwrap_or(""),"systems":initial["systems"].as_array().map(|a|a.iter().filter_map(Value::as_str).collect::<Vec<_>>().join(", ")).unwrap_or_default(),"companyOnly":initial["company_only"].as_bool().unwrap_or(false)}),
    );
    let errors = RwSignal::new(json!({}));
    view! {<form id="scout-onboarding-network-form" novalidate class="max-w-md space-y-3 pt-2" on:submit=move|e|{e.prevent_default();let mut problems=json!({});let answers=if !required.get_untracked(){json!({"required":false,"provider":"","protocol":"","administrator":"","systems":[],"company_only":false})}else{let values=draft.get_untracked();let mut answers=json!({"required":true,"company_only":true});for(key,label)in[("provider","the VPN provider"),("protocol","the protocol"),("administrator","the responsible administrator")]{let text=values[key].as_str().unwrap_or("").trim();if text.is_empty(){problems[key]=json!(format!("Enter {label}."))}else if text.encode_utf16().count()>120{problems[key]=json!("Keep this under 120 characters.")}else if secret(text){problems[key]=json!("Do not enter keys, passwords or VPN profiles here.")}answers[key]=json!(text);}let mut systems=Vec::new();for name in values["systems"].as_str().unwrap_or("").split(['\n',',']).map(str::trim).filter(|s|!s.is_empty()){if !systems.contains(&name){systems.push(name)}}if systems.is_empty(){problems["systems"]=json!("List at least one system Scout should reach.")}else if systems.len()>20{problems["systems"]=json!("List at most 20 systems.")}else if systems.iter().any(|s|s.encode_utf16().count()>80||secret(s)){problems["systems"]=json!("Use short system names, without keys or passwords.")}if values["companyOnly"]!=true{problems["companyOnly"]=json!("Confirm that access is limited to company folders and mail.")}answers["systems"]=json!(systems);answers};let valid=problems.as_object().is_some_and(|v|v.is_empty());errors.set(problems);if valid{leptos::task::spawn_local(async move{if ctx.patch(json!({"action":"network","network":answers})).await{on_saved.run(())}});}}><fieldset class="space-y-1.5"><legend class="text-xs font-medium">"Does Scout need access to systems on your company network?"</legend><div class="flex gap-4 text-xs"><label for="scout-onboarding-network-required-no" class="flex items-center gap-2"><input id="scout-onboarding-network-required-no" type="radio" name="network-required" class="size-4 accent-primary" prop:checked=move||!required.get() on:change=move|_|required.set(false)/>"No"</label><label for="scout-onboarding-network-required-yes" class="flex items-center gap-2"><input id="scout-onboarding-network-required-yes" type="radio" name="network-required" class="size-4 accent-primary" prop:checked=move||required.get() on:change=move|_|required.set(true)/>"Yes"</label></div></fieldset><Show when=move||required.get()><div class="space-y-3">{[("provider","VPN provider","e.g. WireGuard, Tailscale"),("protocol","Protocol",""),("administrator","Responsible administrator","Name or team")].into_iter().map(move|(key,label,hint)|view!{<div class="space-y-1.5"><label for=format!("scout-onboarding-network-{key}") class="block text-xs font-medium">{label}</label><input id=format!("scout-onboarding-network-{key}") autocomplete="off" prop:value=move||draft.with(|v|string(v,key)) on:input=move|e|draft.update(|v|v[key]=json!(event_target_value(&e))) aria-invalid=move||errors.with(|v|v[key].is_string()).then_some("true") aria-describedby=move||errors.with(|v|v[key].is_string()).then(||format!("scout-onboarding-network-{key}-error")) class=INPUT.replace("h-11","h-9") placeholder=hint/>{move||errors.with(|v|v[key].as_str().map(str::to_owned)).map(|message|view!{<p id=format!("scout-onboarding-network-{key}-error") role="alert" class="text-xs text-danger">{message}</p>})}</div>}).collect_view()}<div class="space-y-1.5"><label for="scout-onboarding-network-systems" class="block text-xs font-medium">"Systems Scout needs to reach"</label><textarea id="scout-onboarding-network-systems" rows="2" prop:value=move||draft.with(|v|string(v,"systems")) on:input=move|e|draft.update(|v|v["systems"]=json!(event_target_value(&e))) class=INPUT.replace("h-11","min-h-16 py-2") aria-invalid=move||errors.with(|v|v["systems"].is_string()).then_some("true") aria-describedby=move||errors.with(|v|v["systems"].is_string()).then_some("scout-onboarding-network-systems-error") placeholder="Separate with commas or new lines"/>{move||errors.with(|v|v["systems"].as_str().map(str::to_owned)).map(|message|view!{<p id="scout-onboarding-network-systems-error" role="alert" class="text-xs text-danger">{message}</p>})}</div><div class="space-y-1.5"><label for="scout-onboarding-network-company-only" class="flex items-start gap-2 text-xs"><input id="scout-onboarding-network-company-only" type="checkbox" class="mt-0.5 size-4 accent-primary" prop:checked=move||draft.with(|v|v["companyOnly"]==true) on:change=move|e|draft.update(|v|v["companyOnly"]=json!(event_target_checked(&e))) aria-invalid=move||errors.with(|v|v["companyOnly"].is_string()).then_some("true")/>"I approve access to company folders and company mail only, never personal files."</label>{move||errors.with(|v|v["companyOnly"].as_str().map(str::to_owned)).map(|message|view!{<p id="scout-onboarding-network-company-only-error" role="alert" class="text-xs text-danger">{message}</p>})}</div><p class="text-xs leading-5 text-muted-foreground">"Do not enter private keys, passwords or VPN profiles. Your administrator sets up access; this stays marked as requiring administrator setup until then."</p></div></Show><button id="scout-onboarding-network-submit" type="submit" class=button_classes(PRIMARY,"h-8 w-auto") disabled=move||ctx.busy.get()>"Save answers"</button></form>}
}
fn download(name: &str, mime: &str, content: &str) {
    let Some(window) = web_sys::window() else {
        return;
    };
    let parts = js_sys::Array::of1(&wasm_bindgen::JsValue::from_str(content));
    let options = web_sys::BlobPropertyBag::new();
    options.set_type(&format!("{mime};charset=utf-8"));
    let Ok(blob) = web_sys::Blob::new_with_str_sequence_and_options(&parts, &options) else {
        return;
    };
    let Ok(url) = web_sys::Url::create_object_url_with_blob(&blob) else {
        return;
    };
    if let Some(document) = window.document() {
        if let Ok(link) = document.create_element("a").and_then(|e| {
            e.dyn_into::<web_sys::HtmlAnchorElement>()
                .map_err(Into::into)
        }) {
            link.set_href(&url);
            link.set_download(name);
            if let Some(body) = document.body() {
                let _ = body.append_child(&link);
                link.click();
                link.remove();
            }
        }
    }
    let revoke = wasm_bindgen::closure::Closure::once_into_js(move || {
        let _ = web_sys::Url::revoke_object_url(&url);
    });
    let _ =
        window.set_timeout_with_callback_and_timeout_and_arguments_0(revoke.unchecked_ref(), 1000);
}
#[component]
fn AgentDialog(open: RwSignal<bool>) -> impl IntoView {
    let ctx = expect_context::<SetupContext>();
    let handoff = RwSignal::new(None::<Value>);
    let error = RwSignal::new(String::new());
    let attempt = RwSignal::new(0u32);
    let status = RwSignal::new(String::new());
    let fallback = RwSignal::new(None::<String>);
    Effect::new(move |_| {
        let active = open.get();
        let _ = attempt.get();
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-onboarding-agent-dialog"))
            .and_then(|e| e.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if active {
                let _ = dialog.show_modal();
            } else {
                dialog.close();
            }
        }
        if !active {
            return;
        }
        handoff.set(None);
        error.set(String::new());
        status.set(String::new());
        fallback.set(None);
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/onboarding/handoff",
                "POST",
                Some(&json!({})),
                Some(&ctx.workspace.slug.get_value()),
            )
            .await
            {
                Ok((code, value))
                    if code < 400
                        && value["setupUrl"].is_string()
                        && value["markdown"].is_string()
                        && value["prompt"].is_string()
                        && value["manifest"].is_object() =>
                {
                    if open.get_untracked() {
                        handoff.set(Some(value));
                    }
                }
                Ok((_, value)) => error.set(
                    value["error"]
                        .as_str()
                        .unwrap_or("Scout returned an unexpected handoff.")
                        .into(),
                ),
                Err(message) => error.set(message),
            }
        });
    });
    let copy = move |key: &'static str, label: &'static str| {
        if let Some(value) = handoff.get_untracked() {
            let text = string(&value, key);
            leptos::task::spawn_local(async move {
                let copied = if let Some(w) = web_sys::window() {
                    wasm_bindgen_futures::JsFuture::from(
                        w.navigator().clipboard().write_text(&text),
                    )
                    .await
                    .is_ok()
                } else {
                    false
                };
                if copied {
                    fallback.set(None);
                    status.set(format!("{label} copied."))
                } else {
                    fallback.set(Some(text));
                    status.set(
                        "Your browser blocked copying. Select the text below and copy it yourself."
                            .into(),
                    )
                }
            });
        }
    };
    view! {<dialog id="scout-onboarding-agent-dialog" class="fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg space-y-4 overflow-y-auto rounded-xl border bg-popover p-6 text-popover-foreground shadow-xl backdrop:bg-foreground/20" aria-labelledby="scout-onboarding-agent-title" aria-describedby="scout-onboarding-agent-description" on:close=move|_|{open.set(false);focus("scout-onboarding-agent");}><header class="pr-6"><h2 id="scout-onboarding-agent-title" class="text-lg font-semibold">"Continue with your agent"</h2><p id="scout-onboarding-agent-description" class="mt-2 text-sm leading-6 text-muted-foreground">"Give these instructions to any agent that can follow written steps, such as Codex or Claude Code. It guides you through setup. You sign in and approve each provider yourself, and Scout gives the agent no credentials or access."</p></header><button type="button" aria-label="Close" class="absolute top-3 right-3 flex size-8 items-center justify-center rounded hover:bg-accent" on:click=move|_|open.set(false)><Icon name="close"/></button><Show when=move||handoff.get().is_none()&&error.get().is_empty()><p id="scout-onboarding-agent-loading" role="status" class="text-sm text-muted-foreground">"Preparing instructions…"</p></Show><Show when=move||!error.get().is_empty()><div id="scout-onboarding-agent-error" role="alert" class="space-y-2 text-sm text-danger"><p>{move||error.get()}</p><button id="scout-onboarding-agent-retry" class=button_classes(OUTLINE,"h-8 w-auto") on:click=move|_|attempt.update(|v|*v+=1)>"Try again"</button></div></Show>{move||handoff.get().map(|value|view!{<div class="space-y-3"><div class="flex flex-wrap gap-2"><button id="scout-onboarding-copy-link" type="button" class=button_classes(OUTLINE,"h-9 w-auto") on:click=move|_|copy("setupUrl","Setup link")>"Copy setup link"</button><button id="scout-onboarding-copy-instructions" type="button" class=button_classes(OUTLINE,"h-9 w-auto") aria-label="Copy instructions" on:click=move|_|copy("prompt","Instructions")>"Copy instructions"</button></div><div class="flex flex-wrap gap-2">{[("md","Markdown","scout-setup.md","text/markdown"),("json","JSON","scout-setup.json","application/json")].into_iter().map(move|(id,label,name,mime)|view!{<button id=format!("scout-onboarding-download-{id}") type="button" class=button_classes(OUTLINE,"h-9 w-auto") on:click=move|_|{if let Some(value)=handoff.get_untracked(){let content=if id=="md"{string(&value,"markdown")}else{serde_json::to_string_pretty(&value["manifest"]).unwrap_or_default()};download(name,mime,&content);status.set(format!("{name} downloaded."));}}>{format!("Download {label}")}</button>}).collect_view()}</div>{value["claudeUrl"].as_str().filter(|s|s.starts_with("claude-cli://")&&s.encode_utf16().count()<5000).map(|url|view!{<p class="text-xs leading-5 text-muted-foreground">"Optional: "<a id="scout-onboarding-claude-link" href=url.to_owned() class="underline underline-offset-2">"open in Claude Code"</a>". Works only if Claude Code is installed; it does not send anything for you."</p>})}<p id="scout-onboarding-agent-status" role="status" aria-live="polite" class="min-h-5 text-xs text-muted-foreground">{move||status.get()}</p>{move||fallback.get().map(|text|view!{<textarea id="scout-onboarding-copy-fallback" aria-label="Text to copy manually" readonly rows="6" prop:value=text class=format!("{} h-auto font-mono text-xs",INPUT) on:focus=move|e|{if let Some(input)=e.target().and_then(|t|t.dyn_into::<web_sys::HtmlTextAreaElement>().ok()){input.select();}}/>})}<p class="text-xs leading-5 text-muted-foreground">"The link only opens your normal sign-in; sharing it grants no access."</p></div>})}</dialog>}
}
#[component]
fn SetupStep() -> impl IntoView {
    let ctx = expect_context::<SetupContext>();
    let step = ctx.step();
    let meta = STEPS[ctx.current.get_untracked()];
    let id = meta.0;
    let form = RwSignal::new(false);
    let api_key = RwSignal::new(String::new());
    let nip = RwSignal::new(String::new());
    let nip_error = RwSignal::new(false);
    let mail_label = RwSignal::new("INBOX".to_string());
    let connected = matches!(
        step["state"].as_str(),
        Some("connected" | "sandbox_connected")
    );
    let authorized = id == "slack" && step["display"]["authorized"] == true;
    let (status, tone) = step_status(&step);
    let state = string(&step, "state");
    let connect = move || {
        let body = match id {
            "ksef" => json!({"nip":nip.get_untracked().trim()}),
            "bank" => {
                if api_key.get_untracked().trim().is_empty() {
                    json!({})
                } else {
                    json!({"api_key":api_key.get_untracked().trim()})
                }
            }
            "email" => {
                json!({"label":if mail_label.get_untracked().trim().is_empty(){"INBOX".into()}else{mail_label.get_untracked().trim().to_string()}})
            }
            _ => json!({}),
        };
        ctx.connect(meta.4, body);
    };
    let facts = step["display"]
        .as_object()
        .map(|v| {
            v.iter()
                .filter(|(k, v)| {
                    k.as_str() != "channel_ids"
                        && !v.is_null()
                        && **v != ""
                        && (v.is_string() || v.is_boolean())
                })
                .take(6)
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let network = ctx.setup.with_untracked(|v| {
        v.as_ref()
            .map(|v| v["network"].clone())
            .unwrap_or(Value::Null)
    });
    view! {<div id=format!("scout-onboarding-step-{id}") data-onboarding-step=id data-state=state.clone() role="group" aria-labelledby=format!("scout-onboarding-title-{id}") class="min-w-0"><div class="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] items-start gap-x-3 gap-y-3 px-4 py-4 sm:px-5"><span class="flex size-8 items-center justify-center rounded-lg border bg-background text-muted-foreground"><Icon name=meta.5/></span><div class="min-w-0 space-y-1"><div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1"><h2 id=format!("scout-onboarding-title-{id}") tabindex="-1" class="text-[13px] leading-5 font-medium outline-none">{meta.1}</h2><span id=format!("scout-onboarding-status-{id}") data-onboarding-status=state.clone() class=format!("inline-flex items-center gap-1.5 text-xs {}",match tone{"danger"=>"text-danger","warning"=>"text-warning",_=>"text-muted-foreground"})><span class=format!("size-1.5 rounded-full {}",match tone{"danger"=>"bg-danger","warning"=>"bg-warning-mark","primary"=>"bg-primary",_=>"bg-muted-foreground/50"})/>{status}</span></div><p class="text-xs leading-5 text-muted-foreground">{meta.2}</p>{step["message"].as_str().filter(|s|!s.is_empty()).map(|s|view!{<p id=format!("scout-onboarding-message-{id}") class=if state=="failed"{"text-xs leading-5 break-words text-danger"}else{"text-xs leading-5 break-words text-foreground"}>{s.to_owned()}</p>})}{(!facts.is_empty()).then(||view!{<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-xs leading-5 text-muted-foreground">{facts.into_iter().map(|(key,value)|{let text=key.replace('_'," ");let mut chars=text.chars();let title=chars.next().map(|c|c.to_uppercase().collect::<String>()).unwrap_or_default()+chars.as_str();view!{<div class="contents"><dt>{title}</dt><dd class="min-w-0 break-words font-mono text-foreground">{value.as_str().map(str::to_owned).unwrap_or_else(||if value==true{"Yes".into()}else{"No".into()})}</dd></div>}}).collect_view()}</dl>})}{ctx.editable.then(||view!{<div class="flex flex-wrap items-center gap-2 pt-2">{if state=="skipped"{view!{<button id=format!("scout-onboarding-resume-{id}") type="button" class=button_classes(OUTLINE,"h-8 w-auto") disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{ctx.patch(json!({"action":"step","step":id,"state":"not_started"})).await;})>"Set up"</button>}.into_any()}else{view!{{(state=="connecting").then(||view!{<button id=format!("scout-onboarding-refresh-{id}") type="button" class=button_classes(OUTLINE,"h-8 w-auto") disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{ctx.request("/api/onboarding","GET",None).await;})>"Check status"</button>{(!meta.4.is_empty()).then(||view!{<button id=format!("scout-onboarding-cancel-{id}") type="button" class="h-8 rounded px-3 text-sm hover:bg-accent" disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{ctx.request(&format!("/api/onboarding/connect/{}",meta.4),"DELETE",Some(json!({}))).await;})>"Cancel authorization"</button>})}})}{((!connected&&!authorized&&state!="connecting")||id=="network").then(||view!{<button id=format!("scout-onboarding-action-{id}") type="button" class=button_classes(if connected{OUTLINE}else{PRIMARY},"h-8 w-auto") disabled=move||ctx.busy.get() aria-expanded=move||if matches!(id,"ksef"|"network"){Some(form.get().to_string())}else{None} on:click=move|_|if matches!(id,"ksef"|"network"){form.update(|v|*v=!*v)}else{connect()}>{if state=="failed"{"Try again"}else if id=="network"&&state!="not_started"{"Change answers"}else{meta.3}}</button>})}{((connected||authorized)&&!meta.4.is_empty()).then(||view!{<button id=format!("scout-onboarding-disconnect-{id}") type="button" class=button_classes(OUTLINE,"h-8 w-auto") disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{ctx.request(&format!("/api/onboarding/connect/{}",meta.4),"DELETE",Some(json!({}))).await;})>"Disconnect"</button>})}{(state!="connecting"&&!connected&&!authorized).then(||view!{<button id=format!("scout-onboarding-skip-{id}") type="button" class="h-8 rounded px-3 text-sm text-muted-foreground hover:bg-accent" disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{if ctx.patch(json!({"action":"step","step":id,"state":"skipped"})).await{ctx.proceed().await;}})>"Skip"<span class="sr-only">{format!(" {}",meta.1)}</span></button>})}}.into_any()}}</div>})}{(ctx.editable&&id=="bank"&&!connected&&state!="skipped").then(||view!{<details class="pt-1 text-xs text-muted-foreground"><summary class="cursor-pointer">"Use my own bunq sandbox API key"</summary><div class="mt-2 max-w-sm space-y-1.5"><label for="scout-onboarding-bunq-key" class="block font-medium text-foreground">"bunq sandbox API key (optional)"</label><input id="scout-onboarding-bunq-key" type="password" autocomplete="off" spellcheck="false" prop:value=move||api_key.get() on:input=move|e|api_key.set(event_target_value(&e)) class=INPUT.replace("h-11","h-9")/><p>"Leave empty to create a sandbox company with test data. Sandbox keys only."</p></div></details>})}{(ctx.editable&&id=="email"&&!connected&&!matches!(state.as_str(),"skipped"|"connecting")).then(||view!{<details class="pt-1 text-xs text-muted-foreground"><summary class="cursor-pointer">"Choose the mail label Scout may read"</summary><div class="mt-2 max-w-sm space-y-1.5"><label for="scout-onboarding-gmail-label" class="block font-medium text-foreground">"Gmail label"</label><input id="scout-onboarding-gmail-label" prop:value=move||mail_label.get() maxlength="100" on:input=move|e|mail_label.set(event_target_value(&e)) class=INPUT.replace("h-11","h-9")/><p>"Defaults to INBOX. Google grants read-only mailbox permission; Scout restricts its reads to this label."</p></div></details>})}{(ctx.editable&&authorized).then(||view!{<details id="scout-onboarding-slack-manual" class="pt-1 text-xs text-muted-foreground" open=!connected><summary class="cursor-pointer">"Edit channels manually"</summary><SlackChannels initial=string(&step["display"],"channel_ids")/></details>})}<Show when=move||ctx.editable&&id=="ksef"&&form.get()><form id="scout-onboarding-ksef-form" class="max-w-sm space-y-1.5 pt-2" novalidate on:submit=move|e|{e.prevent_default();let raw=nip.get_untracked();let valid=raw.trim().len()==10&&raw.trim().bytes().all(|b|b.is_ascii_digit());nip_error.set(!valid);if valid{connect()}}><label for="scout-onboarding-ksef-nip" class="block text-xs font-medium">"Company NIP"</label><input id="scout-onboarding-ksef-nip" inputmode="numeric" autocomplete="off" prop:value=move||nip.get() on:input=move|e|nip.set(event_target_value(&e)) aria-invalid=move||nip_error.get().then_some("true") aria-describedby=move||nip_error.get().then_some("scout-onboarding-ksef-nip-error") class=format!("{} font-mono",INPUT.replace("h-11","h-9"))/><Show when=move||nip_error.get()><p id="scout-onboarding-ksef-nip-error" role="alert" class="text-xs text-danger">"Enter the 10-digit NIP, digits only."</p></Show><p class="text-xs text-muted-foreground">"Test environment only. Nothing is submitted to the tax authority."</p><button id="scout-onboarding-ksef-submit" type="submit" class=button_classes(PRIMARY,"h-8 w-auto") disabled=move||ctx.busy.get()>"Check test environment"</button></form></Show><Show when=move||ctx.editable&&id=="network"&&form.get()><NetworkForm initial=network.clone() on_saved=Callback::new(move|_|form.set(false))/></Show></div></div></div>}
}
#[component]
fn SlackChannels(initial: String) -> impl IntoView {
    let ctx = expect_context::<SetupContext>();
    let value = RwSignal::new(initial);
    let error = RwSignal::new(false);
    view! {<form id="scout-onboarding-slack-channels-form" class="max-w-md space-y-2 pt-2" on:submit=move|e|{e.prevent_default();let mut channels=Vec::new();for s in value.get_untracked().split(|c:char|c.is_whitespace()||c==',').filter(|s|!s.is_empty()){if !channels.contains(&s.to_string()){channels.push(s.to_string());}}let valid=channels.len()<=100&&channels.iter().all(|s|(9..=15).contains(&s.len())&&matches!(s.as_bytes()[0],b'C'|b'G')&&s.bytes().skip(1).all(|b|b.is_ascii_uppercase()||b.is_ascii_digit()));error.set(!valid);if valid{leptos::task::spawn_local(async move{ctx.request("/api/onboarding/slack/channels","PUT",Some(json!({"channels":channels}))).await;});}}><label for="scout-onboarding-slack-channels" class="block text-xs font-medium">"Slack channel IDs"</label><input id="scout-onboarding-slack-channels" prop:value=move||value.get() on:input=move|e|value.set(event_target_value(&e)) placeholder="C0123456789, G0123456789" autocomplete="off" class=format!("{} font-mono",INPUT.replace("h-11","h-9")) aria-invalid=move||error.get().then_some("true") aria-describedby="scout-onboarding-slack-channels-hint"/><p id="scout-onboarding-slack-channels-hint" class="text-xs leading-5 text-muted-foreground">"Scout joined these channels when you connected Slack. Remove IDs to stop reading a channel, or add one (invite Scout’s bot to it first). An empty list turns channel reading off."</p><Show when=move||error.get()><p id="scout-onboarding-slack-channels-error" role="alert" class="text-xs text-danger">"Enter at most 100 Slack channel IDs, starting with C or G, separated by commas or spaces."</p></Show><button id="scout-onboarding-slack-channels-save" type="submit" class=button_classes(PRIMARY,"h-8 w-auto") disabled=move||ctx.busy.get()>"Save channels"</button></form>}
}
#[component]
pub fn SetupView() -> impl IntoView {
    let workspace = expect_context::<WorkspaceContext>();
    let ctx = SetupContext {
        workspace,
        setup: RwSignal::new(None),
        current: RwSignal::new(0),
        busy: RwSignal::new(false),
        notice: RwSignal::new(None),
        editable: workspace
            .data
            .with_untracked(|v| v["workspace"]["role"] == "owner"),
    };
    provide_context(ctx);
    let load_error = RwSignal::new(String::new());
    let revision = RwSignal::new(0u32);
    let agent = RwSignal::new(false);
    let requested = StoredValue::new(
        workspace
            .route
            .with_untracked(|r| r.filters.get("step").cloned()),
    );
    Effect::new(move |_| {
        let _ = revision.get();
        load_error.set(String::new());
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/onboarding",
                "GET",
                None,
                Some(&workspace.slug.get_value()),
            )
            .await
            {
                Ok((200, value)) if valid_setup(&value) => {
                    let target = requested
                        .get_value()
                        .and_then(|s| STEPS.iter().position(|m| m.0 == s))
                        .unwrap_or_else(|| {
                            STEPS
                                .iter()
                                .position(|meta| {
                                    value["steps"].as_array().is_some_and(|rows| {
                                        rows.iter().any(|s| s["id"] == meta.0 && !settled(s))
                                    })
                                })
                                .unwrap_or(5)
                        });
                    ctx.current.set(target);
                    ctx.setup.set(Some(value));
                }
                Ok((_, value)) => load_error.set(
                    value["error"]
                        .as_str()
                        .unwrap_or("Scout returned an unexpected setup response.")
                        .into(),
                ),
                Err(_) => load_error
                    .set("Could not reach Scout. Check your connection and try again.".into()),
            }
        });
    });
    Effect::new(move |_| {
        if let Some(step) = workspace.route.with(|r| r.filters.get("step").cloned()) {
            if let Some(index) = STEPS.iter().position(|m| m.0 == step) {
                ctx.go(index);
            }
        }
    });
    Effect::new(move |_| {
        let Some(w) = web_sys::window() else { return };
        let query = w.location().search().unwrap_or_default();
        let mut params = url::form_urlencoded::parse(query.trim_start_matches('?').as_bytes())
            .into_owned()
            .collect::<Vec<_>>();
        if let Some(value) = params
            .iter()
            .find(|(k, _)| k == "setup")
            .map(|(_, v)| v.clone())
        {
            let parts = value.split('-').collect::<Vec<_>>();
            if parts.len() == 2
                && matches!(parts[0], "google" | "slack")
                && matches!(parts[1], "connected" | "failed")
            {
                let step = if parts[0] == "google" {
                    "email"
                } else {
                    "slack"
                };
                requested.set_value(Some(step.into()));
                ctx.current.set(if step == "email" { 3 } else { 4 });
                let name = if step == "email" { "Google" } else { "Slack" };
                let ok = parts[1] == "connected";
                ctx.notice.set(Some((ok,if ok{format!("{name} authorization completed. The status below is what Scout verified.")}else{format!("{name} connection did not complete. Nothing was connected; you can try again.")})));
                params.retain(|(k, _)| k != "setup");
                let mut encoded = url::form_urlencoded::Serializer::new(String::new());
                encoded.extend_pairs(params);
                let encoded = encoded.finish();
                let target = format!(
                    "{}{}{}{}",
                    w.location().pathname().unwrap_or_default(),
                    if encoded.is_empty() { "" } else { "?" },
                    encoded,
                    w.location().hash().unwrap_or_default()
                );
                let _ = w.history().and_then(|h| {
                    h.replace_state_with_url(&wasm_bindgen::JsValue::NULL, "", Some(&target))
                });
            }
        }
    });
    view! {<main id="main-content" tabindex="-1" aria-label="Workspace setup" class="min-h-dvh w-full bg-background px-4 py-5 outline-none sm:px-8"><header id="scout-setup-header" class="mx-auto mb-6 flex w-full max-w-2xl items-center justify-between gap-3 text-sm"><p class="min-w-0 truncate"><span class="font-medium">"Scout"</span><span class="ml-2 text-muted-foreground">{workspace.data.with_untracked(|v|string(&v["workspace"],"name"))}</span></p><ThemeToggle compact=true/></header><section id="scout-onboarding" aria-labelledby="scout-onboarding-title" class="mx-auto w-full max-w-2xl space-y-5 py-2 md:py-6"><div><h1 id="scout-onboarding-title" class="text-2xl font-medium tracking-tight">"Your workspace is ready"</h1><p class="mt-1 text-sm text-muted-foreground">"Connect what Scout should work with, one step at a time. Everything is optional."</p></div>{(!ctx.editable).then(||view!{<p id="scout-onboarding-readonly" role="note" class="rounded-lg border bg-muted/40 px-4 py-3 text-[13px]">"Only workspace owners can change setup. You can see its progress here; ask an owner to connect or skip a step."</p>})}{move||ctx.notice.get().map(|(ok,text)|view!{<div id="scout-onboarding-notice" role=if ok{"status"}else{"alert"} class=format!("flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-[13px] {}",if ok{""}else{"border-danger-border text-danger"})><Icon name=if ok{"resolved"}else{"alert"}/><span class="min-w-0 break-words">{text}</span></div>})}{move||if ctx.setup.get().is_none(){if load_error.get().is_empty(){view!{<div id="scout-onboarding-loading" role="status" aria-label="Loading setup" class="h-20 animate-pulse rounded-xl border bg-muted"/>}.into_any()}else{view!{<div id="scout-onboarding-error" role="alert" class="rounded-xl border border-danger-border bg-card px-4 py-6 text-center text-sm"><p class="text-danger">{move||load_error.get()}</p><button id="scout-onboarding-retry" type="button" class=button_classes(OUTLINE,"mt-3 h-8 w-auto") on:click=move|_|revision.update(|n|*n+=1)>"Try again"</button></div>}.into_any()}}else{view!{<div id="scout-onboarding-progress" class="space-y-2"><div class="flex items-baseline justify-between gap-3 text-xs"><span id="scout-onboarding-progress-label" class="font-medium">{move||format!("Step {} of 6",ctx.current.get()+1)}</span><span class="min-w-0 truncate text-muted-foreground">{move||STEPS[ctx.current.get()].1}</span></div><div id="scout-onboarding-progressbar" role="progressbar" aria-label="Setup progress" aria-valuemin="1" aria-valuemax="6" aria-valuenow=move||(ctx.current.get()+1).to_string() aria-valuetext=move||format!("Step {} of 6: {}",ctx.current.get()+1,STEPS[ctx.current.get()].1) class="flex gap-1">{(0..6).map(move|i|view!{<span class=move||if i<=ctx.current.get(){"h-1 flex-1 rounded-full bg-primary"}else{"h-1 flex-1 rounded-full bg-muted"}/>}).collect_view()}</div></div><div id="scout-onboarding-steps" class="overflow-hidden rounded-xl border bg-card">{move||{let _=ctx.current.get();let _=ctx.setup.get();view!{<SetupStep/>}}}</div><div class="space-y-3"><div class="flex flex-wrap items-center gap-2"><button id="scout-onboarding-back" type="button" class=button_classes(OUTLINE,"h-10 w-auto") disabled=move||ctx.busy.get()||ctx.current.get()==0 on:click=move|_|ctx.go(ctx.current.get_untracked().saturating_sub(1))>"Back"</button><button id="scout-onboarding-next" type="button" class=move||button_classes(if skip_on_next(&ctx.step()){OUTLINE}else{PRIMARY},"h-10 w-auto") disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{if !ctx.editable{if ctx.current.get_untracked()<5{ctx.go(ctx.current.get_untracked()+1)}else{workspace.navigate(Route::view("chat"));}return}let step=ctx.step();if skip_on_next(&step)&&!ctx.patch(json!({"action":"step","step":step["id"],"state":"skipped"})).await{return}ctx.proceed().await;})>{move||if ctx.current.get()<5{"Next"}else{"Finish setup"}}</button>{ctx.editable.then(||view!{<button id="scout-onboarding-agent" type="button" class=button_classes(OUTLINE,"h-10 w-auto sm:ml-auto") disabled=move||ctx.busy.get() on:click=move|_|agent.set(true)>"Continue with your agent"</button>})}</div><Show when=move||{let _=ctx.current.get();let _=ctx.setup.get();ctx.editable&&skip_on_next(&ctx.step())}><p id="scout-onboarding-next-hint" class="text-xs leading-5 text-muted-foreground">{move||if ctx.current.get()<5{"Next skips this step."}else{"Finishing skips this step."}}" You can set it up later from Connections."</p></Show><p class="text-xs leading-5 text-muted-foreground">{ctx.editable.then(||view!{<button id="scout-onboarding-skip" type="button" class="text-xs underline-offset-4 hover:underline" disabled=move||ctx.busy.get() on:click=move|_|leptos::task::spawn_local(async move{ctx.leave("skip").await;})>"Skip setup for now"</button>" · "})}"You can finish setup from Connections."</p></div>}.into_any()}}{ctx.editable.then(||view!{<AgentDialog open=agent/>})}</section></main>}
}
