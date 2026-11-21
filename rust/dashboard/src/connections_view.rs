use crate::{
    navigation::Route,
    primitives::*,
    tasks_view::{full_timestamp, money},
    workspace::*,
};
use leptos::prelude::*;
use serde_json::Value;

fn status(connection: &Value) -> String {
    if connection.is_null() {
        "not configured".into()
    } else if connection["enabled"] == false {
        "disabled".into()
    } else {
        string(connection, "status")
    }
}
fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|s| s.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}
#[component]
pub fn ConnectionsView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let notice = RwSignal::new(None::<(bool, String)>);
    Effect::new(move |_| {
        if let Some(w) = web_sys::window() {
            if let Ok(query) = w.location().search() {
                let mut params =
                    url::form_urlencoded::parse(query.trim_start_matches('?').as_bytes())
                        .into_owned()
                        .collect::<Vec<_>>();
                if let Some((_, value)) = params.iter().find(|(k, _)| k == "google") {
                    let ok = value == "connected";
                    notice.set(Some((
                        ok,
                        if ok {
                            "Google connected. Scout can now read the authorized mail label."
                        } else {
                            "Google connection failed. Try again."
                        }
                        .into(),
                    )));
                    params.retain(|(k, _)| k != "google");
                    let mut serial = url::form_urlencoded::Serializer::new(String::new());
                    serial.extend_pairs(params);
                    let remaining = serial.finish();
                    let target = format!(
                        "{}{}{}{}",
                        w.location().pathname().unwrap_or_default(),
                        if remaining.is_empty() { "" } else { "?" },
                        remaining,
                        w.location().hash().unwrap_or_default()
                    );
                    let _ = w.history().and_then(|h| {
                        h.replace_state_with_url(&wasm_bindgen::JsValue::NULL, "", Some(&target))
                    });
                    leptos::task::spawn_local(async move {
                        gloo_timers::future::TimeoutFuture::new(6000).await;
                        notice.set(None);
                    });
                }
            }
        }
    });
    let owner = ctx
        .data
        .with_untracked(|v| v["workspace"]["role"] == "owner");
    view! {<section id="scout-connections" aria-labelledby="scout-connections-title" class="space-y-5"><div><h1 id="scout-connections-title" class="text-2xl font-medium tracking-tight">"Connections"</h1><p class="mt-1 text-sm text-muted-foreground">"Manage the sources Scout can access. Permissions stay scoped to your workspace."</p></div>{owner.then(||view!{<div id="scout-connections-setup" class="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 sm:px-5"><div class="min-w-0"><h2 class="text-[13px] font-medium">"Workspace setup"</h2><p class="text-xs leading-5 text-muted-foreground">"Connect bank, invoicing, email, Slack and company network with a short, optional checklist."</p></div><button id="scout-onboarding-reopen" type="button" class=button_classes(OUTLINE,"h-8 w-auto shrink-0 shadow-none") on:click=move|_|ctx.navigate(Route::view("setup"))>"Resume setup"</button></div>})}{move||notice.get().map(|(ok,text)|view!{<div id="scout-connections-notice" role=if ok{"status"}else{"alert"} class=format!("flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-[13px] {}",if ok{""}else{"border-danger-border text-danger"})><Icon name=if ok{"check"}else{"alert"}/>{text}</div>})}<section id="scout-connections-list" aria-labelledby="scout-integrations-title" class="min-w-0 overflow-hidden rounded-xl border bg-card"><header class="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-5"><h2 id="scout-integrations-title" class="text-[13px] font-medium">"Integrations"</h2><p id="scout-integrations-count" class="text-xs text-muted-foreground">{move||ctx.data.with(|v|format!("{} of 6 connected",v["scout"]["connections"].as_array().map(|rows|rows.iter().filter(|c|status(c)=="connected").count()).unwrap_or(0)))}</p></header><div class="divide-y">{[("bank","bank","Bank","Read-only bank transactions · bunq sandbox setup","bank","bank"),("invoicing","invoicing","Invoicing","Invoices and purchase orders · Postgres","invoicing","invoicing"),("ksef","ksef","KSeF","National e-invoice system · test environment","ksef","ksef"),("mail","mail","Email","Company mailbox · scoped Gmail access","mail","email"),("slack","chat","Slack","Owner-selected Slack channels · verified read access","chat","slack"),("laptops","files","Company laptops","Authorized work folders over VPN","laptop","network")].into_iter().map(move|(id,kind,title,description,icon,step)|{let connection=ctx.data.with_untracked(|v|v["scout"]["connections"].as_array().and_then(|rows|rows.iter().find(|c|if matches!(id,"invoicing"|"ksef"){c["id"]==id}else{c["kind"]==kind})).cloned().unwrap_or(Value::Null));view!{<ConnectionRow id title description icon step connection owner/>}}).collect_view()}</div></section><p id="scout-connections-permissions-note" class="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><Icon name="policies"/>"Scout only reads authorized sources. Connection status and last sync are reported by the worker."</p></section>}
}
#[component]
fn ConnectionRow(
    id: &'static str,
    title: &'static str,
    description: &'static str,
    icon: &'static str,
    step: &'static str,
    connection: Value,
    owner: bool,
) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let expanded = RwSignal::new(connection["config"]["phase"] == "running");
    let state = status(&connection);
    let config = connection["config"].clone();
    let granted = strings(&config["scopes"]);
    let missing = strings(&config["missing_scopes"]);
    let details = id == "mail"
        || id == "ksef"
        || (id == "slack" && (!granted.is_empty() || !missing.is_empty()));
    if id == "ksef" && config["phase"] == "running" {
        leptos::task::spawn_local(async move {
            loop {
                gloo_timers::future::TimeoutFuture::new(1500).await;
                if ctx.route.with_untracked(|r| r.view != "connections") {
                    break;
                }
                ctx.refresh();
            }
        });
    }
    view! {<article id=format!("connection-{id}") data-connection-row="" aria-labelledby=format!("connection-{id}-title") class="min-w-0"><div class="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] items-start gap-x-3 gap-y-3 px-4 py-4 sm:grid-cols-[2rem_minmax(0,1fr)_auto] sm:items-center sm:px-5"><span data-connection-icon="" class="flex size-8 items-center justify-center rounded-lg border bg-background text-muted-foreground"><Icon name=icon/></span><div class="min-w-0 space-y-1"><h3 id=format!("connection-{id}-title") class="text-[13px] leading-5 font-medium">{title}</h3><p class="text-xs leading-5 text-muted-foreground">{description}</p>{connection["displayName"].as_str().filter(|s|!s.starts_with(title)).map(|s|view!{<p class="text-xs text-muted-foreground">{s.to_owned()}</p>})}{connection["lastError"].as_str().map(|s|view!{<p class="text-xs leading-5 break-words text-danger">{s.to_owned()}</p>})}</div><div class="col-start-2 flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 sm:col-start-auto sm:justify-end"><div class="min-w-0 space-y-1 sm:text-right"><span data-connection-status="" class=format!("inline-flex items-center gap-1.5 text-xs {}",if state=="error"{"text-danger"}else{"text-muted-foreground"})><span aria-hidden="true" class=format!("size-1.5 rounded-full {}",if state=="connected"{"bg-primary"}else{"bg-muted-foreground/50"})/><span class="capitalize">{state.replace('_'," ")}</span></span><p id=format!("connection-{id}-sync") class="text-[11px] leading-4 text-muted-foreground">{connection["lastSyncAt"].as_str().map(|s|format!("Synced {}",full_timestamp(s))).unwrap_or_else(||"Not synced yet".into())}</p></div><button id=if id=="mail"{"scout-connect-google".into()}else if id=="ksef"{"ksef-connect".into()}else{format!("scout-connect-{id}")} type="button" disabled=!owner class=button_classes(OUTLINE,"h-8 w-auto shadow-none") on:click=move|_|{let mut route=Route::view("setup");route.filters.insert("step".into(),step.into());ctx.navigate(route);}>{if !owner{"Owner setup required"}else if state=="connected"{"Manage"}else if id=="laptops"{"Set up access"}else if id=="ksef"{"Set up KSeF"}else{"Connect"}}</button>{details.then(||view!{<button id=format!("connection-{id}-toggle") type="button" class="ml-auto flex size-8 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent sm:ml-0" aria-label=move||format!("{} {title} details",if expanded.get(){"Hide"}else{"Show"}) aria-expanded=move||expanded.get().to_string() aria-controls=format!("connection-{id}-details") on:click=move|_|expanded.update(|v|*v=!*v)><span class=move||if expanded.get(){"rotate-180"}else{""}><Icon name="chevron"/></span></button>})}</div></div>{details.then(||view!{<div id=format!("connection-{id}-details") hidden=move||!expanded.get() class="min-w-0 border-t bg-muted/20 px-4 py-4 text-xs leading-5 text-muted-foreground sm:px-5">{if id=="mail"{view!{<details id="scout-google-admin" class="min-w-0"><summary class="cursor-pointer font-medium text-foreground">"Google Workspace (admin)"</summary><ol class="mt-3 list-decimal space-y-2 pl-4 [overflow-wrap:anywhere]"><li>"In Google Cloud, create a service account for Scout and enable domain-wide delegation; copy its client ID."</li><li>"Enable the Gmail API in the same project."</li><li>"In the Admin console go to Security › Access and data control › API controls › Domain-wide delegation."</li><li>"Add the client ID with the scope "<span class="font-mono">"https://www.googleapis.com/auth/gmail.readonly"</span>" (plus "<span class="font-mono">"gmail.insert"</span>" for demo seeding)."</li><li>"Give Scout the service-account key as a Secret and set the mailbox to impersonate; Scout only reads the authorized label."</li></ol></details>}.into_any()}else if id=="slack"{view!{<div id="scout-slack-permissions" class="space-y-2 [overflow-wrap:anywhere]">{(!granted.is_empty()).then(||view!{<p>"Granted permissions: "<span class="font-mono">{granted.join(", ")}</span></p>})}{(!missing.is_empty()).then(||view!{<p class="text-warning">"Missing permissions: "<span class="font-mono">{missing.join(", ")}</span></p>})}</div>}.into_any()}else{view!{<KsefDetails config/>}.into_any()}}</div>})}</article>}
}
#[component]
fn KsefDetails(config: Value) -> impl IntoView {
    let count = |key: &str| {
        config[key]
            .as_f64()
            .filter(|n| n.is_finite())
            .unwrap_or(0.0)
    };
    let mut summary = vec![format!(
        "{} invoice{} received",
        if count("received") != 0.0 {
            count("received")
        } else {
            count("imported")
        },
        if count("received") == 1.0 || count("received") == 0.0 && count("imported") == 1.0 {
            ""
        } else {
            "s"
        }
    )];
    for (key, total, label) in [
        ("linked", "linked_total", "matched to payments"),
        ("on_file", "", "already on file"),
        ("unpaid", "unpaid_total", "new to pay"),
    ] {
        if count(key) > 0.0 {
            let amount = config[total]
                .as_f64()
                .or_else(|| config[total].as_str().and_then(|s| s.parse::<f64>().ok()))
                .filter(|n| n.is_finite())
                .map(|n| format!(" ({})", money(n, "EUR")))
                .unwrap_or_default();
            summary.push(format!("{} {label}{amount}", count(key)));
            if key == "linked" {
                summary.push("tax-document risk reduced".into());
            }
        }
    }
    let invoices = config["invoices"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(|r| r["number"].as_str().is_some_and(|s| !s.is_empty()))
        .collect::<Vec<_>>();
    let total = invoices.len();
    view! {<div class="min-w-0 space-y-3"><p id="ksef-target" class="break-words">{config["nip"].as_str().map(|s|format!("NIP {s} · "))}{config["api"].as_str().map(|s|url::Url::parse(s).ok().and_then(|u|u.host_str().map(str::to_owned)).unwrap_or_else(||s.into()))}</p><ol id="ksef-steps" aria-label="KSeF sync steps" class="space-y-2">{config["steps"].as_array().cloned().unwrap_or_default().into_iter().filter(|s|s["text"].as_str().is_some_and(|t|!t.is_empty())).enumerate().map(|(index,step)|view!{<li id=format!("ksef-step-{index}") class="flex items-start gap-2"><Icon name=if step["ok"]==false{"close"}else{"check"}/><span class=if step["ok"]==false{"text-danger"}else{"text-foreground"}>{string(&step,"text")}</span></li>}).collect_view()}</ol>{(config["phase"]=="done").then(||view!{<div class="min-w-0 space-y-3"><p id="ksef-summary" class="text-[13px] text-foreground">{summary.join(" · ")}</p>{(!invoices.is_empty()).then(||view!{<div class="min-w-0 overflow-x-auto rounded-lg border bg-card"><table id="ksef-invoices" class="w-full text-xs"><thead><tr>{["Invoice","Supplier","Total","KSeF number","Matched"].into_iter().map(|s|view!{<th class="p-2 text-left">{s}</th>}).collect_view()}</tr></thead><tbody>{invoices.into_iter().take(8).map(|row|view!{<tr class="border-t"><td class="p-2 font-mono">{string(&row,"number")}</td><td class="p-2">{row["supplier"].as_str().unwrap_or("—").to_owned()}</td><td class="p-2 text-right tabular-nums">{row["total"].as_f64().or_else(||row["total"].as_str().and_then(|s|s.parse().ok())).filter(|n|n.is_finite()).map(|n|money(n,row["currency"].as_str().unwrap_or("EUR"))).unwrap_or_else(||row["total"].as_str().unwrap_or("—").into())}</td><td class="max-w-40 truncate p-2 font-mono" title=string(&row,"ksef_number")>{row["ksef_number"].as_str().unwrap_or("—").to_owned()}</td><td class="p-2">{if row["linked"]==true{"Yes"}else{"No"}}</td></tr>}).collect_view()}</tbody></table></div>})}{(total>8).then(||view!{<p>{format!("{} more not shown.",total-8)}</p>})}</div>})}</div>}
}
