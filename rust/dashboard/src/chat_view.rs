use crate::{primitives::*, workspace::*};
use leptos::prelude::*;
use serde_json::{Value, json};
use wasm_bindgen::JsCast;
use wasm_bindgen_futures::JsFuture;
#[derive(Clone, Copy)]
struct ChatState {
    turns: RwSignal<Vec<Value>>,
    draft: RwSignal<String>,
    busy: RwSignal<bool>,
    error: RwSignal<String>,
    notice: RwSignal<String>,
    truncated: RwSignal<bool>,
    following: RwSignal<bool>,
    model: RwSignal<String>,
    external: RwSignal<Option<Value>>,
    conversation: StoredValue<String>,
    generation: RwSignal<u64>,
    controller: StoredValue<Option<web_sys::AbortController>, LocalStorage>,
    copied: RwSignal<String>,
}
fn uuid() -> String {
    web_sys::window()
        .and_then(|w| w.crypto().ok())
        .map(|c| c.random_uuid())
        .unwrap_or_else(|| "server".into())
}
fn update_turn(state: ChatState, id: &str, apply: impl FnOnce(&mut Value)) {
    state.turns.update(|turns| {
        if let Some(turn) = turns.iter_mut().find(|t| t["id"].as_str() == Some(id)) {
            apply(turn)
        }
    });
}
fn quote_units(units: &[u16]) -> String {
    let mut out = String::from("\"");
    let mut i = 0;
    while i < units.len() {
        let u = units[i];
        match u {
            0x22 => out.push_str("\\\""),
            0x5c => out.push_str("\\\\"),
            0x08 => out.push_str("\\b"),
            0x0c => out.push_str("\\f"),
            0x0a => out.push_str("\\n"),
            0x0d => out.push_str("\\r"),
            0x09 => out.push_str("\\t"),
            0..=0x1f => out.push_str(&format!("\\u{u:04x}")),
            0xd800..=0xdbff
                if units
                    .get(i + 1)
                    .is_some_and(|v| (0xdc00..=0xdfff).contains(v)) =>
            {
                let c = 0x10000 + ((u as u32 - 0xd800) << 10) + (units[i + 1] as u32 - 0xdc00);
                if let Some(c) = char::from_u32(c) {
                    out.push(c)
                }
                i += 1;
            }
            0xd800..=0xdfff => out.push_str(&format!("\\u{u:04x}")),
            _ => {
                if let Some(c) = char::from_u32(u as u32) {
                    out.push(c)
                }
            }
        }
        i += 1;
    }
    out.push('"');
    out
}
fn raw_message(role: &str, units: &[u16]) -> Box<serde_json::value::RawValue> {
    serde_json::value::RawValue::from_string(format!(
        "{{\"role\":\"{role}\",\"content\":{}}}",
        quote_units(units)
    ))
    .expect("escaped JSON message")
}
fn request_context(turns: &[Value]) -> (Vec<Box<serde_json::value::RawValue>>, Vec<String>) {
    let summary = turns.first().filter(|t| t["kind"] == "summary");
    let mut pairs = Vec::<(Vec<(&str, Vec<u16>)>, Vec<String>)>::new();
    let mut index = usize::from(summary.is_some());
    while index < turns.len() {
        let user = &turns[index];
        if let Some(answer) = turns.get(index + 1) {
            if matches!(answer["status"].as_str(), Some("complete" | "stopped"))
                && !string(answer, "content").trim().is_empty()
            {
                let mut content = string(answer, "content");
                for update in answer["followups"].as_array().cloned().unwrap_or_default() {
                    content.push_str(&format!("\n\nUpdate: {}", string(&update, "text")));
                }
                pairs.push((
                    vec![
                        ("user", string(user, "content").encode_utf16().collect()),
                        ("assistant", content.encode_utf16().take(16000).collect()),
                    ],
                    vec![string(user, "id"), string(answer, "id")],
                ));
            }
        } else {
            pairs.push((
                vec![("user", string(user, "content").encode_utf16().collect())],
                vec![string(user, "id")],
            ));
        }
        index += 2;
    }
    let (mut length, mut count, mut from) = (0, 0, 0);
    for i in (0..pairs.len()).rev() {
        count += pairs[i].0.len();
        length += pairs[i].0.iter().map(|(_, s)| s.len()).sum::<usize>();
        if count > 62 - usize::from(summary.is_some()) || length > 111000 {
            from = i + 1;
            break;
        }
    }
    let (mut messages, mut sources) = (Vec::new(), Vec::new());
    if let Some(head) = summary {
        messages.push(raw_message(
            "assistant",
            &format!(
                "Summary of the earlier conversation:\n{}",
                string(head, "content")
            )
            .encode_utf16()
            .take(16000)
            .collect::<Vec<_>>(),
        ));
        sources.push(string(head, "id"));
    }
    for (pair, ids) in pairs.into_iter().skip(from) {
        for (role, units) in pair {
            messages.push(raw_message(role, &units));
        }
        sources.extend(ids);
    }
    (messages, sources)
}
#[cfg(test)]
mod context_tests {
    use super::*;
    #[test]
    fn utf16_slice_keeps_the_source_json_surrogate() {
        let text = format!("{}😀", "x".repeat(15999));
        let turns = vec![
            json!({"id":"u","role":"user","content":"question","status":"complete"}),
            json!({"id":"a","role":"assistant","content":text,"status":"complete"}),
        ];
        let (messages, sources) = request_context(&turns);
        assert_eq!(sources, vec!["u", "a"]);
        assert!(messages[1].get().ends_with("\\ud83d\"}"));
        let payload = serde_json::to_string(&ChatRequest {
            model: "fixture".into(),
            messages,
            conversation_id: "fixture-only".into(),
            external: None,
        })
        .unwrap();
        assert!(payload.contains("\\ud83d"));
        assert!(!payload.contains('�'));
    }
    #[test]
    fn failed_exchanges_do_not_become_context() {
        let turns = vec![
            json!({"id":"u1","content":"failed question"}),
            json!({"id":"a1","content":"partial","status":"error"}),
            json!({"id":"u2","content":"retry"}),
        ];
        let (messages, sources) = request_context(&turns);
        assert_eq!(sources, vec!["u2"]);
        assert_eq!(
            messages[0].get(),
            "{\"role\":\"user\",\"content\":\"retry\"}"
        );
    }
}
#[derive(serde::Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<Box<serde_json::value::RawValue>>,
    #[serde(rename = "conversationId")]
    conversation_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    external: Option<Value>,
}
fn handle_event(
    state: ChatState,
    id: &str,
    event: Value,
    sources: &[String],
) -> Result<bool, String> {
    match event["type"].as_str() {
        Some("delta") => update_turn(state, id, |turn| {
            turn["content"] = json!(format!(
                "{}{}",
                string(turn, "content"),
                string(&event, "text")
            ));
        }),
        Some("step") => update_turn(state, id, |turn| {
            let mut steps = turn["steps"].as_array().cloned().unwrap_or_default();
            steps.push(event["text"].clone());
            turn["steps"] = json!(steps);
        }),
        Some("file") => update_turn(state, id, |turn| {
            let mut files = turn["files"].as_array().cloned().unwrap_or_default();
            files.push(json!({"name":event["name"],"mime":event["mime"],"dataB64":event["data_b64"],"sentToSlack":event["sent_to_slack"]}));
            turn["files"] = json!(files);
        }),
        Some("research") => update_turn(state, id, |turn| {
            turn["research"] = event["research"].clone()
        }),
        Some("compacted") => {
            let covered: std::collections::HashSet<_> = sources
                .iter()
                .take(event["replaced"].as_u64().unwrap_or(0) as usize)
                .collect();
            state.turns.update(|turns|{if let Some(last)=turns.iter().rposition(|t|covered.contains(&string(t,"id"))){let replaced:usize=turns[..=last].iter().filter(|t|covered.contains(&string(t,"id"))).map(|t|if t["kind"]=="summary"{t["replaced"].as_u64().unwrap_or(1)as usize}else{1}).sum();let mut next=vec![json!({"id":uuid(),"role":"assistant","kind":"summary","content":event["summary"],"status":"complete","replaced":replaced})];next.extend_from_slice(&turns[last+1..]);*turns=next;}});
            state
                .notice
                .set("Earlier messages were summarized to fit the model.".into());
        }
        Some("error") => {
            return Err(event["message"]
                .as_str()
                .unwrap_or("The response was interrupted. Try again.")
                .into());
        }
        Some("done") => {
            state
                .truncated
                .set(event["truncated"].as_bool().unwrap_or(false));
            update_turn(state, id, |turn| {
                if let Some(task) = event["task_id"].as_str() {
                    turn["taskId"] = json!(task);
                    if event["followups"].as_bool().unwrap_or(false) {
                        turn["awaiting"] = json!(true);
                    }
                }
            });
            return Ok(true);
        }
        _ => (),
    }
    Ok(false)
}
async fn consume(
    response: gloo_net::http::Response,
    state: ChatState,
    id: &str,
    generation: u64,
    sources: &[String],
) -> Result<bool, String> {
    let body = response
        .body()
        .ok_or("The response was interrupted. Try again.")?;
    let reader = body
        .get_reader()
        .dyn_into::<web_sys::ReadableStreamDefaultReader>()
        .map_err(|_| "The response was interrupted. Try again.")?;
    let decoder =
        web_sys::TextDecoder::new().map_err(|_| "The response was interrupted. Try again.")?;
    let mut buffer = String::new();
    let mut completed = false;
    loop {
        if state.generation.get_untracked() != generation {
            let _ = reader.cancel();
            reader.release_lock();
            return Ok(false);
        }
        let read = JsFuture::from(reader.read())
            .await
            .map_err(|_| "The response was interrupted. Try again.")?;
        let done = js_sys::Reflect::get(&read, &"done".into())
            .ok()
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if done {
            buffer.push_str(
                &decoder
                    .decode()
                    .map_err(|_| "The response was interrupted. Try again.")?,
            );
        } else {
            let value = js_sys::Reflect::get(&read, &"value".into())
                .map_err(|_| "The response was interrupted. Try again.")?;
            let data = js_sys::Uint8Array::new(&value).to_vec();
            let options = web_sys::TextDecodeOptions::new();
            options.set_stream(true);
            buffer.push_str(
                &decoder
                    .decode_with_u8_array_and_options(&data, &options)
                    .map_err(|_| "The response was interrupted. Try again.")?,
            );
        }
        while let Some(end) = buffer.find('\n') {
            let line = buffer[..end].trim().to_owned();
            buffer = buffer[end + 1..].to_owned();
            if !line.is_empty() {
                let event = serde_json::from_str(&line)
                    .map_err(|_| "The response was interrupted. Try again.")?;
                completed |= handle_event(state, id, event, sources)?;
            }
        }
        if done {
            break;
        }
    }
    if !buffer.trim().is_empty() {
        completed |= handle_event(
            state,
            id,
            serde_json::from_str(buffer.trim())
                .map_err(|_| "The response was interrupted. Try again.")?,
            sources,
        )?;
    }
    let _ = reader.cancel();
    reader.release_lock();
    Ok(completed)
}
fn reply(ctx: WorkspaceContext, state: ChatState, history: Vec<Value>) {
    if state.busy.get_untracked() {
        return;
    }
    let Ok(controller) = web_sys::AbortController::new() else {
        return;
    };
    let id = uuid();
    let generation = state.generation.get_untracked();
    state.controller.set_value(Some(controller.clone()));
    state.busy.set(true);
    state.error.set(String::new());
    state.notice.set(String::new());
    state.truncated.set(false);
    state.following.set(true);
    let (messages, sources) = request_context(&history);
    let mut turns = history;
    turns.push(json!({"id":id,"role":"assistant","content":"","status":"pending"}));
    state.turns.set(turns);
    let model = state.model.get_untracked();
    let payload = serde_json::to_string(&ChatRequest {
        external: if model == "external" {
            Some(state.external.get_untracked().unwrap_or(Value::Null))
        } else {
            None
        },
        model,
        messages,
        conversation_id: state.conversation.get_value(),
    })
    .expect("serializable chat request");
    leptos::task::spawn_local(async move {
        let attempt = async {
            let response = gloo_net::http::Request::post("/api/chat")
                .header("Content-Type", "application/json")
                .header("X-Scout-Organization", &ctx.slug.get_value())
                .abort_signal(Some(&controller.signal()))
                .body(payload.to_string())
                .map_err(|_| "Could not reach Scout. Try again.".to_string())?
                .send()
                .await
                .map_err(|_| "Could not reach Scout. Try again.".to_string())?;
            if !response.ok() {
                let value = response.json::<Value>().await.unwrap_or(Value::Null);
                return Err(value["error"]
                    .as_str()
                    .unwrap_or("Could not reach Scout. Try again.")
                    .into());
            }
            consume(response, state, &id, generation, &sources).await
        }
        .await;
        if state.generation.get_untracked() != generation {
            return;
        }
        if controller.signal().aborted() {
            update_turn(state, &id, |t| t["status"] = json!("stopped"));
            state.notice.set("Response stopped.".into());
        } else {
            match attempt {
                Ok(true) => {
                    let has_content = state.turns.with_untracked(|turns| {
                        turns
                            .iter()
                            .find(|t| t["id"].as_str() == Some(&id))
                            .is_some_and(|t| {
                                !string(t, "content").trim().is_empty()
                                    || t["files"].as_array().is_some_and(|f| !f.is_empty())
                            })
                    });
                    if has_content {
                        update_turn(state, &id, |t| t["status"] = json!("complete"));
                        state.notice.set("Response received.".into());
                        ctx.refresh();
                    } else {
                        update_turn(state, &id, |t| t["status"] = json!("error"));
                        state
                            .error
                            .set("The response was interrupted. Try again.".into());
                    }
                }
                Ok(false) => {
                    update_turn(state, &id, |t| t["status"] = json!("error"));
                    state
                        .error
                        .set("The response was interrupted. Try again.".into());
                }
                Err(message) => {
                    update_turn(state, &id, |t| t["status"] = json!("error"));
                    state.error.set(message);
                }
            }
        }
        state.controller.set_value(None);
        state.busy.set(false);
    });
}
fn new_chat(state: ChatState) {
    if let Some(c) = state.controller.get_value() {
        c.abort()
    }
    state.controller.set_value(None);
    state.generation.update(|v| *v += 1);
    state.conversation.set_value(uuid().replace('-', ""));
    state.turns.set(Vec::new());
    state.draft.set(String::new());
    state.busy.set(false);
    state.error.set(String::new());
    state.notice.set(String::new());
    state.truncated.set(false);
    state.following.set(true);
    focus("scout-chat-input");
}
fn send(ctx: WorkspaceContext, state: ChatState) {
    let content = state.draft.get_untracked().trim().to_owned();
    if content.is_empty() || state.busy.get_untracked() || content.encode_utf16().count() > 16000 {
        return;
    }
    let mut history = state.turns.get_untracked();
    history.push(json!({"id":uuid(),"role":"user","content":content,"status":"complete"}));
    state.draft.set(String::new());
    reply(ctx, state, history);
    focus("scout-chat-input");
}
fn scroll_latest() {
    if let Some(e) = web_sys::window()
        .and_then(|w| w.document())
        .and_then(|d| d.get_element_by_id("scout-chat-conversation"))
        .and_then(|e| e.dyn_into::<web_sys::HtmlElement>().ok())
    {
        e.set_scroll_top(e.scroll_height());
    }
}
#[component]
pub fn ChatView() -> impl IntoView {
    let ctx = expect_context::<WorkspaceContext>();
    let state = ChatState {
        turns: RwSignal::new(Vec::new()),
        draft: RwSignal::new(String::new()),
        busy: RwSignal::new(false),
        error: RwSignal::new(String::new()),
        notice: RwSignal::new(String::new()),
        truncated: RwSignal::new(false),
        following: RwSignal::new(true),
        model: RwSignal::new("modelcar-granite-4-0-h-tiny".into()),
        external: RwSignal::new(None),
        conversation: StoredValue::new(String::new()),
        generation: RwSignal::new(0),
        controller: StoredValue::new_local(None),
        copied: RwSignal::new(String::new()),
    };
    provide_context(state);
    let provider_open = RwSignal::new(false);
    let provider = RwSignal::new("openai".to_string());
    let provider_model = RwSignal::new("gpt-4.1-mini".to_string());
    let key = RwSignal::new(String::new());
    let provider_error = RwSignal::new(String::new());
    let persist = RwSignal::new(false);
    Effect::new(move |_| {
        state.conversation.set_value(uuid().replace('-', ""));
        leptos::task::spawn_local(async move {
            if let Ok((200, value)) =
                json_request("/api/chat", "GET", None, Some(&ctx.slug.get_value())).await
            {
                if value["external"].is_object() {
                    state.external.set(Some(value["external"].clone()));
                }
            }
        });
    });
    Effect::new(move |_| {
        let _ = state.turns.get();
        if state.following.get() && ctx.route.with(|r| r.view == "chat") {
            scroll_latest();
        }
    });
    Effect::new(move |_| {
        let _ = state.draft.get();
        if let Some(input) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-chat-input"))
            .and_then(|e| e.dyn_into::<web_sys::HtmlTextAreaElement>().ok())
        {
            let _ = web_sys::HtmlElement::style(&input).set_property("height", "auto");
            let _ = web_sys::HtmlElement::style(&input)
                .set_property("height", &format!("{}px", input.scroll_height().min(160)));
        }
    });
    Effect::new(move |_| {
        let open = provider_open.get();
        if let Some(dialog) = web_sys::window()
            .and_then(|w| w.document())
            .and_then(|d| d.get_element_by_id("scout-external-dialog"))
            .and_then(|e| e.dyn_into::<web_sys::HtmlDialogElement>().ok())
        {
            if open {
                let _ = dialog.show_modal();
            } else {
                dialog.close();
            }
        }
    });
    Effect::new(move |_| {
        leptos::task::spawn_local(async move {
            let mut acknowledged = std::collections::HashSet::<i64>::new();
            loop {
                gloo_timers::future::TimeoutFuture::new(4000).await;
                if web_sys::window()
                    .and_then(|w| w.document())
                    .is_none_or(|d| d.hidden())
                {
                    continue;
                }
                let turns = state.turns.get_untracked();
                let watched = turns
                    .iter()
                    .rev()
                    .find(|t| t["awaiting"] == true)
                    .and_then(|t| t["taskId"].as_str())
                    .map(str::to_owned);
                if let Some(task) = watched {
                    if let Ok((200, detail)) = json_request(
                        &format!("/api/tasks/{task}"),
                        "GET",
                        None,
                        Some(&ctx.slug.get_value()),
                    )
                    .await
                    {
                        let known: std::collections::HashSet<_> = turns
                            .iter()
                            .flat_map(|t| t["followups"].as_array().cloned().unwrap_or_default())
                            .filter_map(|v| v["id"].as_i64())
                            .collect();
                        let mut fresh:Vec<_>=detail["steps"].as_array().cloned().unwrap_or_default().into_iter().filter(|s|s["kind"]=="followup"&&!string(s,"text").trim().is_empty()&&s["id"].as_i64().is_some_and(|id|!known.contains(&id))).map(|s|json!({"id":s["id"],"text":string(&s,"text").chars().take(16000).collect::<String>()})).collect();
                        fresh.sort_by_key(|s| s["id"].as_i64());
                        if !fresh.is_empty() {
                            state.turns.update(|turns| {
                                if let Some(target) =
                                    turns.iter_mut().rev().find(|t| t["awaiting"] == true)
                                {
                                    let mut updates =
                                        target["followups"].as_array().cloned().unwrap_or_default();
                                    updates.extend(fresh);
                                    target["followups"] = json!(updates);
                                }
                            });
                        }
                    }
                }
                if ctx.route.with_untracked(|r| r.view == "chat") {
                    for turn in state.turns.get_untracked() {
                        if let Some(task) = turn["taskId"].as_str() {
                            let ids: Vec<_> = turn["followups"]
                                .as_array()
                                .cloned()
                                .unwrap_or_default()
                                .iter()
                                .filter_map(|s| s["id"].as_i64())
                                .filter(|id| !acknowledged.contains(id))
                                .take(50)
                                .collect();
                            if !ids.is_empty() {
                                if let Ok((status, _)) = json_request(
                                    "/api/chat/ack",
                                    "POST",
                                    Some(&json!({"taskId":task,"stepIds":ids})),
                                    Some(&ctx.slug.get_value()),
                                )
                                .await
                                {
                                    if status < 400 {
                                        acknowledged.extend(ids);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        });
    });
    view! {<section id="scout-llm-chat" aria-label="Scout chat" class="flex h-full min-h-0 min-w-0 flex-1 flex-col"><div id="scout-chat-actions" class="flex shrink-0 justify-end"><button id="scout-chat-new" type="button" class="flex h-9 items-center gap-1.5 rounded-md px-3 text-sm text-muted-foreground hover:bg-accent" disabled=move||state.turns.with(|t|t.is_empty())&&state.draft.get().is_empty()&&!state.busy.get() on:click=move|_|new_chat(state)><Icon name="plus"/>"New chat"</button></div>
    <div id="scout-chat-conversation" role="region" aria-label="Conversation" tabindex="0" class="min-h-0 flex-1 overflow-y-auto overscroll-contain outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset" on:scroll=move|e|{if let Some(element)=e.current_target().and_then(|v|v.dyn_into::<web_sys::HtmlElement>().ok()){state.following.set(element.scroll_height()-element.scroll_top()-element.client_height()<80);}}>
      <Show when=move||state.turns.with(|t|t.is_empty()) fallback=move||view!{<h1 id="scout-chat-title" class="sr-only">"Scout chat"</h1><ol id="scout-chat-messages" class="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6"><For each=move||state.turns.with(|v|v.iter().map(|t|string(t,"id")).collect::<Vec<_>>()) key=|id|id.clone() children=move|id|view!{<TurnView id/>}/></ol>}>
        <div id="scout-chat-empty" class="flex min-h-full flex-col items-center justify-center gap-5 px-4 py-6 text-center"><ScoutMark size=40/><div class="space-y-1.5"><h1 id="scout-chat-title" class="text-2xl font-medium tracking-tight">"Ask Scout"</h1><p class="max-w-md text-sm text-muted-foreground">"Scout answers with its tools: missing invoices, cash forecast, tasks, costs, email and Slack."</p></div><div id="scout-chat-suggestions" class="flex max-w-xl flex-wrap justify-center gap-2">{["Which payments are missing invoices?","What does the cash forecast look like for the next 90 days?","Which tasks are waiting on me?","What did we spend on beverages this quarter?","How do I use this dashboard?"].into_iter().enumerate().map(move|(i,text)|view!{<button id=format!("scout-chat-suggestion-{i}") type="button" class="h-auto max-w-full rounded-full border bg-background px-3 py-1.5 text-xs font-normal whitespace-normal text-muted-foreground hover:bg-accent hover:text-foreground" on:click=move|_|{state.draft.set(text.into());focus("scout-chat-input");}>{text}</button>}).collect_view()}</div></div>
      </Show>
    </div>
    <div id="scout-chat-input-area" class="shrink-0 pt-4 pb-1"><div class="mx-auto w-full max-w-3xl space-y-2"><Show when=move||!state.following.get()&&!state.turns.with(|t|t.is_empty())><div class="-mt-12 flex justify-center"><button id="scout-chat-latest" type="button" class=button_classes(OUTLINE,"h-8 rounded-full") on:click=move|_|{state.following.set(true);scroll_latest();}>"Latest"</button></div></Show><Show when=move||!state.error.get().is_empty()><p id="scout-chat-error" role="alert" class="text-sm leading-6 text-danger">{move||state.error.get()}</p></Show><Show when=move||!state.busy.get()&&state.turns.with(|t|t.last().is_some_and(|v|v["role"]=="assistant"&&matches!(v["status"].as_str(),Some("error"|"stopped"))))><button id="scout-chat-retry" type="button" class="h-8 text-sm text-muted-foreground" on:click=move|_|{let mut history=state.turns.get_untracked();history.pop();reply(ctx,state,history);}>"Try again"</button></Show><Show when=move||state.truncated.get()><p role="status" class="text-xs text-muted-foreground">"Response reached its length limit. Send a follow-up to continue."</p></Show>
      <form id="scout-chat-composer" class="rounded-xl border bg-background p-1.5 shadow-xs focus-within:border-ring" on:submit=move|e|{e.prevent_default();send(ctx,state);}>
        <label class="sr-only" for="scout-chat-input">"Message Scout"</label><div class="flex items-end gap-2"><textarea id="scout-chat-input" rows="1" prop:value=move||state.draft.get() maxlength="16000" placeholder="Ask Scout, e.g. which payments are missing invoices?" class="field-sizing-content max-h-40 min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none focus-visible:ring-0" on:input=move|e|state.draft.set(event_target_value(&e)) on:keydown=move|e|{if e.key()=="Enter"&&!e.shift_key()&&!e.is_composing(){e.prevent_default();send(ctx,state);}}/><Show when=move||state.busy.get() fallback=move||view!{<button id="scout-chat-send" type="submit" class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-50" disabled=move||state.draft.get().trim().is_empty() aria-label="Send message"><Icon name="send"/></button>}><button id="scout-chat-stop" type="button" class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-secondary" aria-label="Stop response" on:click=move|_|{if let Some(c)=state.controller.get_value(){c.abort();}}><Icon name="stop"/></button></Show></div>
      </form>
      <div class="flex min-w-0 items-center justify-between gap-3"><details class="relative min-w-0"><summary id="scout-chat-model" aria-label="Select model" class="flex h-7 min-w-0 cursor-pointer list-none items-center gap-1.5 rounded px-2 text-[11px] text-muted-foreground hover:bg-accent"><span class="truncate">{move||if state.model.get()=="external"{state.external.get().map(|v|format!("{} · {}",string(&v,"provider"),string(&v,"model"))).unwrap_or_default()}else{"Granite 4.0 H Tiny".into()}}</span><Icon name="chevron"/></summary><div id="scout-chat-model-menu" class="absolute bottom-9 left-0 z-30 min-w-56 rounded-md border bg-popover p-1 shadow-md"><p class="px-2 py-1 text-xs text-muted-foreground">"Available models"</p><button id="scout-chat-model-modelcar-granite-4-0-h-tiny" type="button" class="min-h-11 w-full rounded px-2 text-left text-xs hover:bg-accent" disabled=move||state.busy.get() on:click=move|_|{new_chat(state);state.model.set("modelcar-granite-4-0-h-tiny".into());}>"Granite 4.0 H Tiny · On premise"</button><Show when=move||state.external.get().is_some()><button id="scout-chat-model-external" type="button" class="min-h-11 w-full rounded px-2 text-left text-xs hover:bg-accent" disabled=move||state.busy.get() on:click=move|_|{new_chat(state);state.model.set("external".into());}>{move||state.external.get().map(|v|format!("{} · {}",string(&v,"provider"),string(&v,"model"))).unwrap_or_default()}</button></Show><button id="scout-chat-configure-provider" type="button" class="min-h-11 w-full rounded border-t px-2 text-left text-xs hover:bg-accent" on:click=move|_|{if let Some(config)=state.external.get(){if config["apiKey"]!="__scout_server_provider__"{provider.set(string(&config,"provider"));provider_model.set(string(&config,"model"));key.set(string(&config,"apiKey"));}}provider_open.set(true);}>{move||if state.external.get().is_some(){"Manage external provider…"}else{"Set up external provider…"}}</button></div></details><p class="truncate text-[11px] text-muted-foreground"><Show when=move||state.busy.get() fallback=||view!{<span class="hidden sm:inline">"Enter to send · Shift + Enter for a new line · "</span>"Stays in this tab"}>"Scout is working…"</Show></p></div><Show when=move||state.model.get()=="external"><p id="scout-chat-external-notice" class="text-xs leading-5 text-muted-foreground">{move||format!("{} answers with Scout's tools: your conversation and the company data those tools return are sent to this provider.",state.external.get().map(|v|string(&v,"provider")).unwrap_or_default())}</p></Show><p role="status" class="sr-only">{move||state.notice.get()}</p>
    </div></div>
    <dialog id="scout-external-dialog" class="fixed inset-0 m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg space-y-4 overflow-y-auto rounded-xl border bg-popover p-6 text-popover-foreground shadow-xl backdrop:bg-foreground/20" on:close=move|_|provider_open.set(false)><header><h2 id="scout-external-title" class="text-lg font-semibold">"External LLM provider"</h2><p id="scout-external-description" class="mt-2 text-sm text-muted-foreground">"Use your own API key to research hiring markets and ask Granite to run private forecasts."</p></header><form id="scout-external-form" class="space-y-4" on:submit=move|e|{e.prevent_default();let m=provider_model.get().trim().to_owned();let k=key.get().trim().to_owned();if m.is_empty()||m.len()>150||!m.bytes().enumerate().all(|(i,c)|c.is_ascii_alphanumeric()||(i>0&&b"._:/-".contains(&c))){provider_error.set("Enter a valid model ID (up to 150 characters).".into());return}if !(8..=512).contains(&k.len())||!k.bytes().all(|b|(0x21..=0x7e).contains(&b)){provider_error.set("Enter an API key (8–512 characters, without spaces).".into());return}let config=json!({"provider":provider.get(),"model":m,"apiKey":k});leptos::task::spawn_local(async move{if persist.get(){match json_request("/api/strategy/settings","PUT",Some(&json!({"external":config})),Some(&ctx.slug.get_value())).await{Ok((status,_))if status<400=>(),Ok((_,body))=>{provider_error.set(body["error"].as_str().unwrap_or("Could not save strategy provider.").into());return},Err(message)=>{provider_error.set(message);return}}}new_chat(state);state.external.set(Some(config));state.model.set("external".into());provider_open.set(false);state.notice.set("Provider configured for this tab. A new chat has started.".into());});}><div class="space-y-1.5"><label for="scout-external-provider" class="text-sm font-medium">"Provider"</label><select id="scout-external-provider" class=INPUT prop:value=move||provider.get() on:change=move|e|{let p=event_target_value(&e);provider_model.set(match p.as_str(){"mistral"=>"mistral-small-latest","groq"=>"llama-3.3-70b-versatile",_=>"gpt-4.1-mini"}.into());provider.set(p);key.set(String::new());provider_error.set(String::new());}><option value="openai">"OpenAI"</option><option value="mistral">"Mistral"</option><option value="groq">"Groq"</option></select></div><div class="space-y-1.5"><label for="scout-external-model" class="text-sm font-medium">"Model ID"</label><input id="scout-external-model" class=INPUT prop:value=move||provider_model.get() on:input=move|e|provider_model.set(event_target_value(&e)) required maxlength="150"/><p class="text-xs text-muted-foreground">"Choose a model available to your account that supports tool calls. Public hiring research is available with OpenAI and Mistral."</p></div><div class="space-y-1.5"><label for="scout-external-key" class="text-sm font-medium">"API key"</label><input id="scout-external-key" class=INPUT type="password" autocomplete="off" spellcheck="false" prop:value=move||key.get() on:input=move|e|key.set(event_target_value(&e)) required maxlength="512"/><p class="text-xs text-muted-foreground">"Kept in this tab unless you save it for Strategy below. Saved Strategy keys are encrypted on the server and survive reloads. Removing the chat provider only clears this tab."</p></div><p id="scout-external-privacy" class="rounded-md border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">"Your public conversation and cited research are shared with the provider so follow-up messages keep their context. Do not paste private company data. Invoices and Granite's results stay private. Hiring proposals create simulated Liquidity actions on premise, without changing company records or hiring anyone."</p><label class="flex items-start gap-2 text-sm"><input type="checkbox" prop:checked=move||persist.get() on:change=move|e|persist.set(event_target_checked(&e))/><span>"Save for daily strategy briefings in this workspace"</span></label><Show when=move||!provider_error.get().is_empty()><p id="scout-external-error" role="alert" class="text-sm text-danger">{move||provider_error.get()}</p></Show><div class="flex flex-wrap justify-end gap-2"><button type="button" class=OUTLINE on:click=move|_|{new_chat(state);state.external.set(None);state.model.set("modelcar-granite-4-0-h-tiny".into());provider_open.set(false);}>"Remove provider"</button><button type="button" class=OUTLINE on:click=move|_|provider_open.set(false)>"Cancel"</button><button type="submit" class=PRIMARY>"Use provider"</button></div></form></dialog>
    </section>}
}
#[component]
fn TurnView(id: String) -> impl IntoView {
    let state = expect_context::<ChatState>();
    let id = StoredValue::new(id);
    let expanded = RwSignal::new(false);
    view! {<li id=format!("scout-chat-message-{}",id.get_value())>{move||{let turn=state.turns.with(|t|t.iter().find(|t|t["id"].as_str()==Some(&id.get_value())).cloned().unwrap_or(Value::Null));let content=string(&turn,"content");if turn["kind"]=="summary"{return view!{<details class="space-y-2"><summary class="cursor-pointer text-center text-[11px] text-muted-foreground">{format!("Earlier messages summarized · {} messages",turn["replaced"].as_u64().unwrap_or(0))}</summary><div class="rounded-lg border bg-muted/40 px-4 py-3 text-[13px] leading-6" inner_html=crate::markdown::render(&content)/></details>}.into_any()}if turn["role"]=="user"{let long=content.encode_utf16().count()>600||content.split('\n').count()>8;return view!{<div class="flex justify-end"><div class="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-6 text-primary-foreground"><div class=if long&&!expanded.get(){"whitespace-pre-wrap line-clamp-6 [overflow-wrap:anywhere]"}else{"whitespace-pre-wrap [overflow-wrap:anywhere]"}><span class="sr-only">"You: "</span>{content.clone()}</div>{long.then(||view!{<button type="button" class="mt-1 text-xs underline underline-offset-2" aria-expanded=move||expanded.get().to_string() on:click=move|_|expanded.update(|v|*v=!*v)>{move||if expanded.get(){"Show less".into()}else{format!("Show all · {} characters",content.encode_utf16().count())}}</button>})}</div></div>}.into_any()}
    let pending=turn["status"]=="pending";let steps=turn["steps"].as_array().cloned().unwrap_or_default();let files=turn["files"].as_array().cloned().unwrap_or_default();let copy_content=content.clone();view!{<div class="flex min-w-0 gap-3"><ScoutMark size=26/><div class="min-w-0 flex-1 space-y-2"><div class="flex items-center gap-2 text-xs text-muted-foreground"><span class="font-medium text-foreground">"Scout"</span>{(turn["status"]=="stopped").then_some("· Stopped")}{(turn["status"]=="error").then(||view!{<span class="text-danger">"· Interrupted"</span>})}</div>{(!steps.is_empty()).then(||view!{<details open=pending class="group rounded-lg border bg-background/60 text-[12px]"><summary class="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-muted-foreground"><Icon name="refresh"/><span>{if pending&&content.is_empty(){"Using tools".into()}else{format!("Used {} {}",steps.len(),if steps.len()==1{"tool"}else{"tools"})}}</span><Icon name="chevron"/></summary><ul aria-label="Scout tool calls" class="space-y-1 border-t px-3 py-2">{steps.into_iter().map(|step|view!{<li class="flex items-start gap-2 font-mono leading-5 text-muted-foreground"><Icon name="check"/><span class="min-w-0 break-words">{step.as_str().unwrap_or("").to_owned()}</span></li>}).collect_view()}</ul></details>})}{files.into_iter().map(|file|view!{<FileChip file/>}).collect_view()}{if !content.is_empty(){view!{<div class="min-w-0 text-sm leading-6 [overflow-wrap:anywhere]" inner_html=crate::markdown::render(&content)/>}.into_any()}else if pending{view!{<span class="inline-flex items-center gap-2 text-sm text-muted-foreground"><Icon name="refresh"/>"Working…"</span>}.into_any()}else{().into_any()}}
    {turn["followups"].as_array().cloned().unwrap_or_default().into_iter().map(|update|view!{<div class="rounded-lg border border-success-border bg-success-muted px-3.5 py-2.5"><p class="mb-1 text-[11px] font-medium tracking-wide text-success uppercase">"Update"</p><div class="text-sm leading-6" inner_html=crate::markdown::render(&string(&update,"text"))/></div>}).collect_view()}
    {turn["awaiting"].as_bool().unwrap_or(false).then(||view!{<p class="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Icon name="refresh"/>"Scout posts updates here as they happen"</p>})}
    {turn["research"].is_object().then(||{let research=turn["research"].clone();view!{<aside aria-label="Public hiring market research" class="space-y-2 rounded-lg border bg-background/60 p-3"><details><summary class="cursor-pointer text-xs font-medium">"Public hiring research"</summary><div class="pt-2 text-sm leading-6" inner_html=crate::markdown::render(&string(&research,"summary"))/></details><ol aria-label="Public research sources" class="space-y-1 border-t pt-2">{research["sources"].as_array().cloned().unwrap_or_default().into_iter().enumerate().filter(|(_,s)|s["url"].as_str().is_some_and(|u|u.starts_with("https://")||u.starts_with("http://"))).map(|(i,s)|view!{<li><a href=string(&s,"url") target="_blank" rel="noopener noreferrer" class="inline-flex min-h-8 items-center gap-1.5 text-xs text-muted-foreground underline">{format!("{}. {}",i+1,string(&s,"title"))}<Icon name="link"/></a></li>}).collect_view()}</ol><p class="text-xs leading-5 text-muted-foreground">"Market estimates depend on role, location and seniority. Hiring scenarios use monthly gross EUR per person and show employer-cost assumptions."</p></aside>}})}
    <div class="flex items-center gap-1">{(!content.is_empty()&&!pending).then(||view!{<button type="button" aria-label="Copy response" class="h-7 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted" on:click=move|_|{let content=copy_content.clone();leptos::task::spawn_local(async move{if let Some(w)=web_sys::window(){let copied=JsFuture::from(w.navigator().clipboard().write_text(&content)).await.is_ok();state.notice.set(if copied{"Response copied."}else{"Could not copy the response."}.into());if copied{state.copied.set(id.get_value());}}});}>"Copy"</button>})}{turn["taskId"].as_str().map(|task|view!{<a href=format!("#task/{task}") class="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted">{format!("Open {task}")}<Icon name="link"/></a>})}</div></div></div>}.into_any()}}</li>}
}
#[component]
fn FileChip(file: Value) -> impl IntoView {
    let name = string(&file, "name");
    let filename = name.clone();
    let mime = string(&file, "mime");
    let data = string(&file, "dataB64");
    let size =
        data.len() * 3 / 4 - usize::from(data.ends_with('=')) - usize::from(data.ends_with("=="));
    let slack = file["sentToSlack"].as_bool().unwrap_or(false);
    view! {<div class="space-y-1.5"><div data-chat-file=name.clone() class="flex w-72 max-w-full items-center gap-3 rounded-lg border bg-background px-3 py-2"><span class="flex size-8 shrink-0 items-center justify-center rounded-md border text-muted-foreground"><Icon name="file"/></span><span class="min-w-0 flex-1"><span class="block truncate text-[13px] font-medium" title=name.clone()>{name.clone()}</span><span class="block text-[11px] text-muted-foreground">{format!("{} · {}",mime.split('/').next_back().unwrap_or(""),if size<1024{format!("{size} B")}else{format!("{:.1} KB",size as f64/1024.0)})}</span></span><button type="button" aria-label=format!("Download {filename}") class="flex size-8 shrink-0 items-center justify-center rounded-md hover:bg-accent" on:click=move|_|{let Some(w)=web_sys::window()else{return};let Ok(decoded)=w.atob(&data)else{return};let bytes:Vec<_>=decoded.chars().map(|c|c as u8).collect();let array=js_sys::Uint8Array::from(bytes.as_slice());let sequence=js_sys::Array::of1(&array);let options=web_sys::BlobPropertyBag::new();options.set_type(&mime);let Ok(blob)=web_sys::Blob::new_with_u8_array_sequence_and_options(&sequence,&options)else{return};let Ok(url)=web_sys::Url::create_object_url_with_blob(&blob)else{return};if let Some(document)=w.document(){if let Ok(link)=document.create_element("a").and_then(|e|e.dyn_into::<web_sys::HtmlAnchorElement>().map_err(Into::into)){link.set_href(&url);link.set_download(&filename);link.set_rel("noopener");if let Some(body)=document.body(){let _=body.append_child(&link);link.click();link.remove();}}}let revoke=wasm_bindgen::closure::Closure::once_into_js(move||{let _=web_sys::Url::revoke_object_url(&url);});let _=w.set_timeout_with_callback_and_timeout_and_arguments_0(revoke.unchecked_ref(),1000);} ><Icon name="send"/></button></div>{slack.then(||view!{<p class="flex items-center gap-1.5 pl-1 text-[11px] text-muted-foreground"><Icon name="send"/>"Also sent to your Slack DM"</p>})}</div>}
}
