use crate::{navigation::Route, primitives::*, tasks_view::money, workspace::*};
use leptos::prelude::*;
use serde_json::Value;
use wasm_bindgen::JsCast;
#[component]
pub fn PoliciesView() -> impl IntoView {
    let policies: Vec<Value> =
        serde_json::from_str(include_str!("../public/policies.json")).unwrap_or_default();
    view! {<section class="space-y-4"><div><h1 class="text-2xl font-medium tracking-tight">"Access policies"</h1><p class="mt-1 text-sm text-muted-foreground">"Read-only. Scout checks these before any connector reads or acts; every decision is written to the audit log."</p></div><div class="grid gap-4 lg:grid-cols-2">{policies.into_iter().map(|policy|view!{<article class="flex flex-col gap-3 rounded-xl border bg-card py-6 text-card-foreground"><header class="space-y-2 px-6"><h2 class="text-sm font-medium">{string(&policy,"role")}</h2><p class="text-sm text-muted-foreground">{string(&policy,"who")}</p></header><div class="px-6"><ul class="space-y-2">{policy["rules"].as_array().cloned().unwrap_or_default().into_iter().map(|rule|{let effect=rule[1].as_str().unwrap_or("").to_string();let tone=match effect.as_str(){"DENY"=>"border-danger-border text-danger","APPROVAL"=>"border-warning-border text-warning",_=>"border-success-border text-success"};view!{<li class="flex items-start justify-between gap-3 text-[13px]"><span>{rule[0].as_str().unwrap_or("").to_owned()}</span><span class=format!("shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] {tone}")>{effect}</span></li>}}).collect_view()}</ul></div></article>}).collect_view()}</div></section>}
}
fn download_csv(csv: &str) {
    let Some(window) = web_sys::window() else {
        return;
    };
    let bytes = js_sys::Uint8Array::from(csv.as_bytes());
    let parts = js_sys::Array::of1(&bytes);
    let options = web_sys::BlobPropertyBag::new();
    options.set_type("text/csv;charset=utf-8;");
    let Ok(blob) = web_sys::Blob::new_with_u8_array_sequence_and_options(&parts, &options) else {
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
            link.set_id("scout-payroll-download-link");
            link.set_href(&url);
            link.set_download("scout-payroll.csv");
            link.set_hidden(true);
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
pub fn StaffingView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let search = RwSignal::new(String::new());
    let export_notice = RwSignal::new(String::new());
    let analysis = RwSignal::new(false);
    let export = move |_| {
        let data = ctx.data.get_untracked();
        let people = data["employees"].as_array().cloned().unwrap_or_default();
        let mut rows = vec![vec![
            "Employee".to_string(),
            "Role".into(),
            "Team".into(),
            "Monthly net salary".into(),
            "Currency".into(),
            "Payment day".into(),
            "Status".into(),
            "Source".into(),
        ]];
        for person in &people {
            rows.push(vec![
                string(person, "name"),
                string(person, "role"),
                string(person, "team"),
                person["salary"]
                    .as_f64()
                    .map(|v| format!("{v:.2}"))
                    .unwrap_or_default(),
                "EUR".into(),
                "Not scheduled".into(),
                "Active".into(),
                "Company database · latest recorded payroll".into(),
            ]);
        }
        let body = rows
            .into_iter()
            .map(|row| {
                row.into_iter()
                    .map(|s| format!("\"{}\"", s.replace('"', "\"\"")))
                    .collect::<Vec<_>>()
                    .join(",")
            })
            .collect::<Vec<_>>()
            .join("\r\n");
        download_csv(&format!("\u{feff}{body}\r\n"));
        export_notice.set(format!("Payroll CSV prepared for {} people. The export includes the whole team and includes the latest recorded monthly payments; missing payments are blank.",people.len()));
    };
    view! {<div id="scout-staffing" class="space-y-6"><section id="scout-staffing-heading" aria-labelledby="scout-staffing-title" class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div class="space-y-2"><p class="font-mono text-xs tracking-wide text-muted-foreground uppercase">"People operations"</p><h1 id="scout-staffing-title" class="text-2xl font-medium tracking-tight md:text-3xl">"Staffing & payroll"</h1><p class="max-w-2xl text-sm leading-6 text-muted-foreground">"A current view of people, responsibilities and recurring salary commitments."</p></div><button id="scout-staffing-back" type="button" class=OUTLINE on:click=move|_|ctx.navigate(Route::view("overview"))>"Back to overview"</button></section><section id="scout-staffing-summary" aria-label="Staffing summary" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{move||ctx.data.with(|data|{let count=data["employees"].as_array().map(Vec::len).unwrap_or(0);[("people","Active team",count.to_string(),"Profiles in the company directory"),("net","Latest recorded payroll",money(data["monthlyPayroll"].as_f64().unwrap_or(0.0),"EUR"),"Latest month with salary bookings"),("contributions","Employer contributions",money(data["monthlyContributions"].as_f64().unwrap_or(0.0),"EUR"),"Social security · monthly"),("scheduled","Records through",string(data,"asOf"),"Latest bank booking date")].into_iter().map(|(id,label,value,note)|view!{<article id=format!("scout-payroll-metric-{id}") class="rounded-xl border bg-card py-6"><div class="space-y-2 px-6 pt-5"><p class="text-sm text-muted-foreground">{label}</p><p class="break-words font-mono text-2xl tracking-tight">{value}</p><p class="text-xs leading-5 text-muted-foreground">{note}</p></div></article>}).collect_view()})}</section>
      <section id="scout-staff-directory" aria-labelledby="scout-team-title" class="min-w-0 overflow-hidden rounded-xl border bg-card py-6"><header class="flex flex-wrap items-center justify-between gap-4 px-6"><div class="space-y-1.5"><h2 id="scout-team-title" class="text-base font-medium">"Team directory"</h2><p class="text-sm text-muted-foreground">"Latest recorded monthly salary payments matched by employee name"</p></div><div class="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center"><input id="scout-team-search" type="search" aria-label="Search team by name, role or team" aria-controls="scout-payroll-table" placeholder="Search people" class=format!("{INPUT} sm:w-56") prop:value=move||search.get() on:input=move|e|search.set(event_target_value(&e))/><button id="scout-payroll-export" type="button" class=OUTLINE on:click=export><Icon name="file"/>"Export payroll"</button></div></header><Show when=move||!export_notice.get().is_empty()><p id="scout-payroll-export-status" role="status" class="px-6 pt-3 text-sm leading-6 text-success">{move||export_notice.get()}</p></Show><div id="scout-payroll-table-scroll" class="mt-6 max-w-full overflow-x-auto"><table id="scout-payroll-table" class="w-full min-w-[680px] text-sm"><caption class="sr-only">"Latest recorded monthly salary payments in EUR. Missing payments are not recorded."</caption><thead><tr class="border-b bg-muted/25"><th class="h-10 pl-6 text-left font-medium">"Employee"</th><th class="h-10 text-left font-medium">"Team"</th><th class="h-10 text-right font-medium">"Monthly net"</th><th class="h-10 pr-6 text-left font-medium">"Status"</th></tr></thead><tbody>{move||{let needle=search.get().trim().to_lowercase();let people=ctx.data.with(|v|v["employees"].as_array().cloned().unwrap_or_default());people.into_iter().filter(|p|format!("{} {} {}",string(p,"name"),string(p,"role"),string(p,"team")).to_lowercase().contains(&needle)).map(|person|view!{<tr id=format!("scout-employee-{}",string(&person,"id")) class="border-b hover:bg-muted/50"><td class="py-5 pl-6"><div class="flex items-center gap-3"><span class=if person["avatar"]=="avatar-orange"{"flex size-10 shrink-0 items-center justify-center rounded-full bg-warning-muted text-warning"}else{"flex size-10 shrink-0 items-center justify-center rounded-full bg-success-muted text-success"} aria-hidden="true">{string(&person,"initials")}</span><div class="space-y-1"><p class="text-sm font-medium">{string(&person,"name")}</p><p class="text-xs text-muted-foreground">{string(&person,"role")}</p></div></div></td><td class="text-sm text-muted-foreground">{string(&person,"team")}</td><td class="text-right"><p class="font-mono text-sm tabular-nums">{person["salary"].as_f64().map(|salary|money(salary,"EUR")).unwrap_or_else(||"Not recorded".into())}</p><p class="mt-1 text-xs text-muted-foreground">"€ · monthly"</p></td><td class="pr-6 pl-2"><span class="rounded-full border border-success-border bg-success-muted px-2 py-0.5 text-xs text-success">"Active"</span></td></tr>}).collect_view()}}</tbody></table><Show when=move||ctx.data.with(|v|{let needle=search.get().trim().to_lowercase();!v["employees"].as_array().is_some_and(|rows|rows.iter().any(|p|format!("{} {} {}",string(p,"name"),string(p,"role"),string(p,"team")).to_lowercase().contains(&needle)))})><div id="scout-team-empty" class="py-8 px-6 text-center"><p class="text-sm">{move||format!("No team members match “{}”.",search.get().trim())}</p><button id="scout-team-clear-search" type="button" class="mt-2 min-h-10 rounded px-3 text-sm hover:bg-accent" on:click=move|_|search.set(String::new())>"Clear search"</button></div></Show></div><footer class="flex flex-wrap items-center justify-between gap-3 border-t px-6 pt-4 text-xs leading-5 text-muted-foreground"><p>"Protected company workspace · Read-only records"</p><p id="scout-team-result-count" role="status" aria-atomic="true">{move||ctx.data.with(|v|{let rows=v["employees"].as_array().cloned().unwrap_or_default();let needle=search.get().trim().to_lowercase();format!("{} of {} people",rows.iter().filter(|p|format!("{} {} {}",string(p,"name"),string(p,"role"),string(p,"team")).to_lowercase().contains(&needle)).count(),rows.len())})}</p></footer></section>
      <section id="scout-payroll-insight" aria-labelledby="scout-payroll-insight-title" class="rounded-xl border border-success-border bg-success-muted py-6"><header class="flex flex-wrap items-start justify-between gap-4 px-6"><div class="space-y-2"><h2 id="scout-payroll-insight-title" class="text-base font-medium">"Scout insight"</h2><p class="max-w-3xl text-sm leading-6 text-muted-foreground">{move||ctx.data.with(|v|format!("The latest salary month contains {} in monthly net payroll. Review these recorded payments alongside other operating expenses.",money(v["monthlyPayroll"].as_f64().unwrap_or(0.0),"EUR")))}</p></div><button id="scout-payroll-analysis-toggle" type="button" class="min-h-11 shrink-0 rounded-md px-3 text-sm hover:bg-accent" aria-expanded=move||analysis.get().to_string() aria-controls="scout-payroll-analysis" on:click=move|_|analysis.update(|v|*v=!*v)>{move||if analysis.get(){"Close analysis"}else{"Open analysis"}}</button></header><Show when=move||analysis.get()><div id="scout-payroll-analysis" class="space-y-2 px-6 pt-4 text-sm leading-6 text-muted-foreground"><p>"Individual payments are matched to employee names in the bank records. A missing match is shown as “Not recorded”; it is not treated as a zero salary."</p><p>{move||ctx.data.with(|v|format!("Employer social contributions of {} are separate from the {} monthly net payroll above. These are recorded payments, not a scheduled payroll forecast.",money(v["monthlyContributions"].as_f64().unwrap_or(0.0),"EUR"),money(v["monthlyPayroll"].as_f64().unwrap_or(0.0),"EUR")))}</p></div></Show></section>
    </div>}
}
