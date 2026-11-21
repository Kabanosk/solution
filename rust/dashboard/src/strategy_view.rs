use crate::{primitives::*, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use wasm_bindgen::JsCast;
const PROMPT: &str = "Find current world news relevant to our company, explain likely implications and suggest next steps.";
#[component]
pub fn StrategySettings() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let settings = RwSignal::new(
        json!({"provider":null,"model":null,"companyContext":"","systemPrompt":PROMPT,"enabled":false,"nextRunAt":null}),
    );
    let dialog = RwSignal::new(false);
    let busy = RwSignal::new(false);
    let loaded = RwSignal::new(false);
    let message = RwSignal::new(String::new());
    Effect::new(move |_| {
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/strategy/settings",
                "GET",
                None,
                Some(&ctx.slug.get_value()),
            )
            .await
            {
                Ok((200, value)) => {
                    settings.set(value);
                    loaded.set(true)
                }
                Ok((403, _)) => {
                    message.set("Only a workspace owner can configure daily research.".into())
                }
                _ => message
                    .set("Strategy settings are temporarily unavailable. Reload to retry.".into()),
            }
        });
    });
    view! {<div id="strategy-settings" class="space-y-3"><details id="strategy-settings-details" class="rounded-lg border bg-muted/20" open><summary id="strategy-settings-summary" class="cursor-pointer px-3 py-2 text-sm font-medium">{move||settings.with(|v|format!("Daily news settings{}",v["provider"].as_str().map(|p|format!(" · {p} · {}",if v["enabled"]==true{"Enabled"}else{"Paused"})).unwrap_or_else(||" · Set up provider".into())))}</summary><div id="strategy-settings-fields" class="space-y-3 border-t p-3"><div id="strategy-provider-row" class="flex flex-wrap items-center justify-between gap-2"><p id="strategy-provider-status" class="break-all text-xs text-muted-foreground">{move||settings.with(|v|v["provider"].as_str().map(|p|format!("{p} · {}",string(v,"model"))).unwrap_or_else(||"Use an OpenAI or Mistral provider with live web-search access.".into()))}</p><button id="strategy-configure-provider" type="button" class=button_classes(OUTLINE,"h-8 w-auto px-3") disabled=move||!loaded.get()||busy.get() on:click=move|_|dialog.set(true)>{move||settings.with(|v|if v["provider"].is_string(){"Change provider"}else{"Register provider"})}</button></div><label id="strategy-context-label" for="strategy-context" class="block text-xs font-medium">"Company context"</label><textarea id="strategy-context" rows="3" maxlength="4000" disabled=move||!loaded.get()||busy.get() prop:value=move||settings.with(|v|string(v,"companyContext")) placeholder="Company, industry, markets, location and topics to watch" aria-describedby="strategy-data-help" class=INPUT.replace("h-11","min-h-20 py-2") on:input=move|e|settings.update(|v|v["companyContext"]=json!(event_target_value(&e)))/><label id="strategy-prompt-label" for="strategy-prompt" class="block text-xs font-medium">"System prompt"</label><textarea id="strategy-prompt" rows="4" maxlength="8000" disabled=move||!loaded.get()||busy.get() prop:value=move||settings.with(|v|string(v,"systemPrompt")) class=INPUT.replace("h-11","min-h-24 py-2") on:input=move|e|settings.update(|v|v["systemPrompt"]=json!(event_target_value(&e)))/><p id="strategy-data-help" class="text-xs leading-5 text-muted-foreground">"This context and prompt are sent to your provider to search news from the last 24 hours. Include only information you want shared. Private Scout records stay on premise. Provider usage may incur charges."</p><label id="strategy-enabled-label" for="strategy-enabled" class="flex min-h-11 items-center gap-2 text-sm"><input id="strategy-enabled" type="checkbox" class="accent-primary" prop:checked=move||settings.with(|v|v["enabled"]==true) disabled=move||!loaded.get()||busy.get() on:change=move|e|settings.update(|v|v["enabled"]=json!(event_target_checked(&e)))/>"Run automatically every 24 hours"</label>{move||settings.with(|v|if v["enabled"]==true{v["nextRunAt"].as_str().map(str::to_owned)}else{None}).map(|at|view!{<p id="strategy-next-run" class="text-xs text-muted-foreground">{format!("Next review: {}",local_date(&at))}</p>})}<button id="strategy-save" type="button" class=button_classes(PRIMARY,"h-8 w-auto px-3") disabled=move||!loaded.get()||busy.get() on:click=move|_|{busy.set(true);message.set(String::new());leptos::task::spawn_local(async move{match json_request("/api/strategy/settings","PUT",Some(&settings.get_untracked()),Some(&ctx.slug.get_value())).await{Ok((code,value))if code<400=>{settings.set(value);message.set("Strategy settings saved.".into())},Ok((_,value))=>message.set(value["error"].as_str().unwrap_or("Could not save settings.").into()),Err(text)=>message.set(text)}busy.set(false);});}>{move||if busy.get(){"Working…"}else{"Save settings"}}</button></div></details><div id="strategy-run-row" class="flex flex-wrap items-center gap-3"><button id="strategy-run" type="button" class=button_classes(OUTLINE,"h-8 w-auto px-3") disabled=move||!loaded.get()||busy.get()||settings.with(|v|!v["provider"].is_string()) title="Run with the last saved settings" on:click=move|_|{busy.set(true);message.set("Researching current news. This can take up to two minutes…".into());leptos::task::spawn_local(async move{match json_request("/api/strategy/review","POST",Some(&json!({})),Some(&ctx.slug.get_value())).await{Ok((code,value))if code<400=>{message.set(if value["running"]==true{"A review is already running."}else{"Review recorded. Open its task below for the result and activity log."}.into());ctx.refresh();},Ok((_,value))=>message.set(value["error"].as_str().unwrap_or("Could not run review.").into()),Err(text)=>message.set(text)}busy.set(false);});}>{move||if busy.get(){"Working…"}else{"Run review now"}}</button><Show when=move||!message.get().is_empty()><p id="strategy-message" role="status" class="text-xs text-muted-foreground">{move||message.get()}</p></Show></div><StrategyProvider open=dialog settings message/></div>}
}
fn local_date(at: &str) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        return js_sys::Date::new(&at.into())
            .to_locale_string("default", &wasm_bindgen::JsValue::UNDEFINED)
            .as_string()
            .unwrap_or_default();
    }
    #[cfg(not(target_arch = "wasm32"))]
    at.into()
}
#[component]
fn StrategyProvider(
    open: RwSignal<bool>,
    settings: RwSignal<Value>,
    message: RwSignal<String>,
) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let provider = RwSignal::new("openai".to_string());
    let model = RwSignal::new("gpt-4.1-mini".to_string());
    let key = RwSignal::new(String::new());
    let saving = RwSignal::new(false);
    let error = RwSignal::new(String::new());
    Effect::new(move |_| {
        let active = open.get();
        if active {
            provider.set("openai".into());
            model.set("gpt-4.1-mini".into());
            key.set(String::new());
            error.set(String::new());
        }
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("strategy-external-dialog"))
            .and_then(|e| e.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if active {
                let _ = dialog.show_modal();
            } else {
                dialog.close();
            }
        }
    });
    view! {<dialog id="strategy-external-dialog" class="fixed inset-0 m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg space-y-4 overflow-y-auto rounded-xl border bg-popover p-6 text-popover-foreground shadow-xl backdrop:bg-foreground/20" aria-labelledby="strategy-external-title" aria-describedby="strategy-external-description" on:close=move|_|{open.set(false);focus("strategy-configure-provider");}><header class="pr-6"><h2 id="strategy-external-title" class="text-lg font-semibold">"External LLM provider"</h2><p id="strategy-external-description" class="mt-2 text-sm text-muted-foreground">"Use your own API key to research hiring markets and ask Granite to run private forecasts."</p></header><button type="button" aria-label="Close provider" class="absolute top-3 right-3 flex size-8 items-center justify-center rounded hover:bg-accent" on:click=move|_|open.set(false)><Icon name="close"/></button><form id="strategy-external-form" class="space-y-4" on:submit=move|e|{e.prevent_default();let m=model.get_untracked().trim().to_string();let k=key.get_untracked().trim().to_string();if m.is_empty()||m.encode_utf16().count()>150||!m.bytes().enumerate().all(|(i,c)|c.is_ascii_alphanumeric()||i>0&&b"._:/-".contains(&c)){error.set("Enter a valid model ID (up to 150 characters).".into());return}if !(8..=512).contains(&k.len())||!k.bytes().all(|b|(0x21..=0x7e).contains(&b)){error.set("Enter an API key (8–512 characters, without spaces).".into());return}saving.set(true);error.set(String::new());leptos::task::spawn_local(async move{let external=json!({"provider":provider.get_untracked(),"model":m,"apiKey":k});match json_request("/api/strategy/settings","PUT",Some(&json!({"external":external})),Some(&ctx.slug.get_value())).await{Ok((code,_))if code<400=>{settings.update(|v|{v["provider"]=external["provider"].clone();v["model"]=external["model"].clone();});open.set(false);message.set("Provider saved for Strategy. Add company context and enable daily research.".into());},Ok((_,value))=>error.set(value["error"].as_str().unwrap_or("Could not save strategy provider.").into()),Err(text)=>error.set(text)}saving.set(false);});}><div class="space-y-1.5"><label for="strategy-external-provider" class="text-sm font-medium">"Provider"</label><select id="strategy-external-provider" class=INPUT prop:value=move||provider.get() on:change=move|e|{let next=event_target_value(&e);model.set(match next.as_str(){"mistral"=>"mistral-small-latest","groq"=>"llama-3.3-70b-versatile",_=>"gpt-4.1-mini"}.into());provider.set(next);key.set(String::new());error.set(String::new());}><option value="openai">"OpenAI"</option><option value="mistral">"Mistral"</option><option value="groq">"Groq"</option></select></div><div class="space-y-1.5"><label for="strategy-external-model" class="text-sm font-medium">"Model ID"</label><input id="strategy-external-model" class=INPUT prop:value=move||model.get() maxlength="150" required on:input=move|e|{model.set(event_target_value(&e));error.set(String::new());}/><p class="text-xs text-muted-foreground">"Choose a model available to your account that supports tool calls. Public hiring research is available with OpenAI and Mistral."</p></div><div class="space-y-1.5"><label for="strategy-external-key" class="text-sm font-medium">"API key"</label><input id="strategy-external-key" class=INPUT type="password" autocomplete="off" spellcheck="false" prop:value=move||key.get() maxlength="512" required on:input=move|e|{key.set(event_target_value(&e));error.set(String::new());}/><p class="text-xs text-muted-foreground">"Kept in this tab unless you save it for Strategy below. Saved Strategy keys are encrypted on the server and survive reloads. Removing the chat provider only clears this tab."</p></div><p class="rounded-md border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">"Your public conversation and cited research are shared with the provider so follow-up messages keep their context. Do not paste private company data. Invoices and Granite's results stay private. Hiring proposals create simulated Liquidity actions on premise, without changing company records or hiring anyone."</p><label class="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" class="accent-primary" checked disabled/>"Save provider for the daily Strategy loop"</label><Show when=move||!error.get().is_empty()><p id="strategy-external-error" role="alert" class="text-sm text-danger">{move||error.get()}</p></Show><div class="flex flex-wrap justify-end gap-2"><button type="button" class=button_classes(OUTLINE,"h-11 w-auto") on:click=move|_|open.set(false)>"Cancel"</button><button id="strategy-external-save" type="submit" disabled=move||saving.get() class=button_classes(PRIMARY,"h-11 w-auto")>{move||if saving.get(){"Saving…"}else{"Save provider"}}</button></div></form></dialog>}
}
