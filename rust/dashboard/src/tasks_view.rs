use crate::{navigation::*, primitives::*, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
#[cfg(target_arch = "wasm32")]
use wasm_bindgen::JsValue;
const STATUSES: &[&str] = &[
    "in_progress",
    "waiting",
    "escalated",
    "todo",
    "done",
    "cancelled",
];
pub fn money(amount: f64, currency: &str) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let options = js_sys::Object::new();
        let _ = js_sys::Reflect::set(&options, &"style".into(), &"currency".into());
        let _ = js_sys::Reflect::set(&options, &"currency".into(), &currency.into());
        let locales = js_sys::Array::of1(&"en-IE".into());
        let formatter = js_sys::Intl::NumberFormat::new(&locales, &options);
        if let Ok(s) = formatter.format().call1(&JsValue::NULL, &amount.into()) {
            return s.as_string().unwrap_or_default();
        }
    }
    format!("{amount:.2} {currency}")
}
pub fn date(iso: &str, _clock: bool) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let options = js_sys::Object::new();
        let _ = js_sys::Reflect::set(&options, &"timeZone".into(), &"Europe/Rome".into());
        for (k, v) in if _clock {
            vec![
                ("hour", "2-digit"),
                ("minute", "2-digit"),
                ("hourCycle", "h23"),
            ]
        } else {
            vec![("day", "numeric"), ("month", "short")]
        } {
            let _ = js_sys::Reflect::set(&options, &k.into(), &v.into());
        }
        let formatter =
            js_sys::Intl::DateTimeFormat::new(&js_sys::Array::of1(&"en-GB".into()), &options);
        let date = js_sys::Date::new(&iso.into());
        if let Ok(s) = formatter.format().call1(&JsValue::NULL, &date) {
            return s.as_string().unwrap_or_default();
        }
    }
    iso.chars().take(10).collect()
}
pub fn full_timestamp(iso: &str) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let options = js_sys::Object::new();
        for (key, value) in [
            ("timeZone", "Europe/Rome"),
            ("dateStyle", "medium"),
            ("timeStyle", "medium"),
        ] {
            let _ = js_sys::Reflect::set(&options, &key.into(), &value.into());
        }
        let formatter =
            js_sys::Intl::DateTimeFormat::new(&js_sys::Array::of1(&"en-GB".into()), &options);
        if let Ok(result) = formatter
            .format()
            .call1(&JsValue::NULL, &js_sys::Date::new(&iso.into()))
        {
            return format!("{} (Rome)", result.as_string().unwrap_or_default());
        }
    }
    format!("{iso} (Rome)")
}
#[component]
pub fn StatusIcon(#[prop(into)] status: String) -> impl IntoView {
    let label = status_label(&status).to_string();
    let class = match status.as_str() {
        "in_progress" => "border-[1.5px] border-warning-mark p-[1.5px]",
        "waiting" => "text-warning",
        "escalated" => "bg-danger text-[9px] font-bold text-primary-foreground",
        "todo" => "border-[1.5px] border-dashed border-muted-foreground",
        "done" => "bg-primary text-primary-foreground",
        _ => "bg-muted-foreground/60 text-primary-foreground",
    };
    view! {<span role="img" aria-label=label.clone() title=label class=format!("inline-flex size-3.5 shrink-0 items-center justify-center rounded-full {class}")>{match status.as_str(){"waiting"=>view!{<Icon name="clock"/>}.into_any(),"done"=>view!{<span class="scale-75"><Icon name="check"/></span>}.into_any(),"cancelled"=>view!{<span class="scale-75"><Icon name="close"/></span>}.into_any(),"escalated"=>view!{<span>"!"</span>}.into_any(),"in_progress"=>view!{<span class="size-full rounded-full" style="background:conic-gradient(var(--warning-mark) 0 50%, transparent 50% 100%)"/>}.into_any(),_=>().into_any()}}</span>}
}
#[component]
pub fn TaskRow(task: Value) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let id = string(&task, "id");
    let target = Route {
        view: "tasks".into(),
        task_id: Some(id.clone()),
        ..Default::default()
    };
    let href = hash_from_route(&target);
    let priority = if task["status"] == "escalated" || task["level"] == "APPROVAL" {
        3
    } else if task["amount"].as_f64().unwrap_or(0.0).abs() >= 1000.0 || task["level"] == "NOTIFY" {
        2
    } else {
        1
    };
    let channel = match task["resolvedVia"].as_str() {
        Some("slack_dm") => "Slack",
        Some("auto") => "Auto",
        Some("invoicing") => "Invoicing",
        Some("files") => "Files",
        Some("chat_context") => "Chat context",
        Some("email") => "Email",
        Some("forecast") => "Forecast",
        Some("approval") => "Approval",
        _ => {
            if task["pendingApprovalFor"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
            {
                "Approval"
            } else if matches!(task["status"].as_str(), Some("waiting" | "escalated")) {
                "Slack"
            } else {
                ""
            }
        }
    };
    let owner = task["ownerId"].as_str().map(|id| {
        ctx.data.with_untracked(|v| {
            v["employees"]
                .as_array()
                .and_then(|rows| rows.iter().find(|p| p["id"] == id))
                .map(|p| string(p, "name"))
                .unwrap_or_else(|| id.into())
        })
    });
    let seen_task = StoredValue::new(task.clone());
    let title = string(&task, "title");
    let status = string(&task, "status");
    let loop_name = loop_label(&string(&task, "loop")).to_string();
    view! {<li><a href=href data-task-id=id.clone() class="flex h-10 items-center gap-3 border-b px-4 text-[13px] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none" on:click=move|e|{if e.ctrl_key()||e.meta_key()||e.shift_key(){return}e.prevent_default();ctx.navigate(target.clone());}>
    <span role="img" aria-label=format!("Priority {priority} of 3") class="inline-flex h-3 shrink-0 items-end gap-[2px]">{(1..=3).map(|n|view!{<span class=if n<=priority{"w-[3px] rounded-[1px] bg-foreground/70"}else{"w-[3px] rounded-[1px] bg-foreground/15"} style=format!("height:{}px",4.0+f64::from(n)*2.5)/>}).collect_view()}</span><span class="w-14 shrink-0 font-mono text-[12px] text-muted-foreground">{id.clone()}</span><StatusIcon status=status.clone()/><span class=if status=="cancelled"{"min-w-0 flex-1 truncate text-muted-foreground line-through"}else{"min-w-0 flex-1 truncate"}>{title}</span><Show when=move||crate::task_signals::unseen(&seen_task.get_value(),&ctx.seen.get())><span class="size-1.5 shrink-0 rounded-full bg-primary" title="New result" aria-label="New result"/></Show><span class="hidden shrink-0 text-[11px] text-muted-foreground md:inline">{loop_name}</span>{task["simulated"].as_bool().unwrap_or(false).then(||view!{<span class="rounded-full border px-1.5 text-[10px]">"SIMULATED"</span>})}{(!channel.is_empty()).then(||view!{<span class=if channel=="Approval"{"h-5 shrink-0 rounded-full border border-danger-border px-2 text-[11px] text-danger"}else{"h-5 shrink-0 rounded-full border px-2 text-[11px] text-muted-foreground"}>{channel}</span>})}<span class="flex w-5 shrink-0 justify-end">{owner.map(|name|view!{<crate::task_content::OwnerAvatar name/>})}</span></a></li>}
}
fn matches_filter(task: &Value, route: &Route, ceo: &str) -> bool {
    for (k, v) in &route.filters {
        match k.as_str() {
            "scope" if v == "mine" => {
                if task["ownerId"].as_str() != Some(ceo) {
                    return false;
                }
            }
            "scope" if v == "inbox" => {
                if !matches!(task["status"].as_str(), Some("waiting" | "escalated"))
                    || !(["ownerId", "pendingApprovalFor"]
                        .iter()
                        .any(|k| task[*k].as_str() == Some(ceo)))
                {
                    return false;
                }
            }
            "loop" | "status" | "owner" => {
                let field = if k == "owner" { "ownerId" } else { k };
                if task[field].as_str() != Some(v) {
                    return false;
                }
            }
            _ => (),
        }
    }
    true
}
#[component]
pub fn TasksView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    view! { {move||if let Some(id)=ctx.route.get().task_id{view!{<TaskDetail id/>}.into_any()}else{view!{<TaskBoard/>}.into_any()}} }
}
#[component]
fn TaskBoard() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let update = move |key: &str, value: String| {
        let mut route = ctx.route.get();
        if value.is_empty() {
            route.filters.remove(key);
        } else {
            route.filters.insert(key.into(), value);
        }
        ctx.navigate(route);
    };
    view! {<section id="scout-task-board" class="overflow-hidden rounded-xl border bg-card"><header class="space-y-3 border-b px-4 py-3"><div class="flex flex-wrap items-center gap-3"><div class="mr-auto min-w-0"><h1 class="text-[15px] font-medium">{move||{let r=ctx.route.get();if r.filters.get("scope").is_some_and(|v|v=="inbox"){"Inbox".into()}else if let Some(name)=r.filters.get("loop"){format!("{} loop",loop_label(name))}else if r.filters.get("scope").is_some_and(|v|v=="mine"){"My tasks".into()}else{"Tasks".into()}}}</h1><p class="mt-0.5 text-xs text-muted-foreground">"Everything Scout works on, from every loop. Filter by loop, status or owner."</p></div><div role="group" aria-label="Scope" class="flex rounded-lg border bg-muted/40 p-0.5">{[("","All"),("inbox","Needs me"),("mine","Mine")].into_iter().map(move|(value,label)|view!{<button id=format!("scout-task-scope-{}",if value.is_empty(){"all"}else{value}) type="button" aria-pressed=move||ctx.route.with(|r|r.filters.get("scope").map(String::as_str).unwrap_or("")==value).to_string() class="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12px] text-muted-foreground hover:bg-background hover:text-foreground" on:click=move|_|update("scope",value.into())>{label}</button>}).collect_view()}</div></div><Show when=move||ctx.route.with(|r|r.filters.get("loop").is_some_and(|s|s=="strategy"))><crate::strategy_view::StrategySettings/></Show><div class="flex flex-wrap items-center gap-2" role="group" aria-label="Filter tasks"><select id="scout-task-filter-loop" aria-label="Loop" class="h-8 rounded-md border bg-card px-2 text-[13px]" prop:value=move||ctx.route.with(|r|r.filters.get("loop").cloned().unwrap_or_default()) on:change=move|e|update("loop",event_target_value(&e))><option value="">"All loops"</option>{["invoicing","cost_control","liquidity","strategy","chat","expenses"].into_iter().map(|name|view!{<option value=name>{loop_label(name)}</option>}).collect_view()}</select><select id="scout-task-filter-status" aria-label="Status" class="h-8 rounded-md border bg-card px-2 text-[13px]" prop:value=move||ctx.route.with(|r|r.filters.get("status").cloned().unwrap_or_default()) on:change=move|e|update("status",event_target_value(&e))><option value="">"All statuses"</option>{STATUSES.iter().map(|s|view!{<option value=*s>{status_label(s)}</option>}).collect_view()}</select><select id="scout-task-filter-owner" aria-label="Owner" class="h-8 rounded-md border bg-card px-2 text-[13px]" prop:value=move||ctx.route.with(|r|r.filters.get("owner").cloned().unwrap_or_default()) on:change=move|e|update("owner",event_target_value(&e))><option value="">"All owners"</option>{move||ctx.data.with(|v|v["employees"].as_array().cloned().unwrap_or_default()).into_iter().map(|person|view!{<option value=string(&person,"id")>{string(&person,"name")}</option>}).collect_view()}</select><Show when=move||!ctx.route.with(|r|r.filters.is_empty())><button id="scout-task-filter-clear" class="flex h-8 items-center gap-1 px-2 text-xs text-muted-foreground" on:click=move|_|ctx.navigate(Route::view("tasks"))><Icon name="close"/>"Clear"</button></Show></div></header>
      {move||{let route=ctx.route.get();let(data,ceo)=ctx.data.with(|v|(tasks(v),string(&v["scout"],"ceoId")));let visible:Vec<_>=data.into_iter().filter(|t|matches_filter(t,&route,&ceo)).collect();if visible.is_empty(){return view!{<p role="status" class="px-4 py-10 text-center text-sm text-muted-foreground">{if route.filters.is_empty(){"No tasks here. Scout will add them as its loops find work."}else{"No tasks match these filters."}}</p>}.into_any()}STATUSES.iter().filter_map(|status|{let mut items:Vec<_>=visible.iter().filter(|t|t["status"].as_str()==Some(*status)).cloned().collect();if items.is_empty(){return None}items.sort_by(|a,b|string(b,"updatedAt").cmp(&string(a,"updatedAt")));let count=items.len();Some(view!{<div><h2 class="flex h-9 items-center gap-2 border-b bg-muted/40 px-4 text-[13px] font-medium"><StatusIcon status=*status/>{status_label(status)}<span class="font-normal text-muted-foreground">{count}</span></h2><ul>{items.into_iter().map(|task|view!{<TaskRow task/>}).collect_view()}</ul></div>})}).collect_view().into_any()}}
    </section>}
}
#[component]
fn TaskDetail(id: String) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let id = StoredValue::new(id);
    let detail = RwSignal::new(None::<Value>);
    let error = RwSignal::new(String::new());
    let pending = RwSignal::new(false);
    let feedback = RwSignal::new(String::new());
    let retry = RwSignal::new(0u64);
    Effect::new(move |_| {
        let _ = retry.get();
        let task_id = id.get_value();
        leptos::task::spawn_local(async move {
            match json_request(
                &format!("/api/tasks/{task_id}"),
                "GET",
                None,
                Some(&ctx.slug.get_value()),
            )
            .await
            {
                Ok((200, value)) => {
                    crate::task_signals::mark_seen(ctx, &value["task"]);
                    detail.set(Some(value));
                    error.set(String::new());
                }
                Ok((404, _)) => error.set(format!("Task {task_id} does not exist.")),
                _ => error.set("The task could not be loaded. Try again shortly.".into()),
            }
        });
    });
    let answer = move |action: &'static str| {
        let Some(value) = detail.get() else { return };
        let approval = string(&value["approval"], "id");
        pending.set(true);
        feedback.set(String::new());
        leptos::task::spawn_local(async move {
            match json_request(
                "/api/approve",
                "POST",
                Some(&json!({"id":approval,"action":action})),
                Some(&ctx.slug.get_value()),
            )
            .await
            {
                Ok((status, _)) if status < 400 => {
                    feedback.set(
                        if action == "approve" {
                            "Approved. Scout continues the task."
                        } else {
                            "Rejected. Scout closes the proposal."
                        }
                        .into(),
                    );
                    ctx.refresh();
                    retry.update(|n| *n += 1);
                }
                _ => {
                    feedback.set("Scout could not record the answer. Try again in a moment.".into())
                }
            }
            pending.set(false);
        });
    };
    view! {<div class="space-y-5"><nav aria-label="Breadcrumb" class="flex items-center gap-1.5 text-[13px] text-muted-foreground"><a href="#tasks" class="hover:text-foreground">"Tasks"</a><span aria-hidden="true">"›"</span><span class="font-mono text-foreground">{id.get_value()}</span></nav>
      <Show when=move||!error.get().is_empty()><div role="alert" class="rounded-xl border bg-card px-4 py-10 text-sm text-muted-foreground">{move||error.get()}<button class=OUTLINE on:click=move|_|retry.update(|n|*n+=1)>"Try again"</button></div></Show>
      {move||detail.get().map(|value|view!{<crate::task_content::TaskContent value pending feedback answer=Callback::new(answer)/>})}
      <Show when=move||detail.get().is_none()&&error.get().is_empty()><div role="status" class="space-y-4 rounded-xl border bg-card p-4"><div class="h-7 w-72 animate-pulse rounded bg-muted"/><div class="h-40 animate-pulse rounded bg-muted"/></div></Show>
    </div>}
}
