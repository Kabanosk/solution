"""What the agent does with free text from the chat channel (demo steps 5 and 7).

Only the CEO talks to the agent this way. Two things are understood:

* a policy sentence ("I always approve equipment myself") becomes a `Rule` (D-005), stored with a `create_rule`
  entry in the decision history;
* a what-if purchase ("what if I buy an oven for 30,000 EUR in March?") runs the liquidity forecast with that
  outflow (D-013 b). If the balance would fall below the safety buffer, the credit line proposal is handed to
  the agent, which asks for approval with buttons.

One LLM call picks between the two tools and fills their arguments; everything after that is code. The numbers
in replies come from the forecast, never from the model.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from cfo.agent.graph import Agent
from cfo.agent.llm_tasks import parse_amount
from cfo.agent.policy import _RULE_TOOL, STRATEGIC_ACTION_TYPES, parse_rule
from cfo.channels.base import ChatMessage, ChatReply
from cfo.db.repo import Repository
from cfo.domain import Decision, PolicyLevel, Rule
from cfo.forecast import liquidity as liq
from cfo.llm import LLM, LLMResponse, Message, ToolSpec

log = logging.getLogger(__name__)

PURCHASE_TOOL = ToolSpec(
    name="plan_purchase",
    description="The owner asks what happens to the company's cash if it buys or pays for something in a given "
                "month (a what-if question). Do not use it for rules about who approves what.",
    parameters={
        "type": "object",
        "properties": {
            "what": {"type": "string", "description": "what is bought, e.g. oven"},
            "amount_eur": {"type": "string", "description": "price in EUR, digits only, e.g. 30000"},
            "month": {"type": "string", "description": "month of the payment as YYYY-MM, or the month name"},
        },
        "required": ["amount_eur", "month"],
    },
)

SYSTEM = (
    "You are the finance agent of a small restaurant and the owner wrote to you. Call exactly one tool. "
    "create_rule: the owner states a standing rule about what you may do alone, what you must report and what "
    "needs the owner's approval (level AUTO = act alone, NOTIFY = act and inform, APPROVAL = ask first; fill "
    "category, supplier and/or action_type only with what the sentence names). "
    f"Action types: {', '.join(sorted(STRATEGIC_ACTION_TYPES))}, pay_invoice. "
    "plan_purchase: the owner asks what would happen if the company bought something. "
    "If the message is neither, answer in one short sentence without calling a tool."
)

HELP = ("I can do two things here: remember a rule (for example \"I always approve equipment myself\") or check a "
        "scenario against the liquidity forecast (for example \"what if I buy an oven for 30,000 EUR in March?\").")

# Month stems: English, Polish, German. Matched against the start of a word.
_MONTH_STEMS: dict[int, tuple[str, ...]] = {
    1: ("jan", "sty"), 2: ("feb", "lut"), 3: ("mar", "mär", "maer"), 4: ("apr", "kwi"), 5: ("may", "maj", "mai"),
    6: ("jun", "cze"), 7: ("jul", "lip"), 8: ("aug", "sie"), 9: ("sep", "wrz"), 10: ("oct", "okt", "paź", "paz"),
    11: ("nov", "lis"), 12: ("dec", "dez", "gru"),
}


def parse_month(value: Any, after: date) -> date | None:
    """`2026-03`, `3`, `March`, `marcu` -> the 15th of that month. Without a year: the next such month after
    `after` (the last day of the bank history)."""
    s = str(value or "").strip().casefold()
    if not s:
        return None
    m = re.search(r"\b(20\d{2})[-/.](\d{1,2})\b", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return date(int(m.group(1)), int(m.group(2)), 15)
    month = int(s) if s.isdigit() and 1 <= int(s) <= 12 else None
    if month is None:
        for word in re.findall(r"[^\W\d_]+", s):
            month = next((n for n, stems in _MONTH_STEMS.items() if word.startswith(stems)), None)
            if month:
                break
    if month is None:
        return None
    year = re.search(r"\b20\d{2}\b", s)
    if year:
        return date(int(year.group(0)), month, 15)
    return date(after.year + (1 if month <= after.month else 0), month, 15)


def _eur(value: Decimal) -> str:
    return f"{value:,.2f} EUR"


class _Replay:
    """An `LLM` that returns an answer we already have, so `parse_rule` validates it without a second call."""

    def __init__(self, response: LLMResponse):
        self._response = response

    def chat(self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None = None, **_: Any) -> LLMResponse:
        return self._response


class ChatRouter:
    """`handle(ChatMessage) -> ChatReply`: the chat handler registered with the channel.

    `review` runs the liquidity forecast with extra outflows and returns a `LiquidityReview`.
    """

    def __init__(self, *, agent: Agent, llm: LLM, repo_factory: Callable[[], Repository],
                 review: Callable[[Iterable[liq.Outflow]], liq.LiquidityReview], ceo_id: str = "vincenzo"):
        self.agent = agent
        self.llm = llm
        self.repo_factory = repo_factory
        self.review = review
        self.ceo_id = ceo_id

    def handle(self, msg: ChatMessage) -> ChatReply:
        if msg.employee_id != self.ceo_id:
            return ChatReply(text="Only the CEO can give me rules or ask for scenarios here. I write to you when I "
                                  "need a document or have looked into your company folders.")
        text = (msg.text or "").strip()
        if not text:
            return ChatReply(text=HELP)
        try:
            response = self.llm.chat(
                [Message(role="system", content=SYSTEM), Message(role="user", content=f"Owner's sentence: {text}")],
                [_RULE_TOOL, PURCHASE_TOOL])
        except Exception:  # noqa: BLE001 - the model being down must not break the channel
            log.exception("chat: LLM call failed")
            return ChatReply(text="I could not process that right now (the language model did not answer). "
                                  "Please try again in a moment.")
        purchase = next((c for c in response.tool_calls if c.name == PURCHASE_TOOL.name), None)
        if purchase is not None:
            return self._what_if(text, purchase.arguments)
        try:
            rule = parse_rule(text, _Replay(response))
        except ValueError:
            return ChatReply(text=HELP)
        return self._save_rule(rule, msg.employee_id)

    # -- rule (demo step 7) -----------------------------------------------------------------------------------
    def _save_rule(self, rule: Rule, by: str) -> ChatReply:
        rule.created_by = by
        scope = ", ".join(f"{k} = {v}" for k, v in (("category", rule.category), ("supplier", rule.supplier),
                                                     ("action", rule.action_type)) if v)
        meaning = {PolicyLevel.AUTO: "I act on my own", PolicyLevel.NOTIFY: "I act and tell you",
                   PolicyLevel.APPROVAL: "I ask you first"}[rule.level]
        with self.repo_factory() as repo:
            repo.add_rule(rule)
            repo.add_decision(Decision(
                action_type="create_rule", level=PolicyLevel.AUTO, actor=by,
                summary=f'New rule from {by}: "{rule.text}" ({scope} -> {rule.level}).',
                reason="The CEO sets the policy; a rule overrides the default autonomy levels (D-005).",
                payload={"rule_id": rule.id, "level": rule.level.value, "category": rule.category,
                         "supplier": rule.supplier, "action_type": rule.action_type}))
        return ChatReply(text=f"Rule saved: {scope} -> {rule.level.value} ({meaning}). It applies from now on and "
                              "is visible in the rules list.")

    # -- what-if (demo step 5, D-013 b) -----------------------------------------------------------------------
    def _what_if(self, text: str, args: dict[str, Any]) -> ChatReply:
        amount = parse_amount(args.get("amount_eur"))
        with self.repo_factory() as repo:
            last = repo._all("SELECT max(booking_date) AS d FROM transactions")[0]["d"]
        when = parse_month(args.get("month"), last or date.today()) or parse_month(text, last or date.today())
        if amount is None or amount <= 0 or when is None:
            return ChatReply(text="For a scenario I need the amount in EUR and the month, for example: "
                                  "\"what if I buy an oven for 30,000 EUR in March?\"")
        what = str(args.get("what") or "purchase").strip()[:60] or "purchase"
        scenario = f"{what} for {_eur(amount)} in {when:%B %Y}"
        try:
            review = self.review([liq.Outflow(on=when, amount=amount, label=what)])
        except ValueError as e:  # outside the forecast horizon
            return ChatReply(text=f"I cannot check {scenario}: {e}")
        except Exception:  # noqa: BLE001
            log.exception("chat: what-if forecast failed")
            return ChatReply(text=f"I could not run the forecast for {scenario}. Please try again.")
        if not review.gaps:
            low = Decimal(str(round(float(review.forecast["balance"].min()), 2)))
            return ChatReply(text=f"Scenario {scenario}: the forecast balance stays above the safety buffer of "
                                  f"{_eur(review.buffer)} (lowest {_eur(low)}), so no credit line is needed.")
        proposal = review.proposals[0]
        result = self.agent.handle(proposal.to_event())
        head = (f"Scenario {scenario}: the forecast balance would fall to {_eur(proposal.min_balance)}, below the "
                f"safety buffer of {_eur(proposal.buffer)}. ")
        if result.status == "awaiting_approval":
            return ChatReply(level=PolicyLevel.APPROVAL, text=(
                f"{head}I prepared a {proposal.instrument} request of {_eur(proposal.amount)} "
                f"({proposal.period_start.isoformat()} to {proposal.period_end.isoformat()}); it waits for your "
                "decision in the message with the Approve / Reject buttons."))
        return ChatReply(level=PolicyLevel.NOTIFY, text=(
            f"{head}Under your rules I recorded a {proposal.instrument} request of {_eur(proposal.amount)} "
            "(execution is SIMULATED)."))
