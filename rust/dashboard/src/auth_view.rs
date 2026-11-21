use crate::{PageBootstrap, primitives::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use unicode_normalization::UnicodeNormalization;
use wasm_bindgen::JsCast;

fn slug_from_name(name: &str) -> String {
    let mut output = String::new();
    for c in name
        .to_lowercase()
        .nfkd()
        .filter(|c| !('\u{0300}'..='\u{036f}').contains(c))
    {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            output.push(c)
        } else if !output.is_empty() && !output.ends_with('-') {
            output.push('-')
        }
    }
    output.trim_end_matches('-').chars().take(63).collect()
}
fn form_values(form: &web_sys::HtmlFormElement) -> Value {
    let mut output = serde_json::Map::new();
    if let Ok(values) = web_sys::FormData::new_with_form(form) {
        for key in [
            "organization",
            "name",
            "slug",
            "ownerName",
            "email",
            "password",
        ] {
            if let Some(value) = values.get(key).as_string() {
                output.insert(key.into(), Value::String(value));
            }
        }
    }
    Value::Object(output)
}
#[component]
pub fn OrganizationAuth(bootstrap: PageBootstrap) -> impl IntoView {
    let login = bootstrap.organization.is_some();
    let org = StoredValue::new(bootstrap.organization.clone());
    let creating = RwSignal::new(bootstrap.initial_creating);
    let pending = RwSignal::new(false);
    let error = RwSignal::new(bootstrap.initial_error.unwrap_or_default());
    let slug = RwSignal::new(String::new());
    let edited = RwSignal::new(false);
    let google = bootstrap.google_available;
    let mode = move || {
        if login {
            "login"
        } else if creating.get() {
            "register"
        } else {
            "find"
        }
    };
    let form_ref = NodeRef::<leptos::html::Form>::new();
    Effect::new(move |_| {
        if !error.get().is_empty() {
            focus("scout-auth-error")
        }
    });
    let submit = move |event: leptos::ev::SubmitEvent| {
        event.prevent_default();
        pending.set(true);
        error.set(String::new());
        let Some(form) = event
            .current_target()
            .and_then(|v| v.dyn_into::<web_sys::HtmlFormElement>().ok())
        else {
            pending.set(false);
            return;
        };
        let mut values = form_values(&form);
        let current = mode();
        if let Some(org) = org.get_value() {
            values["slug"] = json!(org.slug);
        }
        leptos::task::spawn_local(async move {
            match json_request(&format!("/api/auth/{current}"), "POST", Some(&values), None).await {
                Ok((status, body)) if status < 400 => {
                    if current == "find" {
                        if let Some(slug) = body["slug"].as_str() {
                            navigate(&format!("/{slug}"));
                        }
                    } else if let Some(url) = body["redirect"].as_str() {
                        navigate(url);
                    }
                }
                Ok((_, body)) => {
                    error.set(body["error"].as_str().unwrap_or("Please try again.").into())
                }
                Err(message) => error.set(message),
            }
            pending.set(false);
        });
    };
    let google_signup = move |_| {
        if !google || pending.get() {
            return;
        }
        let Some(form) = form_ref.get() else { return };
        for key in ["scout-name-input", "scout-slug-input"] {
            if let Some(input) = web_sys::window()
                .and_then(|w| w.document())
                .and_then(|d| d.get_element_by_id(key))
                .and_then(|v| v.dyn_into::<web_sys::HtmlInputElement>().ok())
            {
                if !input.report_validity() {
                    return;
                }
            }
        }
        let values = form_values(&form);
        let body = json!({"intent":"register","name":values["name"],"slug":values["slug"]});
        pending.set(true);
        error.set(String::new());
        leptos::task::spawn_local(async move {
            match json_request("/api/auth/google/start", "POST", Some(&body), None).await {
                Ok((status, body))
                    if status < 400 && body["url"].as_str().is_some_and(|s| !s.is_empty()) =>
                {
                    navigate(body["url"].as_str().unwrap_or("/"))
                }
                Ok((_, body)) => {
                    error.set(
                        body["error"]
                            .as_str()
                            .unwrap_or("Could not start Google sign-up. Please try again.")
                            .into(),
                    );
                    pending.set(false);
                }
                Err(message) => {
                    error.set(message);
                    pending.set(false);
                }
            }
        });
    };
    view! {
    <main id="scout-auth-page" class="flex min-h-dvh flex-col bg-background px-5 py-6 text-foreground sm:px-8">
      <div id="scout-auth-center" class="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
        <header id="scout-auth-header" class="mb-8 flex items-center justify-between gap-4"><a id="scout-auth-brand" href="/" class="flex items-center gap-2.5 text-base font-semibold tracking-tight" aria-label="Scout home"><ScoutMark/>"Scout"</a><ThemeToggle/></header>
        <div id="scout-auth-card" data-slot="card" class="flex flex-col gap-6 border-0 bg-transparent py-0 shadow-none">
          <div id="scout-auth-card-header" data-slot="card-header" class="grid auto-rows-min grid-rows-[auto_auto] items-start gap-3 px-0">
            {org.get_value().map(|o|view!{<p id="scout-auth-org" class="text-sm font-medium text-muted-foreground">{o.name}</p>})}
            <div id="scout-auth-title" class="text-2xl font-semibold tracking-tight"><h1 id="scout-auth-heading">{move||if login{"Welcome back"}else if creating.get(){"Create your organization"}else{"Your organization, in focus."}}</h1></div>
            <div id="scout-auth-description" class="text-sm leading-6 text-muted-foreground">{move||if login{"Sign in to your organization’s private Scout workspace."}else if creating.get(){"A private workspace for your team and your finance agent."}else{"Find your team’s workspace or start a new one. Your data stays with your organization."}}</div>
          </div>
          <div id="scout-auth-content" class="space-y-5 px-0">
            <form id="scout-auth-form" node_ref=form_ref on:submit=submit class="space-y-4" aria-busy=move||pending.get() aria-describedby=move||if error.get().is_empty(){None}else{Some("scout-auth-error")}>
              {move||if mode()=="find"{view!{<div id="scout-find-field" class="space-y-2"><label id="scout-find-label" for="scout-find-input" class="text-sm font-medium">"Organization name or URL"</label><input id="scout-find-input" name="organization" autocomplete="organization" placeholder="e.g. Pizzeria Amalfi" required maxlength="100" class=INPUT/><p id="scout-find-hint" class="text-xs leading-5 text-muted-foreground">"You’ll sign in on your organization’s own page."</p></div>}.into_any()}else{view!{
                <Show when=move||creating.get()>
                  <div id="scout-name-field" class="space-y-2"><label id="scout-name-label" for="scout-name-input" class="text-sm font-medium">"Organization name"</label><input id="scout-name-input" name="name" autocomplete="organization" required maxlength="100" placeholder="Your company" class=INPUT on:input=move|e|if !edited.get(){slug.set(slug_from_name(&event_target_value(&e)));}/></div>
                  <div id="scout-slug-field" class="space-y-2"><label id="scout-slug-label" for="scout-slug-input" class="text-sm font-medium">"Organization URL"</label><input id="scout-slug-input" name="slug" prop:value=move||slug.get() on:input=move|e|{edited.set(true);slug.set(event_target_value(&e));} autocomplete="off" required minlength="3" maxlength="63" pattern="[a-z0-9]+(-[a-z0-9]+)*" aria-describedby="scout-slug-hint" placeholder="your-company" class=format!("{INPUT} font-mono")/><p id="scout-slug-hint" class="text-xs leading-5 text-muted-foreground">"/"{move||if slug.get().is_empty(){"your-company".to_string()}else{slug.get()}}" · Lowercase letters, numbers and single hyphens."</p></div>
                  <div id="scout-google-signup-section" class="space-y-3"><button id="scout-google-signup" type="button" class=format!("{OUTLINE} w-full") on:click=google_signup disabled=move||pending.get()||!google aria-describedby=if google{"scout-google-signup-hint"}else{"scout-google-signup-unavailable"}><img src="/google-g.png" alt="" aria-hidden="true" width="18" height="18" class="size-[18px] shrink-0 object-contain"/>{move||if pending.get(){"Please wait…"}else{"Create with Google"}}</button>
                    <p id=if google{"scout-google-signup-hint"}else{"scout-google-signup-unavailable"} class="text-xs leading-5 text-muted-foreground">{if google{"Use your Google name and email. No password needed."}else{"Google sign-up hasn’t been configured by your administrator yet."}}</p>
                    <div id="scout-signup-divider" class="flex items-center gap-3 text-xs text-muted-foreground"><span class="h-px flex-1 bg-border"/>"or use email"<span class="h-px flex-1 bg-border"/></div>
                  </div>
                  <div id="scout-owner-field" class="space-y-2"><label id="scout-owner-label" for="scout-owner-input" class="text-sm font-medium">"Your name"</label><input id="scout-owner-input" name="ownerName" autocomplete="name" required maxlength="100" class=INPUT/></div>
                </Show>
                <div id="scout-email-field" class="space-y-2"><label id="scout-email-label" for="scout-email-input" class="text-sm font-medium">"Email"</label><input id="scout-email-input" name="email" type="email" autocomplete="username" required maxlength="254" placeholder="you@company.com" class=INPUT/></div>
                <div id="scout-password-field" class="space-y-2"><label id="scout-password-label" for="scout-password-input" class="text-sm font-medium">"Password"</label><input id="scout-password-input" name="password" type="password" autocomplete=move||if creating.get(){"new-password"}else{"current-password"} required minlength=move||if creating.get(){Some(12)}else{None} maxlength="1024" aria-describedby=move||if creating.get(){Some("scout-password-hint")}else{None} class=INPUT/><Show when=move||creating.get()><p id="scout-password-hint" class="text-xs leading-5 text-muted-foreground">"At least 12 characters. Password managers and paste are welcome."</p></Show></div>
              }.into_any()}}
              <Show when=move||!error.get().is_empty()><p id="scout-auth-error" tabindex="-1" role="alert" class="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{move||error.get()}</p></Show>
              <button id="scout-auth-submit" type="submit" disabled=move||pending.get() class=PRIMARY>{move||if pending.get(){"Please wait…"}else if mode()=="find"{"Continue"}else if creating.get(){"Create organization"}else{"Sign in"}}</button>
            </form>
            {login.then(||view!{<div id="scout-google-section" class="space-y-3"><div id="scout-google-divider" class="flex items-center gap-3 text-xs text-muted-foreground"><span class="h-px flex-1 bg-border"/>"or"<span class="h-px flex-1 bg-border"/></div>
              {if google{view!{<a id="scout-google-login" href=format!("/api/auth/google/start?organization={}",org.get_value().map(|o|o.slug).unwrap_or_default()) class=format!("{OUTLINE} w-full")><img src="/google-g.png" alt="" aria-hidden="true" width="18" height="18"/>"Continue with Google"</a>}.into_any()}else{view!{<button id="scout-google-login" type="button" class=format!("{OUTLINE} w-full") disabled aria-describedby="scout-google-unavailable"><img src="/google-g.png" alt="" aria-hidden="true" width="18" height="18"/>"Continue with Google"</button><p id="scout-google-unavailable" class="text-xs leading-5 text-muted-foreground">"Google sign-in hasn’t been configured by your administrator yet."</p>}.into_any()}}
              <p id="scout-membership-hint" class="text-xs leading-5 text-muted-foreground">"Access is for existing members only. Signing in with Google does not join this organization. First sign in with your password to link Google securely."</p></div>})}
            <div id="scout-auth-bottom" class="border-t pt-5 text-center text-sm text-muted-foreground">{if login{view!{<a id="scout-other-org" href="/" class="text-foreground underline-offset-4 hover:underline">"Find another organization"</a>}.into_any()}else{view!{<span id="scout-mode-prompt">{move||if creating.get(){"Already have a workspace? "}else{"New to Scout? "}}</span><button id="scout-mode-toggle" type="button" on:click=move|_|{creating.update(|v|*v=!*v);error.set(String::new());} disabled=move||pending.get() class="min-h-11 cursor-pointer font-medium text-foreground underline-offset-4 hover:underline focus-visible:rounded focus-visible:outline-2 focus-visible:outline-ring">{move||if creating.get(){"Find your organization"}else{"Create organization"}}</button>}.into_any()}}</div>
          </div>
        </div>
      </div>
      <footer id="scout-auth-footer" class="space-y-1.5 text-center text-xs leading-5 text-muted-foreground"><p id="scout-auth-footer-brand">"Scout · Your finance agent"</p><nav id="scout-auth-legal" aria-label="Legal information" class="flex items-center justify-center gap-3">
        {bootstrap.privacy.map(|url|view!{<a id="scout-auth-privacy" href=url class="underline-offset-4 hover:text-foreground hover:underline">"Privacy"</a>})}
        {bootstrap.terms.map(|url|view!{<a id="scout-auth-terms" href=url class="underline-offset-4 hover:text-foreground hover:underline">"Terms"</a>})}
      </nav></footer>
    </main>}
}
