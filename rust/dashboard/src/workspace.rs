use crate::{navigation::*, primitives::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use wasm_bindgen::JsCast;
#[derive(Clone, Copy)]
pub struct WorkspaceContext {
    pub data: RwSignal<Value>,
    pub seen: RwSignal<std::collections::BTreeMap<String, String>>,
    pub route: RwSignal<Route>,
    pub slug: StoredValue<String>,
}
impl WorkspaceContext {
    pub fn navigate(self, route: Route) {
        let hash = hash_from_route(&route);
        self.route.set(route);
        if let Some(w) = web_sys::window() {
            if w.location().hash().ok().as_deref() != Some(&hash) {
                let _ = w.history().and_then(|h| {
                    h.push_state_with_url(&wasm_bindgen::JsValue::NULL, "", Some(&hash))
                });
            }
            w.scroll_to_with_x_and_y(0.0, 0.0);
        }
        focus("main-content");
    }
    pub fn refresh(self) {
        leptos::task::spawn_local(async move {
            if let Ok((status, data)) =
                json_request("/api/dashboard", "GET", None, Some(&self.slug.get_value())).await
            {
                if status == 200 {
                    self.data.set(data);
                } else if status == 401 {
                    navigate(&format!("/{}", self.slug.get_value()));
                }
            }
        });
    }
}
pub fn string(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or("").into()
}
pub fn tasks(data: &Value) -> Vec<Value> {
    data["scout"]["tasks"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}
pub fn is_open(task: &Value) -> bool {
    !matches!(task["status"].as_str(), Some("done" | "cancelled"))
}
fn active_target(route:&Route,target:&Route)->bool{if route.view!="tasks"{return route.view==target.view}if route.task_id.is_some()||route.filters.contains_key("scope")||!route.filters.contains_key("loop"){target.view=="tasks"&&!target.filters.contains_key("loop")}else{target.view=="tasks"&&route.filters.get("loop")==target.filters.get("loop")}}
fn page_title(route:&Route)->String{if let Some(id)=&route.task_id{return format!("Tasks › {id}")}if route.view=="tasks"&&!route.filters.contains_key("scope"){if let Some(name)=route.filters.get("loop"){return loop_label(name).into()}}view_label(&route.view).into()}
#[component]
fn LoopIndicator(name:String)->impl IntoView{let ctx=expect_context::<WorkspaceContext>();let name=StoredValue::new(name);view!{{move||ctx.data.with(|v|crate::task_signals::loop_signal(&tasks(v),&name.get_value(),&ctx.seen.get())).map(|(signal,label)|view!{<span role="img" aria-label=label title=label class=match signal{"attention"=>"size-1.5 shrink-0 rounded-full bg-warning-mark","result"=>"size-1.5 shrink-0 rounded-full bg-primary",_=>"size-1.5 shrink-0 rounded-full bg-muted-foreground motion-safe:animate-pulse"}/>})}{move||ctx.data.with(|v|{let count=tasks(v).iter().filter(|t|t["loop"]==name.get_value()&&is_open(t)).count();(count>0).then(||view!{<span class="min-w-4 text-right font-mono text-[11px] text-muted-foreground">{count}</span>})})}}}
#[component]
fn SearchResults(query: RwSignal<String>, open: RwSignal<bool>) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    view! {<div class="max-h-80 space-y-1 overflow-y-auto" aria-live="polite">{move||{let data=ctx.data.get();let all=tasks(&data);let ceo=string(&data["scout"],"ceoId");let open_tasks=all.iter().filter(|t|is_open(t)).count();let inbox=all.iter().filter(|t|matches!(t["status"].as_str(),Some("waiting"|"escalated"))&&(t["ownerId"]==ceo||t["pendingApprovalFor"]==ceo)).count();let mut entries=vec![("Tasks".into(),format!("{open_tasks} open tasks"),Route::view("tasks")),("Inbox".into(),format!("{inbox} waiting on you"),route_from_hash("#inbox")),("Scout chat".into(),"Ask Scout; it answers with its tools".into(),Route::view("chat")),("LLM usage".into(),"Model requests, tokens, latency and cost".into(),Route::view("llm-usage")),("Overview".into(),"Cash, forecast, safety buffer, team spend and what Scout did".into(),Route::view("overview")),("Transactions".into(),"Bank bookings and their invoices".into(),Route::view("transactions")),("Connections".into(),"Bank, invoicing, email, Slack and laptops".into(),Route::view("connections")),("Audit log".into(),"ALLOW / DENY decisions".into(),Route::view("audit")),("Staffing & payroll".into(),"Team, salaries and payroll export".into(),Route::view("staffing"))];if data["scout"]["demo"]==true{entries.push(("Demo scenarios".into(),"Play a payment scenario for Scout".into(),Route::view("demo")))}for task in all{let id=string(&task,"id");let detail=format!("{} · {}{}",string(&task,"status").replacen('_'," ",1),loop_label(&string(&task,"loop")),task["counterparty"].as_str().filter(|s|!s.is_empty()).map(|s|format!(" · {s}")).unwrap_or_default());entries.push((format!("{id} · {}",string(&task,"title")),detail,Route{view:"tasks".into(),task_id:Some(id),..Default::default()}));}for person in data["employees"].as_array().cloned().unwrap_or_default(){entries.push((string(&person,"name"),format!("{} · {}",string(&person,"role"),string(&person,"team")),Route::view("staffing")));}let needle=query.get().to_lowercase().trim().to_string();let entries=entries.into_iter().filter(|(label,detail,_)|format!("{label} {detail}").to_lowercase().contains(&needle)).take(40).collect::<Vec<_>>();if entries.is_empty(){view!{<p class="p-3 text-sm text-muted-foreground">"No matching records."</p>}.into_any()}else{entries.into_iter().map(move|(label,detail,target)|view!{<button type="button" class="flex min-h-14 w-full items-start rounded-md px-3 py-2.5 text-left hover:bg-accent" on:click=move|_|{open.set(false);ctx.navigate(target.clone());}><span><span class="block text-sm">{label}</span><span class="mt-1 block whitespace-normal text-xs font-normal text-muted-foreground">{detail}</span></span></button>}).collect_view().into_any()}}}</div>}
}
#[component]
pub fn Workspace(data: Value) -> impl IntoView {
    let slug = StoredValue::new(string(&data["workspace"], "slug"));
    let role = string(&data["workspace"], "role");
    let ctx = WorkspaceContext {
        seen: RwSignal::new(crate::task_signals::baseline(&data)),
        data: RwSignal::new(data),
        route: RwSignal::new(Route::view("chat")),
        slug,
    };
    provide_context(ctx);
    crate::guardrails_view::provide_guardrails(ctx);
    provide_context(crate::records_view::RecordCache(RwSignal::new(
        std::collections::BTreeMap::new(),
    )));
    provide_context(crate::usage_view::UsageCache(RwSignal::new(
        std::collections::BTreeMap::new(),
    )));
    let ready = RwSignal::new(false);
    let sidebar = RwSignal::new(true);
    let mobile = RwSignal::new(false);
    let search = RwSignal::new(false);
    let query = RwSignal::new(String::new());
    let search_origin = StoredValue::new(String::new());
    let notice = RwSignal::new(false);
    let collapsed = RwSignal::new(std::collections::BTreeMap::<String, bool>::new());
    Effect::new(move |_| {
        let Some(w) = web_sys::window() else { return };
        crate::task_signals::load(ctx);
        let storage = wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::StorageEvent)>::new(
            move |event: web_sys::StorageEvent| {
                if event
                    .key()
                    .is_none_or(|k| k == crate::task_signals::storage_key(ctx))
                {
                    crate::task_signals::load(ctx);
                }
            },
        );
        let _ = w.add_event_listener_with_callback("storage", storage.as_ref().unchecked_ref());
        storage.forget();
        let hash = w.location().hash().unwrap_or_default();
        ctx.route.set(route_from_hash(&hash));
        let scope = ctx.data.with_untracked(|v| {
            format!(
                "{}:{}:scout-nav-collapsed",
                slug.get_value(),
                string(&v["workspace"], "userId")
            )
        });
        if let Ok(Some(storage)) = w.local_storage() {
            if let Ok(Some(value)) = storage.get_item(&scope) {
                if let Ok(value) = serde_json::from_str(&value) {
                    collapsed.set(value);
                }
            }
        }
        if hash.is_empty() && role == "owner" {
            leptos::task::spawn_local(async move {
                if let Ok((200, setup)) =
                    json_request("/api/onboarding", "GET", None, Some(&slug.get_value())).await
                {
                    if matches!(setup["status"].as_str(), Some("pending" | "in_progress")) {
                        ctx.navigate(Route::view("setup"));
                    }
                }
                ready.set(true);
            });
        } else {
            ready.set(true);
        }
        let change = wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::Event)>::new(move |_| {
            if let Some(w) = web_sys::window() {
                ctx.route
                    .set(route_from_hash(&w.location().hash().unwrap_or_default()));
                mobile.set(false);
            }
        });
        let _ = w.add_event_listener_with_callback("hashchange", change.as_ref().unchecked_ref());
        let _ = w.add_event_listener_with_callback("popstate", change.as_ref().unchecked_ref());
        change.forget();
        let last_g = std::rc::Rc::new(std::cell::Cell::new(0.0));
        let key = wasm_bindgen::closure::Closure::<dyn FnMut(web_sys::KeyboardEvent)>::new(
            move |e: web_sys::KeyboardEvent| {
                if ctx.route.with_untracked(|r| r.view == "setup") {
                    return;
                }
                if e.key() == "Escape" {
                    search.set(false);
                    notice.set(false);
                    mobile.set(false);
                    return;
                }
                if (e.ctrl_key() || e.meta_key()) && e.key().eq_ignore_ascii_case("k") {
                    e.prevent_default();
                    search.update(|v| *v = !*v);
                    return;
                }
                if e.target()
                    .and_then(|t| t.dyn_into::<web_sys::Element>().ok())
                    .is_some_and(|t| {
                        t.closest("input, textarea, select, [contenteditable=true]")
                            .ok()
                            .flatten()
                            .is_some()
                    })
                {
                    return;
                }
                if e.ctrl_key() || e.meta_key() || e.alt_key() {
                    return;
                }
                let now = js_sys::Date::now();
                if e.key().eq_ignore_ascii_case("g") {
                    last_g.set(now);
                    return;
                }
                if now - last_g.get() < 1200.0 {
                    match e.key().to_lowercase().as_str() {
                        "o" => ctx.navigate(Route::view("overview")),
                        "t" => ctx.navigate(Route::view("tasks")),
                        "c" => ctx.navigate(Route::view("chat")),
                        "i" => ctx.navigate(route_from_hash("#inbox")),
                        _ => (),
                    }
                    last_g.set(0.0);
                }
            },
        );
        let _ = w.add_event_listener_with_callback("keydown", key.as_ref().unchecked_ref());
        key.forget();
    });
    Effect::new(move |_| {
        let open = search.get();
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-search-dialog"))
            .and_then(|d| d.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if open {
                search_origin.set_value(
                    web_sys::window()
                        .and_then(|w| w.document())
                        .and_then(|d| d.active_element())
                        .map(|e| e.id())
                        .unwrap_or_default(),
                );
                let _ = dialog.show_modal();
                focus("scout-search-input");
            } else {
                dialog.close();
                focus(&search_origin.get_value());
            }
        }
    });
    Effect::new(move |_| {
        let open = notice.get();
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-new-task-notice"))
            .and_then(|d| d.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if open {
                let _ = dialog.show_modal();
            } else {
                dialog.close();
            }
        }
    });
    // Views are delegated explicitly while their Rust components are being ported.
    Effect::new(move |_| {
        if ready.get() {
            let route = ctx.route.get();
            if ![
                "tasks",
                "chat",
                "transactions",
                "audit",
                "staffing",
                "policies",
                "connections",
                "overview",
                "llm-usage",
                "setup",
                "demo",
            ]
            .contains(&route.view.as_str())
            {
                if let Some(w) = web_sys::window() {
                    let _ = w.location().set_search("?runtime=original");
                }
            }
        }
    });
    view! {
      <Show when=move||ready.get() fallback=||view!{<div id="scout-workspace-loading" role="status" class="flex min-h-dvh w-full items-center justify-center bg-background text-sm text-muted-foreground">"Preparing your workspace…"</div>}>
      <Show when=move||ctx.route.with(|r|r.view=="setup") fallback=move||view!{
      <div id="scout-tenant-boundary" data-scout-scope=move||ctx.data.with(|v|format!("{}:{}",slug.get_value(),string(&v["workspace"],"userId"))) class="flex min-h-dvh w-full bg-background text-foreground">
        <a id="scout-skip-link" href="#main-content" on:click=move|e|{e.prevent_default();focus("main-content");} class="fixed top-3 left-3 z-50 -translate-y-24 rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground focus:translate-y-0">"Skip to content"</a>
        <Show when=move||mobile.get()><button id="scout-mobile-overlay" aria-label="Close navigation" class="fixed inset-0 z-30 bg-foreground/20 md:hidden" on:click=move|_|mobile.set(false)/></Show>
        <aside id="scout-sidebar" aria-label="Primary navigation" class=move||format!("{} {} fixed inset-y-0 left-0 z-40 w-64 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:sticky md:top-0 md:h-dvh",if mobile.get(){"flex"}else{"hidden"},if sidebar.get(){"md:flex"}else{"md:hidden"})>
          <header class="px-2 pt-3 pb-1"><div class="flex items-center gap-1"><details id="scout-company-menu" class="relative min-w-0 flex-1"><summary id="scout-company-switcher" class="flex h-8 cursor-pointer list-none items-center gap-2 rounded-md px-3 text-[13px] font-medium hover:bg-sidebar-accent" aria-label="Select workspace"><span class="size-5 shrink-0"><ScoutMark size=20/></span><span id="scout-company-name" class="min-w-0 flex-1 truncate">{move||ctx.data.with(|v|string(&v["workspace"],"name"))}</span><Icon name="chevron"/></summary>
            <div id="scout-company-list" class="absolute top-10 left-0 z-50 min-w-56 rounded-md border bg-popover p-1 text-popover-foreground shadow-md"><p class="px-2 py-1.5 text-xs text-muted-foreground">"Scout · Your finance agent"</p>{move||ctx.data.with(|v|v["workspace"]["organizations"].as_array().cloned().unwrap_or_default()).into_iter().map(move|org|{let target=string(&org,"slug");let label=string(&org,"name");view!{<button class="flex min-h-11 w-full items-center rounded px-2 text-left text-sm hover:bg-accent" on:click=move|_|{let target=target.clone();leptos::task::spawn_local(async move{if let Ok((status,result))=json_request("/api/auth/switch","POST",Some(&json!({"slug":target})),None).await{if status<400{if let Some(url)=result["redirect"].as_str(){navigate(url);}}}});}>{label}</button>}}).collect_view()}
              <a id="scout-switch-org" href="/" class="flex min-h-11 items-center gap-2 border-t px-2 text-sm hover:bg-accent"><Icon name="users"/>"Find another organization"</a><form id="scout-link-google-form" method="post" action=move||format!("/api/auth/google/start?organization={}",slug.get_value())><button id="scout-link-google" type="submit" class="flex min-h-11 w-full items-center gap-2 px-2 text-left text-sm hover:bg-accent" disabled=move||!ctx.data.with(|v|v["workspace"]["googleAvailable"].as_bool().unwrap_or(false))><Icon name="key"/>"Link Google sign-in"</button></form>
              <button id="scout-sign-out" class="flex min-h-11 w-full items-center gap-2 border-t px-2 text-left text-sm hover:bg-accent" on:click=move|_|leptos::task::spawn_local(async{if let Ok((status,_))=json_request("/api/auth/logout","POST",Some(&json!({})),None).await{if status<400{navigate("/");}}})><Icon name="logout"/>"Sign out"</button>
            </div></details><button id="scout-sidebar-search" type="button" aria-label="Search (⌘K)" class="flex size-7 items-center justify-center rounded hover:bg-sidebar-accent" on:click=move|_|search.set(true)><Icon name="search"/></button><button id="scout-new-task" type="button" aria-label="New task" class="flex size-7 items-center justify-center rounded hover:bg-sidebar-accent" on:click=move|_|notice.set(true)><Icon name="plus"/></button></div></header>
          <nav class="min-h-0 flex-1 overflow-y-auto px-2" aria-label="Workspace views">{["primary","Loops","Workspace"].into_iter().map(move|section|view!{<section class="py-1.5">{(section!="primary").then(||view!{<button type="button" class="flex h-6 items-center gap-1 rounded px-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground" aria-expanded=move||(!collapsed.with(|v|v.get(section).copied().unwrap_or(false))).to_string() on:click=move|_|{collapsed.update(|v|{let current=v.get(section).copied().unwrap_or(false);v.insert(section.into(),!current);});if let Some(w)=web_sys::window(){if let Ok(Some(storage))=w.local_storage(){let scope=ctx.data.with_untracked(|v|format!("{}:{}:scout-nav-collapsed",slug.get_value(),string(&v["workspace"],"userId")));let _=storage.set_item(&scope,&serde_json::to_string(&collapsed.get_untracked()).unwrap_or_default());}}}>{section}<Icon name="chevron"/></button>})}<Show when=move||section=="primary"||!collapsed.with(|v|v.get(section).copied().unwrap_or(false))><ul class="space-y-px">{NAV.iter().filter(move|entry|entry.0==section&&(entry.1!="demo"||ctx.data.with(|v|v["scout"]["demo"]==true))).map(move|entry|{let href=format!("#{}",entry.1);let target=route_from_hash(&href);let current_target=target.clone();let aria_target=target.clone();view!{<li><a id=format!("scout-nav-{}",entry.1) href=href aria-current=move||ctx.route.with(|r|active_target(r,&aria_target)).then_some("page") class=move||format!("flex h-7 items-center gap-2 rounded-md px-2 text-[13px] hover:bg-sidebar-accent/60 {}",if ctx.route.with(|r|active_target(r,&current_target)){"bg-sidebar-accent font-medium"}else{"font-normal"}) on:click=move|e|{if e.ctrl_key()||e.meta_key(){return}e.prevent_default();ctx.navigate(target.clone());mobile.set(false);}>
              <span class="text-muted-foreground"><Icon name=entry.3/></span><span class="min-w-0 flex-1 truncate">{entry.2}</span>{entry.1.strip_prefix("loop-").map(|name|view!{<LoopIndicator name=name.replace('-',"_")/>})}{(entry.1=="tasks").then(||view!{<span class="font-mono text-[11px] text-muted-foreground">{move||ctx.data.with(|v|{let n=tasks(v).iter().filter(|t|is_open(t)).count();if n>0{n.to_string()}else{String::new()}})}</span>})}</a></li>}}).collect_view()}</ul></Show></section>}).collect_view()}</nav>
          <footer class="flex items-center gap-2 border-t px-2 py-2"><span class="flex size-6 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">{move||ctx.data.with(|v|string(&v["workspace"],"userName").split_whitespace().take(2).filter_map(|s|s.chars().next()).collect::<String>())}</span><span class="min-w-0 flex-1 leading-tight"><span class="block truncate text-[13px]">{move||ctx.data.with(|v|string(&v["workspace"],"userName"))}</span><span class="block truncate text-[11px] text-muted-foreground">{move||ctx.data.with(|v|string(&v["workspace"],"role"))}</span></span></footer>
        </aside>
        <div id="scout-workspace" class="min-w-0 flex-1 bg-background"><header id="scout-topbar" class="sticky top-0 z-20 flex h-12 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur-sm md:px-6"><button aria-label="Toggle navigation" class="flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-accent" on:click=move|_|{if web_sys::window().and_then(|w|w.inner_width().ok()).and_then(|v|v.as_f64()).unwrap_or(1024.0)<768.0{mobile.update(|v|*v=!*v)}else{sidebar.update(|v|*v=!*v)}}><Icon name="menu"/></button><div class="flex min-w-0 flex-1 items-center gap-2 text-[13px]"><span class="hidden text-muted-foreground lg:inline">{move||ctx.data.with(|v|string(&v["workspace"],"name"))}</span><span class="hidden text-muted-foreground lg:inline" aria-hidden="true">"/"</span><strong class="truncate font-medium">{move||page_title(&ctx.route.get())}</strong></div><button id="scout-search-trigger" type="button" class=button_classes(OUTLINE,"hidden h-8 w-full max-w-xs justify-start bg-card text-muted-foreground lg:flex") on:click=move|_|search.set(true)><Icon name="search"/><span class="flex-1 text-left text-xs font-normal">"Search tasks, people…"</span><kbd class="rounded border px-1.5 py-0.5 font-mono text-[10px]">"⌘ K"</kbd></button><button id="scout-search-compact" type="button" aria-label="Search workspace" class="flex size-8 items-center justify-center rounded-md hover:bg-accent lg:hidden" on:click=move|_|search.set(true)><Icon name="search"/></button><ThemeToggle compact=true/><button id="scout-notifications" type="button" aria-label="Inbox" class="flex size-8 items-center justify-center rounded-md hover:bg-accent" on:click=move|_|ctx.navigate(route_from_hash("#inbox"))><Icon name="bell"/></button></header>
          <section id="main-content" tabindex="-1" aria-label=move||page_title(&ctx.route.get()) class=move||format!("mx-auto w-full max-w-[1600px] min-w-0 px-4 outline-none md:px-8 {}",if ctx.route.with(|r|r.view=="chat"){"flex h-[calc(100dvh-3rem-5.5rem)] flex-col pt-4 pb-4 md:h-[calc(100dvh-3rem)] md:pt-5 md:pb-5"}else{"pt-6 pb-28 md:pt-7 md:pb-10"})>
            <crate::guardrails_view::GuardrailsBanner/>{move||ctx.data.with(|v|v["workspace"]["authNotice"].as_str().map(|s|view!{<p id="scout-auth-feedback" role="status" class="mb-5 rounded-md border bg-muted/40 px-4 py-3 text-sm">{s.to_owned()}</p>}))}
            {move||match ctx.route.get().view.as_str(){"overview"=>view!{<crate::overview_view::OverviewView/>}.into_any(),"llm-usage"=>view!{<crate::usage_view::UsageView/>}.into_any(),"demo"=>view!{<crate::demo_view::DemoView/>}.into_any(),"tasks"=>view!{<crate::tasks_view::TasksView/>}.into_any(),"transactions"=>view!{<crate::records_view::RecordsView kind="transactions"/>}.into_any(),"audit"=>view!{<crate::records_view::RecordsView kind="audit"/>}.into_any(),"staffing"=>view!{<crate::people_view::StaffingView/>}.into_any(),"policies"=>view!{<crate::people_view::PoliciesView/>}.into_any(),"connections"=>view!{<crate::connections_view::ConnectionsView/>}.into_any(),_=>().into_any()}}
            <div hidden=move||ctx.route.with(|r|r.view!="chat") class=move||if ctx.route.with(|r|r.view=="chat"){ "flex min-h-0 flex-1 flex-col" }else{""}><crate::chat_view::ChatView/></div>
          </section>
          <nav id="scout-mobile-navigation" aria-label="Mobile navigation" class="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-3 gap-2 border-t bg-sidebar/95 px-3 pt-2 pb-[max(.5rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">{[("overview","Overview","layout"),("chat","Scout chat","chat"),("tasks","Tasks","tasks")].into_iter().map(move|(id,label,icon)|view!{<button id=format!("scout-mobile-{id}") type="button" class="flex min-h-14 flex-col items-center justify-center gap-1 rounded-md text-[11px] hover:bg-accent" on:click=move|_|ctx.navigate(Route::view(id))><Icon name=icon/>{label}</button>}).collect_view()}</nav>
        </div>
      </div>
      <dialog id="scout-search-dialog" class="fixed inset-0 m-auto w-[calc(100%-2rem)] max-w-xl space-y-4 rounded-xl border bg-popover p-6 text-popover-foreground shadow-xl backdrop:bg-foreground/20" on:close=move|_|{search.set(false);}><header class="flex items-center justify-between gap-3"><h2 class="font-semibold">"Search your workspace"</h2><button aria-label="Close search" class="flex size-8 items-center justify-center rounded hover:bg-accent" on:click=move|_|search.set(false)><Icon name="close"/></button></header><p class="text-sm text-muted-foreground">"Find views, agent tasks and people."</p><input id="scout-search-input" aria-label="Search workspace" placeholder="Search tasks, people…" class=INPUT prop:value=move||query.get() on:input=move|e|query.set(event_target_value(&e))/><SearchResults query open=search/></dialog>
      <dialog id="scout-new-task-notice" class="fixed inset-0 m-auto w-[calc(100%-2rem)] max-w-lg space-y-4 rounded-xl border bg-popover p-6 text-popover-foreground shadow-xl backdrop:bg-foreground/20" on:close=move|_|notice.set(false)><h2 class="font-semibold">"New task"</h2><p class="text-sm leading-7 text-muted-foreground">"Scout opens tasks from its loops. To ask for something new, DM Scout in Slack, e.g. “what if I buy an oven for 30k in March?” — it appears here under Tasks."</p><button class=OUTLINE on:click=move|_|{notice.set(false);focus("scout-new-task");}>"Close"</button></dialog>
      }><crate::setup_view::SetupView/></Show>
      </Show>
    }
}
