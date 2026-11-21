"""Run one MCP server as a process.

    python -m cfo.mcp_servers company-db                     # stdio (default)
    python -m cfo.mcp_servers workstations --transport streamable-http --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from cfo.mcp_servers import SERVER_NAMES

DEFAULT_PORTS = {"company-db": 8001, "workstations": 8002, "hr": 8003}


def _factories() -> dict[str, Callable[..., FastMCP]]:
    from cfo.mcp_servers import company_db, hr, workstations

    return {"company-db": company_db.build_server, "workstations": workstations.build_server, "hr": hr.build_server}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m cfo.mcp_servers", description=__doc__.splitlines()[0])
    p.add_argument("server", choices=SERVER_NAMES)
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1", help="streamable-http only (use 0.0.0.0 in the cluster)")
    p.add_argument("--port", type=int, default=None, help="streamable-http only; default per server 8001/8002/8003")
    args = p.parse_args(argv)
    if args.transport != "stdio":
        p.error("MCP HTTP transport is disabled; use the authenticated backend API")
    from cfo.config import get_settings
    from cfo.tenancy import bind_tenant, legacy_connectors_enabled, worker_tenant
    tenant = worker_tenant(get_settings().database_url)
    with bind_tenant(tenant):
        if args.server == "workstations" and not legacy_connectors_enabled():
            p.error("Workstation integration is not configured for this organization")
        server = _factories()[args.server](host=args.host, port=args.port or DEFAULT_PORTS[args.server])
        server.run(transport=args.transport)


if __name__ == "__main__":
    main()
