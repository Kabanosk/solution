"""Invoicing loop: why did money leave? The evidence ladder of the deck.

    picked_up -> invoicing system -> authorized files -> company email -> chat history -> ask a person (Slack)

The first rung that explains the payment ends the climb. Every rung writes its step line at the moment it runs;
the texts are the deck's. Jobs handled here: `bank_txn` (a new outgoing payment), `reminder` (nobody answered
yet) and `reply` (a person or a mail answered, maybe with a PDF).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from scout import guardrails
from scout.documents import document_text
from scout.llm_tasks import classify_chat, classify_reply, explain, extract_invoice
from scout.loops import liquidity
from scout.loops.context import JobContext, eur, invoice_number_in
from scout.models import BankTxn, InvoiceDoc, Job, Task

log = logging.getLogger(__name__)
LOOP = "invoicing"
MAX_CANDIDATES = 5


# -- helpers ----------------------------------------------------------------------------------------------
Candidate = tuple[bytes, str, dict[str, Any]]  # (data, file name, evidence item)


def _kind_of(txn: BankTxn) -> str:
    return "card payment" if txn.type == "card_payment" else "transfer"


def _safe(ctx: JobContext, task: Task, what: str, fn, default):  # noqa: ANN001
    """Run a rung; a failing system becomes an `error` step and the climb goes on."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.exception("%s failed for %s", what, task.id)
        ctx.recorder.step(task.id, "error", f"Couldn't check {what}: {type(e).__name__}", detail={"error": str(e)[:300]})
        return default


def _doc_from_text(ctx: JobContext, text: str, *, number: str | None, supplier: str, fallback_day: date,
                   source: str) -> InvoiceDoc | None:
    if number and number.casefold() not in text.casefold():
        return None
    ex = extract_invoice(ctx.llm, text)
    if ex is None:
        return None
    lines = [ln.model_dump(mode="json") for ln in ex.lines]
    if lines and sum((Decimal(str(ln["amount"])) for ln in lines), Decimal(0)) != ex.total:
        lines = []  # do not store lines that do not add up
    return InvoiceDoc(number=number or ex.number or "", supplier=ex.supplier or supplier,
                      issue_date=ex.issue_date or fallback_day, total=ex.total, lines=lines, source=source)


class Match:
    """What reading the candidate documents gave: a document with the right amount, or the first mismatch."""

    def __init__(self) -> None:
        self.doc: InvoiceDoc | None = None
        self.evidence: dict[str, Any] = {}
        self.name = ""
        self.text = ""
        self.mismatch: InvoiceDoc | None = None
        self.flagged: dict[str, Any] | None = None  # the first document the safety check flagged (never read)
        self.flagged_all: list[dict[str, Any]] = []  # every flagged candidate, the first one included


def _match(ctx: JobContext, task: Task, cands: list[Candidate], number: str | None, day: date) -> Match:
    m = Match()
    for data, name, ev in cands[:MAX_CANDIDATES]:
        text = document_text(data, name)
        if not text:
            continue
        if number and number.casefold() not in text.casefold():
            continue
        v = guardrails.screen_document(ctx, task.id, text, f"{ev.get('kind', 'document')} {name}")
        if v.blocked:  # the model never sees it; the CEO decides (see `hold_flagged`)
            entry = {"name": name, "evidence": ev, "rails": v.rails, "data": data,
                     "from": ev.get("from") or ev.get("location") or ""}
            m.flagged_all.append(entry)
            m.flagged = m.flagged or entry
            continue
        doc = _doc_from_text(ctx, text, number=number, supplier=task.counterparty or "", fallback_day=day,
                             source=ev["ref"])
        if doc is None:
            continue
        if doc.total == abs(task.amount):
            m.doc, m.evidence, m.name, m.text = doc, ev, name, text
            return m
        m.mismatch = m.mismatch or doc
    return m


def _alert_flagged(ctx: JobContext, task: Task, m: Match) -> None:
    """Every flagged candidate reaches the owner, once per document: the first one is on the approval card when no
    candidate matched (`hold_flagged`); the others, and all of them when another candidate matched and was filed,
    get the suspicious-content DM."""
    for f in m.flagged_all[(0 if m.doc else 1):]:
        ev = f["evidence"]
        key = f"doc:{ev.get('ref') or f['name']}:{hashlib.sha256(f['data']).hexdigest()[:16]}"
        guardrails.alert_outside(ctx, key, f"{f['name']}" + (f" ({f['from']})" if f.get("from") else ""),
                                 f["rails"], task.id)


def _keep_document(ctx: JobContext, task: Task, name: str, data: bytes) -> int:
    """The original flagged document, kept in Postgres so that an approved invoice has a retrievable attachment."""
    with ctx.conn_factory() as conn:
        row = conn.execute("INSERT INTO flagged_documents (task_id, name, sha256, content) VALUES (%s, %s, %s, %s) "
                           "RETURNING id", (task.id, name, hashlib.sha256(data).hexdigest(), data)).fetchone()
        conn.commit()
    return row["id"]


def _approval_pending(ctx: JobContext, task: Task) -> bool:
    with ctx.conn_factory() as conn:
        return conn.execute("SELECT 1 FROM approvals WHERE task_id = %s AND status = 'pending'",
                            (task.id,)).fetchone() is not None


def hold_flagged(ctx: JobContext, task: Task, flagged: dict[str, Any], number: str | None, day: date,
                 doc: InvoiceDoc | None = None) -> bool:
    """A document the safety check flagged is never filed on its own: it goes to the CEO's approval card with a
    `Suspicious content` badge and the rail. The model never read the text, so nothing was extracted: Approve
    attaches the ORIGINAL document (its reference goes on the task as evidence) and files the invoice with the
    number and amount of the payment, marked unverified. KSeF documents are filed with their own fields."""
    ev = {**(flagged.get("evidence") or {})}
    ev.setdefault("name", flagged["name"])
    ev.setdefault("kind", "document")
    ev.setdefault("ref", flagged["name"])
    ev["flagged_rails"] = flagged["rails"]
    if flagged.get("data"):  # keep the original: a Slack PDF has no other place to be fetched from
        ev["document_id"] = _keep_document(ctx, task, flagged["name"], flagged["data"])
        ev["sha256"] = hashlib.sha256(flagged["data"]).hexdigest()
    unverified = doc is None
    doc = doc or InvoiceDoc(number=number or f"FLAGGED-{task.id}", supplier=(task.counterparty or "unknown")[:200],
                            issue_date=day, total=abs(task.amount), source=ev["ref"])
    where = flagged["name"] + (f" ({flagged['from']})" if flagged.get("from") else "")
    ctx.recorder.step(task.id, "flagged", f"Suspicious content in {where}: not read by the model, not filed "
                                          f"(rail: {flagged['rails']})", source="guardrails",
                      detail={"rails": flagged["rails"], "document": flagged["name"]})
    summary = (f"file {where} as invoice {doc.number} for {eur(doc.total)}"
               + (", with values taken from the payment" if unverified else ""))
    ctx.recorder.step(task.id, "proposed", f"Proposed to {summary} after approval", source="guardrails",
                      detail={"summary": summary[0].upper() + summary[1:],
                              "flagged_invoice": {"doc": doc.model_dump(mode="json"), "evidence": ev,
                                                  "rails": flagged["rails"], "unverified": unverified}})
    question = (f"*Suspicious content*: {where}, for the {eur(task.amount)} payment to {task.counterparty} "
                f"({task.id}), was flagged by the safety check (rail: {flagged['rails']}). I did not read it "
                f"and did not file it. Approve to {summary} (the document is attached as evidence, its content "
                "unverified), or Reject to leave the payment without an invoice.")
    liquidity._ask_approval(ctx, task, question)  # the same card, buttons and graph as the liquidity approvals
    return True


def decide_flagged(ctx: JobContext, task: Task, proposal: dict[str, Any], approved: bool, who: str) -> None:
    """The CEO answered the card of a flagged invoice (called by `liquidity._after_answer`)."""
    info = proposal["flagged_invoice"]
    if not approved:
        ctx.recorder.step(task.id, "rejected", f"{who} rejected the flagged document", source="approval")
        outcome = f"Closed · {who} rejected the flagged document; the payment stays without an invoice"
        ctx.recorder.step(task.id, "resolved", outcome)
        ctx.recorder.set_status(task.id, "cancelled", outcome=outcome, resolved_via="approval")
        return
    ctx.recorder.step(task.id, "approved", f"{who} approved filing the flagged document", source="approval")
    doc, unverified = InvoiceDoc(**info["doc"]), bool(info.get("unverified"))
    evidence = {"source": "approval", **info["evidence"]}  # the original document: name, ref, where it came from
    ctx.audit.record(task.owner_id or "agent", "file_flagged_invoice", "invoicing",
                     f"{doc.number} -> {task.transaction_id}: original {evidence.get('ref')}; "
                     + ("values taken from the payment, unverified" if unverified else "values from the document"),
                     task_id=task.id, requester=task.owner_id, role="ceo", policy="guardrails: flagged document")
    if _file(ctx, task, doc, via="approval", evidence=evidence, who=who, unverified=unverified) is not True:
        ctx.recorder.set_status(task.id, "escalated", owner_id=ctx.settings.ceo_id)


def _file(ctx: JobContext, task: Task, doc: InvoiceDoc, *, via: str, evidence: dict[str, Any], who: str | None = None,
          text_excerpt: str = "", unverified: bool = False) -> bool | str:
    """File `doc` for the task's payment and close the task. True when filed, else the error code."""
    ok, res = ctx.connectors.invoicing.file(doc, task.transaction_id, task_id=task.id)
    if not ok:
        ctx.recorder.step(task.id, "note", f"Could not file {doc.number}: {res.replace('_', ' ')}", source="invoicing_pg")
        return res
    ctx.recorder.step(task.id, "acted", f"Filed invoice {doc.number} in the invoicing system", source="invoicing_pg",
                      detail={"invoice_id": res, "lines": len(doc.lines), "text_excerpt": text_excerpt[:1500],
                              **({"unverified": True} if unverified else {})},
                      evidence=evidence)
    outcome = (f"Resolved · {eur(doc.total)} filed as {doc.number}"
               + (" (values taken from the payment, the document was not read: unverified)" if unverified else ""))
    ctx.recorder.step(task.id, "resolved", outcome + (f" (sent by {who})" if who else ""), source=evidence.get("source"))
    ctx.recorder.set_status(task.id, "done", outcome=outcome, resolved_via=via, level="AUTO")
    return True


def _file_match(ctx: JobContext, task: Task, m: Match, via: str, who: str | None = None) -> bool:
    ctx.recorder.step(task.id, "extracted", f"Read {m.name}: {m.doc.number or 'invoice'} for {eur(m.doc.total)}, "
                                            "amounts match", source=m.evidence.get("source"),
                      detail={"supplier": m.doc.supplier, "total": str(m.doc.total), "lines": len(m.doc.lines)})
    return _file(ctx, task, m.doc, via=via, evidence=m.evidence, who=who, text_excerpt=m.text) is True


# -- rungs ------------------------------------------------------------------------------------------------
def _rung_system(ctx: JobContext, task: Task, number: str | None, day: date) -> bool:
    inv = ctx.connectors.invoicing
    docs = _safe(ctx, task, "the invoicing system",
                 lambda: inv.find(number=number, task_id=task.id) if number else
                 inv.find(supplier=task.counterparty, amount=task.amount, around=day, task_id=task.id), [])
    usable = [d for d in docs if d.total == abs(task.amount) and d.transaction_id in (None, task.transaction_id)]
    n = len(docs)
    text = f"Searched the invoicing system: {n} match{'es' if n != 1 else ''}"
    other = [d for d in docs if d not in usable]
    if other and not usable:
        d = other[0]
        why = ("belongs to another payment" if d.transaction_id not in (None, task.transaction_id)
               else f"is for {eur(d.total)}")
        text += f", but {d.number} {why}"
    ctx.recorder.step(task.id, "checked_system", text, source="invoicing_pg", result_count=n)
    for d in usable:
        if d.transaction_id == task.transaction_id:
            outcome = f"Resolved · {eur(d.total)} already on file as {d.number}"
            ctx.recorder.step(task.id, "resolved", outcome, source="invoicing_pg")
            ctx.recorder.set_status(task.id, "done", outcome=outcome, resolved_via="invoicing", level="AUTO")
            return True
        if _file(ctx, task, d, via="invoicing", evidence={"name": d.number, "kind": "invoice", "ref": f"invoicing:{d.number}",
                                                          "source": "invoicing_pg"}) is True:
            return True
    return False


def _rung_ksef(ctx: JobContext, task: Task, number: str | None) -> tuple[bool, InvoiceDoc | None]:
    """Poland: every B2B invoice goes through KSeF, so ask KSeF for the number before anyone's laptop."""
    ksef = getattr(ctx.connectors, "ksef", None)
    if ksef is None or not number:
        return False, None
    hits = _safe(ctx, task, "KSeF", lambda: ksef.find(number, task_id=task.id), [])
    if not hits:
        ctx.recorder.step(task.id, "checked_system", f"Searched KSeF (national e-invoice system) for {number}: "
                                                     "not issued there", source="ksef", result_count=0)
        return False, None
    from scout.ksef_sync import trusted_seller

    exact = [(d, k) for d, k in hits if d.get("number") == number and d.get("buyer_nip") == ksef.nip]
    if not exact:
        ctx.recorder.step(task.id, "checked_system", f"Searched KSeF for {number}: {len(hits)} look-alike "
                                                     "invoice(s), none addressed to us under exactly this number",
                          source="ksef", result_count=0)
        return False, None
    mismatch = None
    for doc, ksef_number in exact:
        inv = InvoiceDoc(number=number, supplier=(doc["supplier"] or task.counterparty or "?")[:200],
                         issue_date=doc["issue_date"], total=doc["total"], source=f"ksef:{ksef_number}",
                         lines=[{"description": (ln["description"] or "-")[:300], "quantity": ln["quantity"],
                                 "unit_price": ln["unit_price"], "amount": ln["amount"]} for ln in doc["lines"][:200]])
        ctx.recorder.step(task.id, "checked_system",
                          f"Found {number} in KSeF as {ksef_number}: e-invoice from {inv.supplier} "
                          f"(NIP {doc.get('seller_nip')}), {eur(inv.total)}", source="ksef", result_count=len(hits),
                          detail={"ksef_number": ksef_number, "seller_nip": doc.get("seller_nip"),
                                  "lines": len(inv.lines)})
        if inv.total != abs(task.amount):
            mismatch = mismatch or inv
            continue
        if len(exact) > 1 or not trusted_seller(doc, task.counterparty, ksef.sellers):
            ctx.recorder.step(task.id, "note", f"Not filed automatically: the seller (NIP {doc.get('seller_nip')}) is "
                                               f"not a known supplier and does not match {task.counterparty}"
                              if len(exact) == 1 else f"Not filed automatically: {len(exact)} KSeF invoices carry "
                                                      f"the number {number}", source="ksef")
            mismatch = mismatch or inv
            continue
        v = guardrails.screen_document(ctx, task.id, "\n".join([inv.supplier, *[ln["description"] for ln in inv.lines]]),
                                       f"KSeF {ksef_number}")
        if v.blocked:
            flagged = {"name": f"KSeF {ksef_number}", "rails": v.rails, "from": inv.supplier,
                       "evidence": {"name": f"KSeF {ksef_number}", "kind": "invoice", "ref": f"ksef:{ksef_number}",
                                    "source": "ksef"}}
            return hold_flagged(ctx, task, flagged, number, inv.issue_date, doc=inv), None
        filed = _file(ctx, task, inv, via="invoicing",
                      evidence={"name": f"KSeF {ksef_number}", "kind": "invoice", "ref": f"ksef:{ksef_number}",
                                "source": "ksef"})
        if filed is True:
            return True, None
    return False, mismatch


def _rung_files(ctx: JobContext, task: Task, number: str | None, day: date) -> tuple[bool, InvoiceDoc | None]:
    owner = ctx.settings.document_owner_id
    terms = [t for t in (number, (task.counterparty or "").split()[0] if task.counterparty else None) if t]
    files = _safe(ctx, task, f"{ctx.hr.name(owner)}'s company folders",
                  lambda: ctx.connectors.files.search(owner, terms, task_id=task.id), [])
    cands: list[Candidate] = []
    for f in files[:MAX_CANDIDATES]:
        data = _safe(ctx, task, f["name"], lambda f=f: ctx.connectors.files.read(owner, f["path"], task_id=task.id), None)
        if data:
            cands.append((data, f["name"], {"name": f["name"], "kind": "pdf", "path": f["path"], "location": f"{ctx.hr.name(owner)}'s {f['path'].split('/')[0]}",
                                            "ref": f"files:{owner}/{f['path']}",
                                            "source": f"workstation:{owner}"}))
    m = _match(ctx, task, cands, number, day)
    found = (f"found {m.name}" if m.doc else "found a document with a different amount" if m.mismatch
             else "no receipt")
    ctx.recorder.step(task.id, "checked_files", f"Checked {ctx.hr.name(owner)}'s company folders: {found}",
                      source=f"workstation:{owner}", result_count=len(files))
    _alert_flagged(ctx, task, m)
    if not m.doc and m.flagged and hold_flagged(ctx, task, m.flagged, number, day):
        return True, None
    if m.doc and _file_match(ctx, task, m, "files"):
        ctx.notifier.dm(owner, f"FYI: I took {m.name} from your company folder and filed it for the "
                               f"{eur(task.amount)} payment to {task.counterparty} ({task.id}). I only read company "
                               "folders, and every access is logged.", "NOTIFY", task_id=task.id)
        return True, None
    return False, m.mismatch


def _rung_email(ctx: JobContext, task: Task, number: str | None, day: date) -> tuple[bool, InvoiceDoc | None]:
    mail = ctx.connectors.mail
    if not mail.available():
        ctx.recorder.step(task.id, "checked_email", "Company email: not connected", source="mail")
        return False, None
    terms = [t for t in (number, task.counterparty) if t]
    since = ctx.now() - timedelta(days=ctx.settings.chat_lookback_days)
    hits = _safe(ctx, task, "company email", lambda: mail.search(terms, since, task_id=task.id), [])
    cands: list[Candidate] = []
    for h in hits:
        for name in h.attachments:
            if name.lower().endswith(".pdf"):
                data = _safe(ctx, task, name, lambda h=h, name=name: mail.attachment(h.id, name, task_id=task.id), None)
                if data:
                    cands.append((data, name, {"name": name, "subject": h.subject, "from": h.sender, "kind": "email",
                                               "ref": f"{mail.name}:{h.id}", "source": f"mail:{h.mailbox}"}))
    m = _match(ctx, task, cands, number, day)
    found = (f"found {m.name}" if m.doc else "found a different amount" if m.mismatch else "no receipt")
    ctx.recorder.step(task.id, "checked_email", f"Checked company email: {found}", source=f"mail:{mail.name}",
                      result_count=len(hits))
    _alert_flagged(ctx, task, m)
    if not m.doc and m.flagged and hold_flagged(ctx, task, m.flagged, number, day):
        return True, None
    if m.doc and _file_match(ctx, task, m, "email"):
        return True, None
    return False, m.mismatch


def _rung_chat(ctx: JobContext, task: Task, number: str | None) -> bool:
    since = ctx.now() - timedelta(days=ctx.settings.chat_lookback_days)
    hits = _safe(ctx, task, "chat history", lambda: ctx.connectors.chat.search(
        since=since, invoice_number=number, supplier=task.counterparty, amount=task.amount, task_id=task.id), [])
    facts = {"supplier": task.counterparty, "amount_eur": str(abs(task.amount)), "invoice": number}
    for h in hits:
        verdict = classify_chat(ctx.llm, h.text, facts)
        if verdict.label == "unrelated":
            continue
        quote = h.text if len(h.text) <= 220 else h.text[:217] + "…"
        emp = _employee_of_slack(ctx, h.user) if h.user else None
        who = ctx.hr.name(emp) if emp else (h.user_name or "a colleague")
        evidence = {"kind": "chat", "quote": quote, "author": who, "channel": f"#{h.channel}",
                    "permalink": h.permalink, "ref": h.permalink or f"slack:{h.channel}:{h.ts}",
                    "source": f"slack:#{h.channel}"}
        ctx.recorder.step(task.id, "checked_chat", f"Checked authorized chat history: found context in #{h.channel}",
                          source=f"slack:#{h.channel}", result_count=len(hits), evidence=evidence)
        if verdict.label == "refund_expected":
            amount = verdict.refund_amount or abs(task.amount)
            on = verdict.refund_by or (ctx.now().date() + timedelta(days=7))
            with ctx.conn_factory() as conn:
                conn.execute("INSERT INTO expected_cash_events (on_date, amount, label, task_id) VALUES (%s,%s,%s,%s)",
                             (on, amount, f"{task.counterparty} refund", task.id))
                conn.commit()
            follow = ctx.recorder.open(f"followup:{task.id}", LOOP, f"Watch {task.counterparty} refund, due {on:%b %d}",
                                       status="todo", amount=amount, counterparty=task.counterparty,
                                       owner_id=ctx.settings.document_owner_id)
            ctx.recorder.step(follow.id, "note", f"Created from {task.id}: {eur(amount)} refund expected by {on:%b %d}",
                              evidence=evidence)
            ctx.recorder.step(task.id, "acted", f"Added the expected refund of {eur(amount)} on {on:%b %d} to the "
                                                f"forecast; follow-up {follow.id}", source="forecast")
            outcome = f"Resolved · no invoice expected, {eur(amount)} refund due {on:%b %d} (from chat context)"
        elif verdict.label == "approval":
            outcome = f"Resolved · approved in chat by {who} (from chat context)"
        else:
            outcome = f"Resolved · {verdict.reason or 'explained in chat'} (from chat context)"
        ctx.recorder.step(task.id, "resolved", outcome, source=f"slack:#{h.channel}")
        ctx.recorder.set_status(task.id, "done", outcome=outcome, resolved_via="chat_context", level="AUTO")
        return True
    ctx.recorder.step(task.id, "checked_chat", "Checked authorized chat history: no context", source="slack",
                      result_count=len(hits))
    return False


def _employee_of_slack(ctx: JobContext, slack_id: str | None) -> str | None:
    e = ctx.hr.by_slack_id(slack_id) if slack_id else None
    return e["id"] if e else None


# -- asking people ----------------------------------------------------------------------------------------
def _question(ctx: JobContext, task: Task, who: str, number: str | None, ref: str, mismatch: InvoiceDoc | None,
              day: date) -> str:
    base = (f"Hi {ctx.hr.name(who)}! We paid {eur(task.amount)} to {task.counterparty} on {day:%b %d} "
            f"(reference “{ref}”).")
    if mismatch:
        return (f"{base} I found invoice {mismatch.number} but it shows {eur(mismatch.total)}, not {eur(task.amount)}. "
                "Could you explain the difference or send me the correct invoice as a PDF? Just reply in this thread.")
    return (f"{base} I checked the invoicing system, company folders, company email and chat history but couldn't "
            f"find {'invoice ' + number if number else 'a receipt'}. Could you send me the PDF or tell me what it was "
            "for? Just reply in this thread.")


def _ask(ctx: JobContext, task: Task, number: str | None, reference: str, holder: str | None,
         mismatch: InvoiceDoc | None, day: date) -> None:
    today = ctx.now().date()
    first_choice = holder or ctx.settings.document_owner_id
    target, skipped = ctx.hr.first_available(first_choice, today)
    role = "the card holder" if holder else "the document owner"
    if skipped:
        text = f"{ctx.hr.name(skipped[0])} is on vacation, asked {ctx.hr.name(target)} on Slack"
    else:
        text = f"Asked {role}, {ctx.hr.name(target)}, on Slack"
    ref = ctx.notifier.dm(target, _question(ctx, task, target, number, reference, mismatch, day), "NOTIFY", task_id=task.id)
    ctx.audit.record("agent", "send_dm", f"slack:{target}", f"question about {task.id}", task_id=task.id)
    ctx.recorder.step(task.id, "asked_person", text, source="slack:dm",
                      detail={"to": target, "skipped": skipped, "mismatch": bool(mismatch)})
    ctx.recorder.update(task.id, status="waiting", owner_id=target, slack_ref=ref, level="NOTIFY")
    _schedule_reminder(ctx, task.id)


def hand_over(ctx: JobContext, task_id: str, to: str, by: str, chat_task: str | None = None) -> Task:
    """A waiting invoice question goes to someone else: they get the question in a DM (and answer in its thread),
    the old question says so. Only the person it waits on or the owner (CEO) may do it."""
    task = ctx.recorder.get(task_id)
    if task.status not in ("waiting", "escalated"):
        raise ValueError(f"{task.id} is {task.status}")
    if by not in (task.owner_id, ctx.settings.ceo_id):
        ctx.audit.record("agent", "hand_over", f"task:{task.id}", f"to {to}", "DENY", task_id=task.id, requester=by,
                         policy="tasks: the person it waits on or the owner")
        raise PermissionError(f"{by} may not hand over {task.id}")
    with ctx.conn_factory() as conn:
        txn = conn.execute("SELECT reference, booking_date FROM transactions WHERE id = %s",
                           (task.transaction_id,)).fetchone() or {}
    reference = txn.get("reference") or task.title
    day = txn.get("booking_date") or ctx.now().date()
    who, new = ctx.hr.name(by), ctx.hr.name(to)
    question = _question(ctx, task, to, invoice_number_in(reference), reference, None, day)
    ref = ctx.notifier.dm(to, f"{who} asked you to take this over. {question}", "NOTIFY", task_id=task.id)
    if task.slack_ref:
        ctx.notifier.update(task.slack_ref, f"↪ {task.title}: handed to {new} by {who} ({task.id})", "NOTIFY")
    ctx.recorder.step(task.id, "handed_over", f"{who} handed this to {new}", source="slack:dm",
                      detail={"to": to, **({"chat_task": chat_task} if chat_task else {})})
    ctx.recorder.update(task.id, status="waiting", owner_id=to, slack_ref=ref)
    ctx.audit.record(by, "hand_over", f"task:{task.id}", f"to {to}", task_id=task.id, requester=by)
    _schedule_reminder(ctx, task.id)
    return ctx.recorder.get(task.id)


def _schedule_reminder(ctx: JobContext, task_id: str) -> None:
    n = sum(1 for s in ctx.recorder.steps(task_id) if s.kind in ("asked_person", "reminder", "escalated"))
    ctx.jobs.enqueue("reminder", {"task_id": task_id}, f"reminder:{task_id}:{n}",
                     run_after=ctx.now() + timedelta(hours=ctx.settings.reminder_after_hours))


# -- job handlers -----------------------------------------------------------------------------------------
def on_bank_txn(ctx: JobContext, job: Job) -> Task:
    txn = BankTxn(**job.payload["txn"])
    number = invoice_number_in(txn.reference)
    title = f"Invoice {number} for {txn.counterparty}" if number else f"Receipt for {txn.counterparty}"
    task = ctx.recorder.open(f"txn:{txn.id}", LOOP, title, amount=txn.amount, counterparty=txn.counterparty,
                             transaction_id=txn.id, owner_id=txn.holder_employee_id)
    if ctx.recorder.steps(task.id):
        return task  # this payment is already being handled (a duplicate job)
    day = txn.booked_at.date()
    ctx.recorder.step(task.id, "picked_up", f"Scout picked up the {_kind_of(txn)} from Revolut Business",
                      source="revolut_mock",
                      detail={"reference": txn.reference, "amount": str(txn.amount), "card": txn.card_last4})
    if _rung_system(ctx, task, number, day):
        return ctx.recorder.get(task.id)
    filed, mismatch_ksef = _rung_ksef(ctx, task, number)
    if filed:
        return ctx.recorder.get(task.id)
    filed, mismatch = _rung_files(ctx, task, number, day)
    mismatch = mismatch or mismatch_ksef
    if filed:
        return ctx.recorder.get(task.id)
    filed, mismatch_mail = _rung_email(ctx, task, number, day)
    if filed:
        return ctx.recorder.get(task.id)
    if _rung_chat(ctx, task, number):
        return ctx.recorder.get(task.id)
    _ask(ctx, task, number, txn.reference, txn.holder_employee_id, mismatch or mismatch_mail, day)
    return ctx.recorder.get(task.id)


def on_reminder(ctx: JobContext, job: Job) -> Task:
    task = ctx.recorder.get(job.payload["task_id"])
    if task.status not in ("waiting", "escalated"):
        return task
    steps = ctx.recorder.steps(task.id)
    last_ask = max((i for i, s in enumerate(steps) if s.kind in ("asked_person", "reminder", "escalated")), default=-1)
    if any(s.kind == "reply_received" for s in steps[last_ask + 1:]):
        return task  # an answer came in after the last ask
    current = task.owner_id or ctx.settings.document_owner_id
    already_reminded = last_ask >= 0 and steps[last_ask].kind == "reminder"
    if not already_reminded:
        ctx.notifier.dm(current, f"Reminder: I still need the invoice or an explanation for {eur(task.amount)} to "
                                 f"{task.counterparty}. Just reply in the thread above.", "NOTIFY", task_id=task.id,
                        thread=task.slack_ref)
        ctx.recorder.step(task.id, "reminder", f"Reminded {ctx.hr.name(current)}", source="slack:dm")
        _schedule_reminder(ctx, task.id)
        return ctx.recorder.get(task.id)
    today = ctx.now().date()
    nxt = ctx.hr.manager(current)
    if nxt:
        nxt, _ = ctx.hr.first_available(nxt, today)
    if not nxt or nxt == current:
        ctx.recorder.step(task.id, "escalated", f"Nobody could explain it; left for {ctx.hr.name(current)} to decide",
                          source="slack:dm")
        ctx.recorder.update(task.id, status="escalated")
        return ctx.recorder.get(task.id)
    why = explain(ctx.llm, "Say in one sentence why a paid expense without an invoice matters.",
                  {"rule": "Belegpflicht (German receipt obligation)", "risk": "tax"},
                  "Without the invoice the expense is a tax risk (Belegpflicht).")
    ref = ctx.notifier.dm(nxt, f"Hi {ctx.hr.name(nxt)}! {ctx.hr.name(current)} hasn't been able to explain "
                               f"{eur(task.amount)} to {task.counterparty} yet. {why} Do you know what it was for? You can "
                               "reply with the PDF invoice.", "NOTIFY", task_id=task.id)
    ctx.recorder.step(task.id, "escalated", f"Escalated to {ctx.hr.name(nxt)}", source="slack:dm")
    ctx.recorder.update(task.id, status="escalated", owner_id=nxt, slack_ref=ref)
    _schedule_reminder(ctx, task.id)
    return ctx.recorder.get(task.id)


def on_reply(ctx: JobContext, job: Job) -> Task:
    """A person (or a mail) answered an open task. Payload: task_id, from_id, text, files[{name, data_b64}]."""
    p = job.payload
    task = ctx.recorder.get(p["task_id"])
    if not task.open:
        return task
    who_id = p.get("from_id")
    who = "Company email" if who_id == "email" else ctx.hr.name(who_id)
    if _approval_pending(ctx, task):  # a flagged document waits for the owner: nothing new changes that
        ctx.recorder.step(task.id, "note", f"Ignored a reply from {who} while the owner decides on a flagged document",
                          source="guardrails")
        ctx.audit.record("agent", "flagged_pending", f"task:{task.id}", f"reply from {who_id} ignored", "DENY",
                         task_id=task.id, requester=who_id, policy="guardrails: approval pending")
        return ctx.recorder.get(task.id)
    via = "email" if who_id == "email" else "slack_dm"
    files = p.get("files") or []
    number = invoice_number_in(task.title) or invoice_number_in(p.get("text") or "")
    if files:
        names = ", ".join(f["name"] for f in files)
        ctx.recorder.step(task.id, "reply_received", f"{who} sent {names}", source="slack:dm" if via == "slack_dm" else "mail")
        pdfs = [f for f in files if f["name"].lower().endswith(".pdf") or base64.b64decode(f["data_b64"])[:5] == b"%PDF-"]
        if not pdfs:
            ctx.recorder.step(task.id, "note", "Received a file that is not a PDF")
            if via == "slack_dm":
                ctx.notifier.dm(who_id, "Thanks! I can only read PDF invoices for now. Could you send it as a PDF?",
                                "NOTIFY", task_id=task.id, thread=p.get("thread") or task.slack_ref)
            return ctx.recorder.get(task.id)
        cands = [(base64.b64decode(f["data_b64"]), f["name"],
                  {"name": f["name"], "kind": "pdf", "location": f"sent by {who}", "ref": f"{via}:{f['name']}",
                   "source": "slack:dm" if via == "slack_dm" else "mail"}) for f in pdfs]
        m = _match(ctx, task, cands, number, ctx.now().date())
        mismatch = m.mismatch
        _alert_flagged(ctx, task, m)
        if not m.doc and m.flagged and hold_flagged(ctx, task, m.flagged, number, ctx.now().date()):
            return ctx.recorder.get(task.id)
        if m.doc and _file_match(ctx, task, m, via, who=None if via == "email" else who):
            if via == "slack_dm":
                t = ctx.recorder.get(task.id)
                ctx.notifier.dm(who_id, f"Thanks! I filed {eur(task.amount)} as {m.doc.number or 'an invoice'}. "
                                        f"Marked resolved in Scout ({task.id}).", "AUTO", task_id=task.id,
                                thread=p.get("thread") or task.slack_ref)
                return t
            return ctx.recorder.get(task.id)
        msg = (f"The PDF shows {eur(mismatch.total)}, but the payment was {eur(task.amount)}." if mismatch
               else "I couldn't read an invoice for this payment in that PDF.")
        ctx.recorder.step(task.id, "mismatch" if mismatch else "note", msg)
        if via == "slack_dm":
            ctx.notifier.dm(who_id, f"{msg} Could you check and send the right invoice?", "NOTIFY", task_id=task.id,
                            thread=p.get("thread") or task.slack_ref)
        return ctx.recorder.get(task.id)

    text = (p.get("text") or "").strip()
    quote = text if len(text) <= 160 else text[:157] + "…"
    ctx.recorder.step(task.id, "reply_received", f"{who} replied: “{quote}”", source="slack:dm",
                      evidence={"kind": "chat", "quote": quote, "author": who, "channel": "Slack DM",
                                "permalink": p.get("permalink"), "ref": p.get("permalink") or "slack:dm",
                                "source": "slack:dm"})
    label, category = classify_reply(ctx.llm, text, {"supplier": task.counterparty, "amount_eur": str(abs(task.amount)),
                                                     "invoice": number})
    if label == "explains":
        outcome = f"Resolved · {eur(task.amount)} explained by {who}" + (f" as {category}" if category else "")
        ctx.recorder.step(task.id, "resolved", outcome, source="slack:dm")
        ctx.recorder.update(task.id, status="done", outcome=outcome, resolved_via="slack_dm", category=category)
        ctx.notifier.dm(who_id, f"Thanks! I recorded {eur(task.amount)}" + (f" under {category}" if category else "") +
                        f". Marked resolved in Scout ({task.id}).", "AUTO", task_id=task.id,
                        thread=p.get("thread") or task.slack_ref)
    elif label == "promises_later":
        ctx.recorder.step(task.id, "note", f"{who} will send it later")
        _schedule_reminder(ctx, task.id)
    else:
        ctx.recorder.step(task.id, "note", "The reply doesn't explain the payment yet")
    return ctx.recorder.get(task.id)


HANDLERS = {"bank_txn": on_bank_txn, "reminder": on_reminder, "reply": on_reply}


def run(ctx: JobContext, job: Job) -> Task:
    return HANDLERS[job.kind](ctx, job)
