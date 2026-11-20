"""Investigation sub-agent (D-005, D-006): a small LangGraph the main agent runs on its own when a metric event
arrives. Detection is code (`cfo.analysis.cost_metrics.detect_anomalies`: cost rose a lot while the metric did
not, with the top contributing line items); the LLM only words the finding.

gather -> detect -> explain.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cfo import observability as obs
from cfo.agent.llm_tasks import explain
from cfo.agent.tools import Gateway
from cfo.analysis.cost_metrics import PAIRS, detect_anomalies
from cfo.domain import CostMetric, Invoice
from cfo.llm import LLM


class InvState(TypedDict, total=False):
    rows: list[dict[str, Any]]
    invoices: dict[str, Invoice]
    findings: list[dict[str, Any]]


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
            spec = PAIRS[a.kind]
            facts: dict[str, Any] = {
                "what": spec.unit_cost_noun, "month": a.month.isoformat(), "cost_change_pct": None if a.cost_change_pct is None else float(a.cost_change_pct),
                "metric": spec.metric_noun, "metric_change_pct": float(a.metric_change_pct),
                "cost_per_unit_now": None if a.cost_per_unit is None else str(round(a.cost_per_unit, 4)),
                "cost_per_unit_change_pct": None if a.cost_per_unit_change_pct is None else float(a.cost_per_unit_change_pct),
            }
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
            out.append({**f, "explanation": explain(
                llm, "Explain why this cost looks off compared to the metric it should follow.", f["facts"],
                f["summary"])})
        return {"findings": out}

    g = StateGraph(InvState)
    for name, fn in [("gather", gather), ("detect", detect), ("explain", explain_node)]:
        g.add_node(name, obs.traced_node(f"investigate.{name}", fn))  # child spans of the caller's node (T027)
    g.add_edge(START, "gather")
    g.add_edge("gather", "detect")
    g.add_conditional_edges("detect", lambda s: "explain" if s["findings"] else END, ["explain", END])
    g.add_edge("explain", END)
    return g.compile()
