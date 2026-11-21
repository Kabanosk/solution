---
id: T052
title: Scout calls NeMo checks at three points, strikes and compliance escalation, audit and MLflow
status: review
owner: coder
branch: task/T052-scout-guardrails
depends_on: ["T051"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/scout/test_guardrails.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests/scout backend/tests/test_tracing.py backend/tests/test_deploy_manifests.py"
    timeout: 1800
  - id: AC-3
    manual: "On the cluster with the new image: (1) a DM asking for a named colleague's salary gets the blocked message 'attempt 1 of 3', and the third attempt sends the compliance DM with the three prompts; (2) the poisoned-invoice demo case lands on the CEO approval card with a 'Suspicious content' badge naming the rail instead of being filed; (3) the Audit page shows guardrail rows; (4) MLflow shows Scout LLM traces. Evidence in reports/T052.md"
protected: []
---
## Objective
Wire Scout to the NeMo check service from T051, exactly as decided with Wojtek (D-029). All user-facing text in
English.

1. **Client** `scout/guardrails.py`: `check(config_id, messages) -> Verdict(status, activated_rails, latency)`
   over `POST {GUARDRAILS_URL}/v1/guardrail/checks` with `Authorization: Bearer {GUARDRAILS_TOKEN}` and a short
   timeout (about 5 s). `GUARDRAILS_URL` unset ⇒ disabled (everything passes, no calls). Service down, error or
   timeout ⇒ **fail-open**: pass, log a warning, write an audit row (`guardrail_unavailable`).
2. **Human messages** (Slack DM text and the dashboard `/v1/chat` user turn), before routing and before any LLM
   call: config `user-input`. Blocked ⇒ reply `This request was blocked by our safety policy (attempt n of 3;
   repeated attempts are reported to compliance).`, no LLM call, audit row with the rails.
3. **Strikes and escalation:** count blocked human messages per person in a rolling **7-day** window (in Postgres,
   e.g. a `guardrail_events` table or `audit_log` rows). On the 3rd block: DM to `COMPLIANCE_CONTACT` (new env var
   read from Secret `slack` like the other Slack vars; employee id or Slack user id; empty ⇒ the CEO) with who,
   when, the rails and the text of the three blocked prompts. Then reset that person's counter.
4. **Tool results** in the chat tool loop (the `Toolbox.call` result before it is appended as `role=tool`) and
   document text in the invoicing loop (KSeF / e-mail / file / PDF invoice text before `extract_invoice`): config
   `tool-result`. Blocked ⇒ the model gets `[content withheld: flagged by guardrail]` instead of the content;
   audit row; **not** a strike for the user. If the source is external (supplier invoice, mail, KSeF), DM the CEO
   once per item about suspicious content.
5. **Flagged invoice:** an invoice whose text was flagged is never filed/attached automatically. It goes to the CEO
   approval card (reuse the existing approval / Button mechanism) with a `Suspicious content` badge and the rail
   name; Approve files it, Reject leaves it unfiled; both audited. Add a demo case (poisoned supplier invoice with
   an injected instruction) next to the existing demo cases so it can be played live.
6. **Bot output** (DM reply in `chat._finish`, dashboard reply in `chat_api`): config `bot-output`. Blocked ⇒ the
   user gets `I could not share that answer safely. Please rephrase your question.`; audit row; not a strike.
7. **Audit:** every blocking verdict, every fail-open and every escalation writes `audit_log` (action
   `guardrail_check` / `guardrail_escalation`, decision BLOCK or ALLOW, policy = config_id, reason = activated
   rails), so the existing dashboard Audit page shows them (add a filter chip if trivial).
8. **MLflow:** Scout's LLM calls are traced (wrap `get_llm()` with `traced_llm` and call `init_tracing()` in the
   worker and the chat API, as `cfo/api/app.py` does); guardrail checks appear as spans with config_id, status and
   rails.
9. **Deploy:** env `GUARDRAILS_URL` (in-cluster service of the CR) in the configmap; `GUARDRAILS_TOKEN` and
   `COMPLIANCE_CONTACT` from Secrets (document the `oc` commands in `docs/guardrails.md`); update
   `test_deploy_manifests`.

## Context
- Hook points (from code reading, 2026-11-21): LLM choke point `cfo/llm.py` `OpenAICompatLLM.chat`; chat loop
  `scout/loops/chat.py` (`answer` ~:128, tool append ~:158, `_finish` ~:179, `on_dm` ~:216); tools
  `scout/loops/toolbox.py` (`Toolbox.call` ~:146); invoicing `scout/loops/invoicing.py` and
  `scout/llm_tasks.py` `extract_invoice`; Slack `scout/slack_ui.py` (`dm`, `_blocks`); approvals
  `scout/loops/liquidity.py` (~:119-190); audit `scout/audit.py`; dashboard chat `scout/chat_api.py`; tracing
  `cfo/observability.py`; demo cases `scout/demo.py`, `scout/demo_data.py`.
- Tests use a fake guardrails client (no network): blocked input, strikes 1-2-3 + escalation + reset + 7-day
  window, tool result withheld, flagged invoice → approval card, bot-output rephrase, fail-open, disabled mode.
- T050 (access control, `task/T050-scout-access`) and T051 (NeMo service, `task/T051-nemo-guardrails`) are built in parallel; the coordinator merges them into one
  PR. Do not touch salary filtering or `deploy/openshift/guardrails/` here; add your env/secret docs as a section of `docs/guardrails.md` (T051 owns the rest of that file).

## Out of scope
New rails or policies (T051), access control (T050), Prometheus, approval for `send_invoices`.
