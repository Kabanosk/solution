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
from collections.abc import Callable
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
    ToolSpec(name="dashboard_link", description="Send the asker a one-click sign-in link to the Scout dashboard in "
             "their Slack DM. Use when they ask for a link, login or access to the dashboard.", parameters=_p()),
    ToolSpec(name="missing_invoices", description="Outgoing payments that still have no invoice, and what Scout is "
             "doing about each (task, status, who was asked). Use this for any question about missing invoices.",
             parameters=_p(days=I)),
    ToolSpec(name="search_payments", description="Bank payments. Filter by counterparty, amount in EUR, days back, "
             "or only those without an invoice.",
             parameters=_p(counterparty=S, amount_eur=S, days=I, undocumented_only={"type": "boolean"})),
    ToolSpec(name="search_invoices", description="Invoices on file (by number or supplier, or the newest when empty), "
             "plus invoice PDFs found in email and Slack mentions.",
             parameters=_p(number=S, supplier=S, days=I)),
    ToolSpec(name="email_invoices", description="Invoice PDFs received by email: sender, subject, date, file names. "
             "words: supplier or invoice number, or empty for all. Use for any invoice question.",
             parameters=_p(words=S, days=I)),
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
    ToolSpec(name="send_invoices", description="The ONLY way to attach invoice PDFs: here and in Slack. Searches every "
             "source itself: the invoicing system, email, company folders and KSeF. to: a colleague to DM them to. "
             "words: supplier/number/subject words. Max 10.",
             parameters=_p(number=S, supplier=S, words=S, to=S, days=I, unpaid_only=B)),
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
    ToolSpec(name="send_briefing", description="Send the newest finished daily briefing (owner only) to a colleague's "
             "Slack DM. person = first name. You never see the briefing text.", parameters=_p(person=S)),
    ToolSpec(name="search_web", description="Search the public web for news and current facts. query: a short public "
             "question, nothing private. Returns an answer and sources to cite.", parameters=_p(query=S)),
]
NAMES = {s.name for s in SPECS}
# What a model outside the company's own infrastructure (OpenAI, Mistral, Groq, xAI) may call. None of these returns
# company data to the model: `start_what_if` only queues a local forecast, `send_briefing` returns {sent, to, title,
# date} and never the text. `Toolbox.call()` enforces this set; hiding the specs alone would not be a control.
EXTERNAL_TOOLS: frozenset[str] = frozenset({"dashboard_help", "start_what_if", "send_dm", "send_briefing",
                                            "search_web"})
# `search_web` sends the model's query to a third party, so Granite (whose context holds company data) never gets it:
# it is neither listed for Granite nor run when `allowed` is None.
EXTERNAL_ONLY: frozenset[str] = frozenset({"search_web"})
FILLER_WORDS = frozenset({"invoice", "invoices", "faktura", "faktury", "pdf", "pdfs", "file", "files", "all", "the",
                          "send", "sent", "me", "my", "from", "for", "and", "with", "slack", "email", "mail", "please"})
INVOICE_FILE = re.compile(r"invoice|faktur|rachun|bill|receipt", re.I)
# The asker talking about themself: "me", "ME", "myself", "to me", "me (Pedro)", Polish "mnie" / "sobie"...
SELF_WORDS = frozenset({"me", "my", "i", "myself", "mine", "self", "asker", "the asker", "user", "the user",
                        "current user", "requester", "mnie", "ja", "mi", "mój", "moja", "moje", "sobie", "siebie",
                        "mną", "mna"})
SELF_HEAD = frozenset({"me", "myself", "mnie", "sobie", "siebie"})
SELF_OBJECT = re.compile(r"\b(me|myself|mnie|mi|sobie|siebie|am i)\b", re.I)  # "send me", "who am I"
PERSON_ARG = {"send_dm": "person", "send_briefing": "person", "hand_over_task": "person", "search_files": "person",
              "read_file": "person", "who_is": "person", "send_invoices": "to"}
SELF_BY_DEFAULT = ("send_dm", "send_briefing", "who_is")  # person left out + "send me ...": the asker
BRIEFING_PART = 2500  # characters per Slack message (Slack's section text limit is 3000)
BRIEFING_PARTS = 3


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


def _parts(text: str, size: int) -> list[str]:
    """Split on line breaks into pieces of at most `size` characters (a longer single line is cut)."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > size:
            if current:
                out.append(current)
                current = ""
            out.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size and current:
            out.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [*out, current] if current else out


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in rows[:MAX_ROWS]]


class Toolbox:
    """The tools, bound to one asker and one task (for the audit log and the step log)."""

    def __init__(self, ctx: JobContext, requester: str, role: str, task: Task, question: str = "",
                 allowed: frozenset[str] | set[str] | None = None,
                 web_search: Callable[[str], dict[str, Any]] | None = None):
        self.ctx, self.requester, self.role, self.task = ctx, requester, role, task
        self.allowed = None if allowed is None else frozenset(allowed)  # None: every tool (Granite, Slack DMs)
        self.question = question
        self.web_search = web_search  # the external provider's search for this request; None: unavailable
        self.blocked_reply: str | None = None  # set when the output rails stop a message Scout was to send

    def _from_question(self) -> str | None:
        """Small models often call a search with empty arguments: use the invoice number the user named."""
        return invoice_number_in(self.question)

    # -- helpers -------------------------------------------------------------------------------------------
    @property
    def asker_name(self) -> str:
        """The asker's name and surname, as tools get it when the user says "me"."""
        return self.ctx.hr.full_name(self.requester)

    @staticmethod
    def _is_self(name: Any) -> bool:
        """ "me", "ME", "myself", "to me", "for me", "me (Pedro)", "mnie"...: the asker."""
        if not isinstance(name, str):
            return False
        n = re.sub(r"\(.*?\)", " ", name).strip(" .,!?:;\"'").casefold()
        n = re.sub(r"^(to|for|do|dla)\s+", "", n).strip()
        head = (n.split()[:1] or [""])[0].strip(".,!?:;-")
        return n in SELF_WORDS or head in SELF_HEAD  # "me, Pedro", "myself - Pedro"; never "my manager"

    def _person(self, name: str | None) -> str | None:
        n = " ".join((name or "").strip().casefold().split())
        if not n or self._is_self(n) or n in (self.requester.casefold(), self.asker_name.casefold()):
            return self.requester
        people = self.ctx.hr.all()
        for e in people:  # an exact id or whole name first: two people may share a first name
            if n == str(e["id"]).casefold() or n in [x.casefold() for x in (e["name"], e.get("slack_name"),
                                                                           self.ctx.hr.full_name(e["id"])) if x]:
                return e["id"]
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
        if (name not in NAMES or (self.allowed is not None and name not in self.allowed)
                or (self.allowed is None and name in EXTERNAL_ONLY)):
            visible = NAMES - EXTERNAL_ONLY if self.allowed is None else NAMES & self.allowed
            if name in NAMES:  # a real tool, but this model may not use it: nothing runs, nothing is audited as allowed
                return {"error": "not_available_for_this_model", "tools": sorted(visible),
                        "hint": "Company data is only available with the on-premise model (Granite)."}
            return {"error": "unknown_tool", "tools": sorted(visible)}
        args = self._resolve_self(name, repair_args(args))
        try:
            return getattr(self, f"t_{name}")(**{k: v for k, v in args.items() if v not in (None, "")})
        except TypeError as e:
            return {"error": "bad_arguments", "detail": str(e)[:200]}

    def _resolve_self(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """ "me" / "I" / "myself" in a person argument becomes the asker's name and surname; a person left out of
        send_dm / send_briefing / who_is while the user said "me" is the asker too."""
        key = PERSON_ARG.get(name)
        if key is None or not isinstance(args, dict):
            return args
        value = args.get(key)
        if self._is_self(value) or (not value and name in SELF_BY_DEFAULT and SELF_OBJECT.search(self.question or "")):
            args = {**args, key: self.asker_name}
        return args

    # -- tools ---------------------------------------------------------------------------------------------
    def t_dashboard_help(self, topic: str = "getting_started", **_: Any) -> dict[str, Any]:
        return dashboard_help(topic)

    def t_dashboard_link(self, **_: Any) -> dict[str, Any]:
        from cfo.tenancy import current_tenant
        from scout import login_links
        from scout import slack_directory as directory

        s, tenant = self.ctx.settings, current_tenant(required=False)
        if tenant is None or not s.dashboard_url:
            return {"error": "not_available", "hint": "Tell the user to open the dashboard and sign in there."}
        org = login_links.organization(s.database_url, tenant.id) or (tenant.slug, tenant.slug)
        url = login_links.create(s.database_url, s.dashboard_url, tenant.id, self.requester)
        label, text = f"Sign in to {org[1]}", (f"Here's your sign-in link to the {org[1]} dashboard. "
                                                f"It works once and expires in {login_links.TTL_MINUTES} minutes.")
        if url is None:  # no login yet: the join form creates one
            slack_id = (self.ctx.hr.employee(self.requester) or {}).get("slack_user_id")
            if not slack_id:
                return {"error": "no_account", "hint": "The user has no dashboard login yet."}
            token = directory.new_invite_token(self.ctx.conn_factory, slack_id, self.ctx.hr.full_name(self.requester))
            url = f"{s.dashboard_url.rstrip('/')}/join/{org[0]}/{token}"
            label = f"Join {org[1]} workspace"
            text = (f"You don't have a dashboard login yet. Set one up with the button below "
                    f"and you'll be signed in to {org[1]}.")
        send = getattr(self.ctx.notifier, "link", None)
        sent = False
        if send is not None:
            try:
                sent = bool(send(self.requester, text, label, url))
            except Exception:  # noqa: BLE001
                log.warning("could not send the dashboard link", exc_info=True)
        self.ctx.audit.record("agent", "dashboard_link", f"user:{self.requester}", label, task_id=self.task.id,
                              requester=self.requester, role=self.role)
        return {"sent_to_slack_dm": sent, "url": url, "button": label,
                "note": "The button is in their Slack DM; mention the link works once and expires in 15 minutes."}

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

    def _email_invoices(self, words: list[str], days: int) -> list[dict[str, Any]]:
        """Invoice mails with their PDFs (newest first); empty when email is not connected or not allowed."""
        from scout.connectors.mail import _is_pdf, invoice_mails

        mail = self.ctx.connectors.mail
        if not mail.available() or not self._allowed("mail:company", "mailbox")[0]:
            return []
        since = self.ctx.now() - timedelta(days=min(max(int(days or 90), 1), 3650))
        try:
            hits = invoice_mails(mail, since, words, task_id=self.task.id)
        except Exception:  # noqa: BLE001 - the mailbox failing must not break the answer
            log.warning("invoice mail search failed", exc_info=True)
            return []
        return [{"id": h.id, "from": h.sender, "subject": h.subject, "date": str(h.date)[:10] if h.date else None,
                 "pdfs": [a for a in h.attachments if _is_pdf(a)]} for h in hits]

    def t_email_invoices(self, words: str = "", days: int = 90, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("mail:company", "mailbox")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        if not self.ctx.connectors.mail.available():
            return {"error": "email_not_connected", "hint": "Connect Gmail in Connections to find invoice PDFs."}
        terms = [w for w in (words or "").split() if len(w) >= 2][:4]
        found = self._email_invoices(terms, days)
        out: dict[str, Any] = {"invoice_mails": [{k: v for k, v in m.items() if k != "id"} for m in found[:MAX_ROWS * 2]],
                               "count": len(found)}
        if not found:
            out["note"] = "No invoice PDFs in email for that; say so, do not guess suppliers."
        else:
            out["note"] = "Listed only, NOT attached. To give anyone the PDFs call send_invoices (to: colleague)."
        return out

    def t_search_invoices(self, number: str = "", supplier: str = "", days: int = 90, **_: Any) -> dict[str, Any]:
        ok, policy = self._allowed("invoicing", "invoices")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        number = number or ("" if supplier else (self._from_question() or ""))
        sql, a = ("SELECT number, supplier, issue_date, total, transaction_id IS NOT NULL AS linked_to_payment "
                  "FROM invoices WHERE TRUE"), []
        if not number and not supplier:  # nothing named: the newest ones
            sql += " AND issue_date >= %s"
            a.append((self.ctx.now() - timedelta(days=min(max(int(days or 90), 1), 3650))).date())
        if number:
            sql += " AND upper(number) = upper(%s)"
            a.append(number)
        if supplier:
            sql += " AND supplier ILIKE %s"
            a.append(f"%{supplier}%")
        found = self._q(sql + " ORDER BY issue_date DESC LIMIT 10", a)
        out: dict[str, Any] = {"rows": _rows(found)}
        if not found:  # nothing on file: the invoices that arrived by email
            mails = self._email_invoices([w for w in (number, supplier) if w], days)
            out["email_invoices"] = [{k: v for k, v in m.items() if k != "id"} for m in mails[:MAX_ROWS]]
            if not mails:
                out["note"] = ("No invoices on file or in email for that. Do not guess supplier names. To send PDFs "
                                "call send_invoices: it also checks company folders and KSeF.")
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
            sql += " AND loop <> 'strategy' AND (owner_id = %s OR requested_by = %s)"
            a += [self.requester, self.requester]
        return {"rows": _rows(self._q(sql + " ORDER BY updated_at DESC LIMIT 10", a))}

    def t_task_log(self, task_id: str, **_: Any) -> dict[str, Any]:
        """The task row and its steps are read in one REPEATABLE READ snapshot and the permission is judged on that
        row, so a task that changes hands (even away and back) while we read cannot let other steps in."""
        cols = "id, title, status, outcome, owner_id, requested_by, loop"
        with self.ctx.conn_factory() as conn:
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            task = conn.execute(f"SELECT {cols} FROM agent_tasks WHERE upper(id) = upper(%s)",
                                [task_id.strip()]).fetchone()
            if not task:
                return {"error": "not_found"}
            if task["loop"] == "strategy" and not self._is_exec():
                return {"error": "access_denied", "policy": "strategy: exec-only"}
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
                        to: str = "", words: str = "", **_: Any) -> dict[str, Any]:
        """The invoice PDFs, as real attachments: in this chat and in a Slack DM (the asker's, or `to`'s). Invoices
        on file first, then PDFs that arrived by email (filtered by number / supplier / words)."""
        ok, policy = self._allowed("invoicing", "invoices")
        if not ok:
            return {"error": "access_denied", "policy": policy}
        recipient = self.requester
        if (to or "").strip() and not self._is_self(to) and self._person(to) != self.requester:
            recipient = self._on_slack(to)
            if recipient is None:
                return {"error": "unknown_person", "person": to}
        number = (number or "").strip() or invoice_number_in(words or "") or (
            "" if supplier or words else (self._from_question() or ""))
        supplier = (supplier or "").strip()
        since = (self.ctx.now() - timedelta(days=min(max(int(days or 90), 1), 3650))).date()
        word_list = [w for w in re.split(r"[\s,;]+", words or "") if w]
        unpaid = str(unpaid_only).casefold() in ("true", "1", "yes")
        rows = self._invoice_rows(number, supplier, word_list, since, unpaid, MAX_PDFS + 1)
        more = len(rows) > MAX_PDFS
        rows = rows[:MAX_PDFS]
        originals = self._original_pdfs([r["number"] for r in rows]) if rows else {}
        files: list[tuple[str, bytes]] = []
        listed: list[dict[str, Any]] = []
        for r in rows:
            name = f"Invoice_{r['number']}.pdf"
            if r["number"] in originals:
                data, source = originals[r["number"]]
            else:
                data, source = self._render_invoice(r), "copy from the invoicing system"
            files.append((name, data))
            listed.append({"file": name, "supplier": r["supplier"], "total": eur(Decimal(r["total"])),
                           "date": str(r["issue_date"]), "source": source})
        # PDFs that arrived by email: always looked at, so invoices nobody filed are found too
        terms = [w for w in [number, supplier, *word_list] if w and len(w) >= 2 and w.casefold() not in FILLER_WORDS][:4]
        on_file = [r["number"].upper() for r in rows]
        mails = self._email_invoices(terms, days) if len(files) < MAX_PDFS else []
        for m in mails:
            for name in m["pdfs"]:
                if len(files) >= MAX_PDFS:
                    more = True
                    break
                if any(n in name.upper() for n in on_file) or any(name == f for f, _ in files):
                    continue
                try:
                    data = self.ctx.connectors.mail.attachment(m["id"], name, task_id=self.task.id)
                except Exception:  # noqa: BLE001
                    log.warning("could not download %s", name, exc_info=True)
                    data = None
                if data:
                    files.append((name, data))
                    listed.append({"file": name, "from": m["from"], "subject": m["subject"], "date": m["date"],
                                   "source": "email attachment"})
        # Not on file and not in email: the other sources. Invoice numbers come from the request and from the bank
        # references of payments to that supplier (an invoice the bank names but nobody filed).
        checked = ["invoicing system"] + (["email"] if self.ctx.connectors.mail.available() else [])
        if len(files) < MAX_PDFS:
            have = {n for n, _ in files}
            numbers = [n for n in dict.fromkeys(
                ([number] if number else []) + self._payment_invoice_numbers(supplier, word_list, since))
                if n and not any(n.upper() in f.upper() for f in have) and n.upper() not in on_file]
            extra = self._other_source_pdfs(numbers, [w for w in [supplier, *word_list] if self._useful(w)],
                                            have, MAX_PDFS - len(files), checked)
            for name, data, item in extra:
                files.append((name, data))
                listed.append(item)
        if not files:
            mail_on = self.ctx.connectors.mail.available()
            return {"invoices": 0, "attached": False, "checked": checked,
                    "note": "No invoice PDFs found in " + ", ".join(checked) + (
                        "" if mail_on else "; email is not connected (Connections)") +
                    ". Nothing was sent or attached; say exactly that."}
        who = self.ctx.hr.name(self.requester)
        comment = (f"{len(files)} invoice PDF(s)" + (f" for {' '.join(terms)}" if terms else "")
                   + (f", sent by {who} via Scout" if recipient != self.requester else "") + ".")
        try:
            link = self.ctx.notifier.upload(recipient, files, comment)
        except Exception:  # noqa: BLE001 - the PDFs still reach the dashboard chat
            log.warning("could not upload invoices to Slack", exc_info=True)
            link = None
        for (name, data), item in zip(files, listed, strict=True):
            self.ctx.recorder.step(self.task.id, "sent_file", f"Sent {name} ({item['source']})"
                                   + (f" to {self.ctx.hr.name(recipient)} on Slack" if link else ""), source="scout",
                                   detail={"name": name, "mime": "application/pdf",
                                           "sent_to_slack": bool(link) and recipient == self.requester,
                                           "data_b64": base64.b64encode(data).decode()})
        if recipient != self.requester:
            self.ctx.audit.record("agent", "send_invoices", f"slack:{recipient}", comment, task_id=self.task.id,
                                  requester=self.requester, role=self.role)
        return {"invoices": len(files), "attached": True, "files": listed, "sent_to_slack": bool(link),
                "slack_recipient": self.ctx.hr.full_name(recipient), "more_not_sent": more,
                "note": "The PDFs are attached in this chat" + (f" and in {self.ctx.hr.name(recipient)}'s Slack DM"
                                                                if link else "") + "."}

    @staticmethod
    def _useful(word: str) -> bool:
        return len(word) >= 3 and word.casefold() not in FILLER_WORDS

    def _invoice_rows(self, number: str, supplier: str, words: list[str], since: Any, unpaid: bool,
                      limit: int) -> list[dict[str, Any]]:
        """Invoices on file, most specific match first: the exact number, a partial number, the supplier in the
        period, the supplier at any time, its first word, then any of the words in a number or a supplier name."""
        base = ("SELECT id, number, supplier, issue_date, due_date, total, currency, transaction_id, category "
                "FROM invoices WHERE TRUE")
        tail = (" AND transaction_id IS NULL" if unpaid else "") + f" ORDER BY issue_date DESC, number LIMIT {limit}"
        tries: list[tuple[str, list[Any]]] = []
        if number:
            tries += [(" AND upper(number) = upper(%s)", [number]), (" AND number ILIKE %s", [f"%{number}%"])]
        if supplier:
            tries += [(" AND supplier ILIKE %s AND issue_date >= %s", [f"%{supplier}%", since]),
                      (" AND supplier ILIKE %s", [f"%{supplier}%"])]
            first = supplier.split()[0]
            if first != supplier and self._useful(first):
                tries.append((" AND supplier ILIKE %s", [f"%{first}%"]))
        terms = [w for w in words if self._useful(w)][:4]
        if terms:
            ors = " OR ".join("number ILIKE %s OR supplier ILIKE %s" for _ in terms)
            tries.append((f" AND ({ors})", [x for w in terms for x in (f"%{w}%", f"%{w}%")]))
        if not number and not supplier and not terms:  # nothing named: the newest ones
            tries.append((" AND issue_date >= %s", [since]))
        for where, args in tries:
            try:
                rows = self._q(base + where + tail, args)
            except Exception:  # noqa: BLE001 - the invoicing system failing must not stop the other sources
                log.warning("invoice lookup failed", exc_info=True)
                return []
            if rows:
                return rows
        return []

    def _payment_invoice_numbers(self, supplier: str, words: list[str], since: Any) -> list[str]:
        """Invoice numbers named by outgoing payments to that supplier (the bank knows the number even when the
        invoice was never filed). Only for askers with bank access."""
        terms = [w for w in [supplier, *words] if self._useful(w)][:4]
        if not terms or not self._allowed("bank", "transactions")[0]:
            return []
        ors = " OR ".join("counterparty ILIKE %s OR reference ILIKE %s" for _ in terms)
        try:
            rows = self._q("SELECT invoice_number, booking_date FROM transactions WHERE amount < 0 AND invoice_number "
                           f"IS NOT NULL AND booking_date >= %s AND NOT {salary_sql()} AND ({ors}) "
                           "ORDER BY booking_date DESC LIMIT 20",
                           [since, *[x for w in terms for x in (f"%{w}%", f"%{w}%")]])
        except Exception:  # noqa: BLE001
            log.warning("payment lookup for invoice numbers failed", exc_info=True)
            return []
        return list(dict.fromkeys(r["invoice_number"] for r in rows))[:MAX_PDFS]

    def _other_source_pdfs(self, numbers: list[str], terms: list[str], have: set[str], room: int,
                           checked: list[str]) -> list[tuple[str, bytes, dict[str, Any]]]:
        """PDFs for invoices that are neither on file nor in email: originals in company folders and the mailbox
        (by number), KSeF e-invoices (by number), then invoice-like PDFs in company folders (by supplier words)."""
        out: list[tuple[str, bytes, dict[str, Any]]] = []

        def add(name: str, data: bytes, item: dict[str, Any]) -> None:
            if len(out) < room and name not in have:
                have.add(name)
                out.append((name, data, {"file": name, **item}))

        if numbers:
            checked.append("company folders")
            try:
                originals = self._original_pdfs(numbers)
            except Exception:  # noqa: BLE001
                log.warning("original invoice lookup failed", exc_info=True)
                originals = {}
            for n, (data, source) in originals.items():
                add(f"Invoice_{n}.pdf", data, {"number": n, "source": source})
            ksef = getattr(self.ctx.connectors, "ksef", None)
            if ksef is not None:
                checked.append("KSeF")
                for n in [n for n in numbers if n not in originals][:5]:
                    if len(out) >= room:
                        break
                    got = self._ksef_pdf(ksef, n)
                    if got:
                        data, inv = got
                        add(f"Invoice_{n}.pdf", data, {"supplier": inv.supplier, "total": eur(inv.total),
                                                       "date": str(inv.issue_date), "source": "KSeF e-invoice"})
        if terms and len(out) < room:
            if "company folders" not in checked:
                checked.append("company folders")
            for name, data, source in self._folder_invoice_pdfs(terms, have, room - len(out)):
                add(name, data, {"source": source})
        return out

    def _ksef_pdf(self, ksef: Any, number: str) -> tuple[bytes, Invoice] | None:
        """The KSeF e-invoice with this number addressed to us, as a PDF copy."""
        try:
            hits = ksef.find(number, task_id=self.task.id, days=90, attempts=1)
        except Exception:  # noqa: BLE001 - KSeF unreachable: the other sources still count
            log.warning("KSeF lookup for %s failed", number, exc_info=True)
            return None
        for doc, ksef_number in hits:
            if (doc.get("number") or "").upper() != number.upper() or doc.get("buyer_nip") != ksef.nip:
                continue
            try:
                inv = Invoice(id=f"ksef_{ksef_number}", number=doc["number"], supplier=doc.get("supplier") or "?",
                              issue_date=doc["issue_date"], due_date=doc.get("due_date"), total=Decimal(doc["total"]),
                              currency=doc.get("currency") or "PLN",
                              lines=[InvoiceLine(**ln) for ln in doc.get("lines") or []], source=f"ksef:{ksef_number}")
                return invoice_pdf(inv, title_prefix=f"INVOICE (KSeF {ksef_number})"), inv
            except Exception:  # noqa: BLE001
                log.warning("could not render KSeF invoice %s", number, exc_info=True)
        return None

    def _folder_invoice_pdfs(self, terms: list[str], have: set[str], room: int) -> list[tuple[str, bytes, str]]:
        """Invoice PDFs in the company folders the asker may see whose path names one of the terms."""
        out: list[tuple[str, bytes, str]] = []
        for e in self.ctx.hr.all():
            if len(out) >= room:
                break
            if not self._allowed(f"laptop:{e['id']}", "company folders")[0]:
                continue
            try:
                hits = self.ctx.connectors.files.search(e["id"], terms, task_id=self.task.id)
            except Exception:  # noqa: BLE001
                log.warning("company folder search failed", exc_info=True)
                continue
            for f in hits:
                path = f.get("path") or ""
                name = path.rsplit("/", 1)[-1]
                if (len(out) >= room or not path.lower().endswith(".pdf") or name in have
                        or not any(t.casefold() in path.casefold() for t in terms)
                        or not (INVOICE_FILE.search(name) or invoice_number_in(name.upper()))):
                    continue
                try:
                    data = self.ctx.connectors.files.read(e["id"], path, task_id=self.task.id)
                except Exception:  # noqa: BLE001
                    data = None
                if data and data[:5] == b"%PDF-":
                    have.add(name)
                    out.append((name, data, f"original from {self.ctx.hr.name(e['id'])}'s company folders"))
        return out

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

    def t_send_briefing(self, person: str = "", **_: Any) -> dict[str, Any]:
        """The newest finished strategy task (the CEO daily briefing, or the news review the dashboard writes), sent
        to a colleague's Slack DM. The result never carries the text: a model can forward it without reading it."""
        if not self._is_exec():
            self.ctx.audit.record("agent", "send_briefing", "tasks", "strategy briefing", "DENY", task_id=self.task.id,
                                  requester=self.requester, role=self.role, policy="strategy: exec-only")
            return {"error": "access_denied", "policy": "strategy: exec-only"}
        if not (person or "").strip():
            return {"error": "missing_arguments", "hint": "Call again with person (who gets the briefing)."}
        who = self._on_slack(person)
        if who is None:
            return {"error": "unknown_person", "person": person,
                    "hint": "Nobody by that name in the company or in the Slack workspace."}
        if not self._slack_of(who):
            return {"error": "not_on_slack", "person": self.ctx.hr.full_name(who)}
        rows = self._q("SELECT title, outcome, created_at FROM agent_tasks WHERE loop = 'strategy' AND status = 'done' "
                       "AND coalesce(outcome, '') <> '' ORDER BY created_at DESC, id DESC LIMIT 1", [])
        if not rows:
            return {"error": "no_briefing", "hint": "No daily briefing has finished yet."}
        title, text, created = rows[0]["title"], rows[0]["outcome"].strip(), rows[0]["created_at"]
        sign = f"\n\nfrom {self.ctx.hr.name(self.requester)} via Scout"
        safe = guardrails.screen_output(self.ctx, self.task.id, text[:BRIEFING_PART * 4] + sign, requester=self.requester,
                                        source="send_briefing", role=self.ctx.hr.role(who))  # judged by who receives it
        if safe == guardrails.OUTPUT_REPLY:
            self.blocked_reply = guardrails.OUTPUT_REPLY
            return {"error": "blocked_by_safety_policy", "sent": False}
        parts = _parts(safe, BRIEFING_PART)
        cut = len(text) > BRIEFING_PART * 4 or len(parts) > BRIEFING_PARTS
        if len(parts) > BRIEFING_PARTS:  # what Slack takes in a few messages; the signature stays on the last one
            parts = [*parts[:BRIEFING_PARTS - 1], f"{parts[BRIEFING_PARTS - 1][:BRIEFING_PART - len(sign) - 1].rstrip()}…{sign}"]
        delivered = 0
        try:
            for part in parts:
                if not self.ctx.notifier.dm(who, part, dm_replies.LEVEL, task_id=self.task.id):
                    raise RuntimeError("not delivered")
                delivered += 1
        except Exception:  # noqa: BLE001 - Slack said no: say so, and what already went out
            log.exception("send_briefing failed after %d of %d part(s)", delivered, len(parts))
            if not delivered:
                return {"error": "not_sent", "person": self.ctx.hr.full_name(who),
                        "hint": "Slack did not deliver it. Tell the user it was not sent."}
            self.ctx.audit.record("agent", "send_briefing", f"slack:{who}", title[:200], task_id=self.task.id,
                                  requester=self.requester, role=self.role,
                                  reason=f"partial: {delivered} of {len(parts)} Slack messages delivered")
            return {"sent": "partial", "to": self.ctx.hr.full_name(who), "title": title, "date": f"{created:%Y-%m-%d}",
                    "parts_delivered": delivered, "parts_total": len(parts),
                    "note": f"Only {delivered} of {len(parts)} messages reached them; Slack refused the rest. Tell the "
                            "user it was delivered only in part, do not say it was sent in full, and do not retry "
                            "unless the user asks (a retry sends the delivered part again)."}
        self.ctx.audit.record("agent", "send_briefing", f"slack:{who}", title[:200], task_id=self.task.id,
                              requester=self.requester, role=self.role)
        out: dict[str, Any] = {"sent": True, "to": self.ctx.hr.full_name(who), "title": title,
                               "date": f"{created:%Y-%m-%d}"}
        if cut:
            out["truncated"] = True  # the Slack messages end with an ellipsis; the full text is in the dashboard
        return out

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

    def t_search_web(self, query: str = "", **_: Any) -> dict[str, Any]:
        if not isinstance(query, str):  # a number or list would be str()-ed into a paid search: refuse before any call
            return {"error": "bad_arguments", "note": "query must be text"}
        query = " ".join(query.split())
        if not query:
            return {"error": "missing_arguments", "note": "query is required"}
        if self.web_search is None:
            return {"error": "web_search_unavailable", "note": "Live web search needs OpenAI or Mistral."}
        return self.web_search(query)

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
    if name == "search_web" and "sources" in result:
        return f"Searched the web for \"{str((args or {}).get('query', ''))[:80]}\": {len(result['sources'])} source(s)"
    if name == "send_briefing" and result.get("sent") == "partial":
        return (f"Sent only part of the {result.get('title', 'daily briefing')} to {result.get('to')} "
                f"({result.get('parts_delivered')} of {result.get('parts_total')} messages)")
    if name == "send_briefing" and result.get("sent"):
        return f"Sent the {result.get('title', 'daily briefing')} to {result.get('to')}"
    n = next((len(v) for v in result.values() if isinstance(v, list)), None)
    return f"Looked at {label}({shown})" + (f": {n} result(s)" if n is not None else "")


def dumps(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)[:3500]
