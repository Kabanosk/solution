"""Investigation sub-agent (D-005, D-006): a small LangGraph the main agent runs on its own when a metric event
arrives. Detection is code (`cfo.analysis.cost_metrics.detect_anomalies`: cost rose a lot while the metric did
not, with the top contributing line items); the LLM only words the finding, and code checks that its
sentence still states the cost change in EUR, else the deterministic sentence is used (T049).

gather -> detect -> explain.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cfo import observability as obs
from cfo.agent.llm_tasks import explain
from cfo.agent.tools import Gateway
from cfo.analysis.cost_metrics import PAIRS, CostAnomaly, detect_anomalies
from cfo.domain import CostMetric, Invoice
from cfo.llm import LLM


class InvState(TypedDict, total=False):
    rows: list[dict[str, Any]]
    invoices: dict[str, Invoice]
    findings: list[dict[str, Any]]


INSTRUCTION = (
    "Explain why this cost looks off compared to the metric it should follow. Lead with the cost change: by how "
    "many percent the cost rose and from how many EUR to how many EUR (if new_cost is true, call it a new cost and "
    "give its amount instead of a percent). Then the metric change in percent. Then the cost per unit, before and "
    "now. All amounts are in EUR: write EUR next to every amount, never 'units'."
)

_NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![.,]?\d)")
_NEW = re.compile(r"\bnew\b", re.IGNORECASE)


def _pct1(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _eur(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _facts(a: CostAnomaly) -> dict[str, Any]:
    """What the LLM may word: percents to one decimal, amounts as EUR strings (all numbers come from code)."""
    spec = PAIRS[a.kind]
    facts: dict[str, Any] = {
        "what": spec.cost_noun, "month": a.month.isoformat(), "currency": "EUR",
        "cost_before_eur": _eur(a.previous_cost), "cost_now_eur": _eur(a.cost),
        "cost_change_pct": _pct1(a.cost_change_pct),
        "metric": spec.metric_noun, "metric_change_pct": _pct1(a.metric_change_pct),
        "cost_per_unit_name": spec.unit_cost_noun,
        "cost_per_unit_before": _eur(a.previous_cost_per_unit), "cost_per_unit_now": _eur(a.cost_per_unit),
        "cost_per_unit_change_pct": _pct1(a.cost_per_unit_change_pct),
    }
    if a.cost_change_pct is None:  # the cost rose from zero (T044): there is no percentage to quote
        facts["new_cost"] = True
    return facts


def states_cost_change(text: str, facts: dict[str, Any]) -> bool:
    """True when `text` names the currency and the cost change of `facts`: the percent as a number of its own
    ("50", "50.0" or "50,0" for 50.0; not the 50 in "300.50" or "150"), or the word "new" for a new cost."""
    if "EUR" not in text and "€" not in text:
        return False
    want = facts.get("cost_change_pct")
    if want is None:
        return bool(_NEW.search(text))
    target = Decimal(str(want))
    return any(Decimal(n.replace(",", ".")) == target for n in _NUMBER.findall(text))


def build_investigation_graph(gw: Gateway, llm: LLM, threshold_pct: float = 20.0):
    def gather(state: InvState) -> InvState:
        rows = sorted(gw.cost_metrics(), key=lambda r: (r["kind"], r["month"]))
        # the invoices the metrics point at (ad spend): without them the alert cannot name the contributing lines
        numbers = {str(r["detail"]["invoice_number"]) for r in rows
                   if isinstance(r.get("detail"), dict) and r["detail"].get("invoice_number")}
        invoices: dict[str, Invoice] = {}
        for number in sorted(numbers):
            try:
                inv = gw.invoice(number)
            except Exception:  # noqa: BLE001 - a missing invoice only means fewer contributing lines
                inv = None
            if inv is not None:
                invoices[number] = inv
        return {"rows": rows, "invoices": invoices}

    def detect(state: InvState) -> InvState:
        # Detection lives in cfo.analysis.cost_metrics (T007); only the newest month of each pair is news.
        metrics = [CostMetric(**{k: r[k] for k in CostMetric.model_fields if k in r}) for r in state["rows"]]
        latest: dict[str, Any] = {}
        for m in metrics:
            latest[m.kind] = max(latest.get(m.kind, m.month), m.month)
        findings = []
        for a in detect_anomalies(metrics, state.get("invoices"), cost_growth_pct=threshold_pct):
            if a.month != latest[a.kind]:
                continue
            facts = _facts(a)
            top = None
            if a.drivers:
                d = a.drivers[0]
                top = {"line": d.label, "before": str(d.previous), "now": str(d.current)}
                facts["biggest_increase"] = top
            findings.append({"kind": a.kind.value, "month": a.month.isoformat(), "team_id": a.team_id,
                             "facts": facts, "top_line": top, "summary": a.explanation})
        return {"findings": findings}

    def explain_node(state: InvState) -> InvState:
        out = []
        for f in state["findings"]:
            text = explain(llm, INSTRUCTION, f["facts"], f["summary"])
            # the LLM only words the finding: a sentence that drops the cost change or the currency is not used
            out.append({**f, "explanation": text if states_cost_change(text, f["facts"]) else f["summary"]})
        return {"findings": out}

    g = StateGraph(InvState)
    for name, fn in [("gather", gather), ("detect", detect), ("explain", explain_node)]:
        g.add_node(name, obs.traced_node(f"investigate.{name}", fn))  # child spans of the caller's node (T027)
    g.add_edge(START, "gather")
    g.add_edge("gather", "detect")
    g.add_conditional_edges("detect", lambda s: "explain" if s["findings"] else END, ["explain", END])
    g.add_edge("explain", END)
    return g.compile()
