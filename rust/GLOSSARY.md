# Scout domain glossary

| Original term | Meaning | Rust name |
| --- | --- | --- |
| Tenant | Verified organization, schema and membership | Tenant |
| PolicyLevel | AUTO, NOTIFY or APPROVAL | PolicyLevel |
| Task | Agent investigation with a durable status and evidence | Task |
| Step | Append-only task activity | Step |
| Job | PostgreSQL lease with attempt fencing and deduplication | Job |
| Approval | Explicit approver decision that resumes work | Approval |
| Money | Decimal amount; signed EUR unless currency is provided | Decimal / Money |
| BankTxn | Bank booking | BankTxn |
| InvoiceDoc | Found invoice and source evidence | InvoiceDoc |
| Guardrail | Input, tool-result or output screening | Guardrail |
| Forecast | Model plus deterministic schedules and uncertainty bands | Forecast |
| Loop | invoicing, cost_control, liquidity, chat, expenses, strategy | AgentLoop |
