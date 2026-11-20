"""`cfo smoke`: prove inside the cluster that the moving parts work (T048).

    python -m cfo.smoke            # or: oc rsh deploy/api cfo smoke

One line per check, `PASS|FAIL|WARN|SKIP <name> <detail>`; exit status 1 when any check FAILs.

Checks: the LLM lists the configured model and answers a request with `tools` by `tool_calls` (native tool calling),
the T041 JSON-in-text fallback still yields a call, each MCP server lists its expected tools and answers one harmless
read, Postgres is seeded, MLflow answers `/health` (only when `MLFLOW_TRACKING_URI` is set).

Settings are the ones api/worker use (`cfo.config`, `LLM_VERIFY_TLS` as in `cfo.llm`, `MCP_*_URL` as in `cfo.run`).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from cfo.agent.tools import COMPANY_DB, HR, WORKSTATIONS, McpToolBox
from cfo.config import Settings, get_settings
from cfo.llm import LLM, Message, OpenAICompatLLM, ToolSpec, tls_verify_setting

Status = Literal["PASS", "FAIL", "WARN", "SKIP"]

LLM_TIMEOUT = 60.0
HTTP_TIMEOUT = 10.0
MCP_TIMEOUT = 20.0  # per MCP call; module level so tests can shorten it
TOOLS_HINT = "start vLLM with --enable-auto-tool-choice --tool-call-parser=hermes"

# server -> (env var, tools the agent needs, harmless read call)
MCP_CHECKS: dict[str, tuple[str, tuple[str, ...], str]] = {
    COMPANY_DB: ("MCP_COMPANY_DB_URL", ("list_transactions", "get_transaction", "get_invoice", "list_teams",
                                         "list_rules", "list_decisions", "list_cost_metrics", "attach_invoice"),
                 "list_teams"),
    HR: ("MCP_HR_URL", ("org_chart", "get_employee", "escalation_chain"), "org_chart"),
    WORKSTATIONS: ("MCP_WORKSTATIONS_URL", ("list_workstations", "search_files", "read_file", "read_attachment"),
                   "list_workstations"),
}

WEATHER = ToolSpec(name="get_weather", description="Current weather for a city.",
                   parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]})
QUESTION = "What is the weather in Warsaw right now? Use the get_weather tool."


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""


def render(results: list[CheckResult]) -> str:
    return "\n".join(f"{r.status:<4}  {r.name:<24} {r.detail}".rstrip() for r in results)


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.status == "FAIL" for r in results) else 0


def _guard(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a check must report, never crash the run
        return CheckResult(name, "FAIL", f"{type(e).__name__}: {e}")


def _with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Run `fn` in a daemon thread so a hung server cannot hang the smoke check."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised below
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"timed out after {seconds:g}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# --- LLM -----------------------------------------------------------------------------------------------
def check_models(settings: Settings, http: httpx.Client) -> CheckResult:
    name = "llm.models"
    r = http.get(f"{settings.llm_base_url.rstrip('/')}/models")
    if r.status_code != 200:
        return CheckResult(name, "FAIL", f"GET /models answered HTTP {r.status_code}")
    served = [m.get("id") for m in r.json().get("data", [])]
    if settings.llm_model not in served:
        return CheckResult(name, "FAIL", f"LLM_MODEL {settings.llm_model!r} not served; served: {served}")
    return CheckResult(name, "PASS", f"{settings.llm_model} is served")


def check_tool_calling(settings: Settings, http: httpx.Client) -> CheckResult:
    name = "llm.tool_calling"
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": QUESTION}],
        "tools": [{"type": "function", "function": {"name": WEATHER.name, "description": WEATHER.description,
                                                    "parameters": WEATHER.parameters}}],
        "temperature": 0,
        "max_tokens": 128,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key or 'EMPTY'}"}
    r = http.post(f"{settings.llm_base_url.rstrip('/')}/chat/completions", json=body, headers=headers)
    if r.status_code == 400 and "tool" in r.text.lower():
        return CheckResult(name, "FAIL", f"HTTP 400, the endpoint rejects `tools`; {TOOLS_HINT}. "
                                         f"Answer: {r.text[:200]}")
    if r.status_code != 200:
        return CheckResult(name, "FAIL", f"HTTP {r.status_code}: {r.text[:200]}")
    choice = r.json()["choices"][0]
    calls = choice.get("message", {}).get("tool_calls") or []
    if not calls:
        text = (choice.get("message", {}).get("content") or "")[:120]
        return CheckResult(name, "FAIL", f"no tool_calls in the answer (finish_reason={choice.get('finish_reason')}, "
                                         f"text={text!r}); check --tool-call-parser matches the model (hermes for Granite)")
    return CheckResult(name, "PASS", f"tool_calls: {calls[0]['function']['name']}({calls[0]['function']['arguments']}), "
                                     f"finish_reason={choice.get('finish_reason')}")


def check_fallback(settings: Settings, llm_factory: Callable[[Settings], LLM], native: CheckResult) -> CheckResult:
    """The T041 JSON-in-text path: only a warning while native tool calling works (the fallback is then unused)."""
    name = "llm.fallback"
    bad: Status = "WARN" if native.status == "PASS" else "FAIL"
    try:
        out = llm_factory(settings).chat([Message(role="user", content=QUESTION)], [WEATHER], max_tokens=128)
    except Exception as e:  # noqa: BLE001
        return CheckResult(name, bad, f"{type(e).__name__}: {e}")
    if not out.tool_calls or out.tool_calls[0].name != WEATHER.name:
        return CheckResult(name, bad, f"JSON-in-text answer did not parse into a tool call: {(out.content or '')[:120]!r}")
    return CheckResult(name, "PASS", f"JSON-in-text call parsed: {out.tool_calls[0].name}({out.tool_calls[0].arguments})")


def llm_http_client(settings: Settings, timeout: float = LLM_TIMEOUT) -> httpx.Client:
    return httpx.Client(verify=tls_verify_setting(settings.llm_verify_tls), timeout=timeout)


def fallback_llm(settings: Settings) -> LLM:
    """The production client forced onto the JSON-in-text path (the one T041 uses when `tools` are rejected)."""
    llm = OpenAICompatLLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key, timeout=LLM_TIMEOUT,
                          verify_tls=settings.llm_verify_tls)
    llm._tools_ok = False  # noqa: SLF001 - same package family; there is no public switch for this
    return llm


# --- MCP -----------------------------------------------------------------------------------------------
def check_mcp(server: str, url: str, toolbox: Any) -> list[CheckResult]:
    env_var, expected, read_tool = MCP_CHECKS[server]
    tools_name, read_name = f"mcp.{server}.tools", f"mcp.{server}.read"
    if not url:
        return [CheckResult(tools_name, "FAIL", f"{env_var} is not set (ConfigMap cfo-config)"),
                CheckResult(read_name, "SKIP", f"{env_var} is not set")]
    try:
        offered = _with_timeout(lambda: toolbox.list_tools(server), MCP_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return [CheckResult(tools_name, "FAIL", f"{url}: {type(e).__name__}: {e}"),
                CheckResult(read_name, "SKIP", "list_tools failed")]
    missing = [t for t in expected if t not in offered]
    tools = (CheckResult(tools_name, "FAIL", f"missing tools: {', '.join(missing)} (offered: {', '.join(sorted(offered))})")
             if missing else CheckResult(tools_name, "PASS", f"{len(offered)} tools at {url}"))
    try:
        data = _with_timeout(lambda: toolbox.call(server, read_tool), MCP_TIMEOUT)
        size = f"{len(data)} items" if isinstance(data, list) else type(data).__name__
        read = CheckResult(read_name, "PASS", f"{read_tool}() returned {size}")
    except Exception as e:  # noqa: BLE001
        read = CheckResult(read_name, "FAIL", f"{read_tool}(): {type(e).__name__}: {e}")
    return [tools, read]


# --- Postgres / MLflow -----------------------------------------------------------------------------------
def count_transactions_in_db(database_url: str) -> int:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        return int(conn.execute("SELECT count(*) FROM transactions").fetchone()[0])


def check_seeded(count: Callable[[], int]) -> CheckResult:
    n = count()
    if n <= 0:
        return CheckResult("db.seeded", "FAIL", "transactions table is empty: run the seed job (`cfo load`)")
    return CheckResult("db.seeded", "PASS", f"{n} transactions")


def check_mlflow(uri: str, http: httpx.Client) -> CheckResult:
    if not uri:
        return CheckResult("mlflow.reachable", "SKIP", "MLFLOW_TRACKING_URI not set")
    r = http.get(f"{uri.rstrip('/')}/health")
    if r.status_code != 200:
        return CheckResult("mlflow.reachable", "FAIL", f"{uri}/health answered HTTP {r.status_code}")
    return CheckResult("mlflow.reachable", "PASS", f"{uri}/health OK")


# --- run -----------------------------------------------------------------------------------------------
def run_smoke(
    settings: Settings,
    *,
    urls: Mapping[str, str],
    toolbox: Any,
    llm_http: httpx.Client,
    http: httpx.Client,
    llm_factory: Callable[[Settings], LLM],
    count_transactions: Callable[[], int],
    mlflow_uri: str,
) -> list[CheckResult]:
    results = [_guard("llm.models", lambda: check_models(settings, llm_http))]
    native = _guard("llm.tool_calling", lambda: check_tool_calling(settings, llm_http))
    results.append(native)
    results.append(_guard("llm.fallback", lambda: check_fallback(settings, llm_factory, native)))
    for server in MCP_CHECKS:
        results += check_mcp(server, urls.get(server, ""), toolbox)
    results.append(_guard("db.seeded", lambda: check_seeded(count_transactions)))
    results.append(_guard("mlflow.reachable", lambda: check_mlflow(mlflow_uri, http)))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cfo smoke",
        description="Cluster smoke check: LLM tool calling, the three MCP servers, Postgres seed, MLflow. "
                    "Prints one PASS/FAIL line per check; exit status 1 on any FAIL.")
    p.add_argument("--llm-timeout", type=float, default=LLM_TIMEOUT, help="seconds per LLM request (default %(default)s)")
    args = p.parse_args(argv)

    from cfo.run import get_run_settings  # lazy: pulls in the whole agent stack, which --help does not need

    settings, run = get_settings(), get_run_settings()
    urls = {COMPANY_DB: run.mcp_company_db_url, WORKSTATIONS: run.mcp_workstations_url, HR: run.mcp_hr_url}
    with llm_http_client(settings, args.llm_timeout) as llm_http, httpx.Client(timeout=HTTP_TIMEOUT) as http:
        results = run_smoke(
            settings,
            urls=urls,
            toolbox=McpToolBox({k: v for k, v in urls.items() if v}),
            llm_http=llm_http,
            http=http,
            llm_factory=fallback_llm,
            count_transactions=lambda: count_transactions_in_db(settings.database_url),
            mlflow_uri=os.environ.get("MLFLOW_TRACKING_URI", ""),
        )
    print(render(results), flush=True)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
