const ENCODE_COMPONENT: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'_')
    .remove(b'.')
    .remove(b'!')
    .remove(b'~')
    .remove(b'*')
    .remove(b'\'')
    .remove(b'(')
    .remove(b')');
use std::collections::BTreeMap;
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Route {
    pub view: String,
    pub task_id: Option<String>,
    pub filters: BTreeMap<String, String>,
}
impl Route {
    pub fn view(view: &str) -> Self {
        Self {
            view: view.into(),
            ..Default::default()
        }
    }
}
pub const NAV: &[(&str, &str, &str, &str)] = &[
    ("primary", "overview", "Overview", "layout"),
    ("primary", "chat", "Scout chat", "chat"),
    ("primary", "tasks", "Tasks", "tasks"),
    ("primary", "transactions", "Transactions", "transactions"),
    ("Loops", "loop-invoicing", "Invoicing", "refresh"),
    ("Loops", "loop-cost-control", "Cost control", "refresh"),
    ("Loops", "loop-liquidity", "Liquidity", "refresh"),
    ("Loops", "loop-strategy", "Strategy", "refresh"),
    ("Loops", "loop-chat", "Chat", "refresh"),
    ("Workspace", "connections", "Connections", "connections"),
    ("Workspace", "audit", "Audit log", "audit"),
    ("Workspace", "policies", "Access policies", "policies"),
    ("Workspace", "llm-usage", "LLM usage", "usage"),
];
pub fn route_from_hash(hash: &str) -> Route {
    let raw = hash.strip_prefix('#').unwrap_or(hash);
    if raw.is_empty() {
        return Route::view("chat");
    }
    let mut parts = raw.split('?');
    let path = parts.next().unwrap_or("");
    let query = parts.next().unwrap_or("");
    let mut route = Route::view(path);
    if let Some(id) = path.strip_prefix("task/").filter(|s| !s.is_empty()) {
        route.view = "tasks".into();
        route.task_id = Some(
            percent_encoding::percent_decode_str(id)
                .decode_utf8_lossy()
                .into_owned(),
        );
        return route;
    }
    if path == "inbox" || path == "my-tasks" {
        route.view = "tasks".into();
        route.filters.insert(
            "scope".into(),
            if path == "inbox" { "inbox" } else { "mine" }.into(),
        );
        return route;
    }
    if let Some(name) = path.strip_prefix("loop-") {
        route.view = "tasks".into();
        route.filters.insert("loop".into(), name.replace('-', "_"));
        return route;
    }
    if path == "investigations" || path == "agent-jobs" || path.starts_with("agent-jobs/") {
        return Route::view("tasks");
    }
    if path == "llm" {
        return Route::view("chat");
    }
    if path == "setup" {
        let step = url::form_urlencoded::parse(query.as_bytes())
            .find(|(k, _)| k == "step")
            .map(|(_, v)| v.into_owned());
        if let Some(step) = step.filter(|s| {
            ["bank", "invoicing", "ksef", "email", "slack", "network"].contains(&s.as_str())
        }) {
            route.filters.insert("step".into(), step);
        }
        return route;
    }
    if path == "tasks" {
        for key in ["scope", "loop", "status", "owner"] {
            if let Some((_, v)) =
                url::form_urlencoded::parse(query.as_bytes()).find(|(k, _)| k == key)
            {
                if !v.is_empty() && (key != "scope" || matches!(v.as_ref(), "inbox" | "mine")) {
                    route.filters.insert(key.into(), v.into_owned());
                }
            }
        }
        return route;
    }
    if ![
        "overview",
        "chat",
        "transactions",
        "staffing",
        "connections",
        "audit",
        "policies",
        "llm-usage",
        "demo",
    ]
    .contains(&path)
    {
        return Route::view("overview");
    }
    route
}
pub fn hash_from_route(route: &Route) -> String {
    if let Some(id) = &route.task_id {
        return format!(
            "#task/{}",
            percent_encoding::utf8_percent_encode(id, ENCODE_COMPONENT)
        );
    }
    let mut query = url::form_urlencoded::Serializer::new(String::new());
    for key in if route.view == "setup" {
        vec!["scope", "loop", "status", "owner", "step"]
    } else if route.view == "tasks" {
        vec!["scope", "loop", "status", "owner"]
    } else {
        vec![]
    } {
        if let Some(value) = route.filters.get(key).filter(|s| !s.is_empty()) {
            query.append_pair(key, value);
        }
    }
    let query = query.finish();
    format!(
        "#{}{}{}",
        route.view,
        if query.is_empty() { "" } else { "?" },
        query
    )
}
pub fn view_label(view: &str) -> &str {
    match view {
        "overview" => "Overview",
        "tasks" => "Tasks",
        "chat" => "Scout chat",
        "transactions" => "Transactions",
        "staffing" => "Staffing",
        "connections" => "Connections",
        "audit" => "Audit log",
        "policies" => "Access policies",
        "llm-usage" => "LLM usage",
        "demo" => "Demo",
        "setup" => "Setup",
        _ => "Overview",
    }
}
pub fn loop_label(name: &str) -> &str {
    match name {
        "invoicing" => "Invoicing",
        "cost_control" => "Cost control",
        "liquidity" => "Liquidity",
        "strategy" => "Strategy",
        "chat" => "Chat",
        "expenses" => "Expenses",
        _ => name,
    }
}
pub fn status_label(status: &str) -> &str {
    match status {
        "todo" => "Todo",
        "in_progress" => "In progress",
        "waiting" => "Waiting",
        "escalated" => "Escalated",
        "done" => "Done",
        "cancelled" => "Cancelled",
        _ => status,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hash_aliases_and_filters() {
        assert_eq!(route_from_hash("").view, "chat");
        assert_eq!(route_from_hash("#llm").view, "chat");
        assert_eq!(route_from_hash("#forecast").view, "overview");
        assert_eq!(
            route_from_hash("#loop-cost-control").filters["loop"],
            "cost_control"
        );
        let r = route_from_hash("#tasks?loop=invoicing&owner=ceo");
        assert_eq!(hash_from_route(&r), "#tasks?loop=invoicing&owner=ceo");
    }
}
