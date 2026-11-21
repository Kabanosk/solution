<!-- This file is shared: T051 writes the rest (the NeMo service and its rails); this section is T052's. -->

## App wiring (Scout calls the check service)

Scout (`backend/scout/guardrails.py`) calls `POST {GUARDRAILS_URL}/v1/guardrail/checks` with
`Authorization: Bearer {GUARDRAILS_TOKEN}` (5 s timeout, `{"model": "scout", "messages": [...], "guardrails":
{"config_id": "<config>"}}`) at three points:

| Point | config_id | Role sent | When blocked |
|---|---|---|---|
| A human message (Slack DM text, dashboard chat turn), before routing and before any LLM call | `user-input` | user | Reply `This request was blocked by our safety policy (attempt n of 3; repeated attempts are reported to compliance).` No LLM call. Strike. |
| A tool result in the chat tool loop; the text of an invoice (PDF from files, mail, a reply, KSeF lines) before it is read | `tool-result` | user | The model gets `[content withheld: flagged by guardrail]`. No strike. Outside sources (mail, chat, files) also DM the owner once per item. A flagged invoice is never filed on its own: it goes to the owner's approval card with a `Suspicious content` badge and the rail; Approve files it, Reject leaves it unfiled. |
| What Scout says: DM reply, dashboard reply, `send_dm` message | `bot-output` | assistant | The user gets `I could not share that answer safely. Please rephrase your question.` (a blocked `send_dm` is not sent). No strike. |

**Roles.** `user-input`: one `user` message per human turn (the dashboard sends the user turns of the client-supplied history
too, in the same request; one request is one strike). `tool-result`: `tool` messages with a `tool_call_id`.
`bot-output`: one `assistant` message. Status `blocked` blocks, `success` passes, anything else is fail-open.

**Strikes.** A blocked human message is stored in full in the table `guardrail_strikes` (person, time, prompt, rails),
which no API exposes. Strikes are counted per person in a rolling 7-day window. Recording, counting, the alert and the
reset happen in one transaction under a per-person advisory lock. The 3rd open strike DMs the compliance contact
(who, when, the rails and the prompts it consumes, each shortened to 700 characters in the DM) and marks those
strikes reported (`guardrail_escalation`). If the DM was not delivered (no Slack channel), nothing is reset and
`guardrail_escalation_failed` is audited; the next block reports again.

**Fail-open.** `GUARDRAILS_URL` unset: disabled, no calls. Service down, slow (5 s per check), HTTP error or `status: error`:
the text passes, a warning is logged and an audit row `guardrail_unavailable` is written. The worker fires one warm-up call
(30 s timeout) at start, because the first call after a NeMo restart takes 10-25 s.

**Audit.** Every blocking verdict (`guardrail_check`, decision `DENY`, policy = config id, reason = rails), every
fail-open and every escalation shows up on the dashboard Audit page ("DENY only" filters the blocks). The checked content is
never stored in `audit_log` (its `resource` holds the config id, the length and a short hash): the audit API is readable.

**Outside content.** A flagged mail, file or chat message in a tool result is withheld from the model and the owner is
DMed once per item; the item key (mail hash, file path, chat message hash) is kept in `guardrail_alerts` and only
written after the DM was delivered. A flagged invoice goes to the owner's approval card. Approve attaches the original
document as task evidence (mail id or file path, flagged rails) and files the invoice with the number and amount of the
payment, marked unverified in the step, the outcome and the audit row `file_flagged_invoice`; a KSeF invoice is filed with
its own fields. Reject leaves the payment without an invoice.

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
(`guardrail_strikes`, `guardrail_alerts`) is applied by the worker at start. The poisoned invoice demo needs its mail planted:
re-run the seed Job (`python -m scout.seed` plants `mailbox/002-invoice-AS-77666.eml`) and play it with
`python -m scout.demo play poisoned` or the dashboard demo panel ("Poisoned invoice").
