"""Scout's side of the safety checks: NeMo Guardrails `POST /v1/guardrail/checks` at three points (D-029).

    user-input   a human message (Slack DM text, dashboard chat turn), before routing and before any LLM call
    tool-result  a tool result in the chat loop, or the text of a document Scout is about to read (invoice)
    bot-output   the text Scout sends back (DM reply, dashboard reply, send_dm message)

The service only judges text; everything that follows is code here: strikes and the compliance DM for blocked
human messages, `[content withheld]` for flagged tool results, the CEO approval card for a flagged invoice (see
`loops/invoicing.py`) and the audit rows. `GUARDRAILS_URL` unset means disabled: nothing is sent, everything passes.
The service down, slow or answering `error` means fail-open: the text passes, a warning is logged and an audit row
`guardrail_unavailable` is written.

Audit rows use the existing `audit_log`: action `guardrail_check` (decision DENY = blocked, policy = config id,
reason = the rails that fired), `guardrail_unavailable` and `guardrail_escalation` (decision ALLOW).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cfo.observability import span

log = logging.getLogger(__name__)

USER_INPUT, TOOL_RESULT, BOT_OUTPUT = "user-input", "tool-result", "bot-output"
MAX_STRIKES = 3
WINDOW_DAYS = 7
TIMEOUT_S = 5.0
CHECK_CHARS = 8000  # the rails read the start of a long text; the service has no use for megabytes

WITHHELD = "[content withheld: flagged by guardrail]"
OUTPUT_REPLY = "I could not share that answer safely. Please rephrase your question."
EXTERNAL_TOOLS = frozenset({"search_email", "search_chat", "read_file", "search_files"})  # text written by outsiders


def blocked_reply(attempt: int) -> str:
    return (f"This request was blocked by our safety policy (attempt {attempt} of {MAX_STRIKES}; repeated attempts "
            "are reported to compliance).")


@dataclass(frozen=True)
class Verdict:
    status: str  # success | blocked | error (the service said so) | unavailable (no answer) | disabled
    activated_rails: tuple[str, ...] = ()
    latency: float = 0.0
    detail: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def failed(self) -> bool:
        return self.status in ("error", "unavailable")

    @property
    def rails(self) -> str:
        return ", ".join(self.activated_rails)


def _rail_names(body: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []

    def add(name: Any) -> None:
        if isinstance(name, str) and name and name not in names:
            names.append(name)

    log_ = ((body.get("guardrails_data") or {}).get("log") or {}).get("activated_rails") or []
    for r in log_:
        if isinstance(r, str):
            add(r)
        elif isinstance(r, dict) and r.get("stop", True):
            add(r.get("name"))
    if not names:
        for key, val in (body.get("rails_status") or {}).items():
            state = val.get("status") if isinstance(val, dict) else val
            if state == "blocked":
                add(key)
    return tuple(names)


class HttpGuardrails:
    """The check service over HTTP. `url` empty = disabled."""

    def __init__(self, url: str, token: str = "", *, timeout: float = TIMEOUT_S, model: str = "scout",
                 http: httpx.Client | None = None):
        self.url, self.token, self.timeout, self.model = url.rstrip("/"), token, timeout, model
        self._http = http

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def check(self, config_id: str, messages: list[dict[str, str]]) -> Verdict:
        if not self.enabled:
            return Verdict("disabled")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        body = {"model": self.model, "messages": messages, "guardrails": {"config_id": config_id}}
        started = time.monotonic()
        try:
            client = self._http or httpx.Client(timeout=self.timeout)
            try:
                r = client.post(f"{self.url}/v1/guardrail/checks", json=body, headers=headers, timeout=self.timeout)
            finally:
                if self._http is None:
                    client.close()
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001 - timeout, refused, 4xx/5xx, not JSON: all fail-open
            return Verdict("unavailable", latency=time.monotonic() - started, detail=f"{type(e).__name__}: {e}"[:200])
        latency = time.monotonic() - started
        status = str(data.get("status") or "error")
        if status == "blocked":
            return Verdict("blocked", _rail_names(data) or ("unknown",), latency)
        if status == "success":
            return Verdict("success", (), latency)
        return Verdict("error", (), latency, detail=str(data.get("error") or data.get("detail") or status)[:200])


def from_settings(settings: Any) -> HttpGuardrails:
    return HttpGuardrails(getattr(settings, "guardrails_url", "") or "", getattr(settings, "guardrails_token", "") or "")


# -- one check: tracing, fail-open, audit -----------------------------------------------------------------
def check(ctx: Any, config_id: str, text: str, *, role: str = "user", task_id: str | None = None,
          requester: str | None = None, source: str = "", record_block: bool = True) -> Verdict:
    """Run one check. Never raises. A blocking verdict is audited here (DENY) unless `record_block` is False."""
    client = getattr(ctx, "guard", None)
    if client is None or not getattr(client, "enabled", False) or not text.strip():
        return Verdict("disabled")
    with span("guardrails.check", span_type="TOOL",
              inputs={"config_id": config_id, "source": source, "chars": len(text)}) as s:
        try:
            v = client.check(config_id, [{"role": role, "content": text[:CHECK_CHARS]}])
        except Exception as e:  # noqa: BLE001
            v = Verdict("unavailable", detail=f"{type(e).__name__}: {e}"[:200])
        s.set_attributes({"guardrail.config_id": config_id, "guardrail.status": v.status,
                          "guardrail.rails": v.rails, "guardrail.latency_ms": round(v.latency * 1000)})
        s.set_outputs({"status": v.status, "rails": list(v.activated_rails)})
    try:
        if v.failed:
            log.warning("guardrails %s check failed open (%s)", config_id, v.detail or v.status)
            ctx.audit.record("agent", "guardrail_unavailable", f"guardrails:{config_id}", source or config_id, "ALLOW",
                             task_id=task_id, requester=requester, policy=config_id, reason=v.detail or v.status)
        elif v.blocked and record_block:
            ctx.audit.record("agent", "guardrail_check", f"guardrails:{config_id}", f"{source}: {text}"[:500]
                             if source else text, "DENY", task_id=task_id, requester=requester, policy=config_id,
                             reason=v.rails)
    except Exception:  # noqa: BLE001 - an audit failure must not turn a verdict into a crash
        log.exception("could not audit the guardrail verdict")
    return v


# -- 1. human messages: strikes and compliance ------------------------------------------------------------
def screen_human(ctx: Any, person: str | None, text: str, *, role: str | None = None,
                 task_id: str | None = None, source: str = "") -> str | None:
    """Check a human message. None when it may go on; else the reply that tells the person it was blocked
    (the third blocked message in 7 days also reaches the compliance contact, and the counter starts again)."""
    v = check(ctx, USER_INPUT, text, task_id=task_id, requester=person, source="", record_block=False)
    if not v.blocked:
        return None
    ctx.audit.record("agent", "guardrail_check", f"guardrails:{USER_INPUT}", text, "DENY", task_id=task_id,
                     requester=person, role=role, policy=USER_INPUT, reason=v.rails)
    strikes = _strikes(ctx, person)
    if len(strikes) >= MAX_STRIKES:
        _escalate(ctx, person, role, strikes, task_id)
    return blocked_reply(min(len(strikes), MAX_STRIKES))


def _strikes(ctx: Any, person: str | None) -> list[dict[str, Any]]:
    """The person's blocked messages in the last 7 days since the last escalation, oldest first."""
    with ctx.conn_factory() as conn:
        return conn.execute(
            "SELECT ts, resource, reason FROM audit_log WHERE action = 'guardrail_check' AND policy = %s "
            "AND decision = 'DENY' AND requester IS NOT DISTINCT FROM %s AND ts > greatest(now() - %s * interval "
            "'1 day', coalesce((SELECT max(ts) FROM audit_log WHERE action = 'guardrail_escalation' AND requester "
            "IS NOT DISTINCT FROM %s), '-infinity')) ORDER BY ts, id", (USER_INPUT, person, WINDOW_DAYS, person)
        ).fetchall()


def _compliance_target(ctx: Any) -> str:
    contact = (getattr(ctx.settings, "compliance_contact", "") or "").strip()
    if contact:
        if ctx.hr.employee(contact):
            return contact
        by_slack = ctx.hr.by_slack_id(contact)
        if by_slack:
            return by_slack["id"]
        log.warning("COMPLIANCE_CONTACT %r is neither an employee id nor a known Slack user id", contact)
    return ctx.settings.ceo_id


def _escalate(ctx: Any, person: str | None, role: str | None, strikes: list[dict[str, Any]],
              task_id: str | None) -> None:
    target = _compliance_target(ctx)
    rails = sorted({r for s in strikes for r in (s["reason"] or "").split(", ") if r})
    lines = "\n".join(f"{i}. {s['ts']:%Y-%m-%d %H:%M} UTC: “{s['resource']}”" for i, s in enumerate(strikes, 1))
    who = ctx.hr.name(person) if person else "someone"
    text = (f"Compliance alert: {who} ({(role or 'unknown role').replace('_', ' ')}) had {len(strikes)} messages "
            f"blocked by the safety policy within {WINDOW_DAYS} days. Rails: {', '.join(rails) or 'unknown'}.\n"
            f"The blocked prompts:\n{lines}")
    try:
        ctx.notifier.dm(target, text, "NOTIFY", task_id=task_id)
    except Exception:  # noqa: BLE001 - not reported: the counter keeps running, so the next block reports again
        log.exception("could not send the compliance alert")
        return
    ctx.audit.record("agent", "guardrail_escalation", f"slack:{target}", f"{len(strikes)} blocked prompts of "
                     f"{person} reported to compliance", "ALLOW", task_id=task_id, requester=person, role=role,
                     policy=USER_INPUT, reason=", ".join(rails))


# -- 2. tool results and documents ------------------------------------------------------------------------
_NOTIFIED: set[str] = set()
_LOCK = threading.Lock()


def _tell_ceo_once(ctx: Any, task_id: str | None, source: str, text: str, v: Verdict) -> None:
    key = f"{task_id}:{source}:{hashlib.sha1(text.encode()).hexdigest()[:12]}"
    with _LOCK:
        if key in _NOTIFIED:
            return
        if len(_NOTIFIED) > 5000:
            _NOTIFIED.clear()
        _NOTIFIED.add(key)
    try:
        ctx.notifier.dm(ctx.settings.ceo_id, f"Suspicious content from an outside source ({source}) was blocked "
                        f"before Scout read it. Rails: {v.rails}.", "NOTIFY", task_id=task_id)
    except Exception:  # noqa: BLE001
        log.exception("could not tell the owner about suspicious content")


def screen_tool(ctx: Any, task_id: str | None, tool: str, content: str, *, requester: str | None = None) -> str:
    """The text of a tool result for the model: `content`, or the withheld notice. Not a strike for the asker."""
    v = check(ctx, TOOL_RESULT, content, task_id=task_id, requester=requester, source=f"tool {tool}")
    if not v.blocked:
        return content
    if tool in EXTERNAL_TOOLS:
        _tell_ceo_once(ctx, task_id, f"tool {tool}", content, v)
    return WITHHELD


def screen_document(ctx: Any, task_id: str | None, text: str, source: str) -> Verdict:
    """Check document text (invoice PDF, mail, KSeF) before the model reads it. The caller decides what a block
    means (an invoice goes to the CEO's approval card)."""
    return check(ctx, TOOL_RESULT, text, task_id=task_id, source=source)


# -- 3. what Scout says -----------------------------------------------------------------------------------
def screen_output(ctx: Any, task_id: str | None, text: str, *, requester: str | None = None,
                  source: str = "reply") -> str:
    """`text`, or the rephrase request when the output rails block it. Not a strike."""
    v = check(ctx, BOT_OUTPUT, text, role="assistant", task_id=task_id, requester=requester, source=source)
    return OUTPUT_REPLY if v.blocked else text
