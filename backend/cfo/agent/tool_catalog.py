"""The read-only tools the CEO chat may hand to the LLM (T047).

A tool is defined once, in the MCP server (its method signature and docstring). This module turns those
definitions into `ToolSpec`s for `llm.chat(..., tools)`:

* `McpToolBox`: the schemas come from the server's own `tools/list`;
* `InProcessToolBox`: the same schemas are derived from the tool methods with FastMCP's own conversion, so both
  boxes give the model the same catalog.

Only the explicit allowlist `READ_ONLY_TOOLS` is offered: no write tools (`attach_invoice`, `record_decision`) and
nothing from the workstations server (D-004). `ToolCatalog.call` runs a tool for the model and returns JSON text,
trimmed to what a small model handles. Errors come back as `{"error": ...}` text so the model can react.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cfo.agent.tools import COMPANY_DB, HR, ToolBox, ToolError
from cfo.llm import ToolSpec

log = logging.getLogger(__name__)

READ_ONLY_TOOLS: dict[str, tuple[str, ...]] = {
    COMPANY_DB: ("list_transactions", "get_invoice", "list_teams", "team_costs", "list_cost_metrics",
                 "list_decisions", "list_rules"),
    HR: ("org_chart", "who_is_on_vacation", "manager_of"),
}

MAX_RESULT_CHARS = 4000  # about 1,200 tokens per tool result
MAX_DESCRIPTION_CHARS = 400
MAX_LIMIT = 50
DEFAULT_LIMIT = 25

# For tools whose docstring is missing in the server; a docstring there always wins.
_FALLBACK_DESCRIPTIONS = {
    "list_teams": "All teams of the company (id, name, owner, monthly budget).",
    "list_decisions": "The agent's decision history, newest first: what it did or asked, with the reason.",
    "who_is_on_vacation": "Employees who are on vacation on date `on` (YYYY-MM-DD, default today).",
}


@dataclass(frozen=True)
class _Entry:
    spec: ToolSpec
    server: str
    tool: str


class ToolCatalog:
    """`specs` for the model; `call(name, arguments)` for its tool calls."""

    def __init__(self, box: ToolBox, entries: list[_Entry]):
        self._box = box
        self._entries = {e.spec.name: e for e in entries}

    @property
    def specs(self) -> list[ToolSpec]:
        return [e.spec for e in self._entries.values()]

    def source(self, name: str) -> str:
        e = self._entries[name]
        return f"{e.server}.{e.tool}"

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> str:
        entry = self._entries.get(name)
        if entry is None:
            return _dump({"error": f"unknown tool {name!r}; use one of: {', '.join(self._entries)}"})
        try:
            args = _clean_arguments(entry.spec, dict(arguments or {}))
            result = self._box.call(entry.server, entry.tool, **args)
        except Exception as e:  # noqa: BLE001 - the model gets the error and may retry once
            log.warning("chat tool %s failed: %s", name, e)
            return _dump({"error": f"{type(e).__name__}: {str(e)[:300]}"})
        return _fit(_strip_empty(result))


def build_catalog(toolbox: ToolBox, allowlist: Mapping[str, tuple[str, ...]] = READ_ONLY_TOOLS) -> ToolCatalog:
    """The allowlisted tools the servers really offer. Raises when a server cannot be listed."""
    entries: list[_Entry] = []
    for server, names in allowlist.items():
        schemas = _server_schemas(toolbox, server, names)
        for name in names:
            if name not in schemas:
                log.warning("allowlisted tool %s.%s is not offered by the server", server, name)
                continue
            description, parameters = schemas[name]
            description = " ".join((description or "").split()) or _FALLBACK_DESCRIPTIONS.get(name, name)
            entries.append(_Entry(
                ToolSpec(name=name, description=description[:MAX_DESCRIPTION_CHARS], parameters=_simplify(parameters)),
                server, name))
    if not entries:
        raise ToolError("no tools available")
    return ToolCatalog(toolbox, entries)


# -- reading the schemas --------------------------------------------------------------------------------------
def _server_schemas(toolbox: ToolBox, server: str, names: tuple[str, ...]) -> dict[str, tuple[str, dict[str, Any]]]:
    in_process = getattr(toolbox, "_servers", None)
    if isinstance(in_process, dict):  # InProcessToolBox: same conversion FastMCP applies when it registers a tool
        from mcp.server.fastmcp.tools import Tool

        target = in_process.get(server)
        out = {}
        for n in names:
            fn = getattr(target, n, None)
            if callable(fn):
                t = Tool.from_function(fn)
                out[n] = (t.description, t.parameters)
        return out
    endpoints = getattr(toolbox, "_endpoints", None)
    if isinstance(endpoints, dict):  # McpToolBox: ask the server
        if server not in endpoints:
            raise ToolError(f"no endpoint configured for MCP server {server!r}")
        listed = _run_sync(_list_mcp_tools(endpoints[server]))
        return {t.name: (t.description or "", dict(t.inputSchema or {})) for t in listed}
    raise ToolError(f"cannot list tools of {type(toolbox).__name__}")


async def _list_mcp_tools(endpoint: Any) -> list[Any]:
    from mcp import ClientSession

    if isinstance(endpoint, str):
        from mcp.client.streamable_http import streamablehttp_client

        transport = streamablehttp_client(endpoint)
    else:
        from mcp.client.stdio import stdio_client

        transport = stdio_client(endpoint)
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            return list((await session.list_tools()).tools)


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


# -- shaping schemas, arguments and results for a small model ---------------------------------------------------
def _simplify(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Drop pydantic noise (titles, `anyOf [X, null]`, null defaults): fewer tokens, clearer for Granite Tiny."""
    props: dict[str, Any] = {}
    for key, raw in (schema.get("properties") or {}).items():
        prop = {k: v for k, v in raw.items() if k != "title"}
        if "anyOf" in prop:
            options = [o for o in prop.pop("anyOf") if o.get("type") != "null"]
            if len(options) == 1:
                prop.update({k: v for k, v in options[0].items() if k != "title"})
        if prop.get("default") is None:
            prop.pop("default", None)
        props[key] = prop
    out: dict[str, Any] = {"type": "object", "properties": props}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def _clean_arguments(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    props = spec.parameters.get("properties", {})
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key not in props or value is None or value == "":
            continue  # small models invent arguments and send empty ones
        kind = props[key].get("type")
        if kind == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        elif kind == "boolean" and isinstance(value, str):
            value = value.strip().casefold() in {"true", "1", "yes"}
        out[key] = value
    if "limit" in props:
        limit = out.get("limit")
        out["limit"] = DEFAULT_LIMIT if not isinstance(limit, int) else max(1, min(limit, MAX_LIMIT))
    return out


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_empty(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_strip_empty(v) for v in value]
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _fit(value: Any) -> str:
    text = _dump(value)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    if isinstance(value, list):
        shown = len(value)
        while shown > 0 and len(_dump({"rows_shown": shown, "rows_total": len(value), "rows": value[:shown],
                                       "note": "truncated; narrow the query with dates or filters"})) > MAX_RESULT_CHARS:
            shown = shown * 3 // 4 if shown > 4 else shown - 1
        return _dump({"rows_shown": shown, "rows_total": len(value), "rows": value[:shown],
                      "note": "truncated; narrow the query with dates or filters"})
    return text[:MAX_RESULT_CHARS] + f"... [truncated, {len(text) - MAX_RESULT_CHARS} more characters]"
