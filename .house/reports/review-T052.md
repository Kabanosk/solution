# Review T052

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (6/40 today)
- **Base:** 40ff9fc61a43627ae8c5c7ae09fb4e3220943de3
- **Head:** 499dc10d66e47b14d286c1b9acdab37551c433ac
- **Status:** ok
- **Blockers:** 0
- **Majors:** 7
- **Minors:** 2
- **Nits:** 0
- **Verdict:** Request changes. The diff has guardrail bypasses, incorrect strike accounting, and an approval retry gap. AC-3 cluster evidence is still pending in the task report.
- **Date:** 2026-11-21 01:48

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/scout/loops/chat.py:210 — Slack screens the answer after the raw reply has already been saved in the task step at line 332. The task API exposes that step, and later Slack turns feed the blocked reply back into the model as conversation history.  
MAJOR backend/scout/chat_api.py:160 — Dashboard strikes count blocked text from prior turns. The dashboard resends a blocked prompt alongside its completed refusal, so two harmless follow-ups can trigger compliance escalation after a single blocked prompt.  
MAJOR backend/scout/guardrails.py:159 — The check silently drops all chunks after the first 100. A valid `/v1/chat` request can fill that limit with earlier turns, leaving the current user message unchecked before Scout passes it to the model.  
MAJOR backend/scout/guardrails.py:404 — When the batch check blocks assistant history but an individual recheck times out, the code passes the original turn to the model. A transient failure can therefore release content from a batch that already received a blocking verdict.  
MAJOR backend/scout/chat_api.py:155 — The dashboard streams the LLM-generated conversation summary before applying the output rail. Sensitive content reproduced in that summary reaches the browser even if the final answer is blocked.  
MAJOR backend/scout/loops/invoicing.py:336 — If a flagged invoice is followed by a clean match, Scout sends a warning and files the clean match without creating an approval card for the flagged invoice. The same branch exists for files and replies, so the CEO cannot approve or reject those flagged items.  
MAJOR backend/scout/loops/liquidity.py:194 — The approval is committed before the workflow files the flagged invoice. If the worker crashes or filing raises after that commit, a retry sees the approval as already answered and returns without filing.  
MINOR backend/scout/documents_api.py:25 — Supplier-controlled filenames are inserted directly into the response header. A filename containing characters outside Latin-1, such as `Faktura Łódź.pdf`, causes Starlette to raise while constructing the download response.  
MINOR backend/scout/guardrails.py:339 — A successful suspicious-content DM records deduplication state but no `guardrail_escalation` audit row, so the Audit page cannot show that the CEO was notified.  
VERDICT: Request changes. The diff has guardrail bypasses, incorrect strike accounting, and an approval retry gap. AC-3 cluster evidence is still pending in the task report.
