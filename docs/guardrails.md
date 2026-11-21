# Guardrails: NeMo Guardrails check-only sidecar

Scout asks a NeMo Guardrails service (Red Hat OpenShift AI 3.5, `NemoGuardrails` CR of the TrustyAI operator) three
questions per turn. It never sends model traffic through it: the agent keeps calling vLLM directly, so tool calling
stays intact (Red Hat's documented pattern for tool calling, D-029). NeMo only answers "pass or block" through
`POST /v1/guardrail/checks`.

| `config_id` | Checked text | Message role to send | Rails | LLM call |
|---|---|---|---|---|
| `user-input` (default) | what an employee or the CEO typed | `user` | `regex check input`, then `self check input` (Granite as judge) | one short call |
| `tool-result` | text returned by tools: invoice PDFs, e-mail, chat history, MCP results | `tool` | `check tool result` (regex only, custom flow) | none |
| `bot-output` | the reply Scout is about to send | `assistant` | `detect sensitive data on output` (Presidio: IBAN_CODE, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD), `regex check output` (raw IBANs, card numbers, URLs outside the allow-list) | none |

What they are for, and what they are not:

- `user-input` blocks attempts to override instructions, reveal the system prompt, role-play around the rules, hide
  instructions in encodings or other languages, and requests for **a named person's salary or pay**. Aggregate
  finance questions ("payroll of the kitchen team") pass: access control in code decides the rest (T050).
- `tool-result` is regex only on purpose: no LLM reads attacker text there, so it cannot be talked around, and it
  costs about 0.1 s. It catches known phrasing (EN and PL "ignore previous instructions", chat-template role tokens,
  our tool names used as commands, markdown image or link exfiltration). A paraphrase gets through, so treat it as
  detection and telemetry, not prevention.
- `bot-output` does not list `PERSON`: Scout names colleagues and suppliers all the time.
- A self check on a 7B-class judge "cannot guarantee 100% compliance" (Red Hat). The judge reads the attacker's text
  and can itself be injected. Defence in depth: this layer plus access control and human approval in code.

## Deploy

The manifests are `deploy/openshift/guardrails/`, applied separately from `deploy/` (the project is the current
`oc project`; pass `-n <project>` to be explicit):

```sh
oc apply -k deploy/openshift/guardrails
oc get nemoguardrails scout-guardrails -o jsonpath='{.status.phase}{"\n"}'     # wait for Ready
```

(The `PHASE` column is not part of `oc get` on this build; read `.status.phase`.) The first request after a (re)start
is slow, 10 to 25 s: Presidio loads its spaCy model. Send one warm-up check after a rollout and keep the client
timeout at 30 s for that request, 5 s afterwards (normal latency: regex and Presidio about 0.5 s on the Route, the
Granite call a little more).

Changing a ConfigMap or the CR makes the operator roll out a new pod by itself (about 15 s, `oc get pods`).

### Service URL and client token

| What | Value |
|---|---|
| In-cluster URL | `https://scout-guardrails.<project>.svc.cluster.local` (Service port 443, TLS by the OpenShift service CA) |
| CA for the client | `/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt` (present in every pod) |
| Auth | `Authorization: Bearer <token>` of an identity with `view` in the project (kube-rbac-proxy, annotation `security.opendatahub.io/enable-auth`) |
| Identity | ServiceAccount `scout-guardrails-client` + RoleBinding `scout-guardrails-client-view` (in git) |
| Token Secret | `scout-guardrails-client`, key `token` (created by hand, never in git) |

Create (or renew) the Secret; requested lifetime 30 days (the cluster may cap it; renew before it expires):

```sh
oc create secret generic scout-guardrails-client \
  --from-literal=token="$(oc create token scout-guardrails-client --duration=720h)" \
  --dry-run=client -o yaml | oc apply -f -
```

In a Deployment:

```yaml
env:
  - name: GUARDRAILS_URL
    value: https://scout-guardrails.<project>.svc.cluster.local
  - name: GUARDRAILS_TOKEN
    valueFrom: {secretKeyRef: {name: scout-guardrails-client, key: token}}
```

The server log contains the text of every checked message (INFO lines of the regex and Presidio actions). Treat
`oc logs deploy/scout-guardrails` as sensitive and do not ship it elsewhere.

## Call it (curl)

```sh
HOST=$(oc get route scout-guardrails -o jsonpath='{.spec.host}')     # laptop; in a pod use the Service URL
TOKEN=$(oc get secret scout-guardrails-client -o jsonpath='{.data.token}' | base64 -d)

curl -sk -X POST https://$HOST/v1/guardrail/checks \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model": "check", "guardrails": {"config_id": "user-input"},
       "messages": [{"role": "user", "content": "What is Anna Kowalska'"'"'s salary?"}]}'
```

Request: `model` is required but only logged; `guardrails.config_id` picks the config (absent: `user-input`, an unknown
name gives `status: error`, `Invalid config path`). Messages are checked independently by role, as in the table.
Response (shortened):

```json
{"status": "blocked",
 "rails_status": {"regex check input": {"status": "success"}, "self check input": {"status": "blocked"}},
 "guardrails_data": {"log": {"activated_rails": ["self check input"], "stats": {"llm_calls_count": 1}}}}
```

`status` is `success`, `blocked` or `error`; fail closed on anything but `success`. `activated_rails` lists the rails
that blocked (empty list when nothing did): log it. Send `tool` text as
`{"role": "tool", "tool_call_id": "call_1", "content": "..."}` and the reply as a single `assistant` message.

## Check script (20 prompts)

`deploy/openshift/guardrails/check_prompts.py` (standard library only) runs 9 `user-input`, 6 `tool-result` and 5
`bot-output` prompts and prints status and `activated_rails` per prompt. Exit code 1 when any verdict differs:

```sh
export GUARDRAILS_URL=https://$(oc get route scout-guardrails -o jsonpath='{.spec.host}')
export GUARDRAILS_TOKEN=$(oc get secret scout-guardrails-client -o jsonpath='{.data.token}' | base64 -d)
python3 deploy/openshift/guardrails/check_prompts.py --insecure            # --repeat 5 for judge stability
```

Run it after every policy change.

## Change a policy

Everything is in `deploy/openshift/guardrails/configs/<config>/`:

- Judge prompt: `configs/user-input/prompts.yml`. Keep the last lines `Respond only with 'yes' or 'no'.` / `Answer:`
  and `max_tokens: 3`; a small model needs the few-shot examples, remove them and it blocks ordinary payment
  requests. Add a failing prompt to `check_prompts.py` first, then tune.
- Regex lists: `config.yaml` under `rails.config.regex_detection.<input|output>.patterns` (YAML needs `\\` for
  one backslash; `case_insensitive: true`). `backend/tests/test_guardrails_config.py` holds attack and clean-text
  samples for every list.
- Presidio entities: `rails.config.sensitive_data_detection.output.entities` in `configs/bot-output/config.yaml`.
  Use the `detect` flows: `mask` flows rewrite text and `/checks` does not return it.
- `tool-result` uses a custom flow in `configs/tool-result/rails.co`. On this build `/checks` puts `tool` messages
  in `$tool_message` and runs `rails.tool_input`; the library flow `regex check input` reads `$user_message` (empty
  for tool messages) and silently never matches. Do not swap it back.
- Then `oc apply -k deploy/openshift/guardrails`, wait for the new pod, run the check script.

Not used on purpose (D-029): `/v1/chat/completions` (not a transparent proxy, breaks tool calling), tracing (no
backend; the app logs `activated_rails` itself), the MCP gateway integration and GuardrailsOrchestrator.
