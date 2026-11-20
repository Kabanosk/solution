"""Model-agnostic chat client with tool calling. Nothing else in the codebase may call an LLM (D-009).

`OpenAICompatLLM` talks to any OpenAI-compatible endpoint (vLLM, or the NeMo Guardrails proxy served
at the same URL). Everything depends on the small `LLM` protocol, so tests use `FakeLLM`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from cfo.config import Settings, get_settings
from cfo.usage import recorder


log = logging.getLogger(__name__)


def _tools_rejected(error: Exception) -> bool:
    """HTTP 400 about tools/tool choice (vLLM started without `--enable-auto-tool-choice`)."""
    return getattr(error, "status_code", None) == 400 and "tool" in str(error).lower()


def _calls_from_text(content: str | None, tools: Sequence[ToolSpec]) -> list[ToolCall]:
    """The JSON object in a plain-text answer as a tool call; [] when there is none."""
    m = re.search(r"\{.*\}", content or "", re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    names = {t.name for t in tools}
    name = data.get("name")
    if "name" in data and not isinstance(name, str):
        return []  # a list / object / number as the function name is not a call
    if name in names and isinstance(data.get("arguments"), dict):
        return [ToolCall(id="call_text", name=name, arguments=data["arguments"])]
    if len(tools) == 1:
        return [ToolCall(id="call_text", name=tools[0].name, arguments=data)]
    return []


class ToolSpec(BaseModel):
    """A function the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant messages only
    tool_call_id: str | None = None  # tool messages only: answers this call


class TokenUsage(BaseModel):
    """Token counts the server reported (OpenAI `usage` field)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage | None = None  # None: the server sent none; cfo.usage then estimates the count and flags it

    def as_message(self) -> Message:
        """The assistant message to append to the conversation before sending tool results."""
        return Message(role="assistant", content=self.content, tool_calls=self.tool_calls)


@runtime_checkable
class LLM(Protocol):
    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


def tls_verify_setting(value: str | bool) -> str | bool:
    """Map LLM_VERIFY_TLS to httpx `verify`: true/false, or a CA bundle path kept as a string."""
    if isinstance(value, bool):
        return value
    v = value.strip()
    if v.lower() in {"", "1", "true", "yes", "on"}:
        return True
    if v.lower() in {"0", "false", "no", "off"}:
        return False
    return v


class OpenAICompatLLM:
    """Chat completions against an OpenAI-compatible server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "EMPTY",
        timeout: float = 120.0,
        verify_tls: str | bool = True,
    ):
        import httpx
        from openai import OpenAI

        self.model = model
        self._tools_ok: bool | None = None  # None: not known yet; False: the endpoint rejects `tools`
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            timeout=timeout,
            http_client=httpx.Client(verify=tls_verify_setting(verify_tls), timeout=timeout),
        )

    @staticmethod
    def _to_wire(m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
        wire: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            wire["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in m.tool_calls
            ]
        return wire

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [self._to_wire(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        started = recorder.perf_counter()
        use_tools = bool(tools) and self._tools_ok is not False
        if tools and not use_tools:
            kwargs.pop("tools", None)
            kwargs["messages"] = self._json_in_text_messages(kwargs["messages"], tools)
        try:
            try:
                completion = self._client.chat.completions.create(**kwargs)
            except Exception as e:
                if not (tools and use_tools and self._tools_ok is None and _tools_rejected(e)):
                    raise
                # vLLM without --enable-auto-tool-choice answers 400 to any request with `tools`: ask for JSON in text
                log.warning("endpoint rejected `tools` (%s); using the JSON-in-text path from now on", e)
                self._tools_ok = False
                kwargs.pop("tools", None)
                kwargs["messages"] = self._json_in_text_messages(kwargs["messages"], tools)
                use_tools = False
                completion = self._client.chat.completions.create(**kwargs)
        except Exception:
            recorder.record_call(model=self.model, messages=messages, tools=tools, response=None, started=started,
                                 success=False)
            raise
        if use_tools:
            self._tools_ok = True
        msg = completion.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):  # small models emit broken JSON or `[]` / `null`: no usable arguments
                args = {"_raw": c.function.arguments}
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=args))
        if tools and not use_tools and not calls:
            calls = _calls_from_text(msg.content, tools)
        raw = getattr(completion, "usage", None)
        usage = TokenUsage(prompt_tokens=raw.prompt_tokens or 0, completion_tokens=raw.completion_tokens or 0) if raw else None
        response = LLMResponse(content=msg.content, tool_calls=calls, usage=usage)
        recorder.record_call(model=self.model, messages=messages, tools=tools, response=response, started=started)
        return response

    @staticmethod
    def _json_in_text_messages(wire: list[dict[str, Any]], tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        """Describe the tools in a system message: the model answers with one JSON object instead of a tool call."""
        if len(tools) == 1:
            t = tools[0]
            how = (f"Answer with only one JSON object: the arguments of the function `{t.name}` ({t.description}). "
                   f"Its JSON schema: {json.dumps(t.parameters)}")
        else:
            listing = "; ".join(f"`{t.name}` ({t.description}) schema {json.dumps(t.parameters)}" for t in tools)
            how = ('Answer with only one JSON object {"name": <function>, "arguments": {...}} calling exactly one of '
                   f"these functions: {listing}")
        return [{"role": "system", "content": how}, *wire]


class FakeLLM:
    """Scripted LLM for tests. Each `chat` call returns the next scripted response, or asks `handler`.

        llm = FakeLLM([FakeLLM.tool_call("get_balance", day="2025-06-01"), "Done."])
    """

    def __init__(
        self,
        script: Sequence[LLMResponse | str] = (),
        handler: Callable[[Sequence[Message], Sequence[ToolSpec] | None], LLMResponse | str] | None = None,
    ):
        self._script = list(script)
        self._handler = handler
        self.calls: list[dict[str, Any]] = []  # recorded {"messages": ..., "tools": ...} per call

    @staticmethod
    def tool_call(name: str, call_id: str = "call_1", **arguments: Any) -> LLMResponse:
        return LLMResponse(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "tools": list(tools or [])})
        started = recorder.perf_counter()
        if self._script:
            out = self._script.pop(0)
        elif self._handler:
            out = self._handler(messages, tools)
        else:
            raise AssertionError("FakeLLM script exhausted")
        response = LLMResponse(content=out) if isinstance(out, str) else out
        recorder.record_call(model="fake", messages=messages, tools=tools, response=response, started=started)
        return response


def get_llm(settings: Settings | None = None) -> LLM:
    """The configured LLM client (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`)."""
    s = settings or get_settings()
    return OpenAICompatLLM(s.llm_base_url, s.llm_model, s.llm_api_key, verify_tls=s.llm_verify_tls)
