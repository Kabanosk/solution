# Review T008

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** ce07628870bbfcb30570e317bdf64201514eea5f
- **Head:** 4ef870cbff8064fcfc3f44fea13dc1fbccc04c3e
- **Status:** ok
- **Blockers:** 1
- **Majors:** 2
- **Minors:** 2
- **Nits:** 0
- **Verdict:** The Slack feature is blocked in the current OpenShift deployment. The manifest exists at HEAD. Pytest and live integration were not run per the review constraints.
- **Date:** 2026-11-20 15:29

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER docs/slack-setup.md:68 — The documented startup does not start Slack Socket Mode. The OpenShift worker launches `python -m cfo.run`, which is missing at HEAD, and the API never starts `SlackNotifier`. The worker exits before Slack, Postgres, or vLLM integration can run, so agent DMs, CEO chat, and approval clicks are unavailable.
MAJOR backend/cfo/channels/slack.py:178 — The agent’s `Notifier` interface passes an employee ID string, but this adapter dereferences `employee.id`. Wiring the adapter directly into the agent would raise `AttributeError` on outbound DMs, including approval requests. The fake-client tests pass `Employee` objects and miss this mismatch.
MAJOR backend/cfo/channels/slack.py:275 — Replies to `app_mention` go back to the originating channel. A mapped CEO can ask for financial information in a shared channel, and everyone who can read the thread can see the agent’s answer.
MINOR docs/slack-setup.md:54 — The HR CSV parser discards the documented `slack_id` column, so adding IDs to the sheet leaves seeded Postgres employees unmapped. `SLACK_USER_MAP` is the working configuration path.
MINOR backend/cfo/channels/slack.py:209 — Approval error and refusal messages are sent without a level label, contrary to the requirement that every bot message carry one.
VERDICT: The Slack feature is blocked in the current OpenShift deployment. The manifest exists at HEAD. Pytest and live integration were not run per the review constraints.
