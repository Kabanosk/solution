"""Demo helpers for the live cases in `scout.demo_data`.

    python -m scout.demo seed-slack          # post the chat context of the chat cases to #finance
    python -m scout.demo play <scenario>     # post that case's payment to the bank mock (Scout picks it up)
    python -m scout.demo list

The chat messages are posted by the bot under the colleague's name (`chat:write.customize`), the way a real
colleague's message would sit in #finance. The bot must be a member of the channel.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from cfo.data.people import EMPLOYEES
from scout.config import get_scout_settings
from scout.demo_data import SCENARIOS


def seed_slack(client, channel: str = "finance") -> list[str]:  # noqa: ANN001
    cid = None
    cursor = None
    while cid is None:
        res = client.conversations_list(types="public_channel,private_channel", limit=200, cursor=cursor)
        cid = next((c["id"] for c in res.get("channels", []) if c.get("name") == channel), None)
        cursor = (res.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    if cid is None:
        raise SystemExit(f"#{channel} not found (invite the Scout app to it first)")
    names = {e.id: e.name for e in EMPLOYEES}
    posted = []
    for s in SCENARIOS.values():
        if s.chat_message:
            client.chat_postMessage(channel=cid, text=s.chat_message, username=names.get(s.chat_author, "Colleague"))
            posted.append(s.key)
    return posted


def play(scenario: str, bank_url: str) -> dict:
    s = SCENARIOS[scenario]
    r = httpx.post(f"{bank_url.rstrip('/')}/mock/transactions", timeout=5, json={
        "counterparty": s.counterparty, "amount": str(s.amount), "reference": s.reference, "type": s.type,
        "holder_employee_id": s.holder_employee_id, "card_last4": s.card_last4})
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout.demo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-slack")
    sub.add_parser("list")
    p = sub.add_parser("play")
    p.add_argument("scenario", choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    s = get_scout_settings()
    if args.cmd == "list":
        for sc in SCENARIOS.values():
            print(f"{sc.key:16} {sc.counterparty:30} {sc.amount:>10}  {sc.label}")
    elif args.cmd == "seed-slack":
        if not s.slack_bot_token:
            print("SLACK_BOT_TOKEN is not set", file=sys.stderr)
            return 1
        from slack_sdk import WebClient

        print("posted:", ", ".join(seed_slack(WebClient(token=s.slack_bot_token), s.chat_channels[0])))
    else:
        txn = play(args.scenario, s.bank_api_url)
        print(f"posted {txn['id']} ({args.scenario}) to the bank mock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
