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

**Strikes.** Blocked human messages are counted per person in a rolling 7-day window, from `audit_log`
(action `guardrail_check`, decision `DENY`, policy `user-input`). The 3rd block DMs the compliance contact (who, when,
the rails and the three prompts) and writes `guardrail_escalation`, which resets that person's counter.

**Fail-open.** `GUARDRAILS_URL` unset: disabled, no calls. Service down, slow, HTTP error or `status: error`: the
text passes, a warning is logged and an audit row `guardrail_unavailable` is written.

**Audit.** Every blocking verdict (`guardrail_check`, decision `DENY`, policy = config id, reason = rails), every
fail-open and every escalation (decision `ALLOW`) shows up on the dashboard Audit page ("DENY only" filters the blocks).

**MLflow.** Scout's LLM client is wrapped with `traced_llm` and `init_tracing()` runs in the worker (the API app
already calls it); each job is one trace `scout.<job kind>`, guardrail checks are `guardrails.check` spans with
`guardrail.config_id`, `.status`, `.rails`, `.latency_ms`.

### Configuration on the cluster

| Variable | Where | Meaning |
|---|---|---|
| `GUARDRAILS_URL` | ConfigMap `cfo-config` (`deploy/openshift/app/configmap.yaml`) | In-cluster URL of the `scout-guardrails` Service. Check the real name, port and scheme with `oc get svc` (the manifest has `http://scout-guardrails:8000`). Empty = off. |
| `GUARDRAILS_TOKEN` | Secret `guardrails` (optional `envFrom` of api and scout-worker) | Bearer token of an identity with `view` in the project (the ServiceAccount of T051). |
| `COMPLIANCE_CONTACT` | Secret `slack` (like the other Slack variables) | Employee id (e.g. `grazyna`) or Slack user id who gets the compliance DM. Empty = the CEO. |

```bash
# token of the client ServiceAccount created by deploy/openshift/guardrails/rbac.yaml
oc create secret generic guardrails \
  --from-literal=GUARDRAILS_TOKEN="$(oc create token scout-guardrails-client --duration=720h)"
# who gets the compliance DM (Secret `slack` exists already)
oc patch secret slack --type merge -p '{"stringData": {"COMPLIANCE_CONTACT": "vincenzo"}}'
oc rollout restart deploy/api deploy/scout-worker
```

After a code change rebuild the image (`oc start-build cfo-backend`), then restart both deployments. The poisoned
invoice demo needs its mail planted: re-run the seed Job (`python -m scout.seed` plants `mailbox/002-invoice-AS-77666.eml`)
and play it with `python -m scout.demo play poisoned` or the dashboard demo panel ("Poisoned invoice").
