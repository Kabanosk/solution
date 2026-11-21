"""Scout's tools for answering a DM on its own: everything a finance colleague would look at.

Every tool is read-only except `start_what_if` (which only queues a forecast). Every tool that reaches someone's
data goes through the access policy *for the person asking* (not for Scout), so a waiter cannot read the owner's
laptop by asking Scout to; each call is written to the audit log and as a step of the task.

Built for a small model: short names and descriptions, flat arguments, compact JSON results (at most 10 rows),
and errors returned as data ("access_denied", "not_found") so the model can explain them instead of crashing.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from psycopg import IsolationLevel

from cfo.data.invoices import invoice_pdf
from cfo.domain import Invoice, InvoiceLine
from cfo.llm import ToolSpec
from scout import dm_replies, guardrails
from scout.audit import EXEC_ROLES, can_see_task, salary_sql
from scout.audit import check as access_check
from scout.documents import document_text
from scout.dashboard_help import TOPICS, dashboard_help
from scout.llm_tasks import parse_money
from scout.loops.context import JobContext, eur, invoice_number_in
from scout.models import Task

log = logging.getLogger(__name__)

MAX_ROWS = 10
MAX_PDFS = 10  # files per Slack message


def _p(**props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}

SPECS: list[ToolSpec] = [
    ToolSpec(name="dashboard_help", description="How to use the Scout dashboard, its pages, navigation and controls. Fetch one topic when the user asks for UI help; default: getting_started.",
             parameters=_p(topic={"type": "string", "enum": list(TOPICS)})),
    ToolSpec(name="missing_invoices", description="Outgoing payments that still have no invoice, and what Scout is "
             "doing about each (task, status, who was asked). Use this for any question about missing invoices.",
             parameters=_p(days=I)),
    ToolSpec(name="search_payments", description="Bank payments. Filter by counterparty, amount in EUR, days back, "
             "or only those without an invoice.",
             parameters=_p(counterparty=S, amount_eur=S, days=I, undocumented_only={"type": "boolean"})),
    ToolSpec(name="search_invoices", description="Invoices on file, by invoice number or supplier, plus what the "
             "team said about them in Slack channels.",
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
    ToolSpec(name="search_email", description="Search the company mailbox, newest first. words: supplier, invoice "
             "number or subject words; leave words empty to list the newest mails. days: how far back (1 = today).",
             parameters=_p(words=S, days=I)),
    ToolSpec(name="search_chat", description="Search the authorized Slack channels for a supplier, invoice number "
             "or amount.", parameters=_p(supplier=S, invoice_number=S, amount_eur=S, days=I)),
    ToolSpec(name="send_invoices", description="Send the asker the invoice PDFs in Slack (and here), newest first, "
             "max 10. Optional: number, supplier, days back (default 90), unpaid_only.",
             parameters=_p(number=S, supplier=S, days=I, unpaid_only=B)),
    ToolSpec(name="send_dm", description="Send a colleague a short work message on Slack for the asker (signed "
             "with the asker's name). Work messages only.", parameters=_p(person=S, text=S)),
    ToolSpec(name="close_tasks", description="Close Scout tasks waiting on the asker, with the asker's reason (e.g. 'a "
             "refund, no invoice'). task_ids: SC-123, SC-124 or all. Approves pending approvals.",
             parameters=_p(task_ids=S, reason=S)),
    ToolSpec(name="hand_over_task", description="Hand waiting tasks (task_id: SC-123, several, or all of the "
             "asker's) to another person on Slack: approvals get the Approve card, invoice questions the question.",
             parameters=_p(task_id=S, person=S)),
    ToolSpec(name="who_is", description="People: role, manager, and whether they are on vacation today.",
             parameters=_p(person=S)),
    ToolSpec(name="start_what_if", description="Start a cash forecast for money going out (a purchase) or coming in "
             "(a client paying us): amount in EUR, month, what, direction 'out' or 'in'. The result comes as a "
             "separate message.", parameters=_p(amount_eur=S, month=S, what=S, direction=S)),
]
NAMES = {s.name for s in SPECS}


def _num(v: Any) -> Decimal | None:
    """ "\u20ac30,000", "\u20ac30.000", "642,80", "$500k", "-500000" -> a positive Decimal (None when not one number)."""
    amount = parse_money(v)
    return None if amount is None else abs(amount)


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

    def __init__(self, ctx: JobContext, requester: str, role: str, task: Task, question: str = ""):
        self.ctx, self.requester, self.role, self.task = ctx, requester, role, task
        self.question = question
        self.blocked_reply: str | None = None  # set when the output rails stop a message Scout was to send

    def _from_question(self) -> str | None:
        """Small models often call a search with empty arguments: use the invoice number the user named."""
        return invoice_number_in(self.question)

    # -- helpers -------------------------------------------------------------------------------------------
    def _person(self, name: str | None) -> str | None:
        n = (name or "").strip().casefold()
        if not n or n in ("me", "my", "i"):
            return self.requester
        people = self.ctx.hr.all()
        for e in people:
            names = [x.casefold() for x in (e["name"], e.get("slack_name")) if x]
            if n == e["id"] or any(n in x or x.split()[0] in n for x in names):
                return e["id"]
        if len(n) >= 4:  # nicknames: "Wojtek" for Wojciech, "Grażynka" for Grażyna
            hits = {e["id"] for e in people for x in (e["name"], e.get("slack_name")) if x
                    and x.casefold().split()[0][:3] == n[:3]}
            if len(hits) == 1:
                return hits.pop()
        return None

    def _slack_of(self, employee_id: str) -> str | None:
        """The person's Slack id: their HR link, else what the Slack connection knows (e.g. the installer)."""
        sid = (self.ctx.hr.employee(employee_id) or {}).get("slack_user_id")
        if sid:
            return sid
        known = getattr(self.ctx.notifier, "slack_id", None)
        try:
            return known(employee_id) if known else None
        except Exception:  # noqa: BLE001
            return None

    def _on_slack(self, name: str | None) -> str | None:
        """Slack is the source of truth: the person, found in HR or among the workspace's members, with a Slack id
        (a member who never registered gets an account from their Slack profile now)."""
        who = self._person(name)
        if who is not None and self._slack_of(who):
            return who
        find = getattr(self.ctx.notifier, "slack_person", None)
        if find is None:
            return who
        query = self.ctx.hr.full_name(who) if who is not None else (name or "")
        try:
            found = find(query, who) or (find(name or "", who) if who is not None else None)
        except Exception:  # Slack unreachable: fall back to what HR knows
            log.warning("Slack member lookup failed", exc_info=True)
            found = None
        return found or who

    def _allowed(self, target: str, resource: str) -> tuple[bool, str]:
        ok = self.ctx.audit.check(self.requester, self.role, target, resource, task_id=self.task.id)
        return ok, access_check(self.requester, self.role, target, resource)[1]

    def _q(self, sql: str, args: list[Any]) -> list[dict[str, Any]]:
        with self.ctx.conn_factory() as conn:
            return conn.execute(sql, args).fetchall()

    def _is_exec(self) -> bool:
        return (self.role or "").casefold() in EXEC_ROLES

    def _salary_view(self, rows: list[dict[str, Any]], *, fold: bool = True):  # noqa: ANN202
        """Hide or fold the salary rows the asker may not see (audited); see `scout.audit.filter_salary_rows`."""
        return self.ctx.audit.salary_filter(rows, self.requester, self.role, self.ctx.hr.all(), task_id=self.task.id,
                                            fold=fold)


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
    def t_dashboard_help(self, topic: str = "getting_started", **_: Any) -> dict[str, Any]:
        return dashboard_help(topic)

    def t_search_payments(self, counterparty: str = "", amount_eur: Any = None, days: int = 90,
                          undocumented_only: bool = False, **_: Any) -> dict[str, Any]:
        """Other rows and salary rows are fetched apart, so salary rows can neither crowd out the list nor be pried
        out by a narrow search: the salary part is limited by period only (whole months, see `_salary_rows`) and then
        filtered by who may see what; the search words apply to the rows that remain visible.
        Without bank access the asker still gets their own (and their reports') salary rows, nothing else."""
        full = access_check(self.requester, self.role, "bank", "transactions")[0]
        if full:
            self._allowed("bank", "transactions")
        since = self.ctx.now().date() - timedelta(days=min(int(days or 90), 400))
        amt = _num(amount_eur)

        def wanted(r: dict[str, Any]) -> bool:
            return (r["booking_date"] >= since
                    and (not counterparty or counterparty.casefold() in f"{r['counterparty']} {r['reference']}".casefold())
                    and (amt is None or abs(r["amount"]) == amt)
                    and (not undocumented_only or (r["amount"] < 0 and not r["documented"])))

        other: list[dict[str, Any]] = []
        if full:
            sql = ("SELECT id, booking_date, counterparty, reference, amount, documented, type, category FROM "
                   f"transactions WHERE booking_date >= %s AND NOT {salary_sql()}")
            a: list[Any] = [since]
            if counterparty:
                sql += " AND (counterparty ILIKE %s OR reference ILIKE %s)"
                a += [f"%{counterparty}%"] * 2
            if amt is not None:
                sql += " AND abs(amount) = %s"
                a.append(amt)
            if undocumented_only:
                sql += " AND amount < 0 AND NOT documented"
            other = self._q(sql + " ORDER BY booking_date DESC LIMIT 11", a)
        view = self._salary_view(self._salary_rows(since))
        rows = [r for r in view.visible if wanted(r)] + other
        rows.sort(key=lambda r: r["booking_date"], reverse=True)
        if not full and not rows:  # nothing of theirs matched: the same answer as before this exception existed
            return {"error": "access_denied", "policy": self._allowed("bank", "transactions")[1]}
        out: dict[str, Any] = {"rows": _rows([{k: v for k, v in r.items() if k not in ("id", "type", "category", "team_id")}
                                               for r in rows]), "more": len(rows) > MAX_ROWS}
        if view.summaries:
            out["payroll_summary"] = view.summaries
        if not full:
            out["note"] = "No general bank access: only your own and your team's salary payments are shown."
        return out

    def _salary_rows(self, since: Any) -> list[dict[str, Any]]:
        """Every salary row from the first day of `since`'s month: the totals the asker may get are whole calendar
        months, so the cutoff day of a query can never split one. No row cap (the period is at most 400 days)."""
        return self._q("SELECT id, booking_date, counterparty, reference, amount, documented, type, category, team_id "
                       f"FROM transactions WHERE booking_date >= %s AND {salary_sql()} ORDER BY booking_date DESC",
                       [since.replace(day=1)])

    def t_missing_invoices(self, days: int = 60, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("bank", "transactions")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        rows = self._q(
            "SELECT x.id, x.type, x.category, x.booking_date, x.counterparty, x.amount, x.reference, "
            "t.id AS scout_task, t.status AS task_status, t.owner_id AS asked FROM transactions x LEFT JOIN agent_tasks t ON t.transaction_id = x.id "
            "WHERE x.amount < 0 AND NOT x.documented AND x.invoice_number IS NOT NULL AND x.booking_date >= %s "
            "ORDER BY x.booking_date DESC LIMIT 11",
            [self.ctx.now().date() - timedelta(days=min(int(days or 60), 400))])
        view = self._salary_view(rows, fold=False)  # a salary row never belongs in this list, but never trust the data
        rows = view.visible
        for r in rows:
            r["amount"] = eur(r["amount"])
            r["asked"] = self.ctx.hr.name(r["asked"]) if r["asked"] else None
            r["scout_task"] = r["scout_task"] or "none yet"
            for k in ("id", "type", "category"):
                r.pop(k)
        return {"payments_without_invoice": _rows(rows), "more": len(rows) > MAX_ROWS}

    def t_search_invoices(self, number: str = "", supplier: str = "", **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("invoicing", "invoices")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        number = number or ("" if supplier else (self._from_question() or ""))
        if not number and not supplier:
            return {"error": "missing_arguments", "hint": "Call again with number (e.g. AS-77012) or supplier."}
        sql, a = ("SELECT number, supplier, issue_date, total, transaction_id IS NOT NULL AS linked_to_payment "
                  "FROM invoices WHERE TRUE"), []
        if number:
            sql += " AND upper(number) = upper(%s)"
            a.append(number)
        if supplier:
            sql += " AND supplier ILIKE %s"
            a.append(f"%{supplier}%")
        found = self._q(sql + " ORDER BY issue_date DESC LIMIT 10", a)
        out: dict[str, Any] = {"rows": _rows(found)}
        talk = self._chat_about(number, found)  # by default: what the team said about it on Slack
        if talk:
            out["slack_messages"] = talk
        return out

    def _chat_about(self, number: str, invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Slack channel messages (last 90 days) naming the invoice number, or its supplier with the exact amount."""
        since = self.ctx.now() - timedelta(days=90)
        queries = ([{"invoice_number": number}] if number else []) + [
            {"invoice_number": r.get("number"), "supplier": r.get("supplier"),
             "amount": _num(str(r["total"])) if r.get("total") is not None else None}
            for r in invoices[:3]]
        seen, out = set(), []
        for q in queries:
            try:
                hits = self.ctx.connectors.chat.search(since=since, task_id=self.task.id, **q)
            except Exception:  # Slack unreachable: the invoice answer still stands
                log.warning("chat search for an invoice failed", exc_info=True)
                return out
            for h in hits:
                if (h.channel, h.ts) in seen:
                    continue
                seen.add((h.channel, h.ts))
                out.append({"channel": f"#{h.channel}", "from": h.user_name or h.user, "text": h.text[:300],
                            **({"link": h.permalink} if h.permalink else {})})
        return out[:MAX_ROWS]

    def t_cash_position(self, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("bank", "balance")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        since = self.ctx.now().date() - timedelta(days=30)
        row = self._q("SELECT coalesce(sum(amount),0) AS balance, coalesce(sum(amount) FILTER (WHERE booking_date >= %s "
                      "AND amount > 0),0) AS in_30d, coalesce(sum(amount) FILTER (WHERE booking_date >= %s AND "
                      "amount < 0),0) AS out_30d FROM transactions", [since, since])[0]
        if not self._is_exec():  # one audit row per salary outflow of the 30-day window, one for the all-time balance
            def mask(resource: str, policy: str) -> None:
                self.ctx.audit.record("agent", "salary_filter", "bank", resource, "MASK", task_id=self.task.id,
                                      requester=self.requester, role=self.role, policy=policy)

            for r in self._q("SELECT id FROM transactions WHERE booking_date >= %s AND amount < 0 AND "
                             f"{salary_sql()} ORDER BY booking_date", [since]):
                mask(f"transaction {r['id']}", "cash-position: payroll included in totals")
            mask("balance (all-time)", "cash-position: balance includes payroll")
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
        """Spend per team without salary rows, then plus the salary rows the asker may see (so a one-person team's
        total never gives away that person's pay)."""
        start = self.ctx.now().date().replace(day=1)
        rows = self._q("SELECT t.id AS team, t.monthly_budget AS budget, coalesce(-sum(x.amount) FILTER (WHERE "
                       f"x.amount < 0 AND x.booking_date >= %s AND NOT {salary_sql('x')}), 0) AS spent_this_month "
                       "FROM teams t LEFT JOIN transactions x ON x.team_id = t.id GROUP BY t.id, t.monthly_budget "
                       "ORDER BY t.id", [start])
        pay = self._q("SELECT id, team_id, booking_date, counterparty, reference, amount, type, category FROM transactions WHERE "
                      f"booking_date >= %s AND {salary_sql()}", [start])  # debits and credits, as in search_payments
        view = self._salary_view(pay)
        by_team = {r["team"]: r for r in rows}
        for p in view.visible:
            if p["team_id"] in by_team:
                by_team[p["team_id"]]["spent_this_month"] -= p["amount"]
        out: dict[str, Any] = {"rows": _rows(rows)}
        if view.summaries:
            out["payroll_summary"] = view.summaries
        if view.hidden:
            out["note"] = "Other people's pay is not included in the team totals."
        return out

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
        if not self._is_exec():  # everyone else: only tasks they asked for or were asked to handle
            hidden = self._q("SELECT count(*) AS n FROM (" + sql + " AND coalesce(owner_id, '') <> %s AND "
                             "requested_by <> %s) h", [*a, self.requester, self.requester])[0]["n"]
            if hidden:
                self.ctx.audit.record("agent", "access_check", "tasks", f"{hidden} task(s) of other people left out",
                                      "DENY", task_id=self.task.id, requester=self.requester, role=self.role,
                                      policy="tasks: requester-or-exec-only")
            sql += " AND (owner_id = %s OR requested_by = %s)"
            a += [self.requester, self.requester]
        return {"rows": _rows(self._q(sql + " ORDER BY updated_at DESC LIMIT 10", a))}

    def t_task_log(self, task_id: str, **_: Any) -> dict[str, Any]:
        """The task row and its steps are read in one REPEATABLE READ snapshot and the permission is judged on that
        row, so a task that changes hands (even away and back) while we read cannot let other steps in."""
        cols = "id, title, status, outcome, owner_id, requested_by"
        with self.ctx.conn_factory() as conn:
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            task = conn.execute(f"SELECT {cols} FROM agent_tasks WHERE upper(id) = upper(%s)",
                                [task_id.strip()]).fetchone()
            if not task:
                return {"error": "not_found"}
            steps: list[dict[str, Any]] = []
            if can_see_task(self.requester, self.role, task["requested_by"], task["owner_id"])[0]:
                steps = conn.execute("SELECT ts, text FROM agent_task_steps WHERE task_id = %s ORDER BY id",
                                     [task["id"]]).fetchall()
        if not self.ctx.audit.check_task(self.requester, self.role, task["id"], task["requested_by"], task["owner_id"],
                                         current_task=self.task.id):
            return {"error": "access_denied", "policy": "tasks: requester-or-exec-only"}
        shown = {k: task[k] for k in ("id", "title", "status", "outcome", "owner_id")}
        return {"task": _rows([shown])[0], "steps": [f"{s['ts']:%b %d %H:%M} {s['text']}" for s in steps][-20:]}

    def t_search_files(self, person: str = "", words: str = "", **_: Any) -> dict[str, Any]:
        who = self._person(person)
        if who is None:
            return {"error": "unknown_person", "person": person}
        ok, policy = self._allowed(f"laptop:{who}", "company folders")
        if not ok:
            return {"error": "access_denied", "policy": policy, "person": self.ctx.hr.name(who)}
        terms = [w for w in (words or "").replace(",", " ").split() if len(w) >= 3][:4] or \
            [t for t in [self._from_question()] if t] or ["pdf"]
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
        terms = [w for w in (words or "").split() if len(w) >= 3][:4] or [t for t in [self._from_question()] if t]
        since = self.ctx.now() - timedelta(days=min(max(int(days or 30), 1), 365))
        hits = mail.search(terms, since, task_id=self.task.id)  # no terms: the newest mails
        out: dict[str, Any] = {"mails": [{"from": h.sender, "subject": h.subject,
                                          "date": str(h.date)[:10] if h.date else None,
                                          "attachments": h.attachments, "snippet": (h.snippet or h.body)[:200]}
                                         for h in hits[:MAX_ROWS]]}
        if not hits and terms:
            out["hint"] = "No mail matches those words. Call again with empty words to list the newest mails."
        return out

    def t_search_chat(self, supplier: str = "", invoice_number: str = "", amount_eur: Any = None, days: int = 30,
                      **_: Any) -> dict[str, Any]:
        hits = self.ctx.connectors.chat.search(
            since=self.ctx.now() - timedelta(days=min(int(days or 30), 90)),
            invoice_number=invoice_number or (None if supplier or amount_eur else self._from_question()),
            supplier=supplier or None, amount=_num(amount_eur), task_id=self.task.id)
        return {"messages": [{"channel": f"#{h.channel}", "from": h.user_name or h.user, "text": h.text[:300]}
                             for h in hits[:MAX_ROWS]]}

    def t_send_invoices(self, supplier: str = "", days: int = 90, unpaid_only: Any = False, number: str = "",
                        **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("invoicing", "invoices")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        since = (self.ctx.now() - timedelta(days=min(max(int(days or 90), 1), 3650))).date()
        sql, a = ("SELECT id, number, supplier, issue_date, due_date, total, currency, transaction_id, category "
                  "FROM invoices WHERE TRUE"), []
        if number:
            sql += " AND upper(number) = upper(%s)"
            a.append(number.strip())
        else:
            sql += " AND issue_date >= %s"
            a.append(since)
        if supplier:
            sql += " AND supplier ILIKE %s"
            a.append(f"%{supplier}%")
        if str(unpaid_only).casefold() in ("true", "1", "yes"):
            sql += " AND transaction_id IS NULL"
        rows = self._q(sql + f" ORDER BY issue_date DESC, number LIMIT {MAX_PDFS + 1}", a)
        if not rows:
            return {"invoices": 0, "note": "No invoices match; nothing was sent."}
        more, rows = len(rows) > MAX_PDFS, rows[:MAX_PDFS]
        originals = self._original_pdfs([r["number"] for r in rows])
        files, listed = [], []
        for r in rows:
            name = f"Invoice_{r['number']}.pdf"
            if r["number"] in originals:
                data, source = originals[r["number"]]
            else:
                data, source = self._render_invoice(r), "copy from the invoicing system"
            files.append((name, data))
            listed.append({"file": name, "supplier": r["supplier"], "total": eur(Decimal(r["total"])),
                           "date": str(r["issue_date"]), "source": source})
        total = sum((Decimal(r["total"]) for r in rows), Decimal(0))
        comment = (f"{len(rows)} invoice(s){' from ' + supplier if supplier else ''}, total {eur(total)}"
                   + (f" (the newest {MAX_PDFS}; ask for a supplier or a shorter period for others)" if more else "")
                   + ".")
        try:
            link = self.ctx.notifier.upload(self.requester, files, comment)
        except Exception:  # noqa: BLE001 - the PDFs still reach the dashboard chat
            link = None
        for (name, data), item in zip(files, listed, strict=True):
            self.ctx.recorder.step(self.task.id, "sent_file", f"Sent {name} ({item['source']})"
                                   + (" to Slack" if link else ""), source="scout",
                                   detail={"name": name, "mime": "application/pdf", "sent_to_slack": bool(link),
                                           "data_b64": base64.b64encode(data).decode()})
        return {"invoices": len(rows), "total": eur(total), "sent_to_slack": bool(link), "files": listed,
                "more_not_sent": more}

    def _original_pdfs(self, numbers: list[str]) -> dict[str, tuple[bytes, str]]:
        """Invoice number -> (the original PDF, where it was found), from company folders the asker may see and
        from the company mailbox. Every lookup is audited by the connectors."""
        found: dict[str, tuple[bytes, str]] = {}

        def match(name: str) -> str | None:
            up = name.upper()
            return next((n for n in numbers if n.upper() in up and n not in found), None)

        for e in self.ctx.hr.all():
            if len(found) == len(numbers):
                break
            if not self._allowed(f"laptop:{e['id']}", "company folders")[0]:
                continue
            for f in self.ctx.connectors.files.search(e["id"], numbers, task_id=self.task.id):
                n = match(f["path"])
                if n and f["path"].lower().endswith(".pdf"):
                    data = self.ctx.connectors.files.read(e["id"], f["path"], task_id=self.task.id)
                    if data and data[:5] == b"%PDF-":
                        found[n] = (data, f"original from {self.ctx.hr.name(e['id'])}'s company folders")
        mail = self.ctx.connectors.mail
        if len(found) < len(numbers) and mail.available() and self._allowed("mail:company", "mailbox")[0]:
            for hit in mail.search([n for n in numbers if n not in found], self.ctx.now() - timedelta(days=3650),
                                   task_id=self.task.id):
                for att in hit.attachments:
                    n = match(att)
                    if n and att.lower().endswith(".pdf"):
                        data = mail.attachment(hit.id, att, task_id=self.task.id)
                        if data and data[:5] == b"%PDF-":
                            found[n] = (data, f"original from the email “{hit.subject}”")
        return found

    def _render_invoice(self, r: dict[str, Any]) -> bytes:
        """A PDF copy built from the invoicing system's record and its lines."""
        lines = self._q("SELECT description, quantity, unit_price, amount, category FROM invoice_lines "
                        "WHERE invoice_id = %s ORDER BY position", [r["id"]])
        inv = Invoice(id=r["id"], number=r["number"], supplier=r["supplier"], issue_date=r["issue_date"],
                      due_date=r["due_date"], total=Decimal(r["total"]), currency=r["currency"] or "EUR",
                      lines=[InvoiceLine(**ln) for ln in lines], transaction_id=r["transaction_id"],
                      category=r["category"])
        return invoice_pdf(inv, title_prefix="INVOICE (copy)")

    def t_send_dm(self, person: str = "", text: str = "", **_: Any) -> dict[str, Any]:
        text = (text or "").strip()
        if not person or not text:
            return {"error": "missing_arguments", "hint": "Call again with person and text."}
        who = self._on_slack(person)
        if who is None:
            return {"error": "unknown_person", "person": person,
                    "hint": "Nobody by that name in the company or in the Slack workspace."}
        if not self._slack_of(who):
            return {"error": "not_on_slack", "person": self.ctx.hr.full_name(who)}
        body = f"Message from {self.ctx.hr.name(self.requester)} via Scout: {text[:1500]}"
        safe = guardrails.screen_output(self.ctx, self.task.id, body, requester=self.requester, source="send_dm",
                                        role=self.ctx.hr.role(who))  # judged by who receives it
        if safe == guardrails.OUTPUT_REPLY:
            self.blocked_reply = guardrails.OUTPUT_REPLY
            return {"error": "blocked_by_safety_policy", "sent": False}
        try:
            ref = self.ctx.notifier.dm(who, safe, dm_replies.LEVEL, task_id=self.task.id)
        except Exception:  # noqa: BLE001 - Slack said no: nothing was sent, so say so
            log.exception("send_dm failed")
            ref = None
        if not ref:
            return {"error": "not_sent", "person": self.ctx.hr.full_name(who),
                    "hint": "Slack did not deliver it. Tell the user nothing was sent."}
        dm_replies.register(self.ctx, self.task, self.requester, who, ref, text)
        self.ctx.audit.record("agent", "send_dm", f"slack:{who}", text[:200], task_id=self.task.id,
                              requester=self.requester, role=self.role)
        return {"sent": True, "to": self.ctx.hr.full_name(who), "awaiting_reply": True,
                "note": "Their reply in that Slack thread will be passed on to the asker automatically."}

    def t_close_tasks(self, task_ids: str = "", reason: str = "", **_: Any) -> dict[str, Any]:
        reason = (reason or "").strip()
        if not task_ids or len(reason) < 3:
            return {"error": "missing_arguments", "hint": "Call again with task_ids (or all) and the reason."}
        if str(task_ids).strip().casefold() in ("all", "*", "everything"):
            targets = [t for t in self.ctx.recorder.find_open(owner_id=self.requester) if t.loop != "chat"]
        else:
            ids = list(dict.fromkeys(re.findall(r"SC-\d+", str(task_ids).upper())))
            targets = []
            for i in ids:
                try:
                    targets.append(self.ctx.recorder.get(i))
                except Exception:  # noqa: BLE001
                    targets.append(None)
        closed, skipped = [], []
        who = self.ctx.hr.name(self.requester)
        for t in targets:
            if t is None:
                skipped.append({"task": "?", "why": "no such task"})
            elif t.status in ("done", "cancelled"):
                skipped.append({"task": t.id, "why": f"already {t.status}"})
            elif t.loop == "chat":
                skipped.append({"task": t.id, "why": "a chat, nothing to close"})
            elif self.requester != t.owner_id and self.role != "ceo":
                skipped.append({"task": t.id, "why": "not yours to close"})
                self.ctx.audit.record("agent", "close_task", f"task:{t.id}", reason[:200], "DENY", task_id=t.id,
                                      requester=self.requester, role=self.role, policy="tasks: owner or CEO")
            elif t.approval_id and self._approve(t):
                closed.append(t.id)
            else:
                outcome = f"Closed by {who}: {reason[:200]}"
                self.ctx.recorder.step(t.id, "closed", outcome, source="slack:dm")
                self.ctx.recorder.set_status(t.id, "done", outcome=outcome, resolved_via="slack_dm")
                self.ctx.audit.record(self.requester, "close_task", f"task:{t.id}", reason[:200], task_id=t.id,
                                      requester=self.requester, role=self.role)
                if t.slack_ref:
                    try:
                        self.ctx.notifier.update(t.slack_ref, f"✅ {outcome} ({t.id})", "AUTO")
                    except Exception:  # noqa: BLE001 - the task is closed either way
                        pass
                closed.append(t.id)
        return {"closed": closed, "skipped": skipped}

    def _approve(self, t: Task) -> bool:
        """A task waiting on an approval is closed by approving it (only its approver can)."""
        from scout.loops import liquidity

        try:
            liquidity.resume(self.ctx, t.approval_id, True, self.requester)
        except (PermissionError, KeyError):
            return False
        return self.ctx.recorder.get(t.id).status == "done"

    def t_hand_over_task(self, task_id: str = "", person: str = "", **_: Any) -> dict[str, Any]:
        from scout.loops import invoicing, liquidity

        raw = str(task_id or "").strip()
        if raw.casefold() == "all":
            targets = [t.id for t in self.ctx.recorder.find_open(owner_id=self.requester) if t.loop != "chat"]
        else:
            targets = list(dict.fromkeys(re.findall(r"SC-\d+", raw.upper())))
        if not targets or not person:
            return {"error": "missing_arguments", "hint": "Call again with task_id (SC-…, several, or all) and person."}
        who = self._on_slack(person)
        if who is None:
            return {"error": "unknown_person", "person": person}
        handed, skipped = [], []
        for tid in targets:
            try:
                t = self.ctx.recorder.get(tid)
            except Exception:  # noqa: BLE001
                skipped.append({"task": tid, "why": "no such task"})
                continue
            if t.status not in ("waiting", "escalated"):
                skipped.append({"task": t.id, "why": f"not waiting ({t.status})"})
                continue
            if self.requester not in (t.owner_id, self.ctx.settings.ceo_id):
                skipped.append({"task": t.id, "why": "access_denied"})
                continue
            if not self._slack_of(who):
                return {"error": "not_on_slack", "person": self.ctx.hr.full_name(who)}
            try:
                if t.approval_id and t.loop == "liquidity":
                    liquidity.reassign(self.ctx, t.approval_id, who, self.requester, chat_task=self.task.id)
                elif t.loop == "invoicing":
                    invoicing.hand_over(self.ctx, t.id, who, self.requester, chat_task=self.task.id)
                else:
                    skipped.append({"task": t.id, "why": f"{t.loop} tasks can't be handed over"})
                    continue
            except PermissionError:
                skipped.append({"task": t.id, "why": "access_denied"})
                continue
            except (ValueError, KeyError) as e:
                skipped.append({"task": t.id, "why": str(e)[:80]})
                continue
            handed.append(t.id)
        if not handed and skipped and all(x["why"] == "access_denied" for x in skipped):
            return {"error": "access_denied", "policy": "tasks: the person it waits on or the owner"}
        return {"handed": handed, "skipped": skipped, "to": self.ctx.hr.name(who)}

    def t_who_is(self, person: str = "", **_: Any) -> dict[str, Any]:
        who = self._person(person)
        people = [who] if who else [e["id"] for e in self.ctx.hr.all()]
        today = self.ctx.now().date()
        out = []
        for p in people:
            e = self.ctx.hr.employee(p) or {}
            out.append({"name": self.ctx.hr.full_name(p),
                        "role": e.get("role"), "manager": self.ctx.hr.name(e.get("manager_id"))
                        if e.get("manager_id") else None, "on_vacation_today": self.ctx.hr.on_vacation(p, today)})
        return {"people": out}

    def t_start_what_if(self, amount_eur: Any = None, month: str = "", what: str = "", direction: str = "",
                        **_: Any) -> dict[str, Any]:
        if self.role != "ceo":
            return {"error": "access_denied", "policy": "what-if: owner-only"}
        amt = _num(amount_eur)
        if not amt:
            return {"error": "amount_missing"}
        self.ctx.jobs.enqueue("what_if", {"amount": str(abs(amt)), "month": month, "what": what,
                                          "direction": direction, "from_id": self.requester,
                                          "chat_task": self.task.id},
                              f"what_if:{self.task.id}:{amt}:{month}")
        return {"started": True, "note": "Tell the user the forecast is running; its result, and the approval if one "
                "is needed, will appear in this chat by itself."}


def summarize(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """One line for the task's step log."""
    label = name.replace("_", " ")
    shown = ", ".join(f"{k}={v}" for k, v in (args or {}).items() if v not in (None, ""))[:80]
    if "error" in result:
        return f"Tried {label}({shown}): {result['error'].replace('_', ' ')}"
    if "balance" in result:
        return f"Looked at the cash position: balance {result['balance']}"
    if "series" in result:
        return f"Looked at cost trends: {len(result['series'])} cost pairs"
    if result.get("started"):
        return "Started a cash forecast (what-if)"
    n = next((len(v) for v in result.values() if isinstance(v, list)), None)
    return f"Looked at {label}({shown})" + (f": {n} result(s)" if n is not None else "")


def dumps(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)[:3500]
