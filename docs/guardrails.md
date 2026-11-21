# Guardrails: NeMo Guardrails check-only sidecar

Scout asks a NeMo Guardrails service (Red Hat OpenShift AI 3.5, `NemoGuardrails` CR of the TrustyAI operator) three
questions per turn. It never sends model traffic through it: the agent keeps calling vLLM directly, so tool calling
stays intact (Red Hat's documented pattern for tool calling, D-029). NeMo only answers "pass or block" through
`POST /v1/guardrail/checks`.

| `config_id` | Checked text | Message role to send | Rails | LLM call |
|---|---|---|---|---|
| `user-input` (default) | what an employee or the CEO typed | `user` | `regex check input`, then `self check input` (Granite as judge) | one short call |
| `tool-result` | text returned by tools: invoice PDFs, e-mail, chat history, MCP results | `tool` | `check tool result` (regex only, custom flow) | none |
| `bot-output` | the reply Scout is about to send | `assistant` | `detect sensitive data on output` (Presidio: IBAN_CODE, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD), `regex check output` (raw IBANs, card numbers, URLs whose host is not an allowed domain: `svc.cluster.local`, `localhost`, `127.0.0.1`, `slack.com`, `ksef.mf.gov.pl`, `ksef-test.mf.gov.pl`, each with its subdomains) | none |
| `bot-output-finance` | the same, but for a reply to the owner or the office manager (they may see bank, mail and payroll data) | `assistant` | `regex check output` with only the URL allow-list of `bot-output` (no Presidio, no IBAN or card pattern) | none |

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

### Logs

The stock NeMo server logs the full text of every checked message at INFO (action parameters and results of the
regex, Presidio and dispatcher steps), which would put replies with IBANs, e-mail addresses or salaries into pod logs
and any log collector. The image has no setting for this (the level is `logging.basicConfig(level=INFO)` in the
server code), so every config ships `configs/quiet_logs.py` as `config.py`: the server imports it when it loads the
config and it sets the root and `nemoguardrails` loggers to WARNING. Only uvicorn's access lines remain (method, path,
status, no content). Verify after every change of the CR or the images:

```sh
oc logs deploy/scout-guardrails -c nemo-guardrails --tail=200 | grep -c "<a word you just sent>"    # expect 0
```

If a later image ignores `config.py`, the log holds message text again: then restrict who may read pod logs in the
project (`pods/log` get, admins only) and do not forward them.

## Call it (curl)

```sh
# Laptop: the Route, verified against the cluster ingress CA. In a pod: the Service URL and
# --cacert /var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt (OpenShift service CA).
HOST=$(oc get route scout-guardrails -o jsonpath='{.spec.host}')
oc get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' > ingress-ca.crt
TOKEN=$(oc get secret scout-guardrails-client -o jsonpath='{.data.token}' | base64 -d)

curl -s --cacert ingress-ca.crt -X POST https://$HOST/v1/guardrail/checks \
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

## Check script (32 prompts)

`deploy/openshift/guardrails/check_prompts.py` (standard library only) runs 9 `user-input`, 6 `tool-result`, 11 `bot-output` and 6 `bot-output-finance`
prompts (some try to smuggle a URL past the allow-list) and prints status and `activated_rails`
per prompt. Exit code 1 when any verdict differs or when `guardrails_data.log.activated_rails` is missing from any
response. It always verifies the TLS certificate before it sends the token (no switch to skip that):

```sh
export GUARDRAILS_URL=https://$(oc get route scout-guardrails -o jsonpath='{.spec.host}')
export GUARDRAILS_TOKEN=$(oc get secret scout-guardrails-client -o jsonpath='{.data.token}' | base64 -d)
python3 deploy/openshift/guardrails/check_prompts.py --ca ingress-ca.crt   # --repeat 5 for judge stability
```

Run it after every policy change.

## Change a policy

Everything is in `deploy/openshift/guardrails/configs/<config>/`:

- Judge prompt: `configs/user-input/prompts.yml`. Keep the last lines `Respond only with 'yes' or 'no'.` / `Answer:`
  and `max_tokens: 3`; a small model needs the few-shot examples, remove them and it blocks ordinary payment
  requests. Add a failing prompt to `check_prompts.py` first, then tune.
- URL allow-list: the host must be an allowed domain or a subdomain of it and end right there (an optional `:port`, then `/ ? #`, whitespace,
  `)` or end of text), so `slack.com.evil.net` and `slack.com@evil.net` are blocked. Add domains to the
  alternation in `configs/bot-output/config.yaml` and `configs/bot-output-finance/config.yaml` (keep both the same) and a bypass sample to the test.
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

<!-- This file is shared: T051 writes the rest (the NeMo service and its rails); this section is T052's. -->

## App wiring (Scout calls the check service)

Scout (`backend/scout/guardrails.py`) calls `POST {GUARDRAILS_URL}/v1/guardrail/checks` with
`Authorization: Bearer {GUARDRAILS_TOKEN}` (5 s timeout, `{"model": "scout", "messages": [...], "guardrails":
{"config_id": "<config>"}}`) at three points:

| Point | config_id | Role sent | When blocked |
|---|---|---|---|
| A human message (Slack DM text, dashboard chat turn), before routing and before any LLM call | `user-input` | user | Reply `This request was blocked by our safety policy (attempt n of 3; repeated attempts are reported to compliance).` No LLM call. Strike. |
| A tool result in the chat tool loop; the text of an invoice (PDF from files, mail, a reply, KSeF lines) before it is read | `tool-result` | user | The model gets `[content withheld: flagged by guardrail]`. No strike. Outside sources (mail, chat, files) also DM the owner once per item. A flagged invoice is never filed on its own: it goes to the owner's approval card with a `Suspicious content` badge and the rail; Approve files it, Reject leaves it unfiled. |
| What Scout says: DM reply, dashboard reply, `send_dm` message | `bot-output-finance` for a recipient who is the owner or the office manager, else `bot-output` (the recipient's role decides, also for `send_dm`) | assistant | The user gets `I could not share that answer safely. Please rephrase your question.` (a blocked `send_dm` is not sent). No strike. |

**Role-aware output.** For recipients without finance access Scout first masks IBANs (`[IBAN hidden]`), card numbers of 13-19 digits that pass the Luhn check (`[card hidden]`), e-mail addresses (`[email hidden]`) and phone numbers (`[phone hidden]`) in Python, writes an audit row `guardrail_mask` (decision `MASK`, policy `bot-output-mask`, no content), and sends the masked text to `bot-output`; success sends the masked text, a block gives the rephrase message. Links to Scout's own dashboard (`DASHBOARD_URL`) are swapped for an allow-listed placeholder host during the check and restored afterwards, so no public hostname is committed in the regex. With guardrails disabled nothing is masked.

**Roles.** `user-input`: one `user` message per human turn (the dashboard sends the user turns of the client-supplied history
too, in the same request; one request is one strike). `tool-result`: `tool` messages with a `tool_call_id`.
`bot-output`: one `assistant` message. Status `blocked` blocks, `success` passes, anything else is fail-open.

**Strikes.** A blocked human message is stored in full in the table `guardrail_strikes` (person, time, prompt, rails),
which no API exposes. Strikes are counted per person in a rolling 7-day window. Recording, counting, the alert and the
reset happen in one transaction under a per-person advisory lock. The 3rd open strike DMs the compliance contact
(who, when and the rails in the message; the prompts it consumes in full as a `.txt` file sent with it, shortened to 700 characters in the message only if no file can be sent) and marks those
strikes reported (`guardrail_escalation`). If the DM was not delivered (no Slack channel), nothing is reset and
`guardrail_escalation_failed` is audited; the next block reports again.

**Fail-open.** `GUARDRAILS_URL` unset: disabled, no calls. Service down, slow (5 s per check), HTTP error or `status: error`:
the text passes, a warning is logged and an audit row `guardrail_unavailable` is written.

**Warm-up.** The first check after a NeMo restart takes 10-25 s, which is longer than the 5 s check timeout. Measured
cause: about 9 s of Python imports on the first config load (Presidio pulls in torch, transformers and spaCy for every
config, even `user-input`, and the load blocks the server's event loop), plus up to 10 s until the route sends traffic
to the new pod. The Presidio/spaCy model load of `bot-output` is only ~2 s of it. Every config also builds its own
rails on first use, and the server caches them per `config_id` + request `model`. So `HttpGuardrails.warm_up()` sends
one check to each of the four configs with the app's model name (`scout`, 30 s timeout), once when the worker starts.
A check that fails open on a **timeout** also starts a background warm-up (at most one per 60 s, none while one runs),
so a NeMo restart the worker did not see is re-warmed; connection errors and HTTP errors do not trigger it. Use the same
model name in manual checks (`check_prompts.py` does): a second name costs another ~630 MiB of bot-output rails.

**Resources.** The NemoGuardrails CR has no resources field, so `limitrange.yaml` gives the namespace's containers that
set none (the NeMo container, its CA init container, debug pods) a request of 100m / 1280Mi and a limit of 2 CPU / 2560Mi,
and `nemoguardrails.yaml` caps the BLAS/OpenMP threads at 2 to match. Keep both or neither: the cap without a CPU limit
made bot-output ~20 ms slower at 4 concurrent checks. The memory limit allows three model names before an OOMKill.

**Audit.** Every blocking verdict (`guardrail_check`, decision `BLOCK`, policy = config id, reason = rails), every
fail-open and every escalation shows up on the dashboard Audit page ("DENY / BLOCK only" filters them; BLOCK is a red badge). The checked content is
never stored in `audit_log` (its `resource` holds the config id, the length and a short hash): the audit API is readable.

**Long texts and history.** Texts over 8000 characters are cut into overlapping chunks, each sent as its own message of one `/checks` call (any blocked chunk blocks). The assistant turns of the dashboard history are client-supplied, so they are checked with `tool-result`; a flagged one reaches the model as `[content withheld: flagged by guardrail]` (audited, no strike).

**Outside content.** A flagged mail, file or chat message in a tool result is withheld from the model and the owner is
DMed once per item; the item key (mail hash, file path, chat message hash) is kept in `guardrail_alerts` and only
written after the DM was delivered. A flagged invoice goes to the owner's approval card. Approve attaches the original
document as task evidence (mail id or file path, flagged rails) and files the invoice with the number and amount of the
payment, marked unverified in the step, the outcome and the audit row `file_flagged_invoice`; a KSeF invoice is filed with
its own fields. Reject leaves the payment without an invoice. The original flagged PDF is kept in `flagged_documents` (evidence `document_id`, `sha256`) and served by `GET /v1/documents/{id}` (`scout/documents_api.py`) with the write token. While the card is pending the task ignores new replies (audit `flagged_pending`). A flagged candidate that is not on a card (another candidate matched) gets the once-per-document suspicious-content DM.

**MLflow.** Scout's LLM client is wrapped with `traced_llm` and `init_tracing()` runs in the worker (the API app
already calls it); each job is one trace `scout.<job kind>`, guardrail checks are `guardrails.check` spans with
`guardrail.config_id`, `.status`, `.rails`, `.latency_ms`.

### Configuration on the cluster

| Variable | Where | Meaning |
|---|---|---|
| `GUARDRAILS_URL` | ConfigMap `cfo-config` | `https://scout-guardrails.cfo-agent-uwur.svc.cluster.local` (443). Empty = off. |
| `GUARDRAILS_CA` | optional | CA bundle for that https URL. Default `/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt` (the OpenShift service CA), used when the file exists, else the system CAs. |
| `GUARDRAILS_TOKEN` | env of api and scout-worker from Secret `scout-guardrails-client`, key `token` (optional) | Bearer token of the client ServiceAccount made by T051. Nothing to create here. |
| `COMPLIANCE_CONTACT` | Secret `slack` (like the other Slack variables) | Employee id (e.g. `grazyna`) or a Slack user id (`U...`, used as it is). Empty = the CEO. |

```bash
# who gets the compliance DM (Secret `slack` exists already)
oc patch secret slack --type merge -p '{"stringData": {"COMPLIANCE_CONTACT": "vincenzo"}}'
oc apply -k deploy/ && oc rollout restart deploy/api deploy/scout-worker
```

After a code change rebuild the image (`oc start-build cfo-backend`), then restart both deployments. The schema
(`guardrail_strikes`, `guardrail_alerts`, `flagged_documents`, the BLOCK decision) is no longer applied at worker start: run
`python -m cfo.db.migrate --migrate` (see `docs/scout-auth.md`) once per release. The poisoned invoice demo needs its mail planted:
re-run the seed Job (`python -m scout.seed` plants `mailbox/002-invoice-AS-77666.eml`) and play it with
`python -m scout.demo play poisoned` or the dashboard demo panel ("Poisoned invoice").

## Live demo kit

`docs/guardrails-demo.md` is the presenter's runbook (seven attack scenarios, checklist, troubleshooting). The prompts live
in `backend/scout/demo_guard.py`; `python -m scout.demo guard-preflight` checks them against the NeMo service (no strikes,
no audit rows), `guard-reset` returns the demo personas to "attempt 1 of 3", `plant` writes the evidence files (poisoned mail) and runs as the one-off Job `deploy/openshift/guardrails/demo-plant-job.yaml`, because the worker mounts the data volume read-only.
