use crate::{primitives::*, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use std::collections::BTreeMap;
fn number(value: &Value) -> String {
    value
        .as_f64()
        .map(|v| {
            #[cfg(target_arch = "wasm32")]
            {
                let f = js_sys::Intl::NumberFormat::new(
                    &js_sys::Array::of1(&"en-US".into()),
                    &js_sys::Object::new(),
                );
                if let Ok(s) = f.format().call1(&wasm_bindgen::JsValue::NULL, &v.into()) {
                    return s.as_string().unwrap_or_default();
                }
            }
            v.to_string()
        })
        .unwrap_or_else(|| "—".into())
}
fn n(v: &Value, key: &str) -> f64 {
    v[key].as_f64().unwrap_or(0.0)
}
fn compact(v: f64) -> String {
    if v >= 10000.0 {
        format!("{:.0}k", v / 1000.0)
    } else if v >= 1000.0 {
        format!("{:.1}k", v / 1000.0)
    } else {
        v.to_string()
    }
}
fn ms(v: &Value) -> String {
    v.as_f64()
        .filter(|n| n.is_finite())
        .map(|n| {
            if n >= 1000.0 {
                format!("{:.1} s", n / 1000.0)
            } else {
                format!("{} ms", (n + 0.5).floor())
            }
        })
        .unwrap_or_else(|| "—".into())
}
fn cost(value: &Value) -> String {
    let text = value.as_str().unwrap_or("0.000000");
    let (whole, fraction) = text.split_once('.').unwrap_or((text, "000000"));
    let groups = whole
        .trim_start_matches('0')
        .chars()
        .rev()
        .collect::<Vec<_>>()
        .chunks(3)
        .map(|c| c.iter().rev().collect::<String>())
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect::<Vec<_>>()
        .join(",");
    format!(
        "€{}.{fraction}",
        if groups.is_empty() { "0" } else { &groups }
    )
}
pub fn fill_buckets(report: &Value, hour: bool) -> Vec<Value> {
    #[cfg(target_arch = "wasm32")]
    {
        let width = if hour { 13 } else { 10 };
        let key_of = |key: &str| {
            let at = js_sys::Date::parse(key);
            if at.is_finite() && key.len() > 10 {
                js_sys::Date::new(&at.into())
                    .to_iso_string()
                    .as_string()
                    .unwrap_or_default()
                    .chars()
                    .take(width)
                    .collect::<String>()
            } else {
                key.chars().take(width).collect()
            }
        };
        let mut by_key = Vec::<(String, Value)>::new();
        for row in report["buckets"].as_array().cloned().unwrap_or_default() {
            let key = key_of(row["key"].as_str().unwrap_or(""));
            if let Some(entry) = by_key.iter_mut().find(|(k, _)| *k == key) {
                entry.1 = row
            } else {
                by_key.push((key, row));
            }
        }
        let step = if hour { 3600000.0 } else { 86400000.0 };
        let start = js_sys::Date::parse(report["from"].as_str().unwrap_or(""));
        let end = js_sys::Date::parse(report["to"].as_str().unwrap_or(""));
        let mut out = Vec::new();
        if start.is_finite() && end.is_finite() && end > start && (end - start) / step <= 800.0 {
            let mut at = (start / step).floor() * step;
            while at < end {
                let date = js_sys::Date::new(&at.into());
                let key = date
                    .to_iso_string()
                    .as_string()
                    .unwrap_or_default()
                    .chars()
                    .take(width)
                    .collect::<String>();
                let label = if hour {
                    format!("{:02}:00", date.get_utc_hours())
                } else {
                    format!(
                        "{} {}",
                        date.get_utc_date(),
                        [
                            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                            "Nov", "Dec"
                        ][date.get_utc_month() as usize]
                    )
                };
                let mut row=by_key.iter().position(|(k,_)|*k==key).map(|i|by_key.remove(i).1).unwrap_or_else(||json!({"requests":0,"failedRequests":0,"estimatedRequests":0,"promptTokens":0,"completionTokens":0,"p50Ms":null,"p95Ms":null,"busyCost":"0.000000","amortizedCost":"0.000000"}));
                row["key"] = json!(key);
                row["label"] = json!(label);
                out.push(row);
                at += step;
            }
        }
        for (key, mut row) in by_key {
            row["key"] = json!(key);
            row["label"] = json!(key);
            out.push(row);
        }
        return out;
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let _ = hour;
        report["buckets"].as_array().cloned().unwrap_or_default()
    }
}
#[derive(Clone, Copy)]
pub struct UsageCache(pub RwSignal<BTreeMap<u32, Value>>);
#[component]
pub fn UsageView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let cache = expect_context::<UsageCache>();
    let days = RwSignal::new(7u32);
    let revision = RwSignal::new(0u32);
    let data = RwSignal::new(cache.0.with_untracked(|v| v.get(&7).cloned()));
    let loading = RwSignal::new(false);
    let failed = RwSignal::new(false);
    let generation = RwSignal::new(0u64);
    Effect::new(move |_| {
        let range = days.get();
        let _ = revision.get();
        generation.update(|n| *n += 1);
        let current = generation.get_untracked();
        loading.set(true);
        failed.set(false);
        data.set(cache.0.with_untracked(|v| v.get(&range).cloned()));
        leptos::task::spawn_local(async move {
            let result = json_request(
                &format!("/api/llm-usage?days={range}"),
                "GET",
                None,
                Some(&ctx.slug.get_value()),
            )
            .await;
            if generation.get_untracked() != current {
                return;
            }
            match result {
                Ok((200, value)) => {
                    cache.0.update(|v| {
                        v.insert(range, value.clone());
                    });
                    data.set(Some(value));
                }
                _ => failed.set(true),
            }
            loading.set(false);
        });
    });
    view! {<section id="scout-llm-usage" class="space-y-4"><div class="flex flex-wrap items-end justify-between gap-3"><div><h1 class="text-2xl font-medium tracking-tight">"LLM usage"</h1><p class="mt-1 text-sm text-muted-foreground">"Requests, tokens, latency and estimated cost of the self-hosted model."{move||data.with(|v|v.as_ref().filter(|v|v["preview"]==true).map(|_|" Sample data."))}</p></div><div class="flex items-center gap-2"><button id="scout-llm-usage-reload" type="button" class="flex size-8 items-center justify-center rounded-md hover:bg-accent" aria-label="Reload usage" on:click=move|_|revision.update(|v|*v+=1)><span class=move||if loading.get(){"motion-safe:animate-spin"}else{""}><Icon name="refresh"/></span></button><div class="flex rounded-lg border bg-muted/40 p-0.5" role="group" aria-label="Range">{[1,7,30].into_iter().map(move|range|view!{<button type="button" id=format!("scout-llm-usage-range-{range}") aria-pressed=move||(days.get()==range).to_string() on:click=move|_|days.set(range) class=move||format!("h-7 rounded-md px-3 text-[12px] text-muted-foreground hover:text-foreground {}",if days.get()==range{"bg-background font-medium text-foreground shadow-xs"}else{""})>{if range==1{"24 h".into()}else{format!("{range} days")}}</button>}).collect_view()}</div></div></div><Show when=move||failed.get()><p role="alert" class="text-sm text-danger">"Usage could not be loaded."</p></Show>{move||if let Some(value)=data.get(){view!{<UsageReport data=value days=days.get()/>}.into_any()}else if !failed.get(){view!{<div class="space-y-4" role="status" aria-label="Loading usage"><div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{(0..4).map(|_|view!{<div class="h-[108px] animate-pulse rounded-xl bg-muted"/>}).collect_view()}</div><div class="grid gap-4 xl:grid-cols-2">{(0..4).map(|_|view!{<div class="h-[290px] animate-pulse rounded-xl bg-muted"/>}).collect_view()}</div></div>}.into_any()}else{().into_any()}}</section>}
}
#[component]
fn UsageReport(data: Value, days: u32) -> impl IntoView {
    let report = &data["report"];
    let snapshot = data["snapshot"].clone();
    let total = &report["totals"];
    let tokens = n(total, "promptTokens") + n(total, "completionTokens");
    let metrics = [
        (
            "requests",
            "Requests",
            if report.is_null() {
                "—".into()
            } else {
                number(&total["requests"])
            },
            if report.is_null() {
                "Report unavailable".into()
            } else {
                format!(
                    "{} failed · {} estimated",
                    n(total, "failedRequests"),
                    n(total, "estimatedRequests")
                )
            },
            "chat",
        ),
        (
            "tokens",
            "Tokens",
            if report.is_null() {
                "—".into()
            } else {
                compact(tokens)
            },
            format!(
                "{} in · {} out",
                number(&total["promptTokens"]),
                number(&total["completionTokens"])
            ),
            "usage",
        ),
        (
            "latency",
            "Latency p50 / p95",
            if report.is_null() {
                "—".into()
            } else {
                format!("{} / {}", ms(&total["p50Ms"]), ms(&total["p95Ms"]))
            },
            "Per request, end to end".into(),
            "clock",
        ),
        (
            "cost",
            "Cost (amortized)",
            if report.is_null() {
                "—".into()
            } else {
                let c = cost(&total["amortizedCost"]);
                let p = c.find('.').unwrap_or(c.len());
                c.chars().take(p + 3).collect()
            },
            if report.is_null() {
                String::new()
            } else {
                format!("busy time {}", cost(&total["busyCost"]))
            },
            "transactions",
        ),
    ];
    let mut actions = report["actions"].as_array().cloned().unwrap_or_default();
    actions.sort_by(|a, b| {
        b["amortizedCost"]
            .as_str()
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(0.0)
            .total_cmp(
                &a["amortizedCost"]
                    .as_str()
                    .and_then(|s| s.parse::<f64>().ok())
                    .unwrap_or(0.0),
            )
    });
    let buckets = fill_buckets(report, days == 1);
    let cache = snapshot["cacheUsage"]
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .map(|n| (n * 100.0 + 0.5).floor());
    view! {<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metrics.into_iter().map(|(id,label,value,note,icon)|view!{<article id=format!("scout-llm-metric-{id}") class="min-w-0 space-y-2 rounded-xl border bg-card px-4 py-4"><div class="flex items-center justify-between gap-2"><p class="text-xs text-muted-foreground">{label}</p><span class="text-muted-foreground"><Icon name=icon/></span></div><strong class="block font-mono text-2xl font-medium tracking-tight">{value}</strong>{(id=="tokens"&&tokens>0.0).then(||view!{<div class="flex h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true"><span class="bg-[var(--chart-balance)]" style=format!("width:{}%",n(total,"promptTokens")/tokens*100.0)/><span class="flex-1 bg-[var(--chart-forecast)]"/></div>})}<p class="text-[11px] text-muted-foreground">{note}</p></article>}).collect_view()}</div>{(!report.is_null()).then(||view!{<div class="grid gap-4 xl:grid-cols-2"><UsageChart id="scout-llm-requests" title="Requests" description=if days==1{"Per hour, last 24 hours".into()}else{format!("Per day, last {days} days")} buckets=buckets.clone() keys=vec![("ok","Succeeded","var(--chart-balance)"),("failedRequests","Failed","var(--danger)")] line=false/><UsageChart id="scout-llm-tokens" title="Tokens" description="Prompt and completion tokens" buckets=buckets.clone() keys=vec![("promptTokens","Prompt tokens","var(--chart-balance)"),("completionTokens","Completion tokens","var(--chart-forecast)")] line=false/><UsageChart id="scout-llm-latency" title="Latency" description="p50 and p95 per bucket, milliseconds" buckets keys=vec![("p50Ms","p50","var(--chart-balance)"),("p95Ms","p95","var(--chart-forecast)")] line=true/><article id="scout-llm-actions" class="min-w-0 rounded-xl border bg-card py-4"><header class="space-y-1 px-4"><h2 class="text-sm font-medium">"Cost per action"</h2><p class="text-xs text-muted-foreground">"Amortized cost of one Scout action, by kind"</p></header>{if actions.is_empty(){view!{<p class="flex h-[180px] items-center justify-center text-sm text-muted-foreground">"No attributed actions in this range."</p>}.into_any()}else{let max=actions.iter().filter_map(|a|a["amortizedCost"].as_str().and_then(|s|s.parse::<f64>().ok())).fold(0.0,f64::max).max(0.000001);view!{<div class="space-y-3 px-4 pt-6 pb-4">{actions.clone().into_iter().map(|a|{let amount=a["amortizedCost"].as_str().and_then(|s|s.parse::<f64>().ok()).unwrap_or(0.0);view!{<div class="flex items-center gap-3 text-[11px]"><span class="w-32 shrink-0 truncate text-muted-foreground">{string(&a,"action")}</span><div class="flex-1"><div class="h-5 rounded-r bg-[var(--chart-forecast)]" style=format!("width:{}%",amount/max*100.0) title=format!("{}: {} · {} actions · {} requests",string(&a,"action"),cost(&a["amortizedCost"]),n(&a,"actions"),n(&a,"requests"))/></div></div>}}).collect_view()}</div>}.into_any()}}</article></div>})}<div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]"><div class="min-w-0 rounded-xl border bg-card"><h2 class="border-b px-4 py-2.5 text-[13px] font-medium">"Per action"</h2>{if actions.is_empty(){view!{<p class="px-4 py-6 text-sm text-muted-foreground">"No attributed actions in this range."</p>}.into_any()}else{view!{<div class="overflow-x-auto"><table class="w-full text-[13px]"><thead><tr>{["Action","Actions","Requests","Cost / action"].into_iter().map(|s|view!{<th class="h-10 px-2 text-left font-medium">{s}</th>}).collect_view()}</tr></thead><tbody>{actions.into_iter().map(|a|view!{<tr class="border-t"><td class="p-2 font-mono text-xs">{string(&a,"action")}</td><td class="p-2 text-right">{n(&a,"actions")}</td><td class="p-2 text-right">{n(&a,"requests")}</td><td class="p-2 text-right font-mono text-xs">{cost(&a["amortizedCost"])}</td></tr>}).collect_view()}</tbody></table></div>}.into_any()}}</div><article class="h-fit space-y-3 rounded-xl border bg-card py-6"><header class="space-y-1.5 px-6"><h2 class="flex items-center gap-2 text-sm font-medium"><span class=if !snapshot.is_null()&&snapshot["stale"]==false{"size-2 rounded-full bg-primary motion-safe:animate-pulse"}else{"size-2 rounded-full bg-muted-foreground"}/>"Live model"</h2><p class="text-sm text-muted-foreground">{snapshot["model"].as_str().unwrap_or("Snapshot unavailable").to_owned()}</p></header>{(!snapshot.is_null()).then(||view!{<div class="space-y-3 px-6 text-[13px]">{(snapshot["stale"]==true).then(||view!{<span class="rounded-full border border-warning-border px-2 py-0.5 text-xs text-warning">"stale"</span>})}<div class="grid grid-cols-2 gap-2">{[("running","Running"),("waiting","Waiting")].into_iter().map(|(key,label)|view!{<div class="rounded-lg border px-3 py-2"><p class="text-[11px] text-muted-foreground">{label}</p><p class="font-mono text-lg">{number(&snapshot[key])}</p></div>}).collect_view()}</div><div class="space-y-1"><div class="flex justify-between text-[11px] text-muted-foreground"><span>"KV cache"</span><span class="font-mono">{cache.map(|n|format!("{n}%")).unwrap_or_else(||"—".into())}</span></div><div class="h-2 overflow-hidden rounded-full bg-muted"><div class={if cache.unwrap_or(0.0)>85.0{"h-full rounded-full bg-danger"}else if cache.unwrap_or(0.0)>60.0{"h-full rounded-full bg-warning-mark"}else{"h-full rounded-full bg-[var(--chart-balance)]"}} style=format!("width:{}%",cache.unwrap_or(0.0))/></div></div><p class="text-muted-foreground">{format!("Prompt {} tok/s · generation {} tok/s",snapshot["promptRate"].as_str().unwrap_or("—"),snapshot["generationRate"].as_str().unwrap_or("—"))}</p></div>})}</article></div>{report["notes"].as_array().cloned().filter(|v|!v.is_empty()).map(|notes|view!{<ul class="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{notes.into_iter().filter_map(|s|s.as_str().map(str::to_owned)).map(|s|view!{<li>{s}</li>}).collect_view()}</ul>})}}
}
#[component]
fn UsageChart(
    id: &'static str,
    title: &'static str,
    #[prop(into)] description: String,
    buckets: Vec<Value>,
    keys: Vec<(&'static str, &'static str, &'static str)>,
    line: bool,
) -> impl IntoView {
    let value = |row: &Value, key: &str| {
        if key == "ok" {
            n(row, "requests") - n(row, "failedRequests")
        } else {
            n(row, key)
        }
    };
    let max = buckets
        .iter()
        .map(|row| {
            if line {
                keys.iter()
                    .map(|(k, _, _)| value(row, k))
                    .fold(0.0, f64::max)
            } else {
                keys.iter().map(|(k, _, _)| value(row, k)).sum::<f64>()
            }
        })
        .fold(0.0, f64::max)
        .max(1.0);
    let len = buckets.len().max(1);
    let width = 600.0 / len as f64;
    let rows = StoredValue::new(buckets);
    let legend = keys.clone();
    view! {<article id=id class="min-w-0 space-y-3 rounded-xl border bg-card py-4"><header class="space-y-1 px-4"><h2 class="text-sm font-medium">{title}</h2><p class="text-xs text-muted-foreground">{description}</p></header><div class="px-3"><svg viewBox="0 0 650 200" preserveAspectRatio="none" class="h-[190px] w-full" role="img" aria-label=title>{(0..5).map(|i|{let y=8.0+i as f64*40.0;view!{<g><line x1="40" x2="640" y1=y y2=y stroke="var(--border)"/><text x="35" y=y+4.0 text-anchor="end" class="fill-muted-foreground text-[10px]">{compact(max*(1.0-i as f64/4.0))}</text></g>}}).collect_view()}{if line{keys.into_iter().map(|(key,label,color)|{let points=rows.with_value(|rows|rows.iter().enumerate().filter(|(_,r)|!r[key].is_null()).map(|(i,row)|format!("{},{}",40.0+(i as f64+0.5)*width,168.0-value(row,key)/max*160.0)).collect::<Vec<_>>().join(" "));view!{<polyline points=points fill="none" stroke=color stroke-width="2" stroke-dasharray=if key=="p95Ms"{"5 4"}else{""}><title>{label}</title></polyline>}}).collect_view().into_any()}else{rows.get_value().into_iter().enumerate().map(|(i,row)|{let mut bottom=168.0;keys.iter().map(|(key,label,color)|{let height=value(&row,key)/max*160.0;bottom-=height;view!{<rect x=40.0+i as f64*width+width*0.15 y=bottom width=width*0.7 height=height fill=*color><title>{format!("{} · {label}: {}",string(&row,"label"),number(&json!(value(&row,key))))}</title></rect>}}).collect_view()}).collect_view().into_any()}}{rows.get_value().into_iter().enumerate().step_by((len/6).max(1)).map(|(i,row)|view!{<text x=40.0+(i as f64+0.5)*width y="190" text-anchor="middle" class="fill-muted-foreground text-[10px]">{string(&row,"label")}</text>}).collect_view()}</svg><div class="flex flex-wrap justify-center gap-4 text-xs text-muted-foreground">{legend.into_iter().map(|(_,label,color)|view!{<span class="flex items-center gap-1.5"><span class="size-2 rounded-sm" style=format!("background:{color}")/>{label}</span>}).collect_view()}</div></div></article>}
}
