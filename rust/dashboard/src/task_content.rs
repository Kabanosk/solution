use crate::{
    navigation::{loop_label, status_label},
    primitives::*,
    tasks_view::{StatusIcon, date, full_timestamp, money},
    workspace::*,
};
use leptos::prelude::*;
use serde_json::Value;
pub struct EvidenceView {
    pub kind: &'static str,
    pub label: String,
    pub detail: Option<String>,
    pub href: Option<String>,
}
pub fn evidence_view(e: &Value) -> EvidenceView {
    let text = |k: &str| e[k].as_str().map(str::to_owned);
    let kind = text("kind").or_else(|| text("type")).unwrap_or_default();
    let href = text("permalink")
        .or_else(|| text("url"))
        .or_else(|| text("href"));
    let file = text("filename")
        .or_else(|| text("file"))
        .or_else(|| text("name"))
        .or_else(|| text("path").map(|p| p.rsplit('/').next().unwrap_or("").into()));
    let (kind, label, detail) = if text("subject").is_some_and(|s| !s.is_empty())
        || matches!(kind.as_str(), "email" | "mail")
    {
        (
            "email",
            text("subject").or(file).unwrap_or_else(|| "Email".into()),
            text("from"),
        )
    } else if text("quote").is_some_and(|s| !s.is_empty())
        || matches!(kind.as_str(), "chat" | "slack" | "chat_context")
    {
        (
            "chat",
            text("quote")
                .or_else(|| text("text"))
                .unwrap_or_else(|| "Chat message".into()),
            text("author")
                .or_else(|| text("user"))
                .or_else(|| text("channel")),
        )
    } else if file.as_ref().is_some_and(|s| !s.is_empty()) {
        (
            "file",
            file.unwrap_or_default(),
            text("invoice_number").or_else(|| text("location")),
        )
    } else {
        (
            "other",
            text("label")
                .or_else(|| text("text"))
                .unwrap_or_else(|| e.to_string()),
            None,
        )
    };
    EvidenceView {
        kind,
        label,
        detail,
        href,
    }
}
pub fn resolved_label(value: &str) -> &str {
    match value {
        "auto" => "Automatic",
        "invoicing" => "Invoicing system",
        "files" => "Company files",
        "email" => "Email",
        "chat_context" => "Chat context",
        "slack_dm" => "Slack DM",
        "forecast" => "Forecast",
        "approval" => "CEO approval",
        _ => value,
    }
}
#[component]
pub fn OwnerAvatar(#[prop(into)] name: String) -> impl IntoView {
    let initials = name
        .split_whitespace()
        .take(2)
        .filter_map(|p| p.chars().next())
        .flat_map(char::to_uppercase)
        .collect::<String>();
    view! {<span title=name class="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[9px] font-medium text-muted-foreground">{initials}</span>}
}
#[component]
fn EvidenceItem(evidence: Value) -> impl IntoView {
    let e = evidence_view(&evidence);
    let icon = match e.kind {
        "file" => "file",
        "email" => "mail",
        "chat" => "chat",
        _ => "bot",
    };
    let body = view! {<span class="min-w-0"><span class=if e.kind=="chat"{"block break-words italic"}else{"block break-words"}>{if e.kind=="chat"{format!("“{}”",e.label)}else{e.label}}</span>{e.detail.filter(|s|!s.is_empty()).map(|s|view!{<span class="block text-[11px] text-muted-foreground">{s}</span>})}</span>};
    view! {<li class="flex items-start gap-2 text-[13px]"><span class="mt-0.5 text-muted-foreground"><Icon name=icon/></span>{if let Some(href)=e.href.filter(|s|!s.is_empty()){view!{<a href=href target="_blank" rel="noreferrer noopener" class="underline-offset-2 hover:underline">{body}</a>}.into_any()}else{body.into_any()}}</li>}
}
#[component]
pub fn TaskContent(
    value: Value,
    pending: RwSignal<bool>,
    feedback: RwSignal<String>,
    answer: Callback<&'static str>,
) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let task = value["task"].clone();
    let transaction = value["transaction"].clone();
    let steps = value["steps"].as_array().cloned().unwrap_or_default();
    let approval = value["approval"].clone();
    let owner = task["ownerId"].as_str().map(|id| {
        ctx.data.with_untracked(|v| {
            v["employees"]
                .as_array()
                .and_then(|rows| rows.iter().find(|p| p["id"] == id))
                .map(|p| string(p, "name"))
                .unwrap_or_else(|| id.into())
        })
    });
    let running = task["status"] == "in_progress";
    let hiring = if task["loop"] == "liquidity" {
        steps.iter().rev().find_map(|s| {
            s["hiringReport"]
                .as_str()
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
        })
    } else {
        None
    };
    let mut evidence = task["evidence"].as_array().cloned().unwrap_or_default();
    evidence.extend(
        steps
            .iter()
            .filter_map(|s| s.get("evidence").filter(|v| !v.is_null()).cloned()),
    );
    let mut seen = std::collections::HashSet::new();
    evidence.retain(|e| e["kind"] != "strategy_briefing" && seen.insert(evidence_view(e).label));
    let mut subtitle = Vec::new();
    if let Some(last) = transaction["cardLast4"].as_str().filter(|s| !s.is_empty()) {
        subtitle.push(format!("Company card •{last}"))
    } else if let Some(id) = transaction["id"]
        .as_str()
        .or(task["transactionId"].as_str())
        .filter(|s| !s.is_empty())
    {
        subtitle.push(id.into())
    }
    subtitle.push(date(
        &if transaction.is_null() {
            string(&task, "createdAt")
        } else {
            format!("{}T12:00:00Z", string(&transaction, "bookingDate"))
        },
        false,
    ));
    if let Some(amount) = task["amount"].as_f64() {
        subtitle.push(money(amount, task["currency"].as_str().unwrap_or("EUR")))
    }
    let step_count = steps.len();
    view! {<header class="space-y-1"><h1 class="text-2xl font-medium tracking-tight">{task["counterparty"].as_str().unwrap_or(task["title"].as_str().unwrap_or("")).to_owned()}</h1>{task["counterparty"].as_str().filter(|s|!s.is_empty()).map(|_|view!{<p class="text-sm">{string(&task,"title")}</p>})}<p class="font-mono text-xs text-muted-foreground">{subtitle.join(" · ")}</p></header><div class="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]"><div class="min-w-0 space-y-5">{task["outcome"].as_str().filter(|s|task["loop"]=="strategy"&&!s.is_empty()).map(|s|view!{<section aria-labelledby="strategy-brief-title" class="rounded-xl border bg-card"><h2 id="strategy-brief-title" class="border-b px-4 py-2.5 text-[13px] font-medium">"Daily CEO briefing"</h2><div class="min-w-0 px-4 py-3 text-sm leading-6 [overflow-wrap:anywhere]" inner_html=crate::markdown::render(s)/></section>})}{hiring.map(|s|view!{<section id=format!("scout-hiring-report-{}",string(&task,"id")) aria-labelledby=format!("scout-hiring-report-title-{}",string(&task,"id")) class="rounded-xl border bg-card"><h2 id=format!("scout-hiring-report-title-{}",string(&task,"id")) class="border-b px-4 py-2.5 text-[13px] font-medium">"Hiring simulation"</h2><div class="min-w-0 px-4 py-3 text-sm leading-6 [overflow-wrap:anywhere]" inner_html=crate::markdown::render(&s)/></section>})}<section aria-labelledby="activity-title" class="rounded-xl border bg-card"><h2 id="activity-title" class="border-b px-4 py-2.5 text-[13px] font-medium">"Activity log"</h2>{if steps.is_empty(){view!{<p class="px-4 py-6 text-sm text-muted-foreground">"No activity yet."</p>}.into_any()}else{view!{<ol class="relative px-4 py-2">{steps.into_iter().enumerate().map(|(i,step)|{let resolved=step["kind"]=="resolved";let icon=if resolved{"resolved"}else{match step["source"].as_str(){Some("bank")=>"bank",Some("invoicing")=>"invoicing",Some("files")=>"folder",Some("email"|"mail")=>"mail",Some("chat")=>"chat",Some("slack")=>"slack",Some("forecast")=>"forecast",_=>"bot"}};view!{<li class="relative flex items-start gap-3 py-2 text-[13px]">{(i+1<step_count).then(||view!{<span aria-hidden="true" class="absolute top-7 bottom-[-6px] left-[11px] w-px bg-border"/>})}<span class=format!("relative flex size-6 shrink-0 items-center justify-center rounded-full border bg-background {}",if resolved{"border-success-border text-success"}else if step["kind"]=="escalated"{"border-danger-border text-danger"}else{""})><span class=if i+1==step_count&&running{"animate-spin text-muted-foreground"}else{""}><Icon name=if i+1==step_count&&running{"refresh"}else{icon}/></span></span><span class="min-w-0 flex-1 pt-0.5">{string(&step,"text")}{step["resultCount"].as_i64().map(|count|view!{<span class="ml-2 inline-flex h-5 items-center rounded-full bg-secondary px-1.5 align-middle font-mono text-[10px] font-normal">{format!("{count} {}",if count==1{"result"}else{"results"})}</span>})}</span><time datetime=string(&step,"ts") title=full_timestamp(&string(&step,"ts")) class="shrink-0 pt-0.5 font-mono text-xs text-muted-foreground">{date(&string(&step,"ts"),true)}</time></li>}}).collect_view()}</ol>}.into_any()}}</section>{(!approval.is_null()).then(||view!{<section aria-labelledby="approval-title" class=format!("space-y-3 rounded-xl border p-4 {}",if approval["status"]=="pending"{"border-warning-border bg-warning-muted"}else{"bg-card"})><div class="flex items-center justify-between gap-3"><h2 id="approval-title" class="text-[13px] font-medium">"Approval"</h2><span class="rounded-full border px-2 py-0.5 text-xs">{string(&approval,"status")}</span></div><p class="text-sm">{string(&approval,"question")}</p>{if approval["status"]=="pending"{view!{<div class="flex gap-2"><button id="scout-approval-approve" type="button" class=button_classes(PRIMARY,"h-8 w-auto px-3") disabled=move||pending.get() on:click=move|_|answer.run("approve")>"Approve"</button><button id="scout-approval-reject" type="button" class=button_classes(OUTLINE,"h-8 w-auto px-3") disabled=move||pending.get() on:click=move|_|answer.run("reject")>"Reject"</button></div>}.into_any()}else{let name=approval["answeredBy"].as_str().map(|id|ctx.data.with_untracked(|v|v["employees"].as_array().and_then(|rows|rows.iter().find(|p|p["id"]==id)).map(|p|string(p,"name")).unwrap_or_else(||id.into()))).unwrap_or_else(||"—".into());view!{<p class="text-xs text-muted-foreground">{format!("Answered by {name}{}",approval["answeredAt"].as_str().map(|s|format!(" · {}",full_timestamp(s))).unwrap_or_default())}</p>}.into_any()}}<Show when=move||!feedback.get().is_empty()><p role="status" class="text-xs">{move||feedback.get()}</p></Show></section>})}<section aria-labelledby="task-audit-title" class="rounded-xl border bg-card"><h2 id="task-audit-title" class="border-b px-4 py-2.5 text-[13px] font-medium">{format!("Audit log for {}",string(&task,"id"))}</h2><CompactAudit rows=value["audit"].as_array().cloned().unwrap_or_default()/></section></div><aside aria-label="Task properties" class="h-fit rounded-xl border bg-card px-4 py-3"><dl><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Status"</dt><dd class="min-w-0"><span class="inline-flex items-center gap-2"><StatusIcon status=string(&task,"status")/>{if task["status"]=="done"{"Resolved".into()}else{status_label(&string(&task,"status")).to_owned()}}</span></dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Loop"</dt><dd class="min-w-0">{loop_label(&string(&task,"loop")).to_owned()}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Owner"</dt><dd class="min-w-0">{if let Some(name)=owner{view!{<span class="inline-flex items-center gap-2"><OwnerAvatar name=name.clone()/>{name}</span>}.into_any()}else{view!{<span class="text-muted-foreground">"Scout"</span>}.into_any()}}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Level"</dt><dd class="min-w-0">{task["level"].as_str().map(|s|view!{<span class="rounded-full border px-2 py-0.5 text-xs">{s.to_owned()}</span>}.into_any()).unwrap_or_else(||view!{<span>"—"</span>}.into_any())}{(task["simulated"]==true).then(||view!{<span class="ml-1 rounded-full border px-2 py-0.5 text-xs">"SIMULATED"</span>})}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Resolved via"</dt><dd class="min-w-0">{task["resolvedVia"].as_str().map(resolved_label).unwrap_or("—").to_owned()}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Evidence"</dt><dd class="min-w-0">{if evidence.is_empty(){view!{<span>"—"</span>}.into_any()}else{view!{<ul class="space-y-1.5">{evidence.into_iter().map(|evidence|view!{<EvidenceItem evidence/>}).collect_view()}</ul>}.into_any()}}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Category"</dt><dd class="min-w-0">{task["category"].as_str().unwrap_or("—").replace('_'," ")}</dd></div><div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Transaction"</dt><dd class="min-w-0">{if !transaction.is_null(){view!{<span class="block"><span class="font-mono text-xs">{string(&transaction,"id")}</span><span class="block text-xs text-muted-foreground">{format!("{} · {}",string(&transaction,"reference"),money(transaction["amount"].as_f64().unwrap_or(0.0),"EUR"))}</span></span>}.into_any()}else{view!{<span class="font-mono text-xs">{task["transactionId"].as_str().unwrap_or("—").to_owned()}</span>}.into_any()}}</dd></div>{task["outcome"].as_str().filter(|s|task["loop"]!="strategy"&&!s.is_empty()).map(|s|view!{<div class="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt class="text-muted-foreground">"Outcome"</dt><dd class="min-w-0">{s.to_owned()}</dd></div>})}</dl></aside></div>}
}
#[component]
fn CompactAudit(rows: Vec<Value>) -> impl IntoView {
    view! {<div class="overflow-x-auto"><table class="w-full text-[13px]"><thead><tr>{["Time","Actor","Action","Resource","Decision","Policy"].into_iter().map(|s|view!{<th class="h-10 px-2 text-left font-medium">{s}</th>}).collect_view()}</tr></thead><tbody>{rows.into_iter().map(|row|{let decision=string(&row,"decision");view!{<tr class="border-t" title=string(&row,"reason")><td class="p-2 font-mono text-xs text-muted-foreground" title=full_timestamp(&string(&row,"ts"))>{date(&string(&row,"ts"),true)}</td><td class="p-2">{string(&row,"actor")}</td><td class="p-2 font-mono text-xs">{string(&row,"action")}</td><td class="max-w-48 truncate p-2">{string(&row,"resource")}</td><td class="p-2"><span class=format!("rounded-full border px-2 py-0.5 text-xs {}",if matches!(decision.as_str(),"DENY"|"BLOCK"){"border-danger-border text-danger"}else if decision=="MASK"{"border-warning-border text-warning"}else{"border-success-border text-success"})>{decision.clone()}</span></td><td class="p-2 font-mono text-xs text-muted-foreground">{row["policy"].as_str().unwrap_or("—").to_owned()}</td></tr>}}).collect_view()}</tbody></table></div>}
}
