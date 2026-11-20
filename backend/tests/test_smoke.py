"""`cfo smoke` (T048): one PASS/FAIL line per check. Fakes only: httpx MockTransport, in-process tool objects, a
fake LLM factory. No network, no database."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from cfo import smoke
from cfo.agent.tools import COMPANY_DB, HR, WORKSTATIONS, InProcessToolBox, McpToolBox, ToolError
from cfo.config import Settings
from cfo.llm import FakeLLM, LLMResponse, OpenAICompatLLM, ToolCall

SETTINGS = Settings(llm_base_url="http://llm.test:8080/v1", llm_model="granite", llm_verify_tls="true")
URLS = {COMPANY_DB: "http://mcp-company-db:8001/mcp", HR: "http://mcp-hr:8003/mcp",
        WORKSTATIONS: "http://mcp-workstations:8002/mcp"}
HINT = "--enable-auto-tool-choice --tool-call-parser=hermes"


# --- fakes ---------------------------------------------------------------------------------------------
class CompanyDb:
    def list_teams(self):
        return [{"id": "kitchen"}, {"id": "sales"}]

    def get_transaction(self, transaction_id):
        return None

    def list_transactions(self, **kw):
        return []

    def get_invoice(self, number):
        return None

    def list_decisions(self, limit=50):
        return []

    def list_rules(self):
        return []

    def list_cost_metrics(self, kind=None):
        return []

    def attach_invoice(self, **kw):
        return {}


class Hr:
    def org_chart(self, on=None):
        return [{"id": "rosa"}]

    def get_employee(self, employee_id, on=None):
        return None

    def escalation_chain(self, employee_id, on=None):
        return {}


class Workstations:
    def list_workstations(self):
        return [{"employee": "grazyna"}]

    def search_files(self, **kw):
        return {}

    def read_file(self, **kw):
        return {}

    def read_attachment(self, **kw):
        return {}


def toolbox(**overrides):
    parts = {"company_db": CompanyDb(), "workstations": Workstations(), "hr": Hr()} | overrides
    return InProcessToolBox(**parts)


def llm_server(*, models=("granite",), tool_status=200, tool_calls=True, tool_error="", seen=None,
               arguments='{"city": "Warsaw"}'):
    """httpx MockTransport standing in for the vLLM endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in models]})
        body = json.loads(request.content)
        if tool_status != 200 and body.get("tools"):
            return httpx.Response(tool_status, json={"error": {"message": tool_error}})
        message = {"role": "assistant", "content": None if tool_calls else "It is sunny."}
        if tool_calls:
            message["tool_calls"] = [{"id": "c1", "type": "function",
                                      "function": {"name": "get_weather", "arguments": arguments}}]
        return httpx.Response(200, json={"choices": [{"message": message,
                                                      "finish_reason": "tool_calls" if tool_calls else "stop"}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def mlflow_server(status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="OK")))


def fallback_llm(works=True, arguments=None):
    def factory(settings):
        if works:
            args = {"city": "Warsaw"} if arguments is None else arguments
            return FakeLLM([LLMResponse(tool_calls=[ToolCall(id="t", name="get_weather", arguments=args)])])
        return FakeLLM(["I cannot say."])

    return factory


def run(**kw):
    args = dict(urls=URLS, toolbox=toolbox(), llm_http=llm_server(), http=mlflow_server(), llm_factory=fallback_llm(),
                count_transactions=lambda: 120, mlflow_uri="http://mlflow:5000")
    return smoke.run_smoke(SETTINGS, **(args | kw))


def by_name(results):
    return {r.name: r for r in results}


# --- all green -----------------------------------------------------------------------------------------
def test_everything_up_passes_and_exits_zero():
    results = run()
    assert [r.status for r in results if r.status != "PASS"] == []
    assert {"llm.models", "llm.tool_calling", "llm.fallback", "mcp.company-db.tools", "mcp.company-db.read",
            "mcp.hr.tools", "mcp.hr.read", "mcp.workstations.tools", "mcp.workstations.read", "db.seeded",
            "mlflow.reachable"} <= set(by_name(results))
    assert smoke.exit_code(results) == 0


def test_render_is_one_line_per_check_with_status_first():
    lines = smoke.render(run()).splitlines()
    assert len(lines) == len(run())
    assert all(line.split()[0] in {"PASS", "FAIL", "WARN", "SKIP"} for line in lines)
    assert any(line.startswith("PASS") and "llm.models" in line for line in lines)


def test_tool_calling_request_carries_tools_and_the_configured_model():
    seen: list[httpx.Request] = []
    run(llm_http=llm_server(seen=seen))
    assert [r.url.path for r in seen] == ["/v1/models", "/v1/chat/completions"]
    body = json.loads(seen[1].content)
    assert body["model"] == "granite" and body["tools"][0]["function"]["name"] == "get_weather"


# --- LLM -----------------------------------------------------------------------------------------------
def test_model_not_listed_fails_and_names_what_is_served():
    r = by_name(run(llm_http=llm_server(models=("other-model",))))["llm.models"]
    assert r.status == "FAIL" and "granite" in r.detail and "other-model" in r.detail


def test_tools_rejected_with_400_fails_with_the_vllm_flags_hint():
    results = run(llm_http=llm_server(tool_status=400, tool_error='"auto" tool choice requires --enable-auto-tool-choice'))
    r = by_name(results)["llm.tool_calling"]
    assert r.status == "FAIL" and HINT in r.detail
    assert smoke.exit_code(results) == 1


def test_text_answer_instead_of_tool_calls_fails():
    r = by_name(run(llm_http=llm_server(tool_calls=False)))["llm.tool_calling"]
    assert r.status == "FAIL" and "tool_calls" in r.detail


def test_llm_unreachable_fails_every_llm_check_without_raising():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    results = by_name(run(llm_http=httpx.Client(transport=httpx.MockTransport(boom))))
    assert results["llm.models"].status == "FAIL" and "refused" in results["llm.models"].detail
    assert results["llm.tool_calling"].status == "FAIL"


def test_fallback_failure_is_only_a_warning_while_native_tool_calling_works():
    results = run(llm_factory=fallback_llm(works=False))
    assert by_name(results)["llm.fallback"].status == "WARN"
    assert smoke.exit_code(results) == 0


def test_fallback_failure_fails_when_native_tool_calling_is_down_too():
    results = run(llm_http=llm_server(tool_status=400, tool_error="tools"), llm_factory=fallback_llm(works=False))
    assert by_name(results)["llm.fallback"].status == "FAIL"


def test_fallback_that_works_is_reported_even_when_native_calling_fails():
    results = by_name(run(llm_http=llm_server(tool_status=400, tool_error="tools")))
    assert results["llm.fallback"].status == "PASS"


@pytest.mark.parametrize("arguments", ["{}", '{"city": ""}', '{"city": "  "}', "not json", "[]", '{"town": "Warsaw"}'])
def test_native_tool_call_without_a_city_argument_fails(arguments):
    r = by_name(run(llm_http=llm_server(arguments=arguments)))["llm.tool_calling"]
    assert r.status == "FAIL" and "city" in r.detail


@pytest.mark.parametrize("arguments", [{}, {"city": ""}, {"city": 3}])
def test_fallback_tool_call_without_a_city_argument_fails(arguments):
    r = by_name(run(llm_factory=fallback_llm(arguments=arguments)))["llm.fallback"]
    assert r.status == "WARN" and "city" in r.detail


def test_models_request_sends_the_api_key():
    seen: list[httpx.Request] = []
    run(llm_http=llm_server(seen=seen))
    assert seen[0].headers["authorization"] == "Bearer EMPTY" == seen[1].headers["authorization"]
    custom = Settings(llm_base_url=SETTINGS.llm_base_url, llm_model="granite", llm_api_key="sekret")
    seen.clear()
    smoke.run_smoke(custom, urls=URLS, toolbox=toolbox(), llm_http=llm_server(seen=seen), http=mlflow_server(),
                    llm_factory=fallback_llm(), count_transactions=lambda: 1, mlflow_uri="")
    assert [r.headers["authorization"] for r in seen] == ["Bearer sekret"] * 2


def test_details_are_one_short_line():
    r = smoke.CheckResult("x", "FAIL", "line one\nline two\r\n" + "y" * 500)
    out = smoke.render([r])
    assert "\n" not in out and "\r" not in out and len(out) < 260
    assert len(smoke.render(run(llm_http=llm_server(tool_status=400, tool_error="tool\n" * 300)))
               .splitlines()) == len(run())


def test_llm_timeout_bounds_the_fallback_client(monkeypatch):
    seen = {}

    class Spy(OpenAICompatLLM):
        def __init__(self, *a, timeout, **kw):
            seen["timeout"] = timeout
            super().__init__(*a, timeout=timeout, **kw)

    monkeypatch.setattr(smoke, "OpenAICompatLLM", Spy)
    assert smoke.fallback_llm(SETTINGS, 7.5)._tools_ok is False
    assert seen["timeout"] == 7.5


# --- MCP -----------------------------------------------------------------------------------------------
def test_missing_tool_is_a_fail_naming_it():
    class NoTeams:
        pass

    r = by_name(run(toolbox=toolbox(company_db=NoTeams())))["mcp.company-db.tools"]
    assert r.status == "FAIL" and "list_teams" in r.detail


def test_read_call_error_fails_only_that_server():
    class BrokenHr(Hr):
        def org_chart(self, on=None):
            raise RuntimeError("db down")

    results = by_name(run(toolbox=toolbox(hr=BrokenHr())))
    assert results["mcp.hr.read"].status == "FAIL" and "db down" in results["mcp.hr.read"].detail
    assert results["mcp.hr.tools"].status == "PASS"
    assert results["mcp.company-db.read"].status == "PASS"


def test_unconfigured_mcp_url_fails_with_the_variable_name():
    results = by_name(run(urls={COMPANY_DB: "", HR: URLS[HR], WORKSTATIONS: URLS[WORKSTATIONS]}))
    assert results["mcp.company-db.tools"].status == "FAIL" and "MCP_COMPANY_DB_URL" in results["mcp.company-db.tools"].detail


def test_unreachable_server_fails_in_bounded_time():
    class Unreachable:
        def list_tools(self, server):
            raise ToolError("connection refused")

        def call(self, server, tool, **kw):
            raise ToolError("connection refused")

    results = by_name(run(toolbox=Unreachable()))
    assert results["mcp.hr.tools"].status == "FAIL" and "connection refused" in results["mcp.hr.tools"].detail


def test_hanging_server_times_out(monkeypatch):
    import threading

    monkeypatch.setattr(smoke, "MCP_TIMEOUT", 0.2)
    release = threading.Event()

    class Hangs:
        def list_tools(self, server):
            release.wait(5)
            return []

        def call(self, server, tool, **kw):
            release.wait(5)

    try:
        r = by_name(run(toolbox=Hangs()))["mcp.hr.tools"]
    finally:
        release.set()
    assert r.status == "FAIL" and "timed out" in r.detail


def test_mcp_toolbox_lists_tool_names_through_one_session(monkeypatch):
    box = McpToolBox({"hr": "http://x/mcp"})

    async def alist(server):
        assert server == "hr"
        return ["org_chart", "get_employee"]

    monkeypatch.setattr(box, "_alist", alist)
    assert box.list_tools("hr") == ["org_chart", "get_employee"]
    with pytest.raises(ToolError):
        McpToolBox({}).list_tools("hr")


def test_in_process_toolbox_lists_public_callables_only():
    names = toolbox().list_tools(HR)
    assert "org_chart" in names and not any(n.startswith("_") for n in names)


# --- Postgres / MLflow -----------------------------------------------------------------------------------
def test_empty_database_fails_as_not_seeded():
    r = by_name(run(count_transactions=lambda: 0))["db.seeded"]
    assert r.status == "FAIL" and "seed" in r.detail


def test_database_down_fails_without_raising():
    def down():
        raise OSError("connection refused")

    assert by_name(run(count_transactions=down))["db.seeded"].status == "FAIL"


def test_mlflow_unset_is_skipped_and_down_fails():
    assert by_name(run(mlflow_uri=""))["mlflow.reachable"].status == "SKIP"
    assert by_name(run(http=mlflow_server(503)))["mlflow.reachable"].status == "FAIL"


def test_mlflow_health_endpoint_is_used():
    seen: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200))[1]))
    run(http=client, mlflow_uri="http://mlflow:5000/")
    assert [str(r.url) for r in seen] == ["http://mlflow:5000/health"]


# --- command line --------------------------------------------------------------------------------------
def test_tls_verify_setting_is_passed_to_the_llm_client(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    assert smoke.llm_http_client(Settings(llm_verify_tls="false"), 5).timeout.read == 5
    assert smoke.tls_verify_setting("false") is False and smoke.tls_verify_setting(str(ca)) == str(ca)


def test_help_runs_without_network():
    backend = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-m", "cfo.smoke", "--help"], cwd=backend, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0 and "smoke" in out.stdout.lower()


def test_entrypoint_has_a_smoke_command():
    script = (Path(__file__).resolve().parents[2] / "deploy" / "cfo-entrypoint.sh").read_text()
    assert "smoke)" in script and "python -m cfo.smoke" in script
