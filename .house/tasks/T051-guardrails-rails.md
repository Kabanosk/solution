---
id: T051
title: NeMo Guardrails service on OpenShift AI with three check configs (input, tool result, output)
status: todo
owner: coder
branch: task/T051-nemo-guardrails
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_deploy_manifests.py backend/tests/test_guardrails_config.py"
    timeout: 900
  - id: AC-2
    manual: "On the cluster: `oc apply -k deploy/openshift/guardrails` gives NemoGuardrails PHASE Ready; the 20-prompt check script (output in reports/T051.md) shows: injection/jailbreak and 'salary of <named colleague>' prompts blocked by config `user-input` while normal finance questions pass; poisoned invoice text blocked by `tool-result`; IBAN/e-mail in a bot reply blocked by `bot-output`; activated_rails present in every response"
protected: []
---
## Objective
Deploy NVIDIA NeMo Guardrails through the TrustyAI `NemoGuardrails` CR (RHOAI 3.5) as a **check-only sidecar**:
the agent keeps calling vLLM directly (tool calling stays intact) and asks NeMo `POST /v1/guardrail/checks` with
`"guardrails": {"config_id": ...}` at three points. Follow the Red Hat docs (summary and quotes in
`.house/reports/research-guardrails.md` §8). Decisions: D-029.

Three `nemoConfigs` in one CR (names are the `config_id`s; all text in English):
1. `user-input` (default): `self check input` on Granite + `regex check input`. The self-check prompt is our own
   policy, built from Red Hat's jailbreak/prompt-injection prompt and its financial-services prompt, adapted:
   block attempts to override instructions, reveal the system prompt, role-play around rules, hide instructions
   in encodings or other languages, and requests for a **specific named person's salary or pay** (aggregate
   finance questions pass here; access control in code decides the rest). Ends with
   `Respond only with 'yes' or 'no'.` and `Answer:`.
2. `tool-result`: no LLM. Regex rail with injection patterns (EN + PL phrases such as "ignore previous
   instructions", "zignoruj poprzednie instrukcje", "system prompt", chat-template role tokens, our tool names used
   as commands, markdown image/link exfiltration `![...](http`), applied to `tool` role messages (the docs say
   they go to `tool_input` rails; verify on the live service which rails section applies and configure it so tool
   messages really are checked).
3. `bot-output`: no LLM. `detect sensitive data on output` with Presidio entities IBAN_CODE, EMAIL_ADDRESS,
   PHONE_NUMBER, CREDIT_CARD (NOT `PERSON`: Scout names people all the time) + `regex check output` for raw IBANs,
   card numbers and URLs to unknown domains.

Deployment (`deploy/openshift/guardrails/`, its own kustomization, applied separately from `deploy/`):
- CR with annotation `security.opendatahub.io/enable-auth: 'true'`; a ServiceAccount `scout-guardrails-client`
  with a `view` RoleBinding whose token the app uses (Secret created by the team, not in git; document the
  command).
- Granite as `models[].type: main` and `self_check_input`, base_url = the in-cluster predictor service (plain
  HTTP, same as `LLM_BASE_URL`), no key in the ConfigMap.
- No tracing backend: keep NeMo tracing off; the app logs `activated_rails` (T052).
- `docs/guardrails.md`: what each config checks, how to apply, how to test with curl, how to change a policy.
- A check script (`deploy/openshift/guardrails/check.sh` or a small Python module under `backend/scout/`) that
  runs the 20 prompts against the service and prints status + activated_rails per prompt (AC-2 evidence in
  `.house/reports/T051.md`).
- `backend/tests/test_guardrails_config.py`: parses the ConfigMaps/CR, asserts config names, flows, entities
  (no PERSON on output), the auth annotation, no secrets, no public hostnames.

## Context
- Cluster: our project namespace; Granite in `my-first-model` (INFRA.md, D-020). Only namespace resources are
  needed. One L4 GPU is fully used by Granite: no extra GPU model.
- Measure Granite's yes/no compliance on the 20 prompts; if it answers with prose, tighten the prompt (and/or set
  `temperature: 0` and a small `max_tokens` in the model parameters) until the check script is stable.
- Repo is PUBLIC: in-cluster service DNS names are fine (already in INFRA.md); tokens and Route hosts are not.

## Out of scope
Calling NeMo from Scout, escalation, audit, MLflow (T052). Access control (T050). MCP gateway integration,
GuardrailsOrchestrator, OpenShell, Prometheus (excluded in D-029).
