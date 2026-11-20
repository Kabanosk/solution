# Review T047

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (12/40 today)
- **Base:** 90472beb8d4b8287500e9a6d033db7ae458a5642
- **Head:** 26afc94138926b4c2fca13445d00285a76bac506
- **Status:** ok
- **Blockers:** 0
- **Majors:** 4
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Changes requested before merge. Grounding, incomplete transaction results, and tool-call pairing can produce incorrect answers or fail the chat.
- **Date:** 2026-11-20 17:09

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/chat.py:207 — The router sends a model answer verbatim even when no tool ran or every tool call failed. It can therefore give the CEO a fabricated amount with no source. The grounding prompt alone does not enforce the requirement.  
MAJOR backend/cfo/agent/tool_catalog.py:197 — Transaction queries silently return at most 25 rows by default and 50 at most. The server applies that limit before the catalog sees the result, so even the truncation metadata counts only the returned subset. The model can report a partial sum as the full October spend.  
MAJOR backend/tests/test_ceo_ask.py:206 — The ads-spend test scripts the expected answer using `team_costs`, which aggregates all team outflows and returns no category breakdown. With only ad payments seeded for Marketing in October, the test passes even though the same query would include unrelated marketing expenses.  
MAJOR backend/cfo/chat.py:215 — A response with five or more tool calls is appended in full, but only the first four calls receive tool messages. The next request can fail validation because the assistant turn declares calls with no corresponding results.  
MINOR backend/cfo/chat.py:196 — The booking-date lookup sits outside the error handler. If the database is unavailable after the catalog has been built or cached, the exception escapes instead of returning a chat reply.  
VERDICT: Changes requested before merge. Grounding, incomplete transaction results, and tool-call pairing can produce incorrect answers or fail the chat.
