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
reason = the rails that fired), `guardrail_unavailable`, `guardrail_escalation` and `guardrail_escalation_failed`.
The checked content itself never goes into `audit_log` (the audit API is readable): a row holds the config id, the
rails and the length and a short hash of the text. The full blocked prompts of people live in `guardrail_strikes`,
which no API exposes; the compliance DM is built from it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cfo.observability import span

log = logging.getLogger(__name__)

USER_INPUT, TOOL_RESULT, BOT_OUTPUT = "user-input", "tool-result", "bot-output"
BOT_OUTPUT_FINANCE = "bot-output-finance"  # replies to the owner and the office manager: only the link allow-list
FINANCE_ROLES = frozenset({"ceo", "office_manager"})  # HR role codes that may see bank, mail and payroll data
DASHBOARD_PLACEHOLDER = "http://dashboard.svc.cluster.local"  # an allow-listed host that stands for the real one
MAX_STRIKES = 3
WINDOW_DAYS = 7
TIMEOUT_S = 5.0
WARMUP_TIMEOUT_S = 30.0
PROMPT_CHARS = 700  # per prompt, only when the full prompts could not be uploaded as a file to the compliance DM
CHECK_CHARS = 8000  # one message to the service; longer texts are cut into overlapping chunks, all of them checked
CHUNK_OVERLAP = 300  # so an instruction cut at a chunk boundary is still whole in one of them
MAX_CHUNKS = 100

WITHHELD = "[content withheld: flagged by guardrail]"
OUTPUT_REPLY = "I could not share that answer safely. Please rephrase your question."
SLACK_ID = re.compile(r"^[UW][A-Z0-9_]{2,}$")
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
                 http: httpx.Client | None = None, verify: str | bool = True):
        self.url, self.token, self.timeout, self.model = url.rstrip("/"), token, timeout, model
        self._http, self._verify = http, verify

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def check(self, config_id: str, messages: list[dict[str, str]], *, timeout: float | None = None) -> Verdict:
        timeout = timeout or self.timeout
        if not self.enabled:
            return Verdict("disabled")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        body = {"model": self.model, "messages": messages, "guardrails": {"config_id": config_id}}
        started = time.monotonic()
        try:
            client = self._http or httpx.Client(timeout=timeout, verify=self._verify)
            try:
                r = client.post(f"{self.url}/v1/guardrail/checks", json=body, headers=headers, timeout=timeout)
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


    def warm_up(self, timeout: float = WARMUP_TIMEOUT_S) -> Verdict:
        """The first call after a NeMo restart takes 10-25 s (models load): the worker fires this once at start so
        the first real message does not hit the short per-check timeout."""
        return self.check(USER_INPUT, [{"role": "user", "content": "hello"}], timeout=timeout)


def from_settings(settings: Any) -> HttpGuardrails:
    """The service CA of OpenShift verifies the in-cluster https URL when its file exists, else the system CAs."""
    ca = getattr(settings, "guardrails_ca", "") or ""
    return HttpGuardrails(getattr(settings, "guardrails_url", "") or "", getattr(settings, "guardrails_token", "") or "",
                          verify=ca if ca and os.path.isfile(ca) else True)


# -- one check: tracing, fail-open, audit -----------------------------------------------------------------
def _digest(text: str) -> str:
    return f"{len(text)} chars, sha1:{hashlib.sha1(text.encode()).hexdigest()[:10]}"


def _messages(config_id: str, texts: list[str]) -> list[dict[str, str]]:
    """The shape each config expects: `user` messages, `tool` messages (with a tool_call_id), one `assistant`."""
    chunks = [c for t in texts for c in _chunks(t)]
    if config_id == TOOL_RESULT:
        return [{"role": "tool", "tool_call_id": f"scout-{i}", "content": c} for i, c in enumerate(chunks, 1)]
    if config_id in (BOT_OUTPUT, BOT_OUTPUT_FINANCE):
        return [{"role": "assistant", "content": c} for c in chunks]
    return [{"role": "user", "content": c} for c in chunks]


def _chunks(text: str) -> list[str]:
    """The whole text, as messages of at most CHECK_CHARS that overlap a little (each is checked on its own)."""
    if len(text) <= CHECK_CHARS:
        return [text]
    step = CHECK_CHARS - CHUNK_OVERLAP
    return [text[i:i + CHECK_CHARS] for i in range(0, len(text) - CHUNK_OVERLAP, step)]


def check(ctx: Any, config_id: str, texts: str | list[str], *, task_id: str | None = None,
          requester: str | None = None, source: str = "", record_block: bool = True) -> Verdict:
    """Run one check over one text (or several, in one request). Never raises. A blocking verdict is audited here
    (DENY, without the content) unless `record_block` is False."""
    texts = [texts] if isinstance(texts, str) else list(texts)
    texts = [t for t in texts if t.strip()]
    client = getattr(ctx, "guard", None)
    if client is None or not getattr(client, "enabled", False) or not texts:
        return Verdict("disabled")
    chars = sum(len(t) for t in texts)
    if sum(len(_chunks(t)) for t in texts) > MAX_CHUNKS:  # never pass on what could not be checked whole
        v = Verdict("blocked", ("too long to check",))
        if record_block:
            _audit_block(ctx, config_id, source, texts, v, task_id, requester)
        return v
    with span("guardrails.check", span_type="TOOL",
              inputs={"config_id": config_id, "source": source, "messages": len(texts), "chars": chars}) as s:
        try:
            v = client.check(config_id, _messages(config_id, texts))
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
            _audit_block(ctx, config_id, source, texts, v, task_id, requester)
    except Exception:  # noqa: BLE001 - an audit failure must not turn a verdict into a crash
        log.exception("could not audit the guardrail verdict")
    return v


def _audit_block(ctx: Any, config_id: str, source: str, texts: list[str], v: Verdict, task_id: str | None,
                 requester: str | None) -> None:
    try:
        ctx.audit.record("agent", "guardrail_check", f"guardrails:{config_id}",
                         f"{source or config_id}: {_digest(chr(10).join(texts))}", "BLOCK", task_id=task_id,
                         requester=requester, policy=config_id, reason=v.rails)
    except Exception:  # noqa: BLE001
        log.exception("could not audit the guardrail verdict")


def _audit(conn: Any, action: str, target: str, resource: str, decision: str, *, task_id: str | None,
           requester: str | None, role: str | None, policy: str, reason: str) -> None:
    conn.execute("INSERT INTO audit_log (task_id, actor, requester, role, action, target, resource, decision, policy, "
                 "reason) VALUES (%s, 'agent', %s, %s, %s, %s, %s, %s, %s, %s)",
                 (task_id, requester, role, action, target, resource[:500], decision, policy, reason))


# -- 1. human messages: strikes and compliance ------------------------------------------------------------
def screen_human(ctx: Any, person: str | None, text: str, *, role: str | None = None, task_id: str | None = None,
                 ) -> str | None:
    """Check the current human message, on its own. None when it may go on; else the reply that tells the person
    it was blocked. One block is one strike; the third in 7 days reaches the compliance contact. (Earlier turns of
    a resent dashboard history never add strikes: see `screen_history`.)"""
    texts = [text]
    v = check(ctx, USER_INPUT, texts, task_id=task_id, requester=person, record_block=False)
    if not v.blocked:
        return None
    try:
        n = _strike(ctx, person, role, "\n---\n".join(t for t in texts if t.strip()), v.rails, task_id)
    except Exception:  # noqa: BLE001 - e.g. the tables are not there yet: the message is still blocked
        log.exception("could not record the strike")
        n = 1
    return blocked_reply(min(n, MAX_STRIKES))


def _strike(ctx: Any, person: str | None, role: str | None, prompt: str, rails: str, task_id: str | None) -> int:
    """Record the block, count the person's open strikes and, at three, report them: all in one transaction
    under a per-person lock, so two blocks at once cannot send two alerts or lose a strike. The counter only
    resets (strikes marked reported) when the alert was delivered."""
    who = person or "unknown"
    with ctx.conn_factory() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"guardrail-strikes:{who}",))
        conn.execute("INSERT INTO guardrail_strikes (person, prompt, rails) VALUES (%s, %s, %s)",
                     (who, prompt, rails))
        _audit(conn, "guardrail_check", f"guardrails:{USER_INPUT}", f"{USER_INPUT}: {_digest(prompt)}", "BLOCK",
               task_id=task_id, requester=person, role=role, policy=USER_INPUT, reason=rails)
        strikes = conn.execute(
            "SELECT id, ts, prompt, rails FROM guardrail_strikes WHERE person = %s AND reported_at IS NULL "
            "AND ts > now() - %s * interval '1 day' ORDER BY id", (who, WINDOW_DAYS)).fetchall()
        if len(strikes) >= MAX_STRIKES:
            _report(ctx, conn, person, who, role, strikes, task_id)
        conn.commit()
    return len(strikes)


def _compliance_target(ctx: Any) -> str:
    """COMPLIANCE_CONTACT: an employee id, or a Slack user id used as it is; empty means the CEO."""
    contact = (getattr(ctx.settings, "compliance_contact", "") or "").strip()
    if not contact:
        return ctx.settings.ceo_id
    if ctx.hr.employee(contact):
        return contact
    emp = ctx.hr.by_slack_id(contact)
    return emp["id"] if emp else contact


def _report(ctx: Any, conn: Any, person: str | None, who: str, role: str | None, strikes: list[dict[str, Any]],
            task_id: str | None) -> None:
    target = _compliance_target(ctx)
    rails = sorted({r for s in strikes for r in (s["rails"] or "").split(", ") if r})
    listing = "\n\n".join(f"{i}. {s['ts']:%Y-%m-%d %H:%M} UTC\n{s['prompt']}" for i, s in enumerate(strikes, 1))
    head = (f"Compliance alert: {ctx.hr.name(person) if person else 'someone'} "
            f"({(role or 'unknown role').replace('_', ' ')}) had {len(strikes)} messages blocked by the safety "
            f"policy within {WINDOW_DAYS} days. Rails: {', '.join(rails) or 'unknown'}.")
    link = None
    try:  # the full prompts go as a file, so nothing is cut; the DM stays short
        link = ctx.notifier.upload(target, [(f"blocked-prompts-{who}.txt", listing.encode())],
                                   f"The {len(strikes)} blocked prompts of {who}, in full.")
    except Exception:  # noqa: BLE001
        log.exception("could not upload the blocked prompts")
    if link:
        text = f"{head}\nThe full prompts are in the file sent with this message."
    else:  # no file possible: put the prompts in the message, shortened to what Slack can carry
        short = "\n".join(f"{i}. {s['ts']:%Y-%m-%d %H:%M} UTC: “{s['prompt'][:PROMPT_CHARS]}”"
                          f"{' (shortened)' if len(s['prompt']) > PROMPT_CHARS else ''}"
                          for i, s in enumerate(strikes, 1))
        text = f"{head}\nThe blocked prompts:\n{short}"
    ref: Any = None
    try:
        ref = ctx.notifier.dm(target, text, "NOTIFY", task_id=task_id)
    except Exception:  # noqa: BLE001
        log.exception("could not send the compliance alert")
    if ref is None:  # nobody was told: keep the strikes, so the next block reports again
        log.warning("the compliance alert for %s to %s was not delivered", who, target)
        _audit(conn, "guardrail_escalation_failed", f"slack:{target}", f"{len(strikes)} blocked prompts of {who} not "
               "delivered to compliance", "DENY", task_id=task_id, requester=person, role=role, policy=USER_INPUT,
               reason=", ".join(rails))
        return
    conn.execute("UPDATE guardrail_strikes SET reported_at = now() WHERE id = ANY(%s)", ([s["id"] for s in strikes],))
    _audit(conn, "guardrail_escalation", f"slack:{target}", f"{len(strikes)} blocked prompts of {who} reported to "
           "compliance", "ALLOW", task_id=task_id, requester=person, role=role, policy=USER_INPUT,
           reason=", ".join(rails))


# -- 2. tool results and documents ------------------------------------------------------------------------
def _sha(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _outside_items(tool: str, result: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """(item key, what to call it, text to check) for each outside item of a tool result: a mail, a file, a chat
    message. The key names the item itself (not the answer it came in), so it is the same on every search."""
    r = result or {}
    if tool == "read_file" and r.get("path"):
        return [(f"file:{r['path']}", f"file {r['path']}", str(r.get("text") or ""))]
    if tool == "search_files":
        return [(f"file:{p}", f"file {p}", str(p)) for p in r.get("files") or []]
    if tool == "search_email":
        return [(f"mail:{_sha(m.get('from'), m.get('subject'), m.get('date'))}",
                 f"mail “{str(m.get('subject'))[:80]}” from {m.get('from')}", json.dumps(m, default=str))
                for m in r.get("mails") or []]
    if tool == "search_chat":
        return [(f"chat:{_sha(m.get('channel'), m.get('from'), m.get('text'))}",
                 f"Slack message in {m.get('channel')} from {m.get('from')}", str(m.get("text") or ""))
                for m in r.get("messages") or []]
    return []


def alert_outside(ctx: Any, key: str, label: str, rails: str, task_id: str | None) -> None:
    _notify_ceo_once(ctx, key, label, rails, task_id)


def _notify_ceo_once(ctx: Any, key: str, label: str, rails: str, task_id: str | None) -> None:
    """DM the owner once per outside item, remembered in Postgres. Marked only after the DM was delivered."""
    try:
        with ctx.conn_factory() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"guardrail-alert:{key}",))
            if conn.execute("SELECT 1 FROM guardrail_alerts WHERE item_key = %s", (key,)).fetchone():
                return
            ref = ctx.notifier.dm(ctx.settings.ceo_id, f"Suspicious content from an outside source ({label}) was "
                                  f"blocked before Scout read it. Rails: {rails}.", "NOTIFY", task_id=task_id)
            if ref is not None:
                conn.execute("INSERT INTO guardrail_alerts (item_key, rails) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (key, rails))
                _audit(conn, "guardrail_escalation", f"slack:{ctx.settings.ceo_id}", f"owner told about {label}",
                       "ALLOW", task_id=task_id, requester=None, role=None, policy="suspicious-content", reason=rails)
            conn.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not tell the owner about suspicious content")


def _alert_outside(ctx: Any, task_id: str | None, tool: str, content: str, result: dict[str, Any] | None,
                   v: Verdict) -> None:
    flagged: list[tuple[str, str, str]] = []
    items = _outside_items(tool, result)
    if len(items) == 1:
        flagged = [(items[0][0], items[0][1], v.rails)]
    else:  # which of several items? ask the service about each one (only when something was flagged)
        for key, label, text in items:
            iv = check(ctx, TOOL_RESULT, text, task_id=task_id, source=f"{tool} item", record_block=False)
            if iv.blocked:
                flagged.append((key, label, iv.rails))
    if not flagged:
        flagged = [(f"tool:{tool}:{_sha(content)}", f"tool {tool}", v.rails)]
    for key, label, rails in flagged:
        _notify_ceo_once(ctx, key, label, rails, task_id)


def screen_tool(ctx: Any, task_id: str | None, tool: str, content: str, *, requester: str | None = None,
                result: dict[str, Any] | None = None) -> str:
    """The text of a tool result for the model: `content`, or the withheld notice. Not a strike for the asker."""
    v = check(ctx, TOOL_RESULT, content, task_id=task_id, requester=requester, source=f"tool {tool}")
    if not v.blocked:
        return content
    if tool in EXTERNAL_TOOLS:
        _alert_outside(ctx, task_id, tool, content, result, v)
    return WITHHELD


def screen_document(ctx: Any, task_id: str | None, text: str, source: str) -> Verdict:
    """Check document text (invoice PDF, mail, KSeF) before the model reads it. The caller decides what a block
    means (an invoice goes to the CEO's approval card)."""
    return check(ctx, TOOL_RESULT, text, task_id=task_id, source=source)


# -- 3. what Scout says -----------------------------------------------------------------------------------
# Masks for what a reply to a non-finance person must not carry (the NeMo `bot-output` regexes, as replacements).
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")  # upper case: "toda" is no IBAN block
_CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+\d[\d ()./-]{7,}\d|\b\d{3}[ -]\d{3}[ -]\d{3}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _mask_card(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group())
    return "[card hidden]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group()


def mask_sensitive(text: str) -> tuple[str, list[str]]:
    """`text` with IBANs, card numbers (13-19 digits that pass the Luhn check), e-mail addresses and phone numbers
    replaced; the kinds that were found."""
    kinds: list[str] = []
    for kind, pattern, repl in (("iban", _IBAN_RE, "[IBAN hidden]"), ("card", _CARD_RE, _mask_card),
                                ("email", _EMAIL_RE, "[email hidden]"), ("phone", _PHONE_RE, "[phone hidden]")):
        text, n = pattern.subn(repl, text)
        if kind == "card":
            n = text.count("[card hidden]")
        if n:
            kinds.append(kind)
    return text, kinds


def screen_output(ctx: Any, task_id: str | None, text: str, *, requester: str | None = None,
                  source: str = "reply", role: str | None = None) -> str:
    """`text`, or the rephrase request when the output rails block it. Not a strike. `role` is the RECIPIENT's HR role
    code: the owner and the office manager get the light `bot-output-finance` check (links only); everyone else gets
    IBANs, cards, e-mails and phone numbers masked first (audit MASK) and the masked text checked by `bot-output`.
    Links to Scout's own dashboard are swapped for a placeholder host during the check and restored after."""
    client = getattr(ctx, "guard", None)
    if client is None or not getattr(client, "enabled", False):
        return text
    dash = (getattr(ctx.settings, "dashboard_url", "") or "").rstrip("/")
    swap = dash.startswith("http") and dash in text
    work = text.replace(dash, DASHBOARD_PLACEHOLDER) if swap else text
    if role in FINANCE_ROLES:
        config = BOT_OUTPUT_FINANCE
    else:
        config = BOT_OUTPUT
        work, kinds = mask_sensitive(work)
        if kinds:
            try:
                ctx.audit.record("agent", "guardrail_mask", f"guardrails:{BOT_OUTPUT}", f"masked: {', '.join(kinds)} in "
                                 f"{source}", "MASK", task_id=task_id, requester=requester, role=role,
                                 policy="bot-output-mask", reason=", ".join(kinds))
            except Exception:  # noqa: BLE001
                log.exception("could not audit the masking")
    v = check(ctx, config, work, task_id=task_id, requester=requester, source=source)
    if v.blocked:
        return OUTPUT_REPLY
    return work.replace(DASHBOARD_PLACEHOLDER, dash) if swap else work


# -- 4. earlier turns the client sent --------------------------------------------------------------------
def _batches(turns: list[dict[str, str]], idx: list[int]) -> list[list[int]]:
    """Group turn indexes so that one `/checks` call never needs more than MAX_CHUNKS messages."""
    out: list[list[int]] = [[]]
    used = 0
    for i in idx:
        n = len(_chunks(turns[i]["content"]))
        if out[-1] and used + n > MAX_CHUNKS:
            out.append([])
            used = 0
        out[-1].append(i)
        used += n
    return out


def screen_history(ctx: Any, turns: list[dict[str, str]], *, task_id: str | None = None,
                   requester: str | None = None) -> list[dict[str, str]]:
    """The earlier turns of a dashboard conversation as the model may see them. The client supplies them, so Scout
    cannot know who wrote them: assistant turns are checked like tool results, user turns like user input, in
    batches of one request. If a batch blocks, each of its turns is checked on its own, and a flagged turn (or one
    whose recheck failed: a batch already blocked) becomes the withheld notice. No strike: the current message is
    the only one a person is answerable for."""
    out = list(turns)
    for role, config in (("assistant", TOOL_RESULT), ("user", USER_INPUT)):
        idx = [i for i, t in enumerate(turns) if t["role"] == role and t["content"].strip()]
        for batch in _batches(turns, idx):
            if not batch:
                continue
            v = check(ctx, config, [turns[i]["content"] for i in batch], task_id=task_id, requester=requester,
                      source=f"earlier {role} turns")
            if not v.blocked:
                continue
            for i in batch:
                iv = check(ctx, config, turns[i]["content"], task_id=task_id, requester=requester,
                           source=f"earlier {role} turn", record_block=False)
                if iv.blocked or iv.failed:
                    out[i] = {**turns[i], "content": WITHHELD}
    return out
