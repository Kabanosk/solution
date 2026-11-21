use crate::{Organization, PageBootstrap, primitives::*};
use leptos::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use wasm_bindgen::JsCast;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PublicAuth {
    Join {
        token: String,
        valid: bool,
        #[serde(default)]
        slack_name: String,
    },
    LoginLink {
        token: String,
    },
}
#[component]
pub fn PublicAuthView(bootstrap: PageBootstrap) -> impl IntoView {
    let org = bootstrap.organization.clone().unwrap_or(Organization {
        name: String::new(),
        slug: String::new(),
    });
    match bootstrap.public_auth.clone(){Some(PublicAuth::Join{token,valid,slack_name})=>view!{<JoinWorkspace organization=org token valid slack_name privacy=bootstrap.privacy terms=bootstrap.terms/>}.into_any(),Some(PublicAuth::LoginLink{token})=>view!{<LoginLink organization=org token/>}.into_any(),None=>().into_any()}
}
#[component]
fn JoinWorkspace(
    organization: Organization,
    token: String,
    valid: bool,
    slack_name: String,
    privacy: Option<String>,
    terms: Option<String>,
) -> impl IntoView {
    let organization = StoredValue::new(organization);
    let token = StoredValue::new(token);
    let pending = RwSignal::new(false);
    let error = RwSignal::new(if valid {
        String::new()
    } else {
        "This invite link is invalid or was already used. Message Scout on Slack for a new one."
            .into()
    });
    let mut parts = slack_name.split_whitespace();
    let first = parts.next().unwrap_or("").to_owned();
    let last = parts.collect::<Vec<_>>().join(" ");
    Effect::new(move |_| {
        if !error.get().is_empty() {
            let _ = leptos::task::spawn_local(async move {
                gloo_timers::future::TimeoutFuture::new(0).await;
                focus("scout-join-error");
            });
        }
    });
    view! {<main id="scout-join-page" class="flex min-h-dvh flex-col bg-background px-5 py-6 text-foreground sm:px-8"><div class="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12"><header class="mb-8 flex items-center justify-between gap-4"><a href="/" class="flex items-center gap-2.5 text-base font-semibold tracking-tight" aria-label="Scout home"><ScoutMark size=28/>"Scout"</a><ThemeToggle/></header><section class="flex flex-col gap-6 border-0 bg-transparent py-0"><header class="space-y-3"><p id="scout-join-org" class="text-sm font-medium text-muted-foreground">{organization.get_value().name}</p><h1 id="scout-join-heading" class="text-2xl font-semibold tracking-tight">{format!("Join {} workspace",organization.get_value().name)}</h1><p class="text-sm leading-6 text-muted-foreground">"Create your Scout account. It is linked to your Slack account, so you can also just DM Scout there. Already use Scout elsewhere? Sign up with the same email and password to add this workspace."</p></header><div class="space-y-5"><form id="scout-join-form" class="space-y-4" aria-busy=move||pending.get().to_string() aria-describedby=move||(!error.get().is_empty()).then_some("scout-join-error") on:submit=move|e|{e.prevent_default();let Some(form)=e.current_target().and_then(|t|t.dyn_into::<web_sys::HtmlFormElement>().ok())else{return};let Ok(values)=web_sys::FormData::new_with_form(&form)else{return};let mut body=json!({"slug":organization.get_value().slug,"token":token.get_value()});for key in ["firstName","lastName","email","password"]{body[key]=json!(values.get(key).as_string().unwrap_or_default())}pending.set(true);error.set(String::new());leptos::task::spawn_local(async move{match json_request("/api/auth/join","POST",Some(&body),None).await{Ok((status,value))if status<400=>{if let Some(redirect)=value["redirect"].as_str(){navigate(redirect)}},Ok((_,value))=>{error.set(value["error"].as_str().unwrap_or("Please try again.").into());pending.set(false)},Err(message)=>{error.set(message);pending.set(false)}}});}><div class="grid grid-cols-2 gap-3"><div class="space-y-2"><label for="scout-join-first" class="text-sm font-medium">"First name"</label><input id="scout-join-first" name="firstName" autocomplete="given-name" value=first required maxlength="50" disabled=!valid class=INPUT/></div><div class="space-y-2"><label for="scout-join-last" class="text-sm font-medium">"Last name"</label><input id="scout-join-last" name="lastName" autocomplete="family-name" value=last required maxlength="50" disabled=!valid class=INPUT/></div></div><div class="space-y-2"><label for="scout-join-email" class="text-sm font-medium">"Email"</label><input id="scout-join-email" name="email" type="email" autocomplete="username" required maxlength="254" placeholder="you@company.com" disabled=!valid class=INPUT/></div><div class="space-y-2"><label for="scout-join-password" class="text-sm font-medium">"Password"</label><input id="scout-join-password" name="password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" aria-describedby="scout-join-password-hint" disabled=!valid class=INPUT/><p id="scout-join-password-hint" class="text-xs leading-5 text-muted-foreground">"At least 12 characters, or your existing Scout password."</p></div><Show when=move||!error.get().is_empty()><p id="scout-join-error" tabindex="-1" role="alert" class="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{move||error.get()}</p></Show><button id="scout-join-submit" type="submit" disabled=move||pending.get()||!valid class=PRIMARY>{move||if pending.get(){"Please wait…".into()}else{format!("Join {}",organization.get_value().name)}}</button></form><div class="border-t pt-5 text-center text-sm text-muted-foreground">"Already a member? "<a href=format!("/{}",organization.get_value().slug) class="font-medium text-foreground underline-offset-4 hover:underline">"Sign in"</a></div></div></section></div><footer class="space-y-1.5 text-center text-xs leading-5 text-muted-foreground"><p>"Scout · Your finance agent"</p>{(privacy.is_some()||terms.is_some()).then(||view!{<nav aria-label="Legal information" class="flex items-center justify-center gap-3">{privacy.map(|href|view!{<a href=href class="underline-offset-4 hover:text-foreground hover:underline">"Privacy"</a>})}{terms.map(|href|view!{<a href=href class="underline-offset-4 hover:text-foreground hover:underline">"Terms"</a>})}</nav>})}</footer></main>}
}
#[component]
fn LoginLink(organization: Organization, token: String) -> impl IntoView {
    let organization = StoredValue::new(organization);
    let token = StoredValue::new(token);
    let error = RwSignal::new(String::new());
    let pending = RwSignal::new(false);
    let sign_in = move || {
        pending.set(true);
        error.set(String::new());
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/auth/link",
                "POST",
                Some(&json!({"slug":organization.get_value().slug,"token":token.get_value()})),
                None,
            )
            .await
            {
                Ok((status, value)) if status < 400 => {
                    if let Some(redirect) = value["redirect"].as_str() {
                        navigate(redirect)
                    }
                }
                Ok((_, value)) => {
                    error.set(
                        value["error"]
                            .as_str()
                            .unwrap_or("Please try again.")
                            .into(),
                    );
                    pending.set(false)
                }
                Err(message) => {
                    error.set(message);
                    pending.set(false)
                }
            }
        });
    };
    Effect::new(move |_| sign_in());
    view! {<main id="scout-login-link-page" class="flex min-h-dvh items-center justify-center bg-background px-5 text-foreground"><div class="w-full max-w-sm space-y-6 text-center"><div class="flex items-center justify-center gap-2.5 text-base font-semibold tracking-tight"><ScoutMark size=28/>"Scout"</div><h1 class="text-2xl font-semibold tracking-tight">{format!("Signing in to {}",organization.get_value().name)}</h1>{move||if !error.get().is_empty(){view!{<p role="alert" class="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{error.get()}</p><a href=format!("/{}",organization.get_value().slug) class=button_classes(OUTLINE,"h-11 w-full")>"Sign in with your password"</a>}.into_any()}else{view!{<p class="text-sm text-muted-foreground">{move||if pending.get(){"One moment…"}else{"Continue to your workspace."}}</p><button id="scout-login-link-continue" type="button" disabled=move||pending.get() class=PRIMARY on:click=move|_|sign_in()>{move||if pending.get(){"Signing in…".into()}else{format!("Open {}",organization.get_value().name)}}</button>}.into_any()}}</div></main>}
}
