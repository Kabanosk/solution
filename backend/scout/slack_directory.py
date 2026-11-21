"""Slack is the source of truth for who works here.

Every human member of the installed workspace can talk to Scout and be messaged by it, registered or not:

* `sweep` (on install, then periodically) and `team_join` DM each member without an account once, asking for their
  first and last name; the answer creates their account (`employees` row linked to their Slack id);
* a DM from a member without an account gets the same question instead of being ignored;
* messaging a member who never registered creates their account from their Slack profile on the spot
  (`provision`), so `send_dm` reaches anyone in the workspace.

`slack_members` remembers who was asked, so nobody is asked twice.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
from collections.abc import Iterable
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_ROLE = "Staff"
INVITE = ("*Join {workspace} workspace on Scout*\n"
          "Hi! I'm Scout, the finance assistant for {workspace}. You don't have an account yet. "
          "Set it up here (takes a minute): <{link}|Join {workspace} workspace>\n"
          "Or just reply with your *first and last name* and I'll create it for you.")
ASK = ("Hi! I'm Scout, the finance assistant for your team. You don't have a Scout account yet. "
       "Reply with your *first and last name* (for example: Anna Kowalska) and I'll set it up for you.")
ASK_AGAIN = ("I still need your *first and last name* to set up your Scout account. "
             "Reply with just your name, for example: Anna Kowalska.")
WELCOME = ("Thanks, {name}! Your Scout account is ready. You can DM me here any time, for example "
           "\"what's our cash position?\" or \"show my open tasks\".")
_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.\-](?=[^\W\d_]))*\.?$")
# Words that make a short reply a sentence, not a name ("hello there", "what is this").
_NOT_NAME = {"hi", "hey", "hello", "hej", "cześć", "czesc", "siema", "yo", "thanks", "thank", "thx", "ok", "okay",
             "yes", "no", "nope", "sure", "what", "who", "why", "how", "when", "where", "is", "are", "am", "the",
             "a", "an", "i", "im", "i'm", "my", "me", "you", "your", "this", "that", "it", "please", "help", "scout",
             "good", "morning", "there", "test", "name", "and", "or", "to", "of", "for", "in", "on", "do", "can"}

_ready: set[str] = set()


def _ensure(conn: Any) -> None:
    """The table ships with the schema and its upgrade; create it here too so a fresh deploy works at once."""
    key = str(conn.execute("SELECT current_schema() AS s").fetchone()["s"])
    if key in _ready:
        return
    try:
        conn.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS slack_name TEXT")
        conn.execute("CREATE TABLE IF NOT EXISTS slack_members (slack_user_id TEXT PRIMARY KEY, "
                     "status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'registered')), "
                     "employee_id TEXT REFERENCES employees(id) ON DELETE SET NULL, "
                     "invited_at TIMESTAMPTZ NOT NULL DEFAULT now(), registered_at TIMESTAMPTZ)")
        conn.execute("ALTER TABLE slack_members ADD COLUMN IF NOT EXISTS invite_token_hash TEXT")
        conn.execute("ALTER TABLE slack_members ADD COLUMN IF NOT EXISTS slack_name TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS slack_members_invite_idx ON slack_members (invite_token_hash)")
        conn.commit()
    except Exception:  # noqa: BLE001 - no DDL rights: the operator migration created it
        conn.rollback()
    _ready.add(key)


# -- names ------------------------------------------------------------------------------------------------------
def parse_name(text: str) -> str | None:
    """'anna  kowalska' -> 'Anna Kowalska'; None unless it reads as 2-4 name words."""
    text = re.sub(r"<[^>]*>", " ", text or "")  # mentions, links
    text = re.sub(r"^(?:my name is|i am|i'm|im|it's|its|name:)\s+", "", text.strip(), flags=re.IGNORECASE)
    text = text.strip().strip(".!,;:").strip()
    words = text.split()
    if not 2 <= len(words) <= 4 or len(text) > 80:
        return None
    if any(not _WORD.match(w) or w.casefold().strip(".") in _NOT_NAME for w in words):
        return None
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


def profile_name(member: dict[str, Any]) -> str:
    p = member.get("profile") or {}
    return (member.get("real_name") or p.get("real_name") or p.get("display_name") or member.get("name")
            or member.get("id") or "").strip()


def is_human(member: dict[str, Any]) -> bool:
    return bool(member.get("id")) and not (member.get("is_bot") or member.get("deleted") or member.get("is_app_user")
                                           or member.get("id") == "USLACKBOT")


def members(client: Any) -> list[dict[str, Any]]:
    """Every human in the workspace (`users.list`, paginated)."""
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(50):
        res = client.users_list(limit=200, **({"cursor": cursor} if cursor else {}))
        out += [m for m in res.get("members") or [] if isinstance(m, dict) and is_human(m)]
        cursor = str((res.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    return out


def match(people: Iterable[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """The one member a name, @handle or <@U…> refers to; None when nobody or several match."""
    q = (query or "").strip()
    m = re.search(r"<@([UW][A-Z0-9_]+)(?:\|[^>]*)?>", q)
    people = list(people)
    if m:
        return next((p for p in people if p.get("id") == m.group(1)), None)
    q = q.lstrip("@").casefold()
    if not q:
        return None

    def names(p: dict[str, Any]) -> list[str]:
        pr = p.get("profile") or {}
        return [x.casefold() for x in (p.get("real_name"), pr.get("real_name"), pr.get("display_name"),
                                       p.get("name")) if x]

    for test in (lambda n: n == q, lambda n: n.split()[0] == q.split()[0] and q in n,
                 lambda n: n.split()[0] == q):
        hits = {p["id"]: p for p in people if any(test(n) for n in names(p))}
        if len(hits) == 1:
            return next(iter(hits.values()))
        if len(hits) > 1:
            return None
    return None


# -- accounts ---------------------------------------------------------------------------------------------------
def employee_of(cf: Any, slack_id: str) -> str | None:
    with cf() as conn:
        row = conn.execute("SELECT id FROM employees WHERE slack_user_id = %s ORDER BY id LIMIT 1",
                           (slack_id,)).fetchone()
    return row["id"] if row else None


def _slug(name: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L")).encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", ".", ascii_.decode().casefold()).strip(".")


def provision(cf: Any, slack_id: str, name: str) -> str:
    """The employee linked to this Slack id; created (name, role Staff) when there is none."""
    with cf() as conn:
        _ensure(conn)
        row = conn.execute("SELECT id FROM employees WHERE slack_user_id = %s ORDER BY id LIMIT 1 FOR UPDATE",
                           (slack_id,)).fetchone()
        if row:
            emp = row["id"]
        else:
            base = _slug(name) or f"slack.{slack_id.casefold()}"
            emp, n = base, 1
            while conn.execute("SELECT 1 FROM employees WHERE id = %s", (emp,)).fetchone():
                n += 1
                emp = f"{base}.{n}"
            conn.execute("INSERT INTO employees (id, name, role, slack_user_id, slack_name) VALUES (%s, %s, %s, %s, %s)",
                         (emp, name, DEFAULT_ROLE, slack_id, name))
        conn.execute("INSERT INTO slack_members (slack_user_id, status, employee_id, registered_at) "
                     "VALUES (%s, 'registered', %s, now()) ON CONFLICT (slack_user_id) DO UPDATE "
                     "SET status = 'registered', employee_id = EXCLUDED.employee_id, "
                     "registered_at = COALESCE(slack_members.registered_at, now())", (slack_id, emp))
        conn.commit()
    log.info("Slack member %s has the Scout account %s", slack_id, emp)
    return emp


def link(cf: Any, employee_id: str, slack_id: str, slack_name: str) -> bool:
    """Link an existing employee without a Slack id to this member (unless the member already has an account)."""
    with cf() as conn:
        _ensure(conn)
        if conn.execute("SELECT 1 FROM employees WHERE slack_user_id = %s", (slack_id,)).fetchone():
            return False
        done = conn.execute("UPDATE employees SET slack_user_id = %s, slack_name = COALESCE(slack_name, %s) "
                            "WHERE id = %s AND slack_user_id IS NULL", (slack_id, slack_name, employee_id)).rowcount
        if done:
            conn.execute("INSERT INTO slack_members (slack_user_id, status, employee_id, registered_at) "
                         "VALUES (%s, 'registered', %s, now()) ON CONFLICT (slack_user_id) DO UPDATE "
                         "SET status = 'registered', employee_id = EXCLUDED.employee_id", (slack_id, employee_id))
        conn.commit()
    return bool(done)


def status(cf: Any, slack_id: str) -> str | None:
    with cf() as conn:
        _ensure(conn)
        row = conn.execute("SELECT status FROM slack_members WHERE slack_user_id = %s", (slack_id,)).fetchone()
    return row["status"] if row else None


def claim_invite(cf: Any, slack_id: str) -> bool:
    """True exactly once per member: the caller sends the question."""
    with cf() as conn:
        _ensure(conn)
        row = conn.execute("INSERT INTO slack_members (slack_user_id) VALUES (%s) ON CONFLICT DO NOTHING "
                           "RETURNING slack_user_id", (slack_id,)).fetchone()
        conn.commit()
    return row is not None


def new_invite_token(cf: Any, slack_id: str, slack_name: str | None = None) -> str:
    """A fresh one-use join token for this member (only its SHA-256 is stored; the dashboard checks it)."""
    token = secrets.token_urlsafe(32)
    with cf() as conn:
        _ensure(conn)
        conn.execute("INSERT INTO slack_members (slack_user_id, invite_token_hash, slack_name) VALUES (%s, %s, %s) "
                     "ON CONFLICT (slack_user_id) DO UPDATE SET invite_token_hash = EXCLUDED.invite_token_hash, "
                     "slack_name = COALESCE(EXCLUDED.slack_name, slack_members.slack_name), invited_at = now()",
                     (slack_id, hashlib.sha256(token.encode()).hexdigest(), slack_name))
        conn.commit()
    return token


def registered_ids(cf: Any) -> set[str]:
    with cf() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT slack_user_id FROM employees WHERE slack_user_id IS NOT NULL").fetchall()
    return {r["slack_user_id"] for r in rows}
