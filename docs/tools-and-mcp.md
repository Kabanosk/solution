# Tools and MCP

How the agent and the CEO chat use tools, and how to add one.

## Pieces

- **MCP servers** (`backend/cfo/mcp_servers/`): `company-db`, `hr`, `workstations`. A tool is a method of a tools class
  (`CompanyDbTools`, `HrTools`, ...) registered in the server's `build_server`. Its signature and docstring are the
  tool's definition: name, JSON schema, description.
- **ToolBox** (`cfo/agent/tools.py`): `call(server, tool, **arguments)`. `McpToolBox` talks MCP over streamable HTTP or
  stdio; `InProcessToolBox` calls the tool objects directly (tests, single process).
- **Tool catalog** (`cfo/agent/tool_catalog.py`): turns the allowlisted MCP tools into `ToolSpec`s for the LLM and runs
  the model's tool calls.
- **LLM** (`cfo/llm.py`): `llm.chat(messages, tools)` sends the `ToolSpec`s as OpenAI `tools` to the endpoint (vLLM,
  possibly behind a proxy) and returns `tool_calls`.

## Two ways tools are used

1. **The agent's own work is deterministic code.** `Gateway` (typed helpers over a ToolBox) calls fixed tools in a fixed
   order (read transactions, attach an invoice, find the escalation chain). The LLM only reads documents and
   explains; it never chooses these calls.
2. **The CEO chat can let the model choose.** In `cfo/chat.py` one call routes a message to `create_rule`,
   `plan_purchase` or `ask_data`. `ask_data` starts a loop: the model gets the read-only catalog, calls tools, sees
   results as `tool` messages, repeats for at most 3 rounds (`MAX_TOOL_ROUNDS`), then one last call without tools
   makes it answer with what it has. The reply names the tools used ("Sources: team_costs"). Numbers in the answer must
   come from tool results.

The loop is cheap on purpose, for a small model (Granite 4.0 H Tiny): only the allowlist (10 tools) is offered,
schemas are trimmed (no titles, no `anyOf null`), descriptions are cut at 400 characters, unknown arguments are
dropped, `limit` is clamped to 50 (default 25), empty fields are removed, and each result is cut at 4,000 characters
(lists are cut by whole rows with `rows_shown` / `rows_total`). Tool errors go back to the model as `{"error": ...}`.
Tool text (supplier names, payment references) is untrusted data; the system prompt says never to follow
instructions in it.

## The allowlist

`READ_ONLY_TOOLS` in `cfo/agent/tool_catalog.py`:

| Server | Tools |
|---|---|
| company-db | `list_transactions`, `get_invoice`, `list_teams`, `team_costs`, `list_cost_metrics`, `list_decisions`, `list_rules` |
| hr | `org_chart`, `who_is_on_vacation`, `manager_of` |

Never offered to the model: `attach_invoice`, `record_decision` (writes) and every `workstations` tool (employee
files are read only by the invoice search, D-004).

## Add an MCP tool

1. Add a method with type hints and a docstring (the docstring is what the model reads) to the tools class, e.g.
   `CompanyDbTools`. Dates are `YYYY-MM-DD` strings; return JSON-able data (`to_data`).
2. Register it in that module's `build_server` (`server.tool()(tools.my_tool)`).
3. Test it directly on the tools class (see `tests/test_mcp.py`).

The tool is now available to the agent's code through `ToolBox.call`. It is **not** visible to the LLM.

## Expose a tool to the LLM

1. Make sure it is read-only and its docstring says precisely what it returns and which arguments mean what.
2. Add its name to `READ_ONLY_TOOLS`. The catalog reads the schema from the server (`tools/list` over MCP, or the
   method itself in process), so nothing is declared twice.
3. Keep the list short: every tool costs tokens and confuses a small model. Add a case to
   `tests/test_ceo_ask.py` (the allowlist test names the exact set on purpose).

A tool that is only for the model's routing (not data), like `ask_data`, is a plain `ToolSpec` in `cfo/chat.py`.

## vLLM flags

Native tool calling needs the server flags

```
--enable-auto-tool-choice --tool-call-parser=hermes
```

(the Granite 4.0 H Tiny deployment on the cluster has them). Without them vLLM answers HTTP 400 to any request with
`tools`; `OpenAICompatLLM` then remembers it, stops sending `tools` and asks for one JSON object in plain text
(`{"name": ..., "arguments": {...}}`), which is parsed back into a tool call. The chat loop works on both paths, but
the native path is more reliable. The LLM endpoint may be a proxy; the code only sees an OpenAI-compatible URL
(`LLM_BASE_URL`). `cfo smoke` checks tool calling against the real endpoint.
