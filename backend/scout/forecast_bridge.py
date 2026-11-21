"""The external chat's only local capability: a read-only forecast through Granite.

No prompt, history, provider key or external tool name crosses this interface. The
validated intent is bound by code; Granite can request its execution once, but cannot
choose another tool or rewrite the parameters. Results belong to the local browser,
never to an external provider's tool-result conversation.
"""

from __future__ import annotations

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
    description="Run the single validated read-only cash forecast in the user's request. Parameters are already bound.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)


class ForecastBridgeError(Exception):
    """A safe failure with no forecast or company action performed."""


def _scenario_date(month: str, today: date) -> date:
    if re.fullmatch(r"20\d{2}-\d{2}", month):
        return date(int(month[:4]), int(month[5:]), 1)
    number = list(month_name).index(month)
    return date(today.year + (number < today.month), number, 1)


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
    """Ask the configured on-premise model to spawn exactly one bound forecast."""
    messages = [
        Message(role="system", content="You are the on-premise Granite forecast dispatcher. Call run_forecast "
                "exactly once with {} to execute the validated intent. No other action is available."),
        Message(role="user", content=json.dumps(intent.model_dump(mode="json"))),
    ]
    with usage_context(purpose="forecast_handoff"):
        response = ctx.llm.chat(messages, [RUN_FORECAST], max_tokens=256)
    # Never dispatch arbitrary tools or use the model's proposed parameter values.
    if len(response.tool_calls) != 1:
        raise ForecastBridgeError("Granite did not request the forecasting action. Try again.")
    call = response.tool_calls[0]
    if call.name != RUN_FORECAST.name or call.arguments:
        raise ForecastBridgeError("Granite requested an unsupported action. Nothing was changed.")
    ctx.audit.record("granite", "forecast", "bank", "read-only liquidity forecast", task_id=task.id,
                     requester=requester, policy="forecast-bridge: bound parameters; no company writes")
    step = "Granite started a read-only cash forecast on premise"
    ctx.recorder.step(task.id, "looked_up", step, source="granite", detail={"tool": RUN_FORECAST.name})
    on_step(step)
    review = _review(ctx, intent)
    # Code renders the actual outcome instead of trusting a model's success claim.
    return _report(review, intent)
