"""Investigation sub-agent (D-005, D-006): a small LangGraph the main agent runs on its own when a metric event
arrives. Detection is code (cost per unit rose a lot while the metric did not); the LLM only words the finding.

gather -> detect -> explain. Cost-per-line comparison uses `CostMetric.detail`: either a flat
`{"line name": amount}` mapping or `{"lines": [{"description"|"service"|"name": ..., "amount"|"cost": ...}]}`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from cfo.agent.llm_tasks import explain, parse_amount
from cfo.agent.tools import Gateway
from cfo.llm import LLM

LABELS = {
    "food_cost": ("food cost per order", "orders"),
    "ads_cac": ("customer acquisition cost", "new customers"),
    "cloud_per_order": ("cloud cost per online order", "online orders"),
}


class InvState(TypedDict, total=False):
    rows: list[dict[str, Any]]
    findings: list[dict[str, Any]]


def line_costs(detail: Any) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    if not isinstance(detail, dict):
        return out
    items = detail.get("lines") if isinstance(detail.get("lines"), list) else None
    if items is not None:
        for it in items:
            if not isinstance(it, dict):
                continue
            name = next((it[k] for k in ("description", "service", "name") if it.get(k)), None)
            amount = parse_amount(next((it[k] for k in ("amount", "cost") if k in it), None))
            if name and amount is not None:
                out[str(name)] = amount
        return out
    for k, v in detail.items():
        amount = parse_amount(v) if isinstance(v, (int, float, str, Decimal)) else None
        if amount is not None:
            out[str(k)] = amount
    return out


def build_investigation_graph(gw: Gateway, llm: LLM, threshold_pct: float = 20.0):
    def gather(state: InvState) -> InvState:
        return {"rows": sorted(gw.cost_metrics(), key=lambda r: (r["kind"], r["month"]))}

    def detect(state: InvState) -> InvState:
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for r in state["rows"]:
            by_kind.setdefault(r["kind"], []).append(r)
        findings = []
        for kind, rows in by_kind.items():
            cur = rows[-1]
            ch = cur.get("change_vs_previous")
            if not ch or ch.get("cost_per_unit_pct") is None or ch.get("cost_pct") is None:
                continue
            cost_pct, metric_pct, cpu_pct = ch["cost_pct"], ch.get("metric_pct") or 0.0, ch["cost_per_unit_pct"]
            if cpu_pct < threshold_pct or cost_pct - metric_pct < threshold_pct:
                continue
            what, unit = LABELS.get(kind, (kind, "units"))
            facts: dict[str, Any] = {
                "what": what, "month": cur["month"], "cost_change_pct": cost_pct,
                "metric": unit, "metric_change_pct": metric_pct,
                "cost_per_unit_now": cur.get("cost_per_unit"), "cost_per_unit_change_pct": cpu_pct,
            }
            top = None
            if len(rows) >= 2:
                before, now = line_costs(rows[-2].get("detail")), line_costs(cur.get("detail"))
                deltas = {k: now[k] - before.get(k, Decimal(0)) for k in now}
                if deltas:
                    name = max(deltas, key=lambda k: deltas[k])
                    if deltas[name] > 0:
                        top = {"line": name, "before": str(before.get(name, Decimal(0))), "now": str(now[name])}
                        facts["biggest_increase"] = top
            findings.append({"kind": kind, "month": cur["month"], "team_id": cur.get("team_id"),
                             "facts": facts, "top_line": top})
        return {"findings": findings}

    def explain_node(state: InvState) -> InvState:
        out = []
        for f in state["findings"]:
            fx = f["facts"]
            fallback = (
                f"{fx['what'].capitalize()} rose {fx['cost_per_unit_change_pct']:.0f}% in {fx['month'][:7]}: "
                f"cost {fx['cost_change_pct']:+.0f}% while {fx['metric']} changed {fx['metric_change_pct']:+.0f}%."
            )
            if f["top_line"]:
                fallback += f" Biggest increase: {f['top_line']['line']} ({f['top_line']['before']} -> {f['top_line']['now']} EUR)."
            out.append({**f, "explanation": explain(
                llm, "Explain why this cost looks off compared to the metric it should follow.", fx, fallback)})
        return {"findings": out}

    g = StateGraph(InvState)
    g.add_node("gather", gather)
    g.add_node("detect", detect)
    g.add_node("explain", explain_node)
    g.add_edge(START, "gather")
    g.add_edge("gather", "detect")
    g.add_conditional_edges("detect", lambda s: "explain" if s["findings"] else END, ["explain", END])
    g.add_edge("explain", END)
    return g.compile()
