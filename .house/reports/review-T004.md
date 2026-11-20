# Review T004

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** 7d5d9785a9ad3cca1e33ba2f5ba24d0487c53497
- **Head:** 40354328d90020720d4d1c323ecb3c56bdd6a27b
- **Status:** ok
- **Blockers:** 0
- **Majors:** 3
- **Minors:** 2
- **Nits:** 0
- **Verdict:** Changes required. The findings affect runtime tool calls on OpenShift with Postgres independently of vLLM and Slack Socket Mode. Static review only.
- **Date:** 2026-11-20 15:27

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/mcp_servers/workstations.py:94 — The containment check accepts a company-folder symlink to a private sibling directory because it checks the resolved folder’s parent but never checks that the resolved folder is an allowed company folder. Reads, listings, and searches can expose private files if such a symlink is present on the mounted workstation volume.  
MAJOR backend/cfo/mcp_servers/company_db.py:146 — Attaching an invoice number already linked to another payment overwrites that invoice and its line items in Postgres. The original payment remains marked documented and still points to the overwritten invoice.  
MAJOR backend/cfo/mcp_servers/company_db.py:137 — The documented check runs before an unconditional update without locking the transaction row. Concurrent calls with different invoice numbers can both report success for the same payment, with the last update determining which invoice the payment points to.  
MINOR backend/cfo/mcp_servers/workstations.py:114 — `list_workstations` returns employee directory names and folder availability without writing to `access_log`, bypassing the audit requirement for this registered MCP tool.  
MINOR backend/cfo/mcp_servers/workstations.py:106 — A path containing a NUL reaches the denial audit insert, but Postgres rejects NUL in `TEXT`. The call raises a database error instead of returning `access_denied` and leaves no audit row.  
VERDICT: Changes required. The findings affect runtime tool calls on OpenShift with Postgres independently of vLLM and Slack Socket Mode. Static review only.
