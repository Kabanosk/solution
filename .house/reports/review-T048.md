# Review T048

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (11/40 today)
- **Base:** 0941ea515cde9150655943c95d7ccf8a220b1a35
- **Head:** c0d6ad3c2f28f8d80bbd5588332a584d6cb30e6a
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 2
- **Nits:** 0
- **Verdict:** Changes requested. Findings are from static review only.
- **Date:** 2026-11-20 17:01

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR docs/cluster-setup.md:87 — The reviewed commit lacks `reports/T048.md` with captured in-cluster PASS output, so AC-3 remains unverified.  
MAJOR backend/cfo/smoke.py:141 — The T041 parser assigns `get_weather` to any JSON object when only one tool is offered. The smoke check therefore reports PASS for `{}` even though the required `city` argument is missing.  
MAJOR backend/cfo/smoke.py:97 — `GET /models` omits the configured bearer token. An authenticated endpoint can return 401 for this probe even when both chat checks work with `LLM_API_KEY`.  
MINOR backend/cfo/smoke.py:60 — Details are rendered verbatim, so multiline server responses or exceptions can break the one-line-per-check output.  
MINOR backend/cfo/smoke.py:248 — The fallback client keeps the fixed 60-second timeout, so `--llm-timeout` does not bound every LLM request as the help text promises.  
VERDICT: Changes requested. Findings are from static review only.
