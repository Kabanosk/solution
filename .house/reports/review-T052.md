# Review T052

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (7/40 today)
- **Base:** e86f71547c65389c0c6072548309b3aecb67684f
- **Head:** d7655f86e436b8ca7726f4d49e5eff9266836094
- **Status:** ok
- **Blockers:** 1
- **Majors:** 7
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Request changes. The KSeF approval bypass and flagged-document exposure need fixes, and cluster acceptance remains unverified.
- **Date:** 2026-11-21 02:34

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER backend/scout/worker.py:48 — The KSeF sync path bypasses guardrails and can import or attach the same invoice while its CEO approval is pending or after rejection. The check in the bank-transaction loop does not protect the sync path.

MAJOR backend/scout/documents_api.py:19 — The download endpoint serves stored flagged PDFs before approval and after rejection. It queries by document ID alone, and the shared write-auth dependency allows unauthenticated requests when `API_WRITE_TOKEN` is unset.

MAJOR backend/scout/loops/invoicing.py:338 — When a clean candidate and a flagged candidate appear together, Scout sends an alert for the flagged invoice but never gives the CEO an Approve/Reject card. The same behavior affects file search and PDF replies.

MAJOR backend/scout/loops/invoicing.py:275 — The KSeF check sees only the first 200 lines and the first 300 characters of each description. Suspicious text beyond those limits can escape detection while the invoice is filed automatically.

MAJOR backend/scout/loops/chat.py:326 — A blocked Slack prompt is copied into the task title, which the task read API exposes without write authorization. The dashboard also stores the blocked prompt preview in its task title and step log before screening.

MAJOR deploy/openshift/guardrails/configs/bot-output/config.yaml:25 — The allow-list accepts a colon after an allowed hostname without validating the rest of the URL authority. An allowed-looking hostname can therefore appear in user information while the actual destination is an untrusted host. The finance configuration repeats the same rule.

MAJOR backend/scout/chat_api.py:158 — The endpoint screens history before the current turn and can issue a sequential check for every earlier turn after a batch blocks. With up to 63 earlier turns and a five-second timeout per check, the stream can time out before the current turn is screened or its strike recorded.

MAJOR .house/reports/T052.md:45 — AC-3 is marked incomplete. The report does not provide cluster evidence for the three-strike compliance DM, the poisoned-invoice approval card, the Audit page, or Scout’s MLflow traces.

VERDICT: Request changes. The KSeF approval bypass and flagged-document exposure need fixes, and cluster acceptance remains unverified.
