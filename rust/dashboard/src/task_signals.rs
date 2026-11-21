use crate::workspace::{WorkspaceContext, string, tasks};
use leptos::prelude::*;
use serde_json::Value;
use std::collections::BTreeMap;
pub fn needs_reaction(t: &Value) -> bool {
    matches!(t["status"].as_str(), Some("waiting" | "escalated"))
        || t["pendingApprovalFor"]
            .as_str()
            .is_some_and(|s| !s.is_empty())
            && !matches!(t["status"].as_str(), Some("done" | "cancelled"))
}
pub fn unseen(t: &Value, seen: &BTreeMap<String, String>) -> bool {
    t["status"] == "done"
        && seen.get(&string(t, "id")).is_none_or(|s| {
            s.as_str()
                < t["closedAt"]
                    .as_str()
                    .unwrap_or(t["updatedAt"].as_str().unwrap_or(""))
        })
}
pub fn loop_signal(
    tasks: &[Value],
    name: &str,
    seen: &BTreeMap<String, String>,
) -> Option<(&'static str, &'static str)> {
    let own = tasks
        .iter()
        .filter(|t| t["loop"] == name)
        .collect::<Vec<_>>();
    if own.iter().any(|t| needs_reaction(t)) {
        Some(("attention", "Needs your reaction"))
    } else if own.iter().any(|t| unseen(t, seen)) {
        Some(("result", "New result to review"))
    } else if own.iter().any(|t| t["status"] == "in_progress") {
        Some(("running", "Working"))
    } else {
        None
    }
}
fn scope(ctx: WorkspaceContext) -> String {
    ctx.data.with_untracked(|v| {
        format!(
            "{}:{}:scout-seen-tasks",
            ctx.slug.get_value(),
            string(&v["workspace"], "userId")
        )
    })
}
pub fn baseline(data: &Value) -> BTreeMap<String, String> {
    tasks(data)
        .iter()
        .filter(|t| t["status"] == "done")
        .map(|t| {
            (
                string(t, "id"),
                t["closedAt"]
                    .as_str()
                    .unwrap_or(t["updatedAt"].as_str().unwrap_or(""))
                    .into(),
            )
        })
        .collect()
}
pub fn mark_seen(ctx: WorkspaceContext, t: &Value) {
    let id = string(t, "id");
    let stamp = string(t, "updatedAt").max(string(t, "closedAt"));
    if ctx
        .seen
        .with_untracked(|s| s.get(&id).is_some_and(|v| v >= &stamp))
    {
        return;
    }
    ctx.seen.update(|s| {
        s.insert(id, stamp);
        let mut entries = s
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect::<Vec<_>>();
        entries.sort_by(|a, b| b.1.cmp(&a.1));
        entries.truncate(500);
        *s = entries.into_iter().collect();
    });
    save(ctx);
}
pub fn save(ctx: WorkspaceContext) {
    if let Some(storage) = web_sys::window().and_then(|w| w.local_storage().ok().flatten()) {
        let _ = storage.set_item(
            &scope(ctx),
            &serde_json::to_string(&ctx.seen.get_untracked()).unwrap_or_default(),
        );
    }
}
pub fn load(ctx: WorkspaceContext) {
    if let Some(storage) = web_sys::window().and_then(|w| w.local_storage().ok().flatten()) {
        if let Ok(Some(raw)) = storage.get_item(&scope(ctx)) {
            if let Ok(value) = serde_json::from_str(&raw) {
                ctx.seen.set(value)
            }
        } else {
            save(ctx)
        }
    }
}
pub fn storage_key(ctx: WorkspaceContext) -> String {
    scope(ctx)
}
