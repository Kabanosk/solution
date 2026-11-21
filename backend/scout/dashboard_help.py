"""Small, public UI instructions, retrieved one topic at a time (not added to the system prompt)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_GUIDES: dict[str, dict[str, Any]] = {
    "getting_started": {
        "page": "Scout chat", "href": "/#chat",
        "steps": [
            "Scout chat is the default landing page. Ask a question or select a suggested prompt, then press Enter to send.",
            "Use Overview for cash and forecasts, Tasks for work and approvals, and Transactions for bank records.",
            "The sidebar's Workspace section contains Connections, Audit log, Access policies and LLM usage. Use the top-bar search to find pages, tasks or people.",
        ],
        "related": [{"label": "Overview", "href": "/#overview"}, {"label": "Tasks", "href": "/#tasks"}],
    },
    "chat": {
        "page": "Scout chat", "href": "/#chat",
        "steps": [
            "Choose a suggested prompt or type a question about invoices, costs, tasks or cash forecasts. Enter sends; Shift+Enter adds a line.",
            "Tool steps explain what Scout looked up. Stop cancels a response; Try again retries an interrupted answer.",
            "New chat clears this conversation. Switching pages in this tab preserves it; reloading clears it. Ask 'how do I use the dashboard?' for navigation help.",
        ],
    },
    "overview": {
        "page": "Overview", "href": "/#overview",
        "steps": [
            "Review cash, items needing attention, the liquidity forecast, safety buffer, team spending and Scout activity.",
            "Use the forecast's date ranges and zoom controls; hover for values. Select an attention item to open its task.",
            "For a purchase or incoming payment scenario, ask Scout a what-if with the amount, month and direction. A forecast is not an executed payment.",
        ],
    },
    "tasks": {
        "page": "Tasks", "href": "/#tasks",
        "steps": [
            "Tasks combines work from all loops. Needs me filters items waiting on you; Mine shows your tasks. Filter further by loop, status or owner.",
            "Select a task to read its activity log, evidence and requested action. Use the available approval or reply controls; statuses come from recorded work.",
            "Sidebar Loops open filtered views of the same task list. To request new work, ask Scout in chat or Slack.",
        ],
    },
    "transactions": {
        "page": "Transactions", "href": "/#transactions",
        "steps": [
            "Review bank bookings and search or filter the records to find a payment.",
            "Open an available invoice to inspect the document. Ask Scout about payments with missing invoices; transaction records are read-only here.",
        ],
    },
    "connections": {
        "page": "Connections", "href": "/#connections",
        "steps": [
            "Each integration row shows its authorized connection status and last sync. Open its chevron for permissions, setup information or historical sync results.",
            "Google mailbox authorization, connecting integrations, enable/disable changes and KSeF sync actions are unavailable in this organization-bound release. Setup information is not a working authorization flow.",
            "Existing KSeF details can show recorded progress, errors and matched invoices. Slack details list recorded granted and missing permissions; these read-only details do not grant new access.",
        ],
    },
    "audit": {
        "page": "Audit log", "href": "/#audit",
        "steps": ["Review the recorded access and action history. Use its search and filters to investigate what happened; task activity logs provide the steps of a particular task."],
        "related": [{"label": "Tasks", "href": "/#tasks"}],
    },
    "policies": {
        "page": "Access policies", "href": "/#policies",
        "steps": ["Read the permissions for each role and which actions need approval. This page is read-only; it is not a permission editor and asking Scout does not bypass access checks."],
    },
    "llm_usage": {
        "page": "LLM usage", "href": "/#llm-usage",
        "steps": [
            "Select 24 h, 7 days or 30 days to inspect requests, tokens, latency and estimated amortized cost. Hover over a chart for values.",
            "The per-action table breaks down attributed usage; Live model shows running work, waiting requests and cache use. Reload usage refreshes the report. These are telemetry, not a billing invoice.",
        ],
    },
}
TOPICS = tuple(_GUIDES)


def dashboard_help(topic: str = "getting_started") -> dict[str, Any]:
    """Return one bounded guide, without accessing private data or executing any action."""
    if not isinstance(topic, str) or topic not in _GUIDES:
        return {"error": "unknown_dashboard_topic", "topics": list(TOPICS)}
    return {"topic": topic, **deepcopy(_GUIDES[topic])}
