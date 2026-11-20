# Review T043

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (5/40 today)
- **Base:** 5005ef7e720e0f35f9ef9b4bdcb0fd272e06e99d
- **Head:** b83cce62f7fcddc4402f81c928ec41bcc854dd8f
- **Status:** ok
- **Blockers:** 0
- **Majors:** 1
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested. The acceptance tests were not run because the review was limited to Git and file reading.
- **Date:** 2026-11-20 16:15

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/channels/slack.py:273 — If the chat handler raises on an `app_mention`, the bot posts a public error in the channel thread. The unregistered-user branch at line 264 also posts a public refusal. Both bypass private routing and violate the requirement that the channel receive at most a DM acknowledgment.
MINOR docs/slack-setup.md:54 — The setup guide still references the HR CSV `slack_id` column. The requested documentation fix was to remove that reference if the importer continues to ignore the column.
VERDICT: Changes requested. The acceptance tests were not run because the review was limited to Git and file reading.
