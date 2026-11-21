use crate::{
    navigation::Route,
    overview_model::*,
    primitives::*,
    tasks_view::{date, money},
    workspace::*,
};
use leptos::prelude::*;
use serde_json::Value;
use wasm_bindgen::JsCast;
fn n(v: &Value, k: &str) -> f64 {
    v[k].as_f64().unwrap_or(0.0)
}
fn day(raw: &str, year: bool) -> String {
    let parts = raw.get(..10).unwrap_or(raw).split('-').collect::<Vec<_>>();
    let month = parts
        .get(1)
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|m| m.checked_sub(1))
        .and_then(|m| {
            [
                "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            ]
            .get(m)
        })
        .copied()
        .unwrap_or("undefined");
    format!(
        "{} {month}{}",
        parts
            .get(2)
            .and_then(|s| s.parse::<u32>().ok())
            .unwrap_or(0),
        if year {
            format!(" {}", parts.first().unwrap_or(&""))
        } else {
            String::new()
        }
    )
}
fn eur(amount: f64) -> String {
    #[cfg(target_arch = "wasm32")]
    {
        let options = js_sys::Object::new();
        for (k, v) in [
            ("style", wasm_bindgen::JsValue::from_str("currency")),
            ("currency", "EUR".into()),
            ("maximumFractionDigits", 0.into()),
        ] {
            let _ = js_sys::Reflect::set(&options, &k.into(), &v);
        }
        let f = js_sys::Intl::NumberFormat::new(&js_sys::Array::of1(&"en-IE".into()), &options);
        if let Ok(s) = f
            .format()
            .call1(&wasm_bindgen::JsValue::NULL, &amount.into())
        {
            return s.as_string().unwrap_or_default();
        }
    }
    format!("€{amount:.0}")
}
#[component]
fn Panel(
    id: &'static str,
    title: &'static str,
    #[prop(optional, into)] meta: String,
    #[prop(optional, into)] badge: String,
    children: Children,
) -> impl IntoView {
    view! {<section id=id aria-labelledby=format!("{id}-title") class="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card"><header class="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b px-4 py-2"><div class="min-w-0"><h2 id=format!("{id}-title") class="text-[13px] font-medium">{title}</h2>{(!meta.is_empty()).then(||view!{<p class="text-[11px] text-muted-foreground">{meta}</p>})}</div>{(!badge.is_empty()).then(||view!{<span class="flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium"><span class="size-1.5 rounded-full bg-primary"/>{badge}</span>})}</header>{children()}</section>}
}
#[component]
fn Segmented(
    id: &'static str,
    label: &'static str,
    value: RwSignal<String>,
    options: Vec<(String, String)>,
) -> impl IntoView {
    let options = StoredValue::new(options);
    view! {<div id=id role="radiogroup" aria-label=label class="flex max-w-full rounded-lg border bg-muted/40 p-0.5" on:keydown=move|e|{if !matches!(e.key().as_str(),"ArrowLeft"|"ArrowRight"){return}e.prevent_default();let opts=options.get_value();let index=opts.iter().position(|o|o.0==value.get_untracked()).unwrap_or(0);let next=(index+if e.key()=="ArrowRight"{1}else{opts.len()-1})%opts.len();value.set(opts[next].0.clone());focus(&format!("{id}-{}",opts[next].0));}>{options.get_value().into_iter().map(move|(key,label)|{let key_id=format!("{id}-{key}");let active=key.clone();let tab=key.clone();let target=key.clone();view!{<button id=key_id type="button" role="radio" aria-checked=move||(value.get()==active).to_string() tabindex=move||if value.get()==tab{0}else{-1} class=move||format!("h-7 min-w-0 truncate rounded-md px-2.5 text-[12px] text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring {}",if value.get()==key{"bg-background font-medium text-foreground shadow-xs"}else{""}) on:click=move|_|value.set(target.clone())>{label}</button>}}).collect_view()}</div>}
}
#[component]
pub fn OverviewView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    view! {{move||{let data=ctx.data.get();let all=tasks(&data);let waiting=all.iter().filter(|t|t["status"]=="waiting").count();let escalated=all.iter().filter(|t|t["status"]=="escalated").count();let attention=waiting+escalated;if n(&data["year"],"transactions")==0.0{return view!{<div id="scout-empty-overview" class="space-y-6"><section id="scout-empty-organization" aria-labelledby="scout-empty-title" class="flex min-h-[55dvh] flex-col items-center justify-center px-2 py-12 text-center"><div class="mb-5 flex size-12 items-center justify-center rounded-xl border bg-muted/30"><Icon name="layout"/></div><h1 id="scout-empty-title" class="text-xl font-medium tracking-tight">"Your workspace is ready"</h1><p class="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">"No transactions yet. Your financial overview will appear when data is added to this organization."</p><div class="mt-6 flex flex-wrap justify-center gap-2"><button id="scout-empty-chat" class=button_classes(PRIMARY,"h-11 w-auto gap-2") on:click=move|_|ctx.navigate(Route::view("chat"))><Icon name="chat"/>"Ask Scout"</button><button id="scout-empty-connections" class=button_classes(OUTLINE,"h-11 w-auto gap-2") on:click=move|_|ctx.navigate(Route::view("connections"))><Icon name="connections"/>"View connections"</button></div><p class="mt-5 max-w-sm text-xs leading-5 text-muted-foreground">"A clean start. No sample records or shared connections."</p></section><Attention tasks=all/></div>}.into_any()}
    let documented=n(&data["scout"]["explained"],"documented");let total=n(&data["scout"]["explained"],"total");let share=if total==0.0{"—".into()}else{format!("{:.1}%",(documented*1000.0/total+0.5).floor()/10.0)};let metrics=[("Available balance",money(n(&data,"closingBalance"),"EUR"),format!("Recorded bank bookings · {}",date(&format!("{}T12:00:00Z",string(&data,"asOf")),false)),"transactions"),("Needs attention",attention.to_string(),format!("{waiting} waiting · {escalated} escalated"),"inbox"),("Explained by Scout",share,format!("{documented} of {total} payments with an invoice reference"),"transactions")];let events=data["scout"]["cashEvents"].as_array().cloned().unwrap_or_default();let has_events=!events.is_empty();
    view!{<div id="scout-overview" class="space-y-4"><section class="flex flex-wrap items-end justify-between gap-3"><div><h1 class="text-xl font-medium tracking-tight">"Overview"</h1><p class="mt-1 text-[13px] text-muted-foreground">{format!("{} bank transactions through {} · {attention} tasks wait on people",n(&data["year"],"transactions"),date(&format!("{}T12:00:00Z",string(&data,"asOf")),false))}</p></div><button id="scout-refresh" type="button" class=button_classes(OUTLINE,"h-8 w-auto shrink-0 gap-2") on:click=move|_|ctx.refresh()><Icon name="refresh"/>"Refresh"</button></section><section id="scout-financial-summary" aria-label="Key figures" class="grid divide-y overflow-hidden rounded-xl border bg-card sm:grid-cols-3 sm:divide-x sm:divide-y-0">{metrics.into_iter().map(move|(label,value,note,to)|view!{<button type="button" class="group px-4 py-3 text-left hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring" on:click=move|_|ctx.navigate(crate::navigation::route_from_hash(to))><span class="flex items-center justify-between text-[12px] text-muted-foreground">{label}<span class="opacity-0 group-hover:opacity-100"><Icon name="chevron"/></span></span><strong class="mt-1 block font-mono text-[22px] leading-tight font-medium tracking-tight">{value}</strong><span class="mt-0.5 block truncate text-[11px] text-muted-foreground">{note}</span></button>}).collect_view()}</section><ForecastSection quarter=data["forecast"].clone() year=data["forecastYear"].clone() what_if=latest_what_if(&all)/><section aria-label="Spend and Scout activity" class="grid gap-4 lg:grid-cols-2"><TeamSpend spend=data["quarterSpend"].clone()/><ScoutActivity tasks=all.clone() now=string(&data,"generatedAt")/></section><section class=if !has_events{"grid gap-4"}else{"grid gap-4 lg:grid-cols-2"} aria-label=if !has_events{"Open tasks"}else{"Cash events and open tasks"}>{has_events.then(||view!{<Panel id="cash-events" title="Upcoming cash events"><ul>{events.into_iter().map(|event|view!{<li class="flex items-center gap-4 border-b px-4 py-2.5 text-[13px] last:border-b-0"><span class="w-14 shrink-0 font-mono text-xs text-muted-foreground">{date(&format!("{}T12:00:00Z",string(&event,"onDate")),false)}</span><span class="min-w-0 flex-1 truncate">{string(&event,"label")}{event["taskId"].as_str().map(|id|view!{<a href=format!("#task/{id}") class="font-mono text-xs text-muted-foreground hover:underline">{format!(" · {id}")}</a>})}</span><span class={if n(&event,"amount")>0.0{"shrink-0 font-mono text-xs text-success"}else{"shrink-0 font-mono text-xs"}}>{format!("{}{}",if n(&event,"amount")>0.0{"+"}else{""},money(n(&event,"amount"),"EUR"))}</span></li>}).collect_view()}</ul></Panel>})}<Attention tasks=all/></section></div>}.into_any()}}}
}
#[component]
fn Attention(tasks: Vec<Value>) -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let mut open = tasks.into_iter().filter(is_open).collect::<Vec<_>>();
    open.sort_by(|a, b| {
        let reaction = |t: &Value| matches!(t["status"].as_str(), Some("waiting" | "escalated"));
        reaction(b)
            .cmp(&reaction(a))
            .then_with(|| string(b, "updatedAt").cmp(&string(a, "updatedAt")))
    });
    let count = open.len();
    view! {<section id="scout-attention-list" aria-labelledby="scout-attention-title" class="h-fit overflow-hidden rounded-xl border bg-card"><header class="flex items-center justify-between gap-3 border-b px-4 py-2.5"><h2 id="scout-attention-title" class="text-[13px] font-medium">"Open tasks "<span class="ml-1 font-normal text-muted-foreground">{count}</span></h2><button id="scout-attention-all" type="button" class="flex h-7 items-center gap-2 rounded px-2 text-xs text-muted-foreground hover:bg-accent" on:click=move|_|ctx.navigate(Route::view("tasks"))>"All tasks"<Icon name="chevron"/></button></header><ul>{open.into_iter().take(5).map(|task|view!{<crate::tasks_view::TaskRow task/>}).collect_view()}</ul>{(count==0).then(||view!{<p class="px-4 py-6 text-sm text-muted-foreground">"No open tasks."</p>})}</section>}
}
#[component]
fn ForecastSection(quarter: Value, year: Value, what_if: Option<Value>) -> impl IntoView {
    let quarter = StoredValue::new(quarter);
    let year = StoredValue::new(year);
    let what_if = StoredValue::new(what_if);
    let range = RwSignal::new("90d".to_string());
    let scenario = RwSignal::new("base".to_string());
    let chart_width = element_width("scout-forecast", 724.0);
    view! {<section aria-label="Cash forecast and safety buffer" class="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]"><section id="scout-forecast" aria-labelledby="scout-forecast-title" class="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card"><header class="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b px-4 py-2"><div class="min-w-0"><h2 id="scout-forecast-title" class="text-[13px] font-medium">"Cash forecast"</h2><p class="text-[11px] text-muted-foreground">{move||{let f=if range.get()=="90d"{quarter.get_value()}else{year.get_value()};if f.is_null(){"Forecast unavailable".into()}else{format!("Predicted balance · {} · from {}",string(&f,"model"),day(&string(&f,"asOf"),true))}}}</p></div><div class="flex max-w-full flex-wrap items-center gap-2"><Segmented id="scout-forecast-range" label="Forecast range" value=range options=vec![("90d".into(),"90 days".into()),("12m".into(),"12 months".into())]/>{what_if.get_value().map(|w|view!{<Segmented id="scout-forecast-scenario" label="Scenario" value=scenario options=vec![("base".into(),"Base case".into()),("whatif".into(),format!("What if: {}",string(&w,"what")))]/>})}</div></header>{move||{let f=if range.get()=="90d"{quarter.get_value()}else{year.get_value()};if f.is_null(){view!{<p role="status" class="px-4 py-16 text-center text-sm text-muted-foreground">"The forecast is temporarily unavailable. Bank records stay available."</p>}.into_any()}else{view!{<ForecastChart forecast=f width=(chart_width.get()-34.0).max(100.0) range=range.get() what_if=if scenario.get()=="whatif"{what_if.get_value()}else{None}/>}.into_any()}}}</section>{move||{let f=if range.get()=="90d"{quarter.get_value()}else{year.get_value()};view!{<BufferGauge forecast=if f.is_null(){quarter.get_value()}else{f} range=range.get()/>}}}</section>}
}
fn monotone(points: &[(f64, f64)]) -> String {
    if points.is_empty() {
        return String::new();
    }
    let mut path = format!("M {},{}", points[0].0, points[0].1);
    if points.len() == 1 {
        return path;
    }
    if points.len() == 2 {
        return format!("{path} L {},{}", points[1].0, points[1].1);
    }
    let mut slopes = vec![0.0; points.len()];
    for i in 1..points.len() - 1 {
        let a = points[i - 1];
        let b = points[i];
        let c = points[i + 1];
        let h0 = b.0 - a.0;
        let h1 = c.0 - b.0;
        let s0 = (b.1 - a.1) / h0;
        let s1 = (c.1 - b.1) / h1;
        let p = (s0 * h1 + s1 * h0) / (h0 + h1);
        let sign = |v: f64| if v < 0.0 { -1.0 } else { 1.0 };
        slopes[i] = (sign(s0) + sign(s1)) * s0.abs().min(s1.abs()).min(p.abs() * 0.5);
    }
    slopes[0] = (3.0 * (points[1].1 - points[0].1) / (points[1].0 - points[0].0) - slopes[1]) / 2.0;
    let end = points.len() - 1;
    slopes[end] = (3.0 * (points[end].1 - points[end - 1].1) / (points[end].0 - points[end - 1].0)
        - slopes[end - 1])
        / 2.0;
    for i in 1..points.len() {
        let a = points[i - 1];
        let b = points[i];
        let dx = (b.0 - a.0) / 3.0;
        path.push_str(&format!(
            " C {},{} {},{} {},{}",
            a.0 + dx,
            a.1 + dx * slopes[i - 1],
            b.0 - dx,
            b.1 - dx * slopes[i],
            b.0,
            b.1
        ));
    }
    path
}
#[component]
fn ForecastChart(
    forecast: Value,
    width: f64,
    range: String,
    what_if: Option<Value>,
) -> impl IntoView {
    let points = forecast["points"].as_array().cloned().unwrap_or_default();
    let alternate = with_what_if(&points, what_if.as_ref());
    let buffer = n(&forecast, "safetyBuffer");
    let values = points
        .iter()
        .chain(alternate.iter())
        .map(|p| n(p, "balance"))
        .chain(std::iter::once(buffer))
        .collect::<Vec<_>>();
    let ticks = nice_ticks(
        values.iter().copied().fold(f64::INFINITY, f64::min),
        values.iter().copied().fold(f64::NEG_INFINITY, f64::max),
    );
    let low = *ticks.first().unwrap_or(&0.0);
    let high = *ticks.last().unwrap_or(&1.0);
    let max_day = points.last().map(|p| n(p, "day")).unwrap_or(1.0);
    let rough = max_day / 4.0;
    let digits = if rough == 0.0 {
        1.0
    } else {
        rough.abs().log10().floor() + 1.0
    };
    let magnitude = 10f64.powf(digits);
    let step = ((rough / magnitude) / if digits == 1.0 { 0.1 } else { 0.05 }).ceil()
        * if digits == 1.0 { 0.1 } else { 0.05 }
        * magnitude;
    let mut x_ticks = Vec::new();
    if step > 0.0 {
        let mut value = 0.0;
        while value < max_day {
            x_ticks.push(value);
            value += step;
        }
    }
    x_ticks.push(max_day);let mut visible=Vec::new();for tick in x_ticks.into_iter().rev(){if visible.last().is_none_or(|last:f64|(*last-tick)/max_day.max(1.0)*(width-60.0)>=76.0){visible.push(tick)}}visible.reverse();let x_ticks=visible;
    let x = move |v: f64| 52.0 + v / max_day.max(1.0) * (width - 60.0);
    let y = move |v: f64| 8.0 + (high - v) / (high - low).max(1.0) * 212.0;
    let base = monotone(
        &points
            .iter()
            .map(|p| (x(n(p, "day")), y(n(p, "balance"))))
            .collect::<Vec<_>>(),
    );
    let alternative = monotone(
        &alternate
            .iter()
            .map(|p| (x(n(p, "day")), y(n(p, "balance"))))
            .collect::<Vec<_>>(),
    );
    let hover = RwSignal::new(None::<usize>);
    let points = StoredValue::new(points);
    let alternate = StoredValue::new(alternate);
    let out = what_if.as_ref().is_some_and(|w| {
        points.with_value(|p| {
            p.last()
                .is_some_and(|p| string(w, "on") > string(p, "date"))
        })
    });
    let lowest = alternate.with_value(|p| {
        p.iter()
            .min_by(|a, b| n(a, "balance").total_cmp(&n(b, "balance")))
            .cloned()
            .unwrap_or(Value::Null)
    });
    let last = points.with_value(|p| p.last().cloned().unwrap_or(Value::Null));
    let summary = if let Some(w) = &what_if {
        if out {
            format!(
                "The {} ({}) lands on {}, after this range. Switch to 12 months to see it.",
                string(w, "what"),
                eur(n(w, "amount")),
                day(&string(w, "on"), true)
            )
        } else {
            format!(
                "With the {} ({} on {}) the balance bottoms out at {} on {}, {} the safety buffer.",
                string(w, "what"),
                eur(n(w, "amount")),
                day(&string(w, "on"), true),
                eur(n(&lowest, "balance")),
                day(&string(&lowest, "date"), true),
                if n(&lowest, "balance") < buffer {
                    "below"
                } else {
                    "above"
                }
            )
        }
    } else {
        format!(
            "Base case: the balance moves from {} today to {} on {}.",
            eur(n(&forecast, "openingBalance")),
            eur(n(&last, "balance")),
            day(&string(&last, "date"), true)
        )
    };
    let show = what_if.is_some();
    view! {<div class="flex flex-1 flex-col gap-3 px-4 pt-3 pb-3" data-range=range data-scenario=if show{"whatif"}else{"base"} data-points=points.with_value(Vec::len)><div id="scout-forecast-chart" class="relative h-[248px] w-full"><svg viewBox=format!("0 0 {width} 248") preserveAspectRatio="none" class="h-full w-full" role="application" tabindex="0" aria-label="Predicted cash balance over time" on:keydown=move|e|{if matches!(e.key().as_str(),"ArrowLeft"|"ArrowRight"){e.prevent_default();let count=points.with_value(Vec::len);if count>0{let current=hover.get_untracked().unwrap_or(0);hover.set(Some(if e.key()=="ArrowRight"{(current+1).min(count-1)}else{current.saturating_sub(1)}));}}} on:mousemove=move|e|{let Some(element)=e.current_target().and_then(|t|t.dyn_into::<web_sys::Element>().ok())else{return};let rect=element.get_bounding_client_rect();let px=e.client_x() as f64-rect.left();let target=((px-52.0)/(width-60.0)*max_day).clamp(0.0,max_day);let index=points.with_value(|p|p.iter().enumerate().min_by(|(_,a),(_,b)|(n(a,"day")-target).abs().total_cmp(&(n(b,"day")-target).abs())).map(|(i,_)|i));hover.set(index);} on:mouseleave=move|_|hover.set(None)>{ticks.into_iter().map(move|tick|view!{<g><line x1="52" x2=width-8.0 y1=y(tick) y2=y(tick) stroke="var(--border)" stroke-opacity="0.6"/><text x="46" y=y(tick)+4.0 text-anchor="end" class="fill-muted-foreground text-[11px]">{format!("{}€{}k",if tick<0.0{"−"}else{""},(tick.abs()/1000.0).round())}</text></g>}).collect_view()}{x_ticks.into_iter().map(move|tick|{let p=points.with_value(|rows|rows.iter().min_by(|a,b|(n(a,"day")-tick).abs().total_cmp(&(n(b,"day")-tick).abs())).cloned().unwrap_or(Value::Null));view!{<text x=x(tick) y="241" text-anchor=if tick==max_day{"end"}else{"middle"} class="fill-muted-foreground text-[12px]">{day(&string(&p,"date"),false)}</text>}}).collect_view()}<line x1="52" x2=width-8.0 y1=y(buffer) y2=y(buffer) stroke="var(--chart-buffer)" stroke-dasharray="4 4"/><path d=base fill="none" stroke="var(--chart-balance)" stroke-width=if show{1.25}else{2.0} stroke-opacity=if show{0.45}else{1.0}/>{show.then(||view!{<path d=alternative fill="none" stroke="var(--chart-forecast)" stroke-width="2"/>})}{move||hover.get().map(|i|{let p=points.get_value()[i].clone();view!{<line x1=x(n(&p,"day")) x2=x(n(&p,"day")) y1="8" y2="220" stroke="var(--muted-foreground)" stroke-dasharray="3 3"/>}})}</svg>{move||hover.get().map(|i|{let p=points.get_value()[i].clone();let a=alternate.get_value()[i].clone();view!{<div class="pointer-events-none absolute top-3 right-3 rounded-lg border bg-background px-3 py-2 text-xs shadow-md"><p class="font-medium">{day(&string(&p,"date"),true)}</p><p class="mt-1">{format!("Base case {}",eur(n(&p,"balance")))}</p>{show.then(||view!{<p>{format!("What if {}",eur(n(&a,"balance")))}</p>})}</div>}})}</div><div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground"><span>"■ Base case"</span>{what_if.as_ref().map(|w|view!{<span>{format!("■ What if: {}",string(w,"label"))}</span>})}<span>{format!("┄ Safety buffer {}",eur(buffer))}</span><span class="ml-auto">"Hover the chart for a day's balance"</span></div><p id="scout-forecast-summary" aria-live="polite" class="text-[12px] text-muted-foreground">{summary}{what_if.map(|w|view!{<a href=format!("#task/{}",string(&w,"taskId")) class="ml-1 underline underline-offset-2">{string(&w,"taskId")}</a>})}</p></div>}
}
fn gauge_point(value: f64, radius: f64) -> (f64, f64) {
    let angle = std::f64::consts::PI * (1.0 - value.clamp(0.0, 3.0) / 3.0);
    (
        (110.0 + radius * angle.cos()) * 1e6 / 1e6,
        (104.0 - radius * angle.sin()) * 1e6 / 1e6,
    )
}
fn gauge_arc(from: f64, to: f64) -> String {
    let a = gauge_point(from, 84.0);
    let b = gauge_point(to, 84.0);
    format!(
        "M {:.6} {:.6} A 84 84 0 0 1 {:.6} {:.6}",
        a.0, a.1, b.0, b.1
    )
}
#[component]
fn BufferGauge(forecast: Value, range: String) -> impl IntoView {
    if forecast.is_null() {
        return view!{<Panel id="scout-buffer" title="Safety buffer"><p role="status" class="px-4 py-16 text-center text-sm text-muted-foreground">"Needs the forecast, which is unavailable right now."</p></Panel>}.into_any();
    }
    let balance = n(&forecast, "openingBalance");
    let buffer = n(&forecast, "safetyBuffer");
    let low = n(&forecast["lowest"], "balance");
    let ratio = coverage(balance, buffer);
    let low_ratio = coverage(low, buffer);
    let status = gauge_status(balance, low, buffer);
    let color = match status {
        "below" => "var(--danger)",
        "tight" => "var(--warning-mark)",
        _ => "var(--chart-balance)",
    };
    let horizon = if range == "90d" {
        "next 90 days"
    } else {
        "next 12 months"
    };
    let sentence = format!(
        "Cash today {} covers {} the safety buffer of {}. Lowest balance in the {horizon}: {} on {} ({} the buffer). Status: {}.",
        eur(balance),
        coverage_label(ratio),
        eur(buffer),
        eur(low),
        day(&string(&forecast["lowest"], "date"), true),
        coverage_label(low_ratio),
        status_label(status)
    );
    let a = gauge_point(low_ratio, 73.0);
    let b = gauge_point(low_ratio, 95.0);
    view!{<Panel id="scout-buffer" title="Safety buffer" meta=format!("One month of fixed costs · {horizon}") badge=status_label(status)><div class="flex flex-1 flex-col gap-3 px-4 pt-3 pb-4" data-ratio=format!("{ratio:.3}") data-lowest=low data-buffer=buffer><figure class="relative mx-auto w-full max-w-[260px]"><svg viewBox="0 0 220 122" role="img" aria-label=sentence class="w-full overflow-visible"><path d=gauge_arc(0.0,3.0) fill="none" stroke="var(--muted)" stroke-width="12"/>{(ratio>0.0).then(||view!{<path d=gauge_arc(0.0,ratio.min(3.0)) fill="none" stroke=color stroke-width="12"/>})}{[1.0,1.5].into_iter().map(|v|{let a=gauge_point(v,78.0);let b=gauge_point(v,90.0);view!{<line x1=a.0 y1=a.1 x2=b.0 y2=b.1 stroke="var(--card)" stroke-width="2"/>}}).collect_view()}<line x1=a.0 y1=a.1 x2=b.0 y2=b.1 stroke="var(--foreground)" stroke-width="2"/>{[(0.0,"0"),(1.0,"1× buffer"),(1.5,"1.5×"),(3.0,"3×")].into_iter().map(|(v,label)|{let p=gauge_point(v,104.0);view!{<text x=p.0 y=p.1+3.0 text-anchor=if v==0.0{"end"}else if v==3.0{"start"}else{"middle"} class="fill-muted-foreground text-[9px]">{label}</text>}}).collect_view()}</svg><figcaption class="absolute inset-x-0 bottom-0 text-center" aria-hidden="true"><span class="block font-mono text-[28px] leading-none font-medium tracking-tight">{coverage_label(ratio)}</span><span class="mt-1 block text-[11px] text-muted-foreground">{format!("covers {} the buffer",coverage_label(ratio))}</span></figcaption></figure><dl class="divide-y rounded-lg border text-[12px]">{[("Cash today",eur(balance)),("Safety buffer",eur(buffer)),("Lowest ahead",format!("{} · {}",day(&string(&forecast["lowest"],"date"),false),eur(low)))].into_iter().map(|(label,value)|view!{<div class="flex items-center justify-between gap-3 px-3 py-2"><dt class="text-muted-foreground">{label}</dt><dd class="font-mono tabular-nums">{value}</dd></div>}).collect_view()}</dl><p class="text-[11px] leading-5 text-muted-foreground">"Below 1× is below the buffer, under 1.5× is tight. The status takes the worse of today and the lowest point ahead."</p></div></Panel>}.into_any()
}
#[component]
fn Donut(
    id: &'static str,
    data: Vec<(String, f64, String)>,
    label: String,
    caption: &'static str,
    #[prop(default = false)] large: bool,
) -> impl IntoView {
    let total = data.iter().map(|(_, v, _)| v.max(0.0)).sum::<f64>();
    let count = data.iter().filter(|(_, v, _)| *v > 0.0).count();
    let padding = if count > 1 { 1.5 } else { 0.0 };
    let available = 360.0 - count as f64 * padding;
    let mut offset = 0.0;
    let arcs = data
        .into_iter()
        .filter(|(_, v, _)| *v > 0.0)
        .map(|(name, value, color)| {
            let length = value / total * available / 3.6;
            let start = offset;
            offset += length + padding / 3.6;
            (name, value, color, length, start)
        })
        .collect::<Vec<_>>();
    view! {<div class="relative mx-auto size-[136px] shrink-0 xl:size-[160px]"><svg id=id class="size-full -scale-y-100" role="img" aria-label=label.clone()>{arcs.into_iter().map(|(name,value,color,len,start)|view!{<circle cx="50%" cy="50%" r="calc(42.5% - 4.25px)" fill="none" stroke=color stroke-width="calc(15% - 1.5px)" pathLength="100" stroke-dasharray=format!("{len} {}",100.0-len) stroke-dashoffset=-start><title>{format!("{name}: {value}")}</title></circle>}).collect_view()}</svg><div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center"><span class=if large{"font-mono text-[22px] leading-none font-medium tracking-tight"}else{"font-mono text-[17px] font-medium tracking-tight"}>{label}</span><span class=if large{"mt-1 text-[11px] text-muted-foreground"}else{"text-[11px] text-muted-foreground"}>{caption}</span></div></div>}
}
#[component]
fn TeamSpend(spend: Value) -> impl IntoView {
    let teams = spend["teams"].as_array().cloned().unwrap_or_default();
    let total = (teams.iter().map(|t| n(t, "spent")).sum::<f64>() * 100.0 + 0.5).floor() / 100.0;
    let colors = [
        "var(--chart-balance)",
        "var(--chart-2)",
        "var(--chart-3)",
        "var(--chart-4)",
        "var(--chart-5)",
        "var(--muted)",
    ];
    let mut index = 0;
    let colored = teams
        .into_iter()
        .map(|t| {
            let color = if t["id"].is_null() {
                "var(--chart-5)"
            } else {
                let c = colors[index % colors.len()];
                index += 1;
                c
            };
            (t, color)
        })
        .collect::<Vec<_>>();
    let chart = colored
        .iter()
        .map(|(t, c)| (string(t, "name"), n(t, "spent"), (*c).into()))
        .collect();
    view! {<Panel id="scout-team-spend" title="Where the money goes" meta=format!("{} to date · {} – {} · against 3 monthly budgets",string(&spend,"label"),day(&string(&spend,"start"),false),day(&string(&spend,"through"),true))><div class="flex flex-1 flex-col items-center gap-4 px-4 py-4 sm:flex-row sm:items-start" data-total=format!("{total:.2}")>{if total>0.0{view!{<Donut id="scout-team-spend-donut" data=chart label=eur(total) caption="spent"/>}.into_any()}else{view!{<p class="text-sm text-muted-foreground">"No spending booked this quarter yet."</p>}.into_any()}}<table class="w-full min-w-0 table-fixed text-[12px]" aria-label=format!("Spend per team, {}",string(&spend,"label"))><colgroup><col/><col class="w-[84px]"/><col class="w-[48px]"/></colgroup><thead class="sr-only"><tr><th>"Team"</th><th>"Spent"</th><th>"Budget used"</th></tr></thead><tbody>{colored.into_iter().map(|(t,c)|{let budget=t["budget"].as_f64();let spent=n(&t,"spent");let over=budget.is_some_and(|v|spent>v);view!{<tr class="border-b last:border-b-0" data-spent=format!("{spent:.2}")><th scope="row" class="py-1.5 pr-2 text-left font-normal"><span class="flex min-w-0 items-center gap-2"><span class="inline-block size-2 shrink-0 rounded-[2px]" style=format!("background:{c}")/><span class="truncate">{string(&t,"name")}</span></span></th><td class="py-1.5 pr-2 text-right font-mono tabular-nums">{money(spent,"EUR")}</td><td class="py-1.5 text-right whitespace-nowrap">{if let Some(b)=budget.filter(|v|*v!=0.0){view!{<span title=format!("Budget {} for the quarter",eur(b)) class=format!("inline-block rounded-full border px-1.5 py-px font-mono text-[10.5px] tabular-nums {}",if over{"border-danger-border bg-danger/10 text-danger"}else{"text-muted-foreground"})>{format!("{}%",(spent/b*100.0+0.5).floor())}</span>}.into_any()}else{view!{<span class="text-[11px] text-muted-foreground" title="Unassigned spend (tax, social security, partner payouts) has no budget">"—"</span>}.into_any()}}</td></tr>}}).collect_view()}</tbody><tfoot><tr class="border-t"><th scope="row" class="pt-2 text-left font-medium">"Total"</th><td class="pt-2 pr-2 text-right font-mono font-medium tabular-nums">{money(total,"EUR")}</td><td/></tr></tfoot></table></div><p class="px-4 pb-3 text-[11px] text-muted-foreground">"Pills: share of the team's budget for the quarter used so far. Red means over budget."</p></Panel>}
}
#[component]
fn ScoutActivity(tasks: Vec<Value>, now: String) -> impl IntoView {
    let ring = scout_ring(&tasks, &now);
    let outcomes = [
        (
            "alone",
            "Did it alone",
            "Matched invoices from files, email or chat without anyone",
            "var(--chart-balance)",
        ),
        (
            "told",
            "Did it and told you",
            "Acted, then sent you a note",
            "var(--chart-3)",
        ),
        (
            "asked",
            "Asked you first",
            "Waited for your approval or a Slack answer",
            "var(--chart-4)",
        ),
    ];
    let data = outcomes
        .iter()
        .map(|(k, label, _, c)| ((*label).into(), n(&ring["counts"], k), (*c).into()))
        .collect();
    let actions = notable_actions(&tasks, 3);
    let month = now
        .get(5..7)
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|m| m.checked_sub(1))
        .and_then(|i| {
            [
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ]
            .get(i)
        })
        .copied()
        .unwrap_or("undefined");
    view! {<Panel id="scout-activity" title="What Scout did" meta=format!("Tasks opened in {month}, by how they ended · chat answers not counted")><div class="flex flex-1 flex-col gap-4 px-4 py-4"><div class="flex flex-col items-center gap-4 sm:flex-row sm:items-start">{if n(&ring,"total")>0.0{view!{<Donut id="scout-activity-ring" data label=n(&ring,"total").to_string() caption="tasks ended" large=true/>}.into_any()}else{view!{<p class="text-sm text-muted-foreground">"No tasks ended this month yet."</p>}.into_any()}}<ul class="w-full min-w-0 text-[12px]" aria-label="Tasks by how they ended" data-counts=format!("{},{},{}",n(&ring["counts"],"alone"),n(&ring["counts"],"told"),n(&ring["counts"],"asked"))>{outcomes.into_iter().map(|(key,label,hint,color)|view!{<li class="flex items-start gap-2 border-b py-1.5 last:border-b-0"><span class="mt-1 inline-block size-2 shrink-0 rounded-[2px]" style=format!("background:{color}")/><span class="min-w-0 flex-1"><span class="block">{label}</span><span class="block truncate text-[11px] text-muted-foreground">{hint}</span></span><span class="font-mono tabular-nums">{n(&ring["counts"],key)}</span></li>}).collect_view()}{(n(&ring,"running")>0.0).then(||view!{<li class="pt-1.5 text-[11px] text-muted-foreground">{format!("{} more still running, counted once they end",n(&ring,"running"))}</li>})}</ul></div>{(!actions.is_empty()).then(||view!{<div><h3 class="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">"Latest"</h3><ol class="relative space-y-2 border-l pl-3">{actions.into_iter().map(|a|view!{<li class="relative text-[12px]"><span aria-hidden="true" class="absolute top-1.5 -left-[16.5px] size-2 rounded-full bg-primary ring-2 ring-card"/><a href=format!("#task/{}",string(&a,"id")) class="group flex min-w-0 items-baseline gap-2 rounded-sm focus-visible:outline-2 focus-visible:outline-ring"><span class="shrink-0 font-mono text-[11px] text-muted-foreground">{string(&a,"id")}</span><span class="min-w-0 flex-1 truncate group-hover:underline" title=string(&a,"outcome")>{action_summary(&a)}</span><span class="shrink-0 text-[11px] text-muted-foreground">{date(a["closedAt"].as_str().unwrap_or(a["updatedAt"].as_str().unwrap_or("")),false)}</span></a></li>}).collect_view()}</ol></div>})}</div></Panel>}
}
