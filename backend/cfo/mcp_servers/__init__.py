"""MCP servers the agent uses as tools: `company-db`, `workstations`, `hr` (T004).

Each module has a plain tools class (callable directly in tests, no transport) and a `build_server(...)`
factory returning a `FastMCP` instance. Run one as a process with `python -m cfo.mcp_servers <name>`.
"""

SERVER_NAMES = ("company-db", "workstations", "hr")
