"""Model-agnostic chat client with tool calling. Nothing else in the codebase may call an LLM (D-009).

`OpenAICompatLLM` talks to any OpenAI-compatible endpoint (vLLM, or the NeMo Guardrails proxy served
at the same URL). Everything depends on the small `LLM` protocol, so tests use `FakeLLM`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from cfo.config import Settings, get_settings


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


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

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


class OpenAICompatLLM:
    """Chat completions against an OpenAI-compatible server."""

    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY", timeout: float = 120.0):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)

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
        msg = self._client.chat.completions.create(**kwargs).choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": c.function.arguments}  # small models emit broken JSON; let the caller decide
            calls.append(ToolCall(id=c.id, name=c.function.name, arguments=args))
        return LLMResponse(content=msg.content, tool_calls=calls)


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
        if self._script:
            out = self._script.pop(0)
        elif self._handler:
            out = self._handler(messages, tools)
        else:
            raise AssertionError("FakeLLM script exhausted")
        return LLMResponse(content=out) if isinstance(out, str) else out


def get_llm(settings: Settings | None = None) -> LLM:
    """The configured LLM client (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`)."""
    s = settings or get_settings()
    return OpenAICompatLLM(s.llm_base_url, s.llm_model, s.llm_api_key)
