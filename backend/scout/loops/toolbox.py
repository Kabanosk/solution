"""Scout's tools for answering a DM on its own: everything a finance colleague would look at.

Every tool is read-only except `start_what_if` (which only queues a forecast). Every tool that reaches someone's
data goes through the access policy *for the person asking* (not for Scout), so a waiter cannot read the owner's
laptop by asking Scout to; each call is written to the audit log and as a step of the task.

Built for a small model: short names and descriptions, flat arguments, compact JSON results (at most 10 rows),
and errors returned as data ("access_denied", "not_found") so the model can explain them instead of crashing.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from cfo.llm import ToolSpec
from scout.audit import check as access_check
from scout.documents import document_text
from scout.loops.context import JobContext, eur
from scout.models import Task

MAX_ROWS = 10


def _p(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


S = {"type": "string"}
I = {"type": "integer"}

SPECS: list[ToolSpec] = [
    ToolSpec(name="missing_invoices", description="Outgoing payments that still have no invoice, and what Scout is "
             "doing about each (task, status, who was asked). Use this for any question about missing invoices.",
             parameters=_p(days=I)),
    ToolSpec(name="search_payments", description="Bank payments. Filter by counterparty, amount in EUR, days back, "
             "or only those without an invoice.",
             parameters=_p(counterparty=S, amount_eur=S, days=I, undocumented_only={"type": "boolean"})),
    ToolSpec(name="search_invoices", description="Invoices on file, by invoice number or supplier.",
             parameters=_p(number=S, supplier=S)),
    ToolSpec(name="cash_position", description="Current bank balance, money in and out in the last 30 days, and "
             "expected refunds or payments.", parameters=_p()),
    ToolSpec(name="cost_trends", description="Monthly costs vs business metrics (food cost vs orders, ads vs new "
             "customers, cloud vs online orders) for the last months.", parameters=_p(months=I)),
    ToolSpec(name="team_budgets", description="Each team's monthly budget and what it spent this month.",
             parameters=_p()),
    ToolSpec(name="scout_tasks", description="Scout's own tasks (what it checked, found, asked). Filter by text or "
             "status (waiting, escalated, done).", parameters=_p(text=S, status=S)),
    ToolSpec(name="task_log", description="The full step log of one Scout task, e.g. SC-102.",
             parameters=_p(task_id=S)),
    ToolSpec(name="search_files", description="Search a person's company folders (Downloads, Documents, Mail) by "
             "words in the file name or text. person = first name.", parameters=_p(person=S, words=S)),
    ToolSpec(name="read_file", description="Read the text of a file found with search_files.",
             parameters=_p(person=S, path=S)),
    ToolSpec(name="search_email", description="Search the company mailbox by words (supplier, invoice number).",
             parameters=_p(words=S, days=I)),
    ToolSpec(name="search_chat", description="Search the authorized Slack channels for a supplier, invoice number "
             "or amount.", parameters=_p(supplier=S, invoice_number=S, amount_eur=S, days=I)),
    ToolSpec(name="who_is", description="People: role, manager, and whether they are on vacation today.",
             parameters=_p(person=S)),
    ToolSpec(name="start_what_if", description="Start a cash forecast for a purchase: amount in EUR, month, what. "
             "The result comes as a separate message.", parameters=_p(amount_eur=S, month=S, what=S)),
]
NAMES = {s.name for s in SPECS}


def _num(v: Any) -> Decimal | None:
    try:
        return abs(Decimal(str(v).replace("€", "").replace(",", "").strip()))
    except Exception:  # noqa: BLE001
        return None


_PAIR = re.compile(r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)')


def repair_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Small models often send cut-off or slightly broken JSON (the client then hands us `{"_raw": text}`):
    keep every complete `"key": value` pair instead of dropping all of them."""
    if not args or "_raw" not in args:
        return dict(args or {})
    raw = str(args["_raw"])
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    out: dict[str, Any] = {}
    for key, val in _PAIR.findall(raw):
        try:
            out[key] = json.loads(val)
        except ValueError:
            continue
    return out


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows[:MAX_ROWS]]


class Toolbox:
    """The tools, bound to one asker and one task (for the audit log and the step log)."""

    def __init__(self, ctx: JobContext, requester: str, role: str, task: Task):
        self.ctx, self.requester, self.role, self.task = ctx, requester, role, task

    # -- helpers -------------------------------------------------------------------------------------------
    def _person(self, name: str | None) -> str | None:
        n = (name or "").strip().casefold()
        if not n or n in ("me", "my", "i"):
            return self.requester
        for e in self.ctx.hr.all():
            if n == e["id"] or n in e["name"].casefold() or e["name"].casefold().split()[0] in n:
                return e["id"]
        return None

    def _allowed(self, target: str, resource: str) -> tuple[bool, str]:
        ok = self.ctx.audit.check(self.requester, self.role, target, resource, task_id=self.task.id)
        return ok, access_check(self.requester, self.role, target, resource)[1]

    def _q(self, sql: str, args: list[Any]) -> list[dict[str, Any]]:
        with self.ctx.conn_factory() as conn:
            return conn.execute(sql, args).fetchall()

    # -- dispatch ------------------------------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in NAMES:
            return {"error": "unknown_tool", "tools": sorted(NAMES)}
        args = repair_args(args)
        try:
            return getattr(self, f"t_{name}")(**{k: v for k, v in args.items() if v not in (None, "")})
        except TypeError as e:
            return {"error": "bad_arguments", "detail": str(e)[:200]}

    # -- tools ---------------------------------------------------------------------------------------------
    def t_search_payments(self, counterparty: str = "", amount_eur: Any = None, days: int = 90,
                          undocumented_only: bool = False, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("bank", "transactions")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        sql = ("SELECT booking_date, counterparty, reference, amount, documented FROM transactions "
               "WHERE booking_date >= %s")
        a: list[Any] = [self.ctx.now().date() - timedelta(days=min(int(days or 90), 400))]
        if counterparty:
            sql += " AND (counterparty ILIKE %s OR reference ILIKE %s)"
            a += [f"%{counterparty}%"] * 2
        if (amt := _num(amount_eur)) is not None:
            sql += " AND abs(amount) = %s"
            a.append(amt)
        if undocumented_only:
            sql += " AND amount < 0 AND NOT documented"
        rows = self._q(sql + " ORDER BY booking_date DESC LIMIT 11", a)
        return {"rows": _rows(rows), "more": len(rows) > MAX_ROWS}

    def t_missing_invoices(self, days: int = 60, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("bank", "transactions")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        rows = self._q(
            "SELECT x.booking_date, x.counterparty, x.amount, x.reference, t.id AS scout_task, t.status AS task_status, "
            "t.owner_id AS asked FROM transactions x LEFT JOIN agent_tasks t ON t.transaction_id = x.id "
            "WHERE x.amount < 0 AND NOT x.documented AND x.invoice_number IS NOT NULL AND x.booking_date >= %s "
            "ORDER BY x.booking_date DESC LIMIT 11",
            [self.ctx.now().date() - timedelta(days=min(int(days or 60), 400))])
        for r in rows:
            r["amount"] = eur(r["amount"])
            r["asked"] = self.ctx.hr.name(r["asked"]) if r["asked"] else None
            r["scout_task"] = r["scout_task"] or "none yet"
        return {"payments_without_invoice": _rows(rows), "more": len(rows) > MAX_ROWS}

    def t_search_invoices(self, number: str = "", supplier: str = "", **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("invoicing", "invoices")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        sql, a = "SELECT number, supplier, issue_date, total, transaction_id FROM invoices WHERE TRUE", []
        if number:
            sql += " AND upper(number) = upper(%s)"
            a.append(number)
        if supplier:
            sql += " AND supplier ILIKE %s"
            a.append(f"%{supplier}%")
        return {"rows": _rows(self._q(sql + " ORDER BY issue_date DESC LIMIT 10", a))}

    def t_cash_position(self, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("bank", "balance")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        since = self.ctx.now().date() - timedelta(days=30)
        row = self._q("SELECT coalesce(sum(amount),0) AS balance, coalesce(sum(amount) FILTER (WHERE booking_date >= %s "
                      "AND amount > 0),0) AS in_30d, coalesce(sum(amount) FILTER (WHERE booking_date >= %s AND "
                      "amount < 0),0) AS out_30d FROM transactions", [since, since])[0]
        expected = self._q("SELECT on_date, amount, label FROM expected_cash_events WHERE status = 'expected' "
                           "ORDER BY on_date LIMIT 10", [])
        return {"balance": eur(row["balance"]) if row["balance"] >= 0 else "-" + eur(row["balance"]),
                "money_in_30d": eur(row["in_30d"]), "money_out_30d": eur(row["out_30d"]),
                "expected": _rows(expected)}

    def t_cost_trends(self, months: int = 3, **_: Any) -> dict[str, Any]:
        rows = self._q("SELECT kind, month, cost, metric_value FROM cost_metrics WHERE month >= "
                       "(SELECT max(month) FROM cost_metrics) - make_interval(months => %s) ORDER BY kind, month",
                       [max(1, min(int(months or 3), 12)) - 1])
        out: dict[str, list[dict[str, str]]] = {}
        for r in rows:
            per = (r["cost"] / r["metric_value"]) if r["metric_value"] else None
            out.setdefault(r["kind"], []).append({"month": f"{r['month']:%Y-%m}", "cost": eur(r["cost"]),
                                                  "metric": str(int(r["metric_value"])),
                                                  "cost_per_unit": f"€{per:.2f}" if per else "-"})
        return {"series": out}

    def t_team_budgets(self, **_: Any) -> dict[str, Any]:
        start = self.ctx.now().date().replace(day=1)
        rows = self._q("SELECT t.id AS team, t.monthly_budget AS budget, coalesce(-sum(x.amount) FILTER (WHERE "
                       "x.amount < 0 AND x.booking_date >= %s), 0) AS spent_this_month FROM teams t LEFT JOIN "
                       "transactions x ON x.team_id = t.id GROUP BY t.id, t.monthly_budget ORDER BY t.id", [start])
        return {"rows": _rows(rows)}

    def t_scout_tasks(self, text: str = "", status: str = "", **_: Any) -> dict[str, Any]:
        sql, a = "SELECT id, title, status, outcome, owner_id, updated_at FROM agent_tasks WHERE loop <> 'chat'", []
        words = [w for w in (text or "").split() if len(w) >= 3][:3]
        if words:
            sql += " AND (" + " OR ".join(["(title ILIKE %s OR outcome ILIKE %s OR counterparty ILIKE %s)"] * len(words)) + ")"
            for w in words:
                a += [f"%{w}%"] * 3
        if status:
            sql += " AND status = %s"
            a.append(status.lower())
        return {"rows": _rows(self._q(sql + " ORDER BY updated_at DESC LIMIT 10", a))}

    def t_task_log(self, task_id: str, **_: Any) -> dict[str, Any]:
        t = self._q("SELECT id, title, status, outcome, owner_id FROM agent_tasks WHERE upper(id) = upper(%s)",
                    [task_id.strip()])
        if not t:
            return {"error": "not_found"}
        steps = self._q("SELECT ts, text FROM agent_task_steps WHERE task_id = %s ORDER BY id", [t[0]["id"]])
        return {"task": _rows(t)[0], "steps": [f"{s['ts']:%b %d %H:%M} {s['text']}" for s in steps][-20:]}

    def t_search_files(self, person: str = "", words: str = "", **_: Any) -> dict[str, Any]:
        who = self._person(person)
        if who is None:
            return {"error": "unknown_person", "person": person}
        ok, policy = self._allowed(f"laptop:{who}", "company folders")
        if not ok:
            return {"error": "access_denied", "policy": policy, "person": self.ctx.hr.name(who)}
        terms = [w for w in (words or "").replace(",", " ").split() if len(w) >= 3][:4] or ["pdf"]
        files = self.ctx.connectors.files.search(who, terms, task_id=self.task.id)
        return {"person": self.ctx.hr.name(who), "files": [f["path"] for f in files[:MAX_ROWS]]}

    def t_read_file(self, person: str = "", path: str = "", **_: Any) -> dict[str, Any]:
        who = self._person(person)
        if who is None:
            return {"error": "unknown_person", "person": person}
        ok, policy = self._allowed(f"laptop:{who}", path or "file")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        data = self.ctx.connectors.files.read(who, path, task_id=self.task.id)
        if not data:
            return {"error": "not_found_or_private", "path": path}
        text = document_text(data, path) or ""
        return {"path": path, "text": text[:2500], "truncated": len(text) > 2500}

    def t_search_email(self, words: str = "", days: int = 30, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("mail:company", "mailbox")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        mail = self.ctx.connectors.mail
        if not mail.available():
            return {"error": "email_not_connected"}
        terms = [w for w in (words or "").split() if len(w) >= 3][:4]
        hits = mail.search(terms, self.ctx.now() - timedelta(days=min(int(days or 30), 365)), task_id=self.task.id)
        return {"mails": [{"from": h.sender, "subject": h.subject, "date": str(h.date)[:10] if h.date else None,
                           "attachments": h.attachments, "snippet": (h.snippet or h.body)[:200]}
                          for h in hits[:MAX_ROWS]]}

    def t_search_chat(self, supplier: str = "", invoice_number: str = "", amount_eur: Any = None, days: int = 30,
                      **_: Any) -> dict[str, Any]:
        hits = self.ctx.connectors.chat.search(
            since=self.ctx.now() - timedelta(days=min(int(days or 30), 90)), invoice_number=invoice_number or None,
            supplier=supplier or None, amount=_num(amount_eur), task_id=self.task.id)
        return {"messages": [{"channel": f"#{h.channel}", "from": h.user_name or h.user, "text": h.text[:300]}
                             for h in hits[:MAX_ROWS]]}

    def t_who_is(self, person: str = "", **_: Any) -> dict[str, Any]:
        who = self._person(person)
        people = [who] if who else [e["id"] for e in self.ctx.hr.all()]
        today = self.ctx.now().date()
        out = []
        for p in people:
            e = self.ctx.hr.employee(p) or {}
            out.append({"name": e.get("name"), "role": e.get("role"), "manager": self.ctx.hr.name(e.get("manager_id"))
                        if e.get("manager_id") else None, "on_vacation_today": self.ctx.hr.on_vacation(p, today)})
        return {"people": out}

    def t_start_what_if(self, amount_eur: Any = None, month: str = "", what: str = "", **_: Any) -> dict[str, Any]:
        if self.role != "ceo":
            return {"error": "access_denied", "policy": "what-if: owner-only"}
        amt = _num(amount_eur)
        if not amt:
            return {"error": "amount_missing"}
        self.ctx.jobs.enqueue("what_if", {"amount": str(amt), "month": month, "what": what or "the purchase",
                                          "from_id": self.requester}, f"what_if:{self.task.id}:{amt}:{month}")
        return {"started": True, "note": "The forecast result will arrive as its own Slack message."}


def summarize(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """One line for the task's step log."""
    label = name.replace("_", " ")
    shown = ", ".join(f"{k}={v}" for k, v in (args or {}).items() if v not in (None, ""))[:80]
    if "error" in result:
        return f"Tried {label}({shown}): {result['error'].replace('_', ' ')}"
    n = next((len(v) for v in result.values() if isinstance(v, list)), None)
    return f"Looked at {label}({shown})" + (f": {n} result(s)" if n is not None else "")


def dumps(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)[:3500]
