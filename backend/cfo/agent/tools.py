"""How the agent calls the T004 MCP tools.

The agent only knows `ToolBox.call(server, tool, **arguments)`. Two implementations:

* `InProcessToolBox`: calls the tool objects (`CompanyDbTools`, `WorkstationTools`, `HrTools`) directly.
  Used by tests and by a single-process deployment; no transport, same code and same access logging.
* `McpToolBox`: production over MCP, using the official `mcp` client (already a dependency) against the
  servers started with `python -m cfo.mcp_servers <name> --transport streamable-http` (or stdio). We use the
  plain client instead of `langchain-mcp-adapters` because the agent calls tools from deterministic code, not
  through a LangChain tool-calling loop, and it avoids one more dependency.

`Gateway` wraps a ToolBox with typed helpers returning domain models.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from datetime import date
from typing import Any, Protocol

from cfo.domain import CostMetricKind, Decision, Invoice, Rule, Transaction

COMPANY_DB = "company-db"
WORKSTATIONS = "workstations"
HR = "hr"


class ToolError(RuntimeError):
    pass


class ToolBox(Protocol):
    def call(self, server: str, tool: str, **arguments: Any) -> Any: ...


class InProcessToolBox:
    def __init__(self, company_db: Any, workstations: Any, hr: Any):
        self._servers = {COMPANY_DB: company_db, WORKSTATIONS: workstations, HR: hr}

    def call(self, server: str, tool: str, **arguments: Any) -> Any:
        target = self._servers.get(server)
        fn = getattr(target, tool, None) if target is not None and not tool.startswith("_") else None
        if fn is None or not callable(fn):
            raise ToolError(f"unknown tool {server}.{tool}")
        # round-trip through JSON so callers see exactly what an MCP client would
        return json.loads(json.dumps(fn(**arguments), default=str))


class McpToolBox:
    """`endpoints` maps a server name to a streamable-HTTP URL (`http://host:8002/mcp`) or to
    `mcp.StdioServerParameters`. One short session per call: simple and robust, fine at demo volume.
    Safe to call from sync code and from inside a running event loop (runs in a helper thread then)."""

    def __init__(self, endpoints: Mapping[str, Any]):
        self._endpoints = dict(endpoints)

    def call(self, server: str, tool: str, **arguments: Any) -> Any:
        if server not in self._endpoints:
            raise ToolError(f"no endpoint configured for MCP server {server!r}")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._acall(server, tool, arguments))
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = asyncio.run(self._acall(server, tool, arguments))
            except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
                box["error"] = e

        t = threading.Thread(target=run)
        t.start()
        t.join()
        if "error" in box:
            raise box["error"]
        return box["value"]

    async def _acall(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession

        endpoint = self._endpoints[server]
        if isinstance(endpoint, str):
            from mcp.client.streamable_http import streamablehttp_client

            transport = streamablehttp_client(endpoint)
        else:
            from mcp.client.stdio import stdio_client

            transport = stdio_client(endpoint)
        async with transport as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        if result.isError:
            raise ToolError(f"{server}.{tool}: " + " ".join(getattr(c, "text", "") for c in result.content))
        data = result.structuredContent
        if data is not None:
            # FastMCP wraps non-object results (lists, null) as {"result": ...}
            return data["result"] if set(data) == {"result"} else data
        text = "".join(getattr(c, "text", "") for c in result.content)
        return json.loads(text) if text else None


class Gateway:
    """Typed helpers over a ToolBox: the only place that knows tool names and argument spelling."""

    def __init__(self, tools: ToolBox):
        self.tools = tools

    # company-db
    def transaction(self, transaction_id: str) -> Transaction | None:
        raw = self.tools.call(COMPANY_DB, "get_transaction", transaction_id=transaction_id)
        return Transaction(**raw) if raw else None

    def transactions(self, **kwargs: Any) -> list[Transaction]:
        return [Transaction(**r) for r in self.tools.call(COMPANY_DB, "list_transactions", limit=500, **kwargs)]

    def decisions(self, limit: int = 500) -> list[Decision]:
        return [Decision(**r) for r in self.tools.call(COMPANY_DB, "list_decisions", limit=limit)]

    def rules(self) -> list[Rule]:
        return [Rule(**r) for r in self.tools.call(COMPANY_DB, "list_rules")]

    def invoice(self, number: str) -> Invoice | None:
        raw = self.tools.call(COMPANY_DB, "get_invoice", number=number)
        return Invoice(**raw) if raw else None

    def teams(self) -> list[dict[str, Any]]:
        return self.tools.call(COMPANY_DB, "list_teams")

    def cost_metrics(self, kind: CostMetricKind | str | None = None) -> list[dict[str, Any]]:
        args = {"kind": str(kind)} if kind else {}
        return self.tools.call(COMPANY_DB, "list_cost_metrics", **args)

    def attach_invoice(self, **kwargs: Any) -> dict[str, Any]:
        return self.tools.call(COMPANY_DB, "attach_invoice", **kwargs)

    # hr
    def employee(self, employee_id: str, on: date) -> dict[str, Any] | None:
        return self.tools.call(HR, "get_employee", employee_id=employee_id, on=on.isoformat())

    def escalation_chain(self, employee_id: str, on: date) -> dict[str, Any]:
        return self.tools.call(HR, "escalation_chain", employee_id=employee_id, on=on.isoformat())

    # workstations (every call is logged by the server, D-004)
    def search_files(self, employee: str, query: str, reason: str) -> list[dict[str, Any]]:
        res = self.tools.call(WORKSTATIONS, "search_files", employee=employee, query=query, reason=reason)
        return res.get("files", []) if res.get("ok") else []

    def read_file(self, employee: str, path: str, reason: str) -> dict[str, Any]:
        return self.tools.call(WORKSTATIONS, "read_file", employee=employee, path=path, reason=reason)

    def read_attachment(self, employee: str, path: str, attachment: str, reason: str) -> dict[str, Any]:
        return self.tools.call(
            WORKSTATIONS, "read_attachment", employee=employee, path=path, attachment=attachment, reason=reason
        )
