"""Validated external forecast requests handed by Granite to the Liquidity queue.

No prompt, history, provider key or external tool name crosses this interface. The
validated intent is bound by code; Granite can request its execution once, but cannot
choose another tool or rewrite the parameters. Results belong to the local browser,
never to an external provider's tool-result conversation.
"""

from __future__ import annotations

import hashlib
import json
import re
from calendar import month_name
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from cfo.llm import Message, ToolSpec
from cfo.usage.recorder import usage_context
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scout.loops.context import JobContext
from scout.models import Task


class ForecastIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(90, ge=1, le=365, strict=True)
    amount_eur: Decimal | None = Field(None, gt=0, le=1_000_000_000, max_digits=12, decimal_places=2)
    month: str | None = Field(None, max_length=20)
    direction: Literal["in", "out"] = "out"

    @field_validator("month")
    @classmethod
    def valid_month(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value in month_name[1:]:
            return value
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
            raise ValueError("Use an English month name or YYYY-MM.")
        return value

    @model_validator(mode="after")
    def scenario_month(self) -> ForecastIntent:
        if self.amount_eur is not None and self.month is None:
            raise ValueError("A what-if amount requires a month.")
        if self.amount_eur is None and self.month is not None:
            raise ValueError("A scenario month requires an amount.")
        return self


class ForecastRequest(ForecastIntent):
    conversation_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{8,64}$")

    def intent(self) -> ForecastIntent:
        return ForecastIntent.model_validate(self.model_dump(exclude={"conversation_id"}))


RUN_FORECAST = ToolSpec(
    name="run_forecast",
    description="Queue the validated read-only cash forecast for the Liquidity worker. Parameters are already bound.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)
DISPATCH_SYSTEM = (
    "You are the on-premise Granite forecast dispatcher. The server has validated the read-only forecast "
    "request and bound all parameters. Call run_forecast exactly once with {} to enqueue its Liquidity job. "
    "The worker will execute it later; do not claim it has completed. Do not supply forecast "
    "parameters or any other arguments. No other action is available."
)
DISPATCH_REQUEST = "Run the validated forecast now."


class ForecastBridgeError(Exception):
    """A safe failure with no forecast or company action performed."""


def _empty_arguments(arguments: dict[str, Any]) -> bool:
    """Granite may quote its JSON arguments; accept only an encoded empty object.

    The OpenAI adapter preserves non-object arguments under `_raw`. Decoding this
    one known shape never permits the model to supply or change forecast inputs.
    """
    if not arguments:
        return True
    if set(arguments) != {"_raw"} or not isinstance(arguments["_raw"], str):
        return False
    try:
        value = json.loads(arguments["_raw"])
        if isinstance(value, str):
            value = json.loads(value)
    except (ValueError, TypeError):
        return False
    return isinstance(value, dict) and not value


def _scenario_date(month: str, today: date) -> date:
    if re.fullmatch(r"20\d{2}-\d{2}", month):
        return date(int(month[:4]), int(month[5:]), 1)
    number = list(month_name).index(month)
    return date(today.year + (number < today.month), number, 1)


def task_key(intent: ForecastIntent, requester: str, conversation: str) -> str:
    scenario = intent.model_dump(mode="json")
    if intent.amount_eur is not None:
        scenario["amount_eur"] = f"{intent.amount_eur:.2f}"
    digest = hashlib.sha256(json.dumps(scenario, sort_keys=True).encode()).hexdigest()[:32]
    return f"web-forecast:{requester}:{conversation}:{digest}"


def _review(ctx: JobContext, intent: ForecastIntent) -> Any:
    from cfo.forecast import features
    from cfo.forecast import model as fm
    from cfo.forecast.liquidity import review_liquidity

    outflows = []
    if intent.amount_eur is not None:
        outflows.append({"on": _scenario_date(intent.month, ctx.now().date()), "amount": intent.amount_eur,
                         "direction": intent.direction, "label": "Chat forecast scenario"})
    with ctx.conn_factory() as conn:
        # A second boundary behind the tool allowlist: the forecast may read the
        # authenticated organization's rows, but may not modify any company rows.
        conn.execute("SET TRANSACTION READ ONLY")
        transactions = features.fetch_transactions(conn)
        if transactions.empty:
            raise ForecastBridgeError("There are no bank transactions to forecast from.")
        days = intent.horizon_days
        if outflows:
            # The forecasting engine starts after the last booking, which may be
            # older than today in a snapshot. Include the scenario and 45 days
            # after it, while preserving the bridge's one-year resource limit.
            last_booking = transactions["booking_date"].max().date()
            scenario_day = outflows[0]["on"]
            required_days = (scenario_day - last_booking).days + 45
            if scenario_day <= last_booking or required_days > 365:
                raise ForecastBridgeError("Choose a scenario month after the latest bank booking and within "
                                          "its next 320 days, so the forecast can include 45 days afterward.")
            days = max(days, required_days)
        # Train only on this request's tenant rows. Never consult or update a
        # filesystem model cache, and never export company data to MLflow.
        model = fm.train(transactions, log_to_mlflow=False)
        return review_liquidity(outflows, conn=conn, transactions=transactions, model=model, days=days)


def _money(value: Any) -> str:
    return f"€{Decimal(str(value)):,.2f}"


def _report(review: Any, intent: ForecastIntent) -> str:
    frame = review.forecast
    if frame.empty or "balance" not in frame:
        raise ForecastBridgeError("The local forecast returned no balance data.")
    low_day = frame["balance"].idxmin()
    first, last = frame.index[0], frame.index[-1]
    report = (f"Granite ran the {len(frame)}-day cash forecast locally "
              f"({first:%Y-%m-%d} to {last:%Y-%m-%d}).\n\n"
              f"Predicted closing balance: **{_money(frame.iloc[-1]['balance'])}**. "
              f"Lowest predicted balance: **{_money(frame.loc[low_day, 'balance'])}** on {low_day:%Y-%m-%d}. "
              f"Safety buffer: **{_money(review.buffer)}**. "
              f"{len(review.gaps)} period(s) fall below that buffer.")
    if intent.amount_eur is not None:
        report += (f"\n\nScenario: {_money(intent.amount_eur)} "
                   f"{'coming in' if intent.direction == 'in' else 'going out'} in {intent.month}.")
    return report + "\n\nForecast results stay on premise and are shown here only. No company records were changed."


def run(ctx: JobContext, task: Task, intent: ForecastIntent, requester: str,
        on_step: Callable[[str], None]) -> str:
    """Ask Granite to enqueue a bound forecast; only the Liquidity worker runs it."""
    lock_id = int.from_bytes(hashlib.sha256(task.dedupe_key.encode()).digest()[:8], signed=True)
    with ctx.conn_factory() as lock:
        lock.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
        return _enqueue(ctx, task, intent, requester, on_step)


def _enqueue(ctx: JobContext, task: Task, intent: ForecastIntent, requester: str,
             on_step: Callable[[str], None]) -> str:
    if any(step.kind == "queued" for step in ctx.recorder.steps(task.id)):
        # Also repairs an interrupted handoff between recording and enqueueing.
        ctx.jobs.enqueue("chat_forecast", {"task_id": task.id, "intent": intent.model_dump(mode="json")},
                         task.dedupe_key)
        return f"Forecast task {task.id} is available in Loops → Liquidity. Open it to see its status and results."
    # The action is already bound to the validated intent. Giving Tiny the JSON
    # again makes it copy those fields into the empty-argument tool call.
    messages = [Message(role="system", content=DISPATCH_SYSTEM), Message(role="user", content=DISPATCH_REQUEST)]
    with usage_context(purpose="forecast_handoff"):
        response = ctx.llm.chat(messages, [RUN_FORECAST], max_tokens=256)
    # Never dispatch arbitrary tools or use the model's proposed parameter values.
    if len(response.tool_calls) != 1:
        raise ForecastBridgeError("Granite did not request the forecasting action. Try again.")
    call = response.tool_calls[0]
    if call.name != RUN_FORECAST.name or not _empty_arguments(call.arguments):
        raise ForecastBridgeError("Granite requested an unsupported action. Nothing was changed.")
    ctx.audit.record("granite", "forecast", "bank", "read-only liquidity forecast", task_id=task.id,
                     requester=requester, policy="forecast-bridge: bound parameters; no company writes")
    step = f"Granite queued forecast task {task.id} in Liquidity"
    ctx.recorder.set_status(task.id, "todo")
    ctx.recorder.step(task.id, "queued", step, source="granite", detail={"tool": RUN_FORECAST.name})
    ctx.jobs.enqueue("chat_forecast", {"task_id": task.id, "intent": intent.model_dump(mode="json")},
                     task.dedupe_key)
    on_step(step)
    return (f"Queued forecast task **{task.id}** in **Loops → Liquidity**. "
            "The Liquidity worker will run it and save the results there. No company records were changed.")


def complete(ctx: JobContext, task: Task, intent: ForecastIntent) -> Task:
    """Execute within the Liquidity worker, preserving tenant-local forecasting."""
    from scout import guardrails

    ctx.recorder.set_status(task.id, "in_progress")
    ctx.recorder.step(task.id, "picked_up", "Liquidity worker started the queued cash forecast", source="forecast")
    report = _report(_review(ctx, intent), intent)
    report = guardrails.screen_output(ctx, task.id, report, requester=task.requested_by,
                                     source="local forecast reply", role=ctx.hr.role(task.requested_by))
    ctx.recorder.step(task.id, "forecast_result", report, source="forecast")
    return ctx.recorder.set_status(task.id, "done", outcome="Cash forecast completed", resolved_via="forecast",
                                   level="AUTO")
