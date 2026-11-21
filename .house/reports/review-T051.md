# Review T051

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (3/40 today)
- **Base:** ab3d64149783c2131a0d8a7b087fc5b16093b347
- **Head:** 8ad92e173a024bf7db1a6af3c3ed9f406537aa10
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes before approval. Review was static, with cluster results taken from the task report.
- **Date:** 2026-11-21 01:23

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR docs/guardrails.md:72 — NeMo logs every checked message in plaintext, including blocked replies containing financial or contact data. The deployment leaves this logging enabled, exposing the data to pod-log readers and any configured log collectors.

MAJOR deploy/openshift/guardrails/configs/bot-output/config.yaml:20 — The URL allow-list uses a word boundary where it needs an authority boundary. An unknown hostname beginning with an allowed domain followed by a dot can pass the output rail.

MAJOR docs/guardrails.md:81 — The documented curl command and the script example both disable TLS verification while sending the reusable service-account bearer token. Use the ingress CA or the OpenShift service CA so the client verifies the endpoint before sending credentials.

MINOR deploy/openshift/guardrails/check_prompts.py:134 — The script accepts successful responses with a missing `activated_rails` field because it converts absence to an empty list and checks the field only for blocks. The reported 20/20 therefore cannot establish the AC-2 requirement that every response contains the field.

VERDICT: Request changes before approval. Review was static, with cluster results taken from the task report.
