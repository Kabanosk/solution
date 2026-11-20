---
id: T047
title: "CEO asks data questions: LLM tool calling over read-only MCP tools"
status: done
owner: coder
branch: task/T047-ceo-ask-mcp-tools
depends_on: ["T041"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_ceo_ask.py backend/tests/test_golden_path.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests"
    timeout: 1500
  - id: AC-3
    run: "test -s docs/tools-and-mcp.md"
protected: []
---
## Objective
The CEO can ask the agent questions about company data in the chat channel ("how much did we spend on ads in
October?", "who is on vacation next week?", "why did cloud costs go up?") and gets a short answer whose numbers
come only from tool results. Today `cfo/chat.py` understands only a rule or a what-if purchase.
1. Tool catalog: build `ToolSpec`s from the MCP servers' own tool schemas (`list_tools` over `McpToolBox`; for
   `InProcessToolBox` derive the same schemas from the tool methods) so a tool is defined once, in the MCP server.
   An explicit READ-ONLY allowlist: company-db `list_transactions`, `get_invoice`, `list_teams`, `team_costs`,
   `list_cost_metrics`, `list_decisions`, `list_rules`; hr `org_chart`, `who_is_on_vacation`, `manager_of`.
   Never `attach_invoice`, `record_decision`, nor any workstations tool (D-004: employee files only for invoice search).
2. `ask_data` as a third chat intent next to create_rule / plan_purchase: a tool-calling loop of at most 3 tool
   rounds (then answer with what it has), tool results passed back as `tool` messages, results truncated to a
   size Granite 4.0 H Tiny handles. The reply carries a level label like other bot messages and names its sources
   (tool names). Tool text is untrusted data (system prompt says so).
3. Works through the T041 no-tools fallback too (endpoint without auto tool choice): test both paths with FakeLLM.
4. `docs/tools-and-mcp.md` (English, short): how tools work here (ToolSpec → `llm.chat(..., tools)` → vLLM),
   how to add an LLM tool, how to add an MCP tool to a server and expose it (allowlist), how the agent's
   deterministic calls differ from the CEO chat loop, and the vLLM flags needed
   (`--enable-auto-tool-choice --tool-call-parser=hermes`).

## Context
- `cfo/agent/tools.py` (ToolBox, McpToolBox, Gateway), `cfo/mcp_servers/*`, `cfo/chat.py`, `cfo/llm.py`.
- Every LLM call stays traced (T027). Demo-flow step 5/7 behaviour (rule, what-if → credit line) must not change.
- Tests: FakeLLM scripted tool calls, InProcessToolBox, per-process Postgres schema (like the other tests).

## Out of scope
Write tools for the LLM, workstation access from chat, new MCP servers, Slack transport changes.
