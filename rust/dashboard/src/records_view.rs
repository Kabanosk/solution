use crate::{
    primitives::*,
    tasks_view::{full_timestamp, money},
    workspace::*,
};
use leptos::prelude::*;
use serde_json::Value;
use wasm_bindgen::JsCast;
#[derive(Clone, Copy)]
pub struct RecordCache(pub RwSignal<std::collections::BTreeMap<String, Value>>);
#[component]
pub fn RecordsView(#[prop(into)] kind: String) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let cache = expect_context::<RecordCache>();
    let initial = cache.0.with_untracked(|v| {
        v.get(&format!("records:{kind}"))
            .and_then(Value::as_array)
            .cloned()
    });
    let kind = StoredValue::new(kind);
    let data = RwSignal::new(initial);
    let error = RwSignal::new(false);
    let revision = RwSignal::new(0u64);
    Effect::new(move |_| {
        let _ = revision.get();
        leptos::task::spawn_local(async move {
            match json_request(
                &format!("/api/records/{}", kind.get_value()),
                "GET",
                None,
                Some(&ctx.slug.get_value()),
            )
            .await
            {
                Ok((200, value)) if value.is_array() => {
                    cache.0.update(|v| {
                        v.insert(format!("records:{}", kind.get_value()), value.clone());
                    });
                    data.set(value.as_array().cloned());
                    error.set(false)
                }
                _ => error.set(true),
            }
        });
    });
    view! {{move||if let Some(rows)=data.get(){if kind.get_value()=="transactions"{view!{<Transactions rows/>}.into_any()}else{view!{<AuditLog rows on_changed=Callback::new(move|_|revision.update(|n|*n+=1))/>}.into_any()}}else if error.get(){view!{<div role="alert" class="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">"These records could not be loaded. "<button class="text-primary underline" on:click=move|_|revision.update(|n|*n+=1)>"Try again"</button></div>}.into_any()}else{view!{<div role="status" class="space-y-3 rounded-xl border bg-card p-4"><div class="h-8 w-48 animate-pulse rounded bg-muted"/><div class="h-40 animate-pulse rounded bg-muted"/></div>}.into_any()}}}
}
#[component]
fn Transactions(rows: Vec<Value>) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let rows = StoredValue::new(rows);
    let query = RwSignal::new(String::new());
    let selected = RwSignal::new(None::<Value>);
    let documented = rows.with_value(|r| {
        r.iter()
            .filter(|v| {
                v["invoiceNumber"].as_str().is_some_and(|s| !s.is_empty())
                    && v["documented"] == true
            })
            .count()
    });
    let missing = rows.with_value(|r| {
        r.iter()
            .filter(|v| {
                v["invoiceNumber"].as_str().is_some_and(|s| !s.is_empty())
                    && v["documented"] != true
            })
            .count()
    });
    view! {<section class="space-y-4"><div class="flex flex-wrap items-end justify-between gap-3"><div><h1 class="text-2xl font-medium tracking-tight">"Transactions"</h1><p class="mt-1 text-sm text-muted-foreground">"Latest 100 bank bookings from Revolut Business. Click a documented invoice to see it."</p></div><div class="flex flex-wrap items-center gap-2 text-xs"><span class="rounded-full border border-success-border px-2 py-0.5 text-success">{format!("{documented} documented")}</span><span class="rounded-full border border-warning-border px-2 py-0.5 text-warning">{format!("{missing} missing")}</span><div class="relative"><input id="scout-transactions-search" aria-label="Filter transactions" placeholder="Filter…" class=format!("{} h-8 w-48 text-[13px]",INPUT.replace("h-11","h-8")) prop:value=move||query.get() on:input=move|e|query.set(event_target_value(&e))/></div></div></div><div class="overflow-hidden rounded-xl border bg-card"><div class="relative w-full overflow-x-auto"><table class="w-full caption-bottom text-[13px]"><thead><tr class="border-b"><th class="h-10 px-2 text-left font-medium">"Date"</th><th class="h-10 px-2 text-left font-medium">"Counterparty"</th><th class="h-10 px-2 text-left font-medium">"Reference"</th><th class="h-10 px-2 text-left font-medium">"Category"</th><th class="h-10 px-2 text-left font-medium">"Invoice"</th><th class="h-10 px-2 text-left font-medium">"Task"</th><th class="h-10 px-2 text-right font-medium">"Amount"</th></tr></thead><tbody>{move||{let needle=query.get().trim().to_lowercase();let data=rows.get_value();let task_for:std::collections::HashMap<_,_>=ctx.data.with(tasks).into_iter().filter_map(|t|t["transactionId"].as_str().map(|id|(id.to_string(),string(&t,"id")))).collect();data.into_iter().filter(|r|needle.is_empty()||format!("{} {} {} {}",string(r,"counterparty"),string(r,"reference"),string(r,"invoiceNumber"),string(r,"category")).to_lowercase().contains(&needle)).map(|row|{let id=string(&row,"id");let select=row.clone();let inv=string(&row,"invoiceNumber");let task=task_for.get(&id).cloned();view!{<tr data-transaction-id=id class="border-b last:border-b-0 hover:bg-muted/50"><td class="p-2 font-mono text-xs whitespace-nowrap">{string(&row,"bookingDate")}</td><td class="p-2">{string(&row,"counterparty")}</td><td class="max-w-56 truncate p-2 text-muted-foreground">{string(&row,"reference")}</td><td class="p-2">{row["category"].as_str().unwrap_or("—").replace('_'," ")}</td><td class="p-2">{if inv.is_empty(){view!{<span>"—"</span>}.into_any()}else if row["documented"]==true{view!{<button type="button" aria-label=format!("Show invoice {inv}") class="inline-flex items-center gap-1 rounded-md border border-success-border px-2 py-0.5 text-xs text-success hover:bg-success-muted focus-visible:ring-2 focus-visible:ring-ring" on:click=move|_|selected.set(Some(select.clone()))><Icon name="file"/>{format!("{inv} · documented")}</button>}.into_any()}else{view!{<span class="rounded-full border border-warning-border px-2 py-0.5 text-xs text-warning">{format!("{inv} · missing")}</span>}.into_any()}}</td><td class="p-2">{task.map(|id|view!{<a href=format!("#task/{id}") class="font-mono text-xs hover:underline">{id.clone()}</a>})}</td><td class={if row["amount"].as_f64().unwrap_or(0.0)>0.0{"p-2 text-right font-mono text-success"}else{"p-2 text-right font-mono"}}>{money(row["amount"].as_f64().unwrap_or(0.0),"EUR")}</td></tr>}}).collect_view()}}</tbody></table></div><Show when=move||{let q=query.get().trim().to_lowercase();!rows.with_value(|rows|rows.iter().any(|row|format!("{} {} {} {}",string(row,"counterparty"),string(row,"reference"),string(row,"invoiceNumber"),string(row,"category")).to_lowercase().contains(&q)))}><p class="px-4 py-8 text-center text-sm text-muted-foreground">{move||format!("No bookings match “{}”.",query.get())}</p></Show></div><InvoiceSheet selected/></section>}
}
#[component]
fn InvoiceSheet(selected: RwSignal<Option<Value>>) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let cache = expect_context::<RecordCache>();
    let data = RwSignal::new(None::<Value>);
    let error = RwSignal::new(String::new());
    Effect::new(move |_| {
        let row = selected.get();
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-invoice-sheet"))
            .and_then(|e| e.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if row.is_some() {
                let _ = dialog.show_modal();
            } else {
                dialog.close();
            }
        }
        if let Some(row) = row {
            let id = string(&row, "id");
            data.set(None);
            error.set(String::new());
            if let Some(saved) = cache
                .0
                .with_untracked(|v| v.get(&format!("invoice:{id}")).cloned())
            {
                data.set(Some(saved));
                return;
            }
            leptos::task::spawn_local(async move {
                match json_request(
                    &format!("/api/invoices/{id}"),
                    "GET",
                    None,
                    Some(&ctx.slug.get_value()),
                )
                .await
                {
                    Ok((200, value)) => {
                        if selected.with_untracked(|v| {
                            v.as_ref().is_some_and(|r| r["id"].as_str() == Some(&id))
                        }) {
                            cache.0.update(|v| {
                                v.insert(format!("invoice:{id}"), value.clone());
                            });
                            data.set(Some(value));
                        }
                    }
                    Ok((_, body)) => error.set(
                        body["error"]
                            .as_str()
                            .unwrap_or("The invoice could not be loaded.")
                            .into(),
                    ),
                    Err(message) => error.set(message),
                }
            });
        }
    });
    view! {<dialog id="scout-invoice-sheet" class="fixed top-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-xl overflow-y-auto border-l bg-background p-0 text-foreground shadow-xl backdrop:bg-foreground/20" on:close=move|_|selected.set(None)><button type="button" aria-label="Close invoice" class="absolute top-3 right-3 flex size-8 items-center justify-center rounded hover:bg-accent" on:click=move|_|selected.set(None)><Icon name="close"/></button>{move||selected.get().map(|row|view!{<header class="space-y-2 border-b p-4 pr-12"><p class="font-mono text-[11px] tracking-wide text-muted-foreground uppercase">"Invoice"</p><h2 class="text-xl font-medium tracking-tight">{data.get().and_then(|d|d["invoice"]["number"].as_str().map(str::to_owned)).unwrap_or_else(||string(&row,"invoiceNumber"))}</h2><p class="text-sm text-muted-foreground">{format!("{} · paid {} · {}",string(&row,"counterparty"),string(&row,"bookingDate"),money(row["amount"].as_f64().unwrap_or(0.0),"EUR"))}</p></header>})}<div class="space-y-5 p-4"><Show when=move||!error.get().is_empty()><p role="alert" class="text-sm text-danger">{move||error.get()}</p></Show>{move||data.get().map(|value|{let inv=&value["invoice"];let fields=[("Supplier",string(inv,"supplier")),("Total",money(inv["total"].as_f64().unwrap_or(0.0),inv["currency"].as_str().unwrap_or("EUR"))),("Issued",string(inv,"issueDate")),("Due",inv["dueDate"].as_str().unwrap_or("—").into()),("Category",inv["category"].as_str().unwrap_or("—").replace('_'," ")),("Source",if inv["source"]=="db"{"Invoicing system".into()}else{string(inv,"source")})];let lines=value["lines"].as_array().cloned().unwrap_or_default();let line_count=lines.len();let mismatch=selected.with_untracked(|r|r.as_ref().is_some_and(|r|(inv["total"].as_f64().unwrap_or(0.0)*100.0+0.5).floor()!=(-r["amount"].as_f64().unwrap_or(0.0)*100.0+0.5).floor()));let payment=selected.with_untracked(|r|r.as_ref().map(|r|-r["amount"].as_f64().unwrap_or(0.0)).unwrap_or(0.0));let total: f64=lines.iter().map(|l|(l["amount"].as_f64().unwrap_or(0.0)*100.0).round()).sum::<f64>()/100.0;view!{<dl class="grid grid-cols-2 gap-3 text-[13px]">{fields.into_iter().map(|(label,value)|view!{<div class="rounded-lg border bg-card px-3 py-2"><dt class="text-[11px] text-muted-foreground">{label}</dt><dd>{value}</dd></div>}).collect_view()}</dl>{mismatch.then(||view!{<p class="rounded-md border border-warning-border bg-warning-muted px-3 py-2 text-xs text-warning">{format!("The invoice total differs from the payment ({}).",money(payment,"EUR"))}</p>})}<section aria-labelledby="scout-invoice-lines-title" class="overflow-hidden rounded-xl border"><h3 id="scout-invoice-lines-title" class="border-b bg-muted/40 px-3 py-2 text-[13px] font-medium">{format!("Line items {}",lines.len())}</h3>{(line_count==0).then(||view!{<p class="px-3 py-4 text-sm text-muted-foreground">"No line items recorded."</p>})}<div hidden=line_count==0 class="overflow-x-auto"><table class="w-full text-[13px]"><thead><tr><th class="p-2 text-left">"Description"</th><th class="p-2 text-right">"Qty"</th><th class="p-2 text-right">"Unit"</th><th class="p-2 text-right">"Amount"</th></tr></thead><tbody>{lines.into_iter().map(|line|view!{<tr class="border-t"><td class="max-w-56 p-2 whitespace-normal">{string(&line,"description")}</td><td class="p-2 text-right font-mono text-xs">{line["quantity"].as_f64().unwrap_or(0.0)}</td><td class="p-2 text-right font-mono text-xs">{money(line["unitPrice"].as_f64().unwrap_or(0.0),"EUR")}</td><td class="p-2 text-right font-mono text-xs">{money(line["amount"].as_f64().unwrap_or(0.0),"EUR")}</td></tr>}).collect_view()}</tbody><tfoot class="border-t"><tr><td colspan="3" class="p-2">"Total"</td><td class="p-2 text-right font-mono text-xs">{money(total,"EUR")}</td></tr></tfoot></table></div></section><section class="space-y-2"><h3 id="scout-invoice-document-title" class="text-[13px] font-medium">"Document Scout read"</h3>{if value["document"].is_object(){let document=&value["document"];view!{<p class="text-xs text-muted-foreground">{document.get("evidence").filter(|e|!e.is_null()).map(|e|{let e=crate::task_content::evidence_view(e);format!("{}{} · ",e.label,e.detail.filter(|s|!s.is_empty()).map(|s|format!(" · {s}")).unwrap_or_default())})}"filed by "<a href=format!("#task/{}",string(document,"taskId")) class="font-mono hover:underline">{string(document,"taskId")}</a></p>{document["textExcerpt"].as_str().filter(|s|!s.is_empty()).map(|text|view!{<pre class="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap">{text.to_owned()}</pre>})}}.into_any()}else{view!{<p class="text-sm text-muted-foreground">"Matched in the invoicing system; no separate document was read."</p>}.into_any()}}</section>}})}<Show when=move||data.get().is_none()&&error.get().is_empty()><div role="status" aria-label="Loading invoice" class="h-48 animate-pulse rounded-lg bg-muted"/></Show></div></dialog>}
}
#[component]
fn AuditLog(rows: Vec<Value>, on_changed: Callback<()>) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let rows = StoredValue::new(rows);
    let deny_only = RwSignal::new(false);
    view! {<section class="space-y-4"><div class="flex flex-wrap items-end justify-between gap-3"><div><h1 class="text-2xl font-medium tracking-tight">"Audit log"</h1><p class="mt-1 text-sm text-muted-foreground">"Every connector read, DM and access-policy decision, newest first."</p></div><button type="button" aria-pressed=move||deny_only.get().to_string() class=move||button_classes(if deny_only.get(){PRIMARY}else{OUTLINE},"h-8 w-auto px-3") on:click=move|_|deny_only.update(|v|*v=!*v)>"DENY / BLOCK only"</button></div>{ctx.data.with_untracked(|v|v["workspace"]["role"]=="owner").then(||view!{<crate::guardrails_view::GuardrailsSwitch on_changed/>})}<div class="overflow-hidden rounded-xl border bg-card"><div class="overflow-x-auto"><table class="w-full text-[13px]"><thead><tr class="border-b">{["Time","Actor","Requester / role","Action","Target","Resource","Decision","Policy","Task"].into_iter().map(|h|view!{<th class="h-10 px-2 text-left font-medium whitespace-nowrap">{h}</th>}).collect_view()}</tr></thead><tbody>{move||rows.get_value().into_iter().filter(|r|!deny_only.get()||matches!(r["decision"].as_str(),Some("DENY"|"BLOCK"))).map(|row|{let decision=string(&row,"decision");let tone=match decision.as_str(){"DENY"|"BLOCK"=>"border-danger-border text-danger","MASK"=>"border-warning-border text-warning",_=>"border-success-border text-success"};view!{<tr class="border-b last:border-b-0 hover:bg-muted/50" title=string(&row,"reason")><td class="p-2 font-mono text-xs whitespace-nowrap text-muted-foreground">{full_timestamp(&string(&row,"ts"))}</td><td class="p-2">{string(&row,"actor")}</td><td class="p-2">{row["requester"].as_str().unwrap_or("—").to_owned()}<span class="block text-[11px] text-muted-foreground">{string(&row,"role")}</span></td><td class="p-2 font-mono text-xs">{string(&row,"action")}</td><td class="p-2">{string(&row,"target")}</td><td class="max-w-48 truncate p-2">{string(&row,"resource")}</td><td class="p-2"><span class=format!("rounded-full border px-2 py-0.5 font-mono text-[10px] {tone}")>{decision}</span></td><td class="p-2 font-mono text-xs text-muted-foreground">{row["policy"].as_str().unwrap_or("—").to_owned()}</td><td class="p-2">{row["taskId"].as_str().map(|id|view!{<a href=format!("#task/{id}") class="font-mono text-xs hover:underline">{id.to_owned()}</a>})}</td></tr>}}).collect_view()}</tbody></table></div></div></section>}
}
