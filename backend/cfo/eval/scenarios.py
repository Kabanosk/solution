"""Scenario file format. See `scenarios.yaml` for the documented example set.

A scenario is: `seed` (what is in the company on top of the base company of `cfo.eval.harness`), `steps`
(played in order) and `expect` (what the scorers of `cfo.eval.scoring` compare against).

Steps, one key each:

* `event`: an agent event. `{kind, payload}`; `transaction_id` is a shortcut for `payload.transaction_id`;
  `repeat: n` sends it n times (reminders), each one simulated day after the previous step.
* `answer`: the CEO's click on the pending approval, `{approved: true|false, by: vincenzo}`.
* `classify`: a payment handed to the policy engine with the context of the seeded bank statement,
  `{action_type, amount, supplier, category}`. The agent graph has no payment step of its own, so the D-005
  payment rules are evaluated where they live (`cfo.agent.policy.classify`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_PATH = Path(__file__).with_name("scenarios.yaml")
STEP_KINDS = ("event", "answer", "classify")


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    away: list[str] = Field(default_factory=list)  # employees on vacation for the whole of December 2025
    managers: dict[str, str] = Field(default_factory=dict)  # override: employee id -> manager id
    transactions: list[dict[str, Any]] = Field(default_factory=list)  # {id, date, counterparty, amount, invoice_number, ...}
    files: dict[str, str] = Field(default_factory=dict)  # path in the office manager's company folders -> text
    rules: list[dict[str, Any]] = Field(default_factory=list)  # CEO rules: {text, level, action_type|category|supplier}
    cost_metrics: list[dict[str, Any]] = Field(default_factory=list)  # {kind, month, team_id, cost, metric_value, detail}


class Expect(BaseModel):
    """Every field is optional; a scorer skips a scenario that names none of its fields."""

    model_config = ConfigDict(extra="forbid")

    level: str | None = None  # AUTO | NOTIFY | APPROVAL | NONE (no decision): highest level among the decisions
    actions: list[str] | None = None  # action types of the decisions, oldest first
    recipient: str | None = None  # who the last decision was addressed to
    documented: dict[str, bool] | None = None  # transaction id -> has an invoice afterwards
    outcome: str | None = None  # status of the last step: done | awaiting_approval | ignored | already_resolved
    status: str | None = None  # status of the last decision: executed | pending | rejected | escalated
    executed_simulated: bool | None = None  # of the last decision
    says: list[str] | None = None  # fixed wording that must be in the messages (labels such as SIMULATED)
    cites: list[str] | None = None  # numbers (EUR amounts, percentages) the user-facing text must contain


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    source: str = ""  # where the expectation comes from: demo-flow step, decision id
    seed: Seed = Field(default_factory=Seed)
    steps: list[dict[str, Any]]
    expect: Expect

    @model_validator(mode="after")
    def _steps_are_known(self) -> Scenario:
        if not self.steps:
            raise ValueError(f"scenario {self.id}: no steps")
        for step in self.steps:
            if len(step) != 1 or next(iter(step)) not in STEP_KINDS:
                raise ValueError(f"scenario {self.id}: a step has exactly one of {STEP_KINDS}, got {sorted(step)}")
        return self

    @property
    def target(self) -> str:
        """`policy` when the scenario only asks the policy engine, else `agent` (the graph)."""
        return "policy" if all("classify" in s for s in self.steps) else "agent"


def load_scenarios(path: str | Path | None = None, only: list[str] | None = None) -> list[Scenario]:
    raw = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    items = raw.get("scenarios") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path or DEFAULT_PATH}: expected a list of scenarios")
    scenarios = [Scenario.model_validate(x) for x in items]
    ids = [s.id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate scenario ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    if only:
        unknown = sorted(set(only) - set(ids))
        if unknown:
            raise ValueError(f"unknown scenario ids: {unknown}")
        scenarios = [s for s in scenarios if s.id in only]
    return scenarios
